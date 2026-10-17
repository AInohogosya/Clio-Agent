"""A delegation must never be reported as work that was done when it was not.

Four separate paths could turn a failed, blocked or interrupted sub-agent run
into a success, and all four ended the same way: the GSL loop read `ok=True` off
the envelope, marked the plan step done, and advanced. The step after that one
believed the objective had been met.

They are tested here together rather than per-layer because the bug was never in
one layer -- each was locally reasonable, and only the composition was wrong.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from ethos.bus import EventBus
from ethos.guardian.gate import GuardianGate
from ethos.guardian.journal import ActionJournal, DuplicateAction
from ethos.guardian.trash import TrashStore
from ethos.memory.continuity import ContinuityManager
from ethos.schemas.gsl import SolverOutcome
from ethos.schemas.tools import ToolErrorCode, ToolResult
from ethos.subagents.base import STATUS_BLOCKED, STATUS_FAILED, SubAgentResult
from ethos.toolhost.server import Tool, ToolContext, Toolhost
from tests.conftest import (
    FakeRegistry,
    FakeVerifier,
    ListAudit,
    NoopControl,
    NoopSnapshots,
    make_config,
)

pytestmark = pytest.mark.scenario


class _CountingFoundry(Tool):
    """A sub-agent tool that records how many times it was actually invoked."""

    name = "subagent.foundry"
    description = "fake foundry"
    timeout_s = 30.0
    input_schema = {"type": "object", "properties": {"task": {"type": "string"}}}

    def __init__(self, result: SubAgentResult) -> None:
        self.result = result
        self.calls = 0

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        from ethos.subagents.tools import _as_result

        self.calls += 1
        return _as_result(self.result.to_dict())


async def _host(db, tmp_path: Path, tools: list[Tool]) -> Toolhost:
    config = make_config(tmp_path)
    config.paths.ensure()
    host = Toolhost(
        config,
        gate=GuardianGate(
            config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "trash"),
            snapshots=NoopSnapshots(), audit=ListAudit(),
            verifier=FakeVerifier(supported=True), control=NoopControl(),
        ),
        journal=ActionJournal(db=db),
        services={},
    )
    for tool in tools:
        host.register(tool)
    return host


def _key() -> str:
    return f"{uuid.uuid4()}:1:s1:0"


def _loop_outcome(result: ToolResult) -> SolverOutcome:
    """Exactly the branch `gsl/loop.py` takes on a tool's envelope."""
    all_ok = result.ok
    return SolverOutcome.step_done if all_ok else SolverOutcome.step_failed


# ---- a sub-agent that did not finish ----------------------------------------

@pytest.mark.parametrize(
    "status, label",
    [
        (STATUS_FAILED, "failed"),
        (STATUS_BLOCKED, "blocked"),
        ("unknown", "an outcome that may or may not have happened"),
        ("timeout", "timed out"),
    ],
)
async def test_a_subagent_that_did_not_finish_is_not_a_done_step(db, tmp_path: Path, status, label):
    tool = _CountingFoundry(SubAgentResult(
        agent="foundry", status=status, summary="0/3 tasks merged", mode="inprocess",
    ))
    host = await _host(db, tmp_path, [tool])
    key = _key()

    result = await host.execute(
        "subagent.foundry", {"task": "build it"},
        ToolContext(thread_id="self", idempotency_key=key),
    )

    assert result.ok is False, f"a run that {label} is not a successful action"
    assert result.content["status"] == status, "the real status must survive to the caller"
    assert _loop_outcome(result) is SolverOutcome.step_failed

    row = await db.fetchrow(
        "SELECT status FROM action_journal WHERE idempotency_key = $1", key
    )
    assert row["status"] != "ok", "a failed delegation must not be journalled as a success"


async def test_a_completed_subagent_is_still_a_done_step(db, tmp_path: Path):
    """The fix must not turn honest successes into failures."""
    tool = _CountingFoundry(SubAgentResult(
        agent="foundry", status="done", summary="3/3 tasks merged", mode="inprocess",
    ))
    host = await _host(db, tmp_path, [tool])
    key = _key()

    result = await host.execute(
        "subagent.foundry", {"task": "build it"},
        ToolContext(thread_id="self", idempotency_key=key),
    )

    assert result.ok is True
    assert result.content["summary"] == "3/3 tasks merged"
    assert _loop_outcome(result) is SolverOutcome.step_done
    row = await db.fetchrow(
        "SELECT status FROM action_journal WHERE idempotency_key = $1", key
    )
    assert row["status"] == "ok"


# ---- a duplicate of a completed action --------------------------------------

async def test_a_duplicate_of_a_completed_action_replays_it(db, tmp_path: Path):
    """The guarantee the idempotency key exists for, and the reason it may not
    be weakened: a repeat of work already done returns the first answer."""
    tool = _CountingFoundry(SubAgentResult(agent="foundry", status="done", summary="3/3 merged"))
    host = await _host(db, tmp_path, [tool])
    key = _key()

    first = await host.execute("subagent.foundry", {"task": "t"}, ToolContext(idempotency_key=key))
    second = await host.execute("subagent.foundry", {"task": "t"}, ToolContext(idempotency_key=key))

    assert first.ok and second.ok
    assert second.content.get("idempotent") is True
    assert tool.calls == 1, "a completed action must not run twice"


# ---- the interrupted case: a crash mid-delegation ---------------------------

async def test_a_crash_mid_delegation_is_never_reported_as_success(db, tmp_path: Path):
    """The bug this whole file exists for.

    A sub-agent was running when the process died. On restart the journal row is
    reconciled to `interrupted` -- which says the attempt may or may not have
    taken effect. The GSL loop replans the same step, and before the fix the
    replay answered `ok=True`, so the step was marked done and the plan advanced
    over a delegation that had never returned.
    """
    tool = _CountingFoundry(SubAgentResult(agent="foundry", status="done"))
    host = await _host(db, tmp_path, [tool])
    key = _key()

    # The WAL row is written before execution and never closed: a crash.
    await host.journal.begin(
        thread_id="self", intention_id=None, tool="subagent.foundry",
        args_redacted={"task": "build the thing"}, reason="step s1",
        idempotency_key=key, side_effects=["execute"],
    )

    bus = EventBus(db, source="test-crash")
    await bus.start()
    try:
        report = await ContinuityManager(make_config(tmp_path), db=db, bus=bus).wake_up()
    finally:
        await bus.stop()

    assert report["reconciled_actions"] == 1
    assert "subagent.foundry" in report["interrupted_tools"]

    result = await host.execute(
        "subagent.foundry", {"task": "build the thing"},
        ToolContext(thread_id="self", idempotency_key=key),
    )

    assert result.ok is False, "an interrupted delegation is not a completed one"
    assert "unknown" in (result.error or "").lower(), \
        "the caller must be told the effect is unknown, not that it failed cleanly"
    assert _loop_outcome(result) is SolverOutcome.step_failed
    assert tool.calls == 0, "an attempt of unknown effect must not be re-run blind"


async def test_reconciliation_explains_itself(db, tmp_path: Path):
    """`NULL || jsonb` is NULL in Postgres, so the note was being discarded in
    exactly the case it exists for: an attempt that died before writing one."""
    host = await _host(db, tmp_path, [])
    key = _key()
    action_id = await host.journal.begin(
        thread_id="self", intention_id=None, tool="subagent.foundry",
        args_redacted={}, reason="step s1", idempotency_key=key, side_effects=[],
    )
    bus = EventBus(db, source="test-note")
    await bus.start()
    try:
        await ContinuityManager(make_config(tmp_path), db=db, bus=bus).wake_up()
    finally:
        await bus.stop()

    row = await db.fetchrow(
        "SELECT status, result_digest FROM action_journal WHERE id = $1", action_id
    )
    assert row["status"] == "interrupted"
    digest = row["result_digest"]
    if isinstance(digest, str):
        digest = json.loads(digest)
    assert digest is not None, "the row must say why it was interrupted"
    assert digest["outcome"] == "unknown"
    assert "subagent.foundry" in digest["detail"]


# ---- a failed attempt must not block its own retry -------------------------

async def test_a_failed_attempt_does_not_block_its_retry(db, tmp_path: Path):
    """`idempotency_key` is UNIQUE, so a failed attempt left its key permanently
    unusable: every retry collided with the corpse of the attempt that had just
    failed, and was answered as though it were the original outcome."""
    tool = _CountingFoundry(SubAgentResult(agent="foundry", status=STATUS_FAILED, summary="gate failed"))
    host = await _host(db, tmp_path, [tool])
    key = _key()
    ctx = ToolContext(thread_id="self", idempotency_key=key)

    first = await host.execute("subagent.foundry", {"task": "t"}, ctx)
    assert first.ok is False
    corpse = await db.fetchrow(
        "SELECT id FROM action_journal WHERE idempotency_key = $1", key
    )

    # The step is retried: the same intention, strategy, step and action index.
    second = await host.execute("subagent.foundry", {"task": "t"}, ctx)

    assert tool.calls == 2, "a failed attempt must not block the retry that replaces it"
    assert second.ok is False, "and the retry reports its own outcome, not the old row's"
    assert second.content.get("prior") is None

    rows = await db.fetch(
        "SELECT status, idempotency_key FROM action_journal "
        "WHERE id = $1 OR idempotency_key = $2 ORDER BY ts_start",
        corpse["id"], key,
    )
    assert [r["status"] for r in rows] == ["failed", "failed"]
    assert rows[0]["idempotency_key"] is None, "the corpse keeps its audit row but frees the key"


async def test_an_in_determinate_attempt_is_not_released(db, tmp_path: Path):
    """The opposite decision, for the opposite reason. An interrupted attempt may
    still be in effect, so its key is not handed to another attempt -- two
    attempts must not come to own one piece of work."""
    journal = ActionJournal(db=db)
    key = _key()
    action_id = await journal.begin(
        thread_id="self", intention_id=None, tool="subagent.foundry",
        args_redacted={}, reason="s", idempotency_key=key, side_effects=[],
    )
    await db.execute(
        "UPDATE action_journal SET status = 'interrupted', ts_end = now() WHERE id = $1",
        action_id,
    )

    with pytest.raises(DuplicateAction) as caught:
        await journal.begin(
            thread_id="self", intention_id=None, tool="subagent.foundry",
            args_redacted={}, reason="s", idempotency_key=key, side_effects=[],
        )
    assert caught.value.prior["status"] == "interrupted"

    row = await db.fetchrow("SELECT idempotency_key FROM action_journal WHERE id = $1", action_id)
    assert row["idempotency_key"] == key, "an attempt of unknown effect keeps its key"


# ---- the journal's own rule -------------------------------------------------

async def test_only_a_completed_success_is_replayable():
    from ethos.guardian.journal import replayable

    assert replayable({"status": "ok"}) is True
    for status in ("failed", "timeout", "rejected", "interrupted", "running", "", None):
        assert replayable({"status": status}) is False, f"{status!r} must not be replayed"
    assert replayable(None) is False


async def test_subagent_tools_carry_their_own_error_codes():
    from ethos.subagents.tools import _as_result

    assert _as_result({"status": "done"}).ok is True
    assert _as_result({"status": "timeout", "agent": "foundry"}).error_code == ToolErrorCode.TIMEOUT
    assert _as_result({"status": "unavailable"}).error_code == ToolErrorCode.ENVIRONMENT
    assert _as_result({"status": STATUS_FAILED}).error_code == ToolErrorCode.EXECUTION
    # An empty status is not a success by default.
    assert _as_result({"summary": "no status at all"}).ok is False
