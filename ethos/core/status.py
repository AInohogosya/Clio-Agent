from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime

from ethos.config import EthosConfig
from ethos.db import Database

# The kind the life loop publishes on, on every cycle and after every failed one.
#
# It is the evidence the whole product agrees on: the interface's agent link
# treats anything arriving on it as a sign of life, the bridge relays it, and this
# module reads the same rows — so `ethos status`, the terminal client and the
# browser page cannot report three different states for one machine.
PRESENCE_EVENT = "presence.update"

# How long the agent may say nothing at all before it counts as gone.
#
# The same window Phone's agent link uses before it answers a question with "the
# agent is not running" (`AGENT_ABSENT_MS`, fifteen minutes), and for the same
# reason: the agent publishes on every cycle while it works and every half minute
# while it is paused, so a gap this wide is not a slow agent. Two code bases, one
# number, on purpose — a status command with its own idea of "gone" would call an
# agent present at exactly the moment the browser calls it missing.
AGENT_ABSENT_S = 900.0


def agent_liveness(
    config: EthosConfig,
    *,
    absent_after_s: float = AGENT_ABSENT_S,
    after: datetime | None = None,
) -> tuple[bool, float | None, str]:
    """Whether the agent is running, how long since it was last heard, and why not.

    Read from the agent's own bus rather than from a pid or a port, because those
    answer a different question. A supervisor whose children have all died is a
    live process with nothing behind it, and a bridge with the agent down serves
    the agent's last known state perfectly — so both would happily report a
    machine where nobody is home. The sign of life is the one thing that cannot be
    left over from a process that has ended.

    `after` asks the sharper question a launcher has: not "is it running" but "has
    it said anything since I started it". Freshness is what proves a start-up
    worked, where presence inside the window only proves the agent was there at
    some point in the last quarter of an hour.
    """
    dsn = os.environ.get("ETHOS_DSN") or config.database.dsn
    try:
        seen, now = asyncio.run(_observation(dsn))
    except Exception as exc:  # a status command reports, it does not raise
        return False, None, str(exc)
    if seen is None:
        return False, None, "the agent has never published a sign of life"
    age = (now - seen).total_seconds()
    if age < 0:
        # A clock ahead of the database's, which is a disagreement rather than a
        # fact about the agent. Counting it as life is the kinder reading: it
        # cannot invent an absent agent out of a skew, only a present one.
        age = 0.0
    if after is not None and seen <= after:
        return False, age, "nothing since this command started"
    stopped = _recorded_stop(config, seen)
    if stopped is not None:
        return False, age, stopped
    if age > absent_after_s:
        return False, age, f"nothing for {int(age)}s (the agent counts as gone after {int(absent_after_s)}s)"
    return True, age, ""


def _recorded_stop(config: EthosConfig, seen: datetime | None) -> str | None:
    """The agent's own record of having stopped, when it is newer than its last word.

    Recency alone has one blind spot, and it is the common one: an agent that
    stopped a minute ago still has presence from a minute ago, so for as long as
    the window lasts a machine with nobody on it reads as an agent that is merely
    quiet. The life loop already writes down how its last pass ended, and a
    snapshot newer than the last sign of life that says `stopped` is the one piece
    of positive evidence that closes the gap.

    It is a record of an ending, exactly as the control plane's `stopped` flag is
    (see `ControlPlane.load`), so it is only believed when it is *newer* than the
    last thing the agent said. An agent killed outright leaves no such record and
    keeps the benefit of the doubt until the window closes — which is the same
    answer the browser gives, and for the same reason.
    """
    path = config.paths.data_dir / "continuity.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if not isinstance(state, dict) or state.get("state") != "stopped":
        return None
    saved_at = _as_utc_from_iso(payload.get("saved_at"))
    if seen is None or saved_at is None or saved_at <= seen:
        return None
    stamp = saved_at.astimezone().strftime("%H:%M:%S")
    return f"it recorded its own stop at {stamp}, after its last sign of life"


def _as_utc_from_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _observation(dsn: str) -> tuple[datetime | None, datetime]:
    """The agent's last sign of life and the database's own clock, in one round trip.

    Both timestamps come from PostgreSQL on purpose. Comparing an event the
    database stamped against a clock read on this machine is how a few seconds of
    skew becomes "the agent has been gone for four minutes", and a launcher that
    waits on that waits for the wrong thing.
    """
    db = await Database.try_connect(dsn, timeout_s=3.0)
    if db is None:
        raise RuntimeError(
            f"no answer from the database at {dsn.rsplit('@', 1)[-1]} — "
            "try `scripts/ensure_postgres.sh up`"
        )
    try:
        row = await db.fetchrow(
            "SELECT now() AS now, "
            "(SELECT ts FROM events WHERE kind = $1 ORDER BY ts DESC LIMIT 1) AS seen",
            PRESENCE_EVENT,
        )
    finally:
        await db.close()
    return _as_utc(row["seen"]), _as_utc(row["now"]) or datetime.now(UTC)


def database_now(config: EthosConfig) -> datetime:
    """The database's clock, so a wait can be measured in the same clock as the evidence."""
    dsn = os.environ.get("ETHOS_DSN") or config.database.dsn
    try:
        return asyncio.run(_observation(dsn))[1]
    except Exception:
        return datetime.now(UTC)


def interface_port(config: EthosConfig) -> int:
    """The port the bridge is on, resolved the way the bridge resolves it.

    `ETHOS_WEB_PORT` first, then `config/channels.yaml` — the same order and the
    same file the bridge uses. A status line pointing at a different port than the
    one the bridge is listening on would be the most useless output here.
    """
    from_env = os.environ.get("ETHOS_WEB_PORT")
    if from_env:
        try:
            port = int(from_env)
            if 0 < port < 65536:
                return port
        except ValueError:
            pass
    return config.channels.web.http_port


def _page_state(url: str, timeout_s: float) -> tuple[bool, str]:
    """Whether the bridge is serving the page, and what it said instead.

    The health route answers long before the page does, and it answers just as
    happily from a bridge that started before the interface was built: that one
    holds the port, serves its whole API, and has no page at all. Checking only
    `/api/health` is what let `ethos status` print `interface: up` over a 404 —
    so this asks for the page too, and the two answers are reported separately.

    A 503 carrying the bridge's own "not built" page is counted as served: it is
    a bridge that is working correctly and has nothing to serve, and the message
    it returns says how to fix that. A page that is missing or is not HTML is not
    that — it is a bridge that cannot do the one job this URL exists for.
    """
    try:
        with urllib.request.urlopen(f"{url}/", timeout=timeout_s) as response:
            content_type = response.headers.get("Content-Type", "")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except OSError:
            pass
        if exc.code == 503 and "not built" in body:
            return True, "the interface is not built yet (`npm run build`)"
        return False, f"the page is not served (answered {exc.code})"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"the page is not served ({getattr(exc, 'reason', exc)})"
    if "text/html" not in content_type:
        return False, f"the page is not served (answered {status} as {content_type or 'nothing'})"
    return True, "page served"


def interface_health(config: EthosConfig, timeout_s: float = 2.0) -> tuple[str, bool, str]:
    """The bridge's address, whether it is serving the interface, and what it said.

    The second value is False for a bridge that answers its API but cannot serve
    the page, which is the state that used to be reported as healthy: every
    command in this project treats that URL as a person opening a browser, so a
    bridge that cannot render the page is not up, however healthy its API is.
    """
    url = f"http://127.0.0.1:{interface_port(config)}"
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return url, False, f"answered {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return url, False, f"no answer ({getattr(exc, 'reason', exc)})"
    database = payload.get("db")
    database_detail = "database up" if database == "up" else f"database {database}"
    page_up, page_detail = _page_state(url, timeout_s)
    return url, page_up, f"{database_detail}, {page_detail}"


def describe_age(age_s: float | None) -> str:
    """A duration as the shortest honest phrase: `just now`, `40s ago`, `3m ago`."""
    if age_s is None:
        return "moments ago"
    if age_s < 10:
        return "just now"
    if age_s < 90:
        return f"{int(age_s)}s ago"
    if age_s < 5400:
        return f"{int(age_s // 60)}m ago"
    if age_s < 172800:
        return f"{int(age_s // 3600)}h ago"
    return f"{int(age_s // 86400)}d ago"


def _as_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def agent_log_dir(config: EthosConfig) -> str:
    """Where the agent's per-process logs are, for a message that has to point at one."""
    return str(config.paths.data_dir / "logs")
