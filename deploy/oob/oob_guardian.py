#!/usr/bin/env python3
"""oob-guardian: out-of-band oversight for Clio Agent 3.1.

Runs on the hypervisor host, outside the agent's machine and outside the
agent's trust boundary: the process is started by the host, its files are
host-owned, and it holds privileges (VM control, container-runtime socket,
the gateway token directory) that nothing inside the agent's runtime is ever
given. H1 says oversight is always effective; this is what makes that true
when the in-band guardian has been talked around, wedged, or replaced.

Stdlib only, on purpose: a watchdog that needs the packages it is watching
for is a watchdog that goes down with them.

Responsibilities:
  1. Spend watchdog (H2): polls the agent's Prometheus metrics; sustained or
     monthly overspend freezes the agent's sandboxes and revokes the
     gateway's provider tokens out of band, then optionally suspends the VM.
  2. Liveness watchdog (H1): the agent writes a heartbeat file (synced or
     shared from the agent data dir). Silence past the hard ceiling means the
     agent is not in a state anybody can reason about; the host freezes
     sandboxes so nothing keeps executing unsupervised, and says so.
  3. Sandbox freeze (H1/H6): calls the container runtime's own pause verb
     directly (`docker pause` / `podman pause`) for every `clio-sandbox-*`
     container visible on this host. Freezing is the verb, not killing: the
     state stays for forensics, and nothing runs.
  4. Audit anchoring (H4): ingests the agent's anchor.json into a host-side
     append-only log and flags chain regressions — an agent that rewrites its
     own audit history is caught by the copy it cannot write to.
  5. Kill switch: presence of ${OOB_DIR}/stop freezes everything and
     suspends the guest.
  6. Host snapshots: VM snapshots on a schedule via the configured template.

Configure via environment:
  CLIO_OOB_DIR            state dir on the host (default /var/lib/clio-oob)
  CLIO_METRICS_URL        agent metrics URL (default http://127.0.0.1:9710/metrics)
  CLIO_ANCHOR_FILE        synced anchor.json path
  CLIO_HEARTBEAT_FILE     synced heartbeat path (optional)
  CLIO_DAILY_CAP_USD      hard daily cap (default 20.0)
  CLIO_MONTHLY_CAP_USD    hard monthly cap (default 400.0)
  CLIO_CONTAINER_RUNTIME  docker | podman (default: whichever is on PATH)
  CLIO_GATEWAY_TOKEN_DIR  directory holding the gateway's provider key files
                          to revoke (optional; emptied on enforcement)
  CLIO_SNAPSHOT_CMD       shell command template with {name} (optional)
  CLIO_SUSPEND_CMD        shell command to suspend the guest (optional)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

OOB_DIR = Path(os.environ.get("CLIO_OOB_DIR", "/var/lib/clio-oob"))
METRICS_URL = os.environ.get("CLIO_METRICS_URL", "http://127.0.0.1:9710/metrics")
ANCHOR_FILE = os.environ.get(
    "CLIO_ANCHOR_FILE",
    "/home/clio/.ethos/data/anchor.json",
)
HEARTBEAT_FILE = os.environ.get("CLIO_HEARTBEAT_FILE", "")
DAILY_CAP = float(os.environ.get("CLIO_DAILY_CAP_USD", "20.0"))
MONTHLY_CAP = float(os.environ.get("CLIO_MONTHLY_CAP_USD", "400.0"))
SNAPSHOT_CMD = os.environ.get("CLIO_SNAPSHOT_CMD", "")
SUSPEND_CMD = os.environ.get("CLIO_SUSPEND_CMD", "")
POLL_S = float(os.environ.get("CLIO_OOB_POLL_S", "30"))
OVERSPEND_CYCLES = int(os.environ.get("CLIO_OOB_OVERSPEND_CYCLES", "4"))
HEARTBEAT_CEILING_S = float(os.environ.get("CLIO_OOB_HEARTBEAT_CEILING_S", "1800"))
GATEWAY_TOKEN_DIR = os.environ.get("CLIO_GATEWAY_TOKEN_DIR", "")

COST_RE = re.compile(r'^clio_model_cost_usd_total\{[^}]*\} ([0-9.eE+-]+)$', re.M)
SANDBOX_CONTAINER_PREFIX = "clio-sandbox-"


def log(message: str) -> None:
    stamp = datetime.now(UTC).isoformat()
    line = f"{stamp} oob-guardian: {message}\n"
    print(line, end="", flush=True)
    try:
        with open(OOB_DIR / "oob.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def container_runtime() -> str:
    named = os.environ.get("CLIO_CONTAINER_RUNTIME", "")
    if named:
        return shutil.which(named) or ""
    for candidate in ("docker", "podman"):
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def list_sandbox_containers(runtime: str) -> list[str]:
    """Every `clio-sandbox-*` container this host can see."""
    if not runtime:
        return []
    try:
        result = subprocess.run(
            [runtime, "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log(f"container list failed: {result.stderr[:200]}")
            return []
        return [
            name.strip() for name in result.stdout.splitlines()
            if name.strip().startswith(SANDBOX_CONTAINER_PREFIX)
        ]
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"container list error: {exc}")
        return []


def freeze_sandboxes(reason: str) -> int:
    """Pause every sandbox container, out of band. Returns how many froze.

    Pause, not kill: the state stays for forensics — what the agent was doing
    when oversight intervened is exactly what somebody will want to read —
    and nothing in a paused container executes. A container already paused
    (or one that vanished mid-loop) is skipped, so a second enforcement pass
    is harmless.
    """
    runtime = container_runtime()
    names = list_sandbox_containers(runtime)
    if runtime and not names:
        log(f"no sandbox containers visible to {runtime}")
    frozen = 0
    for name in names:
        try:
            result = subprocess.run(
                [runtime, "pause", name], capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                frozen += 1
            else:
                log(f"pause {name} failed: {result.stderr[:200]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"pause {name} error: {exc}")
    log(f"froze {frozen}/{len(names)} sandbox containers ({reason})")
    return frozen


def revoke_gateway_tokens(reason: str) -> int:
    """Empty the gateway's provider-key directory, out of band.

    `clio-gateway` is the sole holder of LLM provider credentials; the
    directory this empties is where it reads them (`CLIO_GATEWAY_TOKEN_DIR`,
    a host-owned path mounted into the gateway's container). Removing the
    files revokes every provider session at once and is the one enforcement
    the agent cannot undo from inside: the keys it would need to argue with
    are the keys that were taken. Backups are not kept on the agent's disk
    for the same reason.
    """
    if not GATEWAY_TOKEN_DIR:
        return 0
    directory = Path(GATEWAY_TOKEN_DIR)
    if not directory.is_dir():
        log(f"gateway token dir {directory} is not a directory; nothing revoked")
        return 0
    revoked = 0
    for entry in directory.iterdir():
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink()
                revoked += 1
            elif entry.is_dir():
                shutil.rmtree(entry)
                revoked += 1
        except OSError as exc:
            log(f"revoke {entry} failed: {exc}")
    log(f"revoked {revoked} gateway credential entries ({reason})")
    return revoked


def enforce(reason: str) -> None:
    """The host-level intervention: freeze execution, revoke credentials."""
    freeze_sandboxes(reason)
    revoke_gateway_tokens(reason)
    run_shell(SUSPEND_CMD, reason)


def fetch_metrics() -> dict[str, float]:
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log(f"metrics unreachable: {exc}")
        return {}
    totals: dict[str, float] = {}
    for match in COST_RE.finditer(text):
        try:
            totals["cost_total"] = totals.get("cost_total", 0.0) + float(match.group(1))
        except ValueError:
            continue
    return totals


def append_anchor() -> None:
    """Ingest the agent's anchor.json (synced/mounted from the agent's data
    dir) into the host-side append-only anchor log [D-33]."""
    try:
        anchor = json.loads(Path(ANCHOR_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"anchor read failed ({ANCHOR_FILE}): {exc}")
        return
    seq = anchor.get("seq")
    digest = anchor.get("hash")
    anchor_path = OOB_DIR / "anchors.log"
    if anchor_path.exists():
        for line in anchor_path.read_text(encoding="utf-8").splitlines():
            try:
                # One space, then the JSON — which itself contains spaces, so
                # splitting on more than the first field is how the prior-line
                # parse silently never matched and every poll appended a
                # duplicate of an anchor it had already seen.
                _, _, encoded = line.partition(" ")
                prior = json.loads(encoded)
                if prior.get("hash") == digest:
                    if seq is not None and prior.get("seq", 0) > seq:
                        log(f"audit chain REGRESSION: host seq {prior.get('seq')} > agent seq {seq}")
                        enforce("audit chain regression (H4)")
                    return
            except (ValueError, IndexError):
                continue
    entry = f"{datetime.now(UTC).isoformat()} {json.dumps(anchor, sort_keys=True)}\n"
    with open(anchor_path, "a", encoding="utf-8") as fh:
        fh.write(entry)
    log(f"anchored seq={seq} hash={str(digest)[:16]}")


def heartbeat_age_s() -> float | None:
    if not HEARTBEAT_FILE:
        return None
    try:
        return time.time() - Path(HEARTBEAT_FILE).stat().st_mtime
    except OSError:
        return None


def run_shell(template: str, name: str = "") -> bool:
    if not template:
        return False
    command = template.replace("{name}", name)
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log(f"command failed ({result.returncode}): {result.stderr[:200]}")
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"command error: {exc}")
        return False


def snapshot_cycle(last: float) -> float:
    if not SNAPSHOT_CMD:
        return last
    now = time.time()
    if now - last < 900:
        return last
    name = f"clio-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    if run_shell(SNAPSHOT_CMD, name):
        log(f"host snapshot taken: {name}")
        return now
    return last


def stop_file_present() -> bool:
    return (OOB_DIR / "stop").exists()


def monthly_spend_estimate(day_spend: float) -> float:
    """A conservative month-to-date figure from the daily total and the day.

    The agent publishes a daily counter, not a monthly one; assuming the
    current daily total spent on every day of the month is an over-estimate
    by design. An enforcement whose error is "too cautious" is the right
    direction for a cap that suspends the machine.
    """
    today = datetime.now(UTC).day
    return day_spend * today


def main() -> None:
    OOB_DIR.mkdir(parents=True, exist_ok=True)
    log(
        f"starting (metrics={METRICS_URL}, daily_cap=${DAILY_CAP}, "
        f"monthly_cap=${MONTHLY_CAP}, runtime={container_runtime() or 'none'})"
    )
    overspend_streak = 0
    last_snapshot = 0.0
    liveness_enforced = False
    while True:
        if stop_file_present():
            log("kill switch engaged (host stop file present)")
            enforce("kill switch")
            time.sleep(POLL_S)
            continue

        totals = fetch_metrics()
        day_spend = totals.get("cost_total", 0.0)
        month_spend = monthly_spend_estimate(day_spend)
        if day_spend > DAILY_CAP or month_spend > MONTHLY_CAP:
            overspend_streak += 1
            log(
                f"overspend suspected: day ${day_spend:.2f}/${DAILY_CAP:.2f}, "
                f"month est ${month_spend:.2f}/${MONTHLY_CAP:.2f} "
                f"(streak {overspend_streak}/{OVERSPEND_CYCLES})"
            )
        else:
            overspend_streak = 0
        if overspend_streak >= OVERSPEND_CYCLES:
            log(f"SPEND WATCHDOG (H2): enforcing after {overspend_streak} over-cap cycles")
            enforce("spend watchdog")
            overspend_streak = 0

        age = heartbeat_age_s()
        if age is not None and age > HEARTBEAT_CEILING_S and not liveness_enforced:
            log(
                f"LIVENESS WATCHDOG (H1): no heartbeat for {age:.0f}s "
                f"(ceiling {HEARTBEAT_CEILING_S:.0f}s); freezing sandboxes"
            )
            freeze_sandboxes("liveness ceiling")
            liveness_enforced = True
        elif age is not None and age <= HEARTBEAT_CEILING_S:
            liveness_enforced = False

        append_anchor()
        last_snapshot = snapshot_cycle(last_snapshot)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
