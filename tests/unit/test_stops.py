"""The endings of work are facts with kinds, and the agent tells them itself.

NA-P3 asked for three things and this file holds each in place:

* *the record* -- every stop carries a distinguishable kind (completed, stopped,
  model_stop, limit_reached, error) and a reason, in a ledger that survives a
  restart;
* *the routing* -- model-call failures and tool endings are mapped to those
  kinds where they happen, not reconstructed later from prose;
* *the telling* -- the ledger reaches the workspace as a block, and the
  identity prompt tells the agent to report endings in its own words. Nothing
  in the path composes a sentence for a person: that is NA-H4, and the test
  that matters here is the negative one -- the block carries facts, never
  boilerplate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from clio.engine.stops import (
    LEDGER_SIZE,
    StopKind,
    StopsLedger,
    disposition_from_exception,
)
from clio.engine.workspace import WorkspaceAssembler
from clio.gateway.cost import BudgetExceededError
from clio.gateway.providers.base import ProviderError
from clio.gateway.router import NoModelAvailable
from clio.prompts.identity import (
    GOLDEN_RULE_NOTE,
    HUMAN_LIKENESS_NOTE,
    STOP_DISPOSITION_NOTE,
    build_identity_kernel,
)

# ------------------------------------------------------------------ the ledger


def test_every_stop_records_its_kind_and_reason():
    ledger = StopsLedger()
    ledger.record(StopKind.completed, subject="ship the release",
                  reason="verified at rung 2")
    ledger.record(StopKind.limit_reached, subject="ship the release",
                  reason="rate limited (429); waiting about 5m", what="model_call")
    ledger.record("error", subject="cycle", reason="the pass raised", what="cycle")
    entries = ledger.entries()
    assert [e.kind for e in entries] == [
        StopKind.completed, StopKind.limit_reached, StopKind.error,
    ]
    assert entries[1].what == "model_call"
    assert "429" in entries[1].reason


def test_an_unknown_kind_is_recorded_as_an_error_rather_than_lost():
    ledger = StopsLedger()
    entry = ledger.record("mystery", subject="x", reason="y")
    assert entry.kind == StopKind.error


def test_the_ledger_is_bounded():
    ledger = StopsLedger()
    for n in range(LEDGER_SIZE + 40):
        ledger.record(StopKind.error, subject=f"task {n}", reason="r")
    assert len(ledger.entries()) == LEDGER_SIZE


def test_the_block_says_what_happened_and_who_decides_the_words():
    ledger = StopsLedger()
    ledger.record(StopKind.stopped, subject="tidy the archive",
                  reason="the owner stopped the agent", what="control")
    block = ledger.render()
    assert "How recent work ended" in block
    assert "stopped" in block
    assert "the owner stopped the agent" in block
    # NA-H4: the block instructs the agent's own words and carries no
    # pre-composed sentence a person could ever receive.
    assert "in your own words" in block


def test_an_empty_ledger_renders_nothing():
    assert StopsLedger().render() == ""


def test_the_ledger_survives_a_round_trip_through_the_snapshot():
    ledger = StopsLedger()
    ledger.record(StopKind.limit_reached, subject="deploy",
                  reason="budget cap", what="model_call")
    restored = StopsLedger.restore(ledger.snapshot())
    assert restored.entries()[0].kind == StopKind.limit_reached
    assert restored.entries()[0].reason == "budget cap"
    # And junk degrades to an empty ledger rather than a crash on wake.
    assert StopsLedger.restore(None).entries() == []
    assert StopsLedger.restore(["nonsense"]).entries() == []


# ------------------------------------------------------------- the dispositions


def test_a_budget_cap_is_a_limit_not_an_error():
    exc = BudgetExceededError("over", bucket="discretionary", spent=11.0, cap=10.0)
    assert disposition_from_exception(exc) == StopKind.limit_reached


def test_a_rate_limit_is_a_limit_not_an_error():
    exc = ProviderError(provider="openai", status=429, message="slow down",
                        retryable=True)
    assert disposition_from_exception(exc) == StopKind.limit_reached


def test_no_eligible_model_is_a_limit_not_an_error():
    exc = NoModelAvailable("nothing can serve tier T3")
    assert disposition_from_exception(exc) == StopKind.limit_reached


def test_a_plain_provider_failure_is_an_error():
    exc = ProviderError(provider="openai", status=500, message="broken",
                        retryable=True)
    assert disposition_from_exception(exc) == StopKind.error


# ---------------------------------------------------------------- the workspace


def _assemble(**overrides):
    from tests.conftest import make_config as _cfg

    config = _cfg(Path("/tmp/clio-test-stops-ws"))
    kwargs = {
        "identity_doc": build_identity_kernel("Aria"),
        "tools": [],
        "focus": None,
        "percepts": [],
        "state": "THINKING_FOCUSED",
        "energy": 0.8,
        "budget_summary": "day $0.00/$5.00",
        "commitments": [],
        "memories": [],
        "thoughts": [],
        "now": datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return WorkspaceAssembler(config).assemble(**kwargs)


def _block(workspace, name):
    return next(b.text for b in workspace.blocks if b.name == name)


def test_the_workspace_carries_the_stops_block_when_work_has_ended():
    ledger = StopsLedger()
    ledger.record(StopKind.error, subject="migrate the db",
                  reason="the model call for step s2 failed: TimeoutError")
    workspace = _assemble(stops=ledger.render())
    block = _block(workspace, "stops")
    assert "migrate the db" in block
    assert "model_call" in block or "error" in block


def test_the_workspace_names_the_absence_when_nothing_has_stopped():
    workspace = _assemble(stops="")
    assert "nothing has stopped" in _block(workspace, "stops")


# ------------------------------------------------------------------- the prompts


def test_the_golden_rule_is_in_the_kernel_and_says_performance_wins():
    kernel = build_identity_kernel("Aria")
    assert "The one priority" in kernel
    assert GOLDEN_RULE_NOTE in kernel
    assert "the work wins" in kernel
    assert "coding" in kernel


def test_the_kernel_states_the_golden_rule_without_per_case_rules():
    kernel = build_identity_kernel("Aria")
    # NA-P1.3: the priority is stated; no per-case arbitration is spelled out.
    assert "no standing rule that trades one against the other" in kernel


def test_the_kernel_carries_the_human_likeness_terms():
    kernel = build_identity_kernel("Aria")
    assert HUMAN_LIKENESS_NOTE in kernel
    # NA-H1: a default, not a ceiling.
    assert "Short by default" in kernel
    assert "exactly as long as the work requires" in kernel
    # NA-H4: no machine-shaped output.
    assert "no boilerplate" in kernel or "machine-shaped" in kernel
    # NA-H2: self-initiated conduct.
    assert "without being asked" in kernel
    # NA-H3: the person who wrote last is one input among many.
    assert "one voice among several" in kernel


def test_the_kernel_carries_the_stop_disposition_note():
    kernel = build_identity_kernel("Aria")
    assert STOP_DISPOSITION_NOTE in kernel
    assert "in my own" in kernel
