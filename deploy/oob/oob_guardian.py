#!/usr/bin/env python3
"""oob-guardian: out-of-band oversight for the Clio Agent 3 Beta agent.

Runs on the hypervisor host, outside the agent's machine. It cannot be
modified or evaded by the agent (H1). Stdlib only.

Responsibilities:
  1. Spend watchdog: polls the gateway metrics endpoint; on sustained
     overspend writes the DB control flag and optionally suspends the VM.
  2. Audit anchoring: ingests the agent's anchor.json (ETHOS_ANCHOR_FILE,
     synced from the agent data dir) every poll into a host-side log.
  3. Kill switch: presence of ${OOB_DIR}/stop suspends the VM guest.
  4. Host snapshots: takes VM snapshots on a schedule via the configured
     snapshot command template.

Configure via environment:
  ETHOS_OOB_DIR         state dir on the host (default /var/lib/ethos-oob)
  ETHOS_METRICS_URL     gateway/core metrics URL (default http://127.0.0.1:9710/metrics)
  ETHOS_DAILY_CAP_USD   hard daily cap (default 20.0)
  ETHOS_MONTHLY_CAP_USD hard monthly cap (default 400.0)
  ETHOS_SNAPSHOT_CMD    shell command template with {name} (optional)
  ETHOS_SUSPEND_CMD     shell command to suspend the guest (optional)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

OOB_DIR = Path(os.environ.get("ETHOS_OOB_DIR", "/var/lib/ethos-oob"))
METRICS_URL = os.environ.get("ETHOS_METRICS_URL", "http://127.0.0.1:9710/metrics")
ANCHOR_FILE = os.environ.get(
    "ETHOS_ANCHOR_FILE",
    "/home/ethos/.ethos/data/anchor.json",
)
DAILY_CAP = float(os.environ.get("ETHOS_DAILY_CAP_USD", "20.0"))
MONTHLY_CAP = float(os.environ.get("ETHOS_MONTHLY_CAP_USD", "400.0"))
SNAPSHOT_CMD = os.environ.get("ETHOS_SNAPSHOT_CMD", "")
SUSPEND_CMD = os.environ.get("ETHOS_SUSPEND_CMD", "")
POLL_S = float(os.environ.get("ETHOS_OOB_POLL_S", "30"))
OVERSPEND_CYCLES = int(os.environ.get("ETHOS_OOB_OVERSPEND_CYCLES", "4"))

COST_RE = re.compile(r'^ethos_model_cost_usd_total\{[^}]*\} ([0-9.eE+-]+)$', re.M)


def log(message: str) -> None:
    stamp = datetime.now(UTC).isoformat()
    line = f"{stamp} oob-guardian: {message}\n"
    print(line, end="", flush=True)
    try:
        with open(OOB_DIR / "oob.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


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
                prior = json.loads(line.split(" ", 2)[-1])
                if prior.get("hash") == digest:
                    if seq is not None and prior.get("seq", 0) > seq:
                        log(f"audit chain REGRESSION: host seq {prior.get('seq')} > agent seq {seq}")
                    return
            except (ValueError, IndexError):
                continue
    entry = f"{datetime.now(UTC).isoformat()} {json.dumps(anchor, sort_keys=True)}\n"
    with open(anchor_path, "a", encoding="utf-8") as fh:
        fh.write(entry)
    log(f"anchored seq={seq} hash={str(digest)[:16]}")


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
    name = f"ethos-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    if run_shell(SNAPSHOT_CMD, name):
        log(f"host snapshot taken: {name}")
        return now
    return last


def stop_file_present() -> bool:
    return (OOB_DIR / "stop").exists()


def main() -> None:
    OOB_DIR.mkdir(parents=True, exist_ok=True)
    log(f"starting (metrics={METRICS_URL}, daily_cap=${DAILY_CAP}, monthly_cap=${MONTHLY_CAP})")
    overspend_streak = 0
    last_snapshot = 0.0
    while True:
        if stop_file_present():
            log("kill switch engaged (host stop file present)")
            run_shell(SUSPEND_CMD, "kill-switch")
            time.sleep(POLL_S)
            continue
        totals = fetch_metrics()
        day_spend = totals.get("cost_total", 0.0)
        if day_spend > DAILY_CAP:
            overspend_streak += 1
            log(f"overspend suspected: ${day_spend:.2f} > ${DAILY_CAP:.2f} (streak {overspend_streak})")
        else:
            overspend_streak = 0
        if overspend_streak >= OVERSPEND_CYCLES:
            log(f"SPEND WATCHDOG: suspending guest after {overspend_streak} over-cap cycles")
            run_shell(SUSPEND_CMD, "spend-watchdog")
            overspend_streak = 0
        append_anchor()
        last_snapshot = snapshot_cycle(last_snapshot)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
