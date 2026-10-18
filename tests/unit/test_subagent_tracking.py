"""A sub-agent's run, read back by the agent that ordered it.

One report, three causes, all on the same afternoon in the Telegram transcript.
The agent was asked to use APEX; it said "Running APEX once more now" three times
over four hours while the action journal held not one `subagent.apex` row; and
the one real delegation it did make had failed hours earlier on a missing
dependency that `subagent.status` had sworn did not exist.

Three fixes, and the tests hold each in place:

* *the record* -- the deliberation's execution-state block carries what the
  sub-agents were actually asked to do and what came back, over a window long
  enough to cover the conversation the delegation happened inside;
* *the guard* -- a present-tense claim that names a sub-agent is checked against
  the journal's delegation rows, not against "some action happened once";
* *the routing* -- the plan and step prompts say what the sub-agents are for,
  and say it again, binding, when the requester named the worker, and by
  work-type when the work itself names its worker.

They are one file because they are one failure: an agent that cannot see its
sub-agent's runs invents them, and an agent that invents them has no reason to
make them real.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ethos.prompts.deliberation import execution_state_block
from ethos.prompts.gsl import PLAN_PROMPT, delegation_note
from ethos.schemas.messages import SocialAction, SocialDecision
from ethos.social.deliberation import (
    SocialCognition,
    _ExecutionState,
    claims_work_is_underway,
    subagents_named,
)
from tests.conftest import make_config


def _config(name: str):
    config = make_config(Path(f"/tmp/ethos-test-{name}"))
    config.paths.ensure()
    return config


def _decision(*texts: str) -> SocialDecision:
    return SocialDecision(
        action=SocialAction.reply_now, reply_texts=list(texts),
    )


def _state(
    *,
    journaled: list[str] | None = None,
    delegations: list[str] | None = None,
    delegation_names: set[str] | None = None,
    readable: bool = True,
) -> _ExecutionState:
    return _ExecutionState(
        readable, journaled or [], [], 15.0,
        delegations=delegations, delegation_names=delegation_names or set(),
    )


# ------------------------------------------------------------------ the record


def test_delegation_rows_reach_the_reply_prompt_with_their_verdict() -> None:
    """What APEX reported is evidence, and the prompt has to carry it.

    The old block rendered journal rows as bare `tool -> status` over fifteen
    minutes, so a delegation that failed at 14:35 was invisible to a reply
    written at 17:17 — and the reply invented a run that was never made. The
    verdict comes from the journal's own digest, which is the sub-agent's
    summary, not the loop's paraphrase of it.
    """
    block = execution_state_block(
        "Clio", journaled=["shell.run -> ok"], blocked=[], window_min=15.0,
        delegations=[
            "apex: failed, started 2.8 h ago — apex crashed before it could "
            "report a task outcome (exit 1): ModuleNotFoundError",
        ],
        delegation_window_h=24.0,
    )

    assert "Sub-agent delegations" in block
    assert "apex: failed" in block
    assert "ModuleNotFoundError" in block


def test_no_delegations_is_said_rather_than_implied() -> None:
    """An empty delegation line must not read as a clean record of delegation.

    The whole failure was a claim about a sub-agent with no row behind it. The
    empty case says outright that no sub-agent was asked to do anything, so the
    absence is evidence too — the same trade the stage line's empty case makes.
    """
    block = execution_state_block("Clio", [], [], 15.0, stages=[], delegations=[])

    assert "no sub-agent has been asked" in block.lower()


def test_the_delegation_line_says_what_a_missing_name_means() -> None:
    """A sub-agent not in the list was not invoked — the prompt says so.

    The model resolves ambiguity towards whichever line looks like current
    state. Left to infer, "no delegation rows" reads as "no *recent* rows", and
    the invention resumes; said outright, the inference has nothing to stand on.
    """
    block = execution_state_block("Clio", [], [], 15.0, delegations=[])

    assert "was not invoked at all" in block


def test_the_delegation_query_reads_the_journal_over_its_own_window() -> None:
    """The window is the delegation's, not the journal's.

    A desktop run blocks for as long as the work takes; a person asking an hour
    later is asking about a row the fifteen-minute window never sees. This is
    the query that failed silently before — a fake DB records it being asked,
    and a `running` row marks the sub-agent as genuinely in flight.
    """
    class _Db:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            self.queries.append(query)
            if "subagent.%" in query:
                return [{
                    "tool": "subagent.apex", "status": "running",
                    "ts_start": datetime.now(UTC) - timedelta(minutes=40),
                    "result_digest": None,
                }]
            return []

    config = _config("delegation-query")
    db = _Db()
    social = SocialCognition(config, db=db)

    state = asyncio.run(social._execution_state(None))

    assert any("subagent.%" in q for q in db.queries), (
        "the delegation window is read, not assumed empty"
    )
    assert "apex" in state.delegation_names, (
        "a delegation still running forty minutes in is a run 'underway now'"
    )
    assert any("apex: running" in line for line in state.delegations)


def test_a_running_delegation_satisfies_the_running_claim_even_outside_the_journal_window() -> None:
    """A long desktop run is 'running now' for its whole duration.

    The journal's fifteen-minute window would expire mid-run and turn a true
    claim into a withheld one. The `running` status is the fact that matters,
    for as long as the row says so.
    """
    class _Db:
        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            if "subagent.%" in query:
                return [{
                    "tool": "subagent.apex", "status": "running",
                    "ts_start": datetime.now(UTC) - timedelta(hours=1),
                    "result_digest": None,
                }]
            return []

    config = _config("delegation-longrun")
    social = SocialCognition(config, db=_Db())

    state = asyncio.run(social._execution_state(None))

    assert "apex" in state.delegation_names


# -------------------------------------------------------------------- the guard


def test_running_apex_claims_from_the_transcript_are_matched() -> None:
    """The exact sentences the guardrail let through are the ones it now catches.

    These are not invented shapes: each is a message the agent sent while the
    journal held no APEX row at all. The old patterns matched "I'm working on
    it" and not these, which is how the invention reached the person.
    """
    for text in (
        "Running APEX once more now — target is dark mode.",
        "The APEX run is going now; next message is the setting.",
        "Everything dark. Running it through APEX now.",
        "Delegating to APEX as the top priority.",
        "Spawning APEX on it now.",
    ):
        assert claims_work_is_underway(text), text


def test_future_promises_and_finished_facts_are_still_not_matched() -> None:
    """The boundary stays at the present tense.

    "I'll run APEX after lunch" is the ordinary promise a commission is built
    of, and "APEX ran and failed at verification" is a report of the past.
    Catching either would withhold true sentences, which is a worse outage than
    the one being guarded against.
    """
    for text in (
        "I'll run APEX after lunch and report back.",
        "APEX ran and failed at verification — the check read the wrong mode.",
        "Not yet — nothing has run since my last message.",
    ):
        assert not claims_work_is_underway(text), text


def test_subagent_names_are_read_off_the_reply() -> None:
    """The names a claim pins itself to, and nothing invented."""
    assert subagents_named("Running APEX once more now") == {"apex"}
    assert subagents_named("Foundry is building it now") == {"foundry"}
    assert subagents_named("Running it now") == set()


def test_a_named_claim_without_a_delegation_is_withheld_even_when_other_work_was_journaled() -> None:
    """The exact failure, reproduced and refused.

    `shell.run -> ok` was in the journal; the claim was about APEX. Under the
    old rule any journaled action blessed any underway claim, so the reply went
    out describing a sub-agent run that had never been asked for.
    """
    social = SocialCognition(_config("guard-named"))
    decision = _decision("Running APEX once more now — target is dark mode.")
    state = _state(journaled=["shell.run -> ok"])

    social._withhold_unearned_progress(decision, state)

    assert decision.reply_texts == [], (
        "an underway claim about APEX needs an APEX row, whatever else was done"
    )
    assert decision.action is SocialAction.act_silently
    assert decision.decided is False


def test_a_named_claim_with_a_running_delegation_stands() -> None:
    """The guardrail stops invention, not reporting.

    A delegation that is genuinely in flight is exactly what a present-tense
    claim describes, and withholding it would be the underclaiming failure the
    stage-notice work exists to prevent.
    """
    social = SocialCognition(_config("guard-running"))
    decision = _decision("Running APEX once more now — target is dark mode.")
    state = _state(delegation_names={"apex"})

    social._withhold_unearned_progress(decision, state)

    assert decision.reply_texts == ["Running APEX once more now — target is dark mode."]
    assert decision.decided is True


def test_a_named_claim_about_the_other_subagent_is_still_withheld() -> None:
    """A running APEX row says nothing about Foundry.

    The names are checked individually, because a row for one sub-agent is not
    evidence about the other and a claim that names both is judged on both.
    """
    social = SocialCognition(_config("guard-other"))
    decision = _decision("Foundry is building the deploy now.")
    state = _state(journaled=["subagent.apex -> ok"], delegation_names={"apex"})

    social._withhold_unearned_progress(decision, state)

    assert decision.reply_texts == []


def test_a_general_claim_keeps_the_old_rule() -> None:
    """'On it' with nothing journaled is still withheld; with work journaled, kept.

    The named-subagent check is a tightening for claims that can name their
    evidence. A claim about the work in general stays on the rule it had, so
    this change cannot quietly widen what gets withheld.
    """
    social = SocialCognition(_config("guard-general"))
    empty = _decision("I'm on it — next message is the result.")
    full = _decision("I'm on it — next message is the result.")

    social._withhold_unearned_progress(empty, _state(journaled=[]))
    social._withhold_unearned_progress(full, _state(journaled=["shell.run -> ok"]))

    assert empty.reply_texts == []
    assert full.reply_texts == ["I'm on it — next message is the result."]


def test_an_unreadable_journal_disarms_the_guard_entirely() -> None:
    """The guard is only as honest as its evidence, and it knows it.

    A database that could not be read is not a clean record; withholding every
    reply over a briefly unreachable table would be a worse outage than the
    invention. This asymmetry is deliberate and predates the delegation work —
    asserted here so the tightening does not quietly lose it.
    """
    social = SocialCognition(_config("guard-unreadable"))
    decision = _decision("Running APEX once more now.")

    social._withhold_unearned_progress(decision, _state(readable=False))

    assert decision.reply_texts == ["Running APEX once more now."]


# -------------------------------------------------------------------- routing


def test_the_plan_prompt_carries_the_delegation_text() -> None:
    """The sub-agents are taught where plans are chosen, not in a docstring.

    `PLAN_PROMPT` gained a `{delegation}` slot so the planner can render the
    working relationship into every plan request; a plan written without it is
    how four hours of shell improvisation stood in for one APEX delegation.
    """
    assert "{delegation}" in PLAN_PROMPT
    intention = SimpleNamespace(
        title="build a 500K model", desired_end_state="a model", constraints=None,
    )
    rendered = PLAN_PROMPT.format(
        name="Clio", commissioned="C", success_checks="S",
        delegation=delegation_note(intention), approach="A",
        title=intention.title, desired_end_state=intention.desired_end_state,
    )
    assert "subagent.foundry" in rendered
    assert "subagent.apex" in rendered


def test_a_requester_who_named_the_worker_gets_a_binding_routing_line() -> None:
    """'Have APEX change the mode' is a routing decision, already made.

    The general text says what APEX is for; the routing line says THIS work was
    asked for by name through it, so a plan that reaches the same goal by hand
    has not done what was asked.
    """
    intention = SimpleNamespace(
        title="Have APEX change the device mode from dark to light",
        desired_end_state="the mode reads light",
        constraints=None,
    )
    note = delegation_note(intention)

    assert "That choice is binding" in note
    assert "subagent.apex`" in note
    assert "repair" in note.lower(), "the failure path is named in the same breath"


def test_work_that_names_no_subagent_gets_the_relationship_without_the_routing() -> None:
    """The general text stands alone when the work names no worker, either way.

    Detection is on the intention's own words -- a name, or the words of a
    job one of the sub-agents exists to do -- so a goal that is neither (a
    model build in Colab) gets the relationship and not a routing line. The
    moment the work *is* a sub-agent's kind of work, the routing fires
    without anybody naming the worker; the tests below pin that.
    """
    intention = SimpleNamespace(
        title="build a 500K model in Colab",
        desired_end_state="a model and an npm-start interface",
        constraints=None,
    )
    note = delegation_note(intention)

    assert "subagent.foundry" in note
    assert "That choice is binding" not in note


def test_a_browser_goal_routes_to_apex_without_anybody_naming_it() -> None:
    """'Open the browser and change the setting' is APEX's job by what it is.

    The observed failure: asked to do a task in a web browser, the agent
    answered "I can't do that" -- the capability sat in the catalog behind
    `subagent.apex` the whole time, and nothing told the plan the catalog was
    where its reach came from. The cue sentence routes by the work's own
    words, so the requester does not have to know the worker's name.
    """
    intention = SimpleNamespace(
        title="Open the browser and turn off autofill on the settings page",
        desired_end_state="the setting reads off",
        constraints=None,
    )
    note = delegation_note(intention)

    assert "subagent.apex`" in note
    assert "APEX's job by what the work is" in note
    assert "That choice is binding" not in note, \
        "work-type routing is the planner's own decision, not the requester's"


def test_a_repo_change_routes_to_foundry_without_anybody_naming_it() -> None:
    """Work that ends in changed code in a repo is Foundry's job by default."""
    intention = SimpleNamespace(
        title="Refactor the parser so it stops misreading nested lists",
        desired_end_state="the repo's tests pass",
        constraints=None,
    )
    note = delegation_note(intention)

    assert "subagent.foundry`" in note
    assert "Foundry's job by what the work is" in note
    assert "That choice is binding" not in note


def test_a_foundry_named_work_routes_to_foundry() -> None:
    """The routing names the worker the requester chose, not a default."""
    intention = SimpleNamespace(
        title="Use Foundry to add the parser fix", desired_end_state="tests pass",
        constraints=None,
    )

    assert "subagent.foundry" in delegation_note(intention)


# ----------------------------------------------------------------- honest health


def test_apex_health_probes_the_real_import_chain(monkeypatch) -> None:
    """Health that only checks the planner imports is how 'available' lied.

    The observed run: the planner modules imported cleanly, `subagent.status`
    reported available, and every actual run died on the native pyobjc import
    inside `create_screen_controller`. The probe now takes the same chain a
    real run takes, so a deployment whose desktop half is broken reads as
    unavailable before the agent plans a delegation on it.
    """
    from ethos.subagents.apex import ApexSubAgent

    def _fake_import_plane(self):
        class _Mode:
            HIDDEN = "hidden"

        def _raise(_mode):
            raise RuntimeError(
                "pyobjc ApplicationServices/Quartz is unavailable"
            )

        return (object, object, object, object, _Mode, object, _raise)

    monkeypatch.setattr(ApexSubAgent, "_import_plane", _fake_import_plane)
    settings = SimpleNamespace(mode="auto", env={}, root="/tmp/apex-health-test")
    agent = ApexSubAgent(
        SimpleNamespace(subagents=SimpleNamespace(gateway_url="")), settings,
    )

    health = asyncio.run(agent.health())

    assert health["available"] is False
    assert "pyobjc" in str(health["import"]), (
        "the dependency the run would die on is named, not hidden behind 'unknown'"
    )


def test_apex_health_reports_available_when_the_whole_chain_imports(monkeypatch) -> None:
    """A health that can say yes is as necessary as one that says no."""
    from ethos.subagents.apex import ApexSubAgent

    class _Mode:
        HIDDEN = "hidden"

    def _controller(_mode):
        return object()

    monkeypatch.setattr(
        ApexSubAgent, "_import_plane",
        lambda self: (object, object, object, object, _Mode, object, _controller),
    )
    settings = SimpleNamespace(mode="auto", env={}, root="/tmp/apex-health-ok")
    agent = ApexSubAgent(
        SimpleNamespace(subagents=SimpleNamespace(gateway_url="")), settings,
    )

    health = asyncio.run(agent.health())

    assert health["available"] is True
    assert health["import"] == "in-process"
