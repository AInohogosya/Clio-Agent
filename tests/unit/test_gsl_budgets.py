from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clio.gsl.loop import was_truncated
from tests.conftest import make_config


@pytest.mark.parametrize("reason", ["length", "max_tokens", "incomplete", "MAX_TOKENS"])
def test_a_response_cut_off_by_its_ceiling_is_truncated(reason):
    """Each provider spells the same event with its own word.

    The gateway passes that word straight through into `finish_reason`, so the
    loop has to recognise all four spellings or a truncated reply arrives looking
    like a complete one. An unfinished JSON object does not parse, which is how a
    reply that was cut off mid-file came to be indistinguishable from a reply
    that was never going to contain a file.
    """
    assert was_truncated(reason) is True


@pytest.mark.parametrize("reason", [None, "", "stop", "end_turn", "tool_use", "COMPLETED"])
def test_a_complete_response_is_not_truncated(reason):
    assert was_truncated(reason) is False


# --------------------------------------------------------------------------
# A tool that raises is a failed tool, not a dead life loop


class _RaisingToolhost:
    """A toolhost whose one tool raises rather than answering."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict, ctx) -> Any:
        self.calls.append((name, args))
        raise self.error


class _SilentToolhost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict, ctx) -> Any:
        from clio.toolhost.server import ToolResult

        self.calls.append((name, args))
        return ToolResult(ok=True, content={"wrote": args.get("path", "")})


def _step_loop(toolhost: Any) -> Any:
    """A goal loop with one step left, driving the real `_execute_step`."""
    from clio.gsl.loop import GeneralSolverLoop
    from clio.schemas.gsl import Plan

    solver = GeneralSolverLoop.__new__(GeneralSolverLoop)
    solver.config = make_config(Path("/tmp/clio-test-tool-raises"))
    solver.gateway = None
    solver.toolhost = toolhost
    solver.thread_id = "self"
    solver.context = None
    solver.memory = None
    solver.audit = None
    return solver, Plan(
        approach_id="a1", approach="do the thing",
        steps=[{
            "id": "s1", "description": "write the file", "status": "pending",
            "attempts": 0, "result": None,
        }],
        strategy_number="1",
    )


async def _scripted_step(request: Any) -> tuple[dict, float, bool]:
    """The model returns one action for the step; what happens to it is the test."""
    import json as _json

    return (
        _json.loads(
            '{"thought": "writing it", "actions": [{"tool": "fs.write", '
            '"args": {"path": "/tmp/x.md", "content": "hi"}}]}'
        ),
        0.0,
        False,
    )


async def test_a_tool_that_raises_fails_the_step_and_not_the_cycle() -> None:
    """One bad call is one bad call.

    `_execute_step` called `toolhost.execute` with nothing around it, so a tool
    that *raised* -- rather than returning `ok=False`, which is how a tool says it
    did not work -- unwound `advance`, then `_advance_goal`, then `act`, then
    `_cycle`. The life loop was gone for that cycle, and the intention kept the
    focus it crashed on, so the next cycle re-ran the identical failing step, and
    the one after that. The agent produced neither work nor words for as long as
    the fault lasted, which from the outside is an agent that has stopped.

    This is not hypothetical: `classify_shell` raised `AttributeError` on any
    command containing `2>&1`, it runs on the toolhost's path before the tool does
    anything, and the traceback in the agent's own log is
    `life.cycle_failed -> gsl.advance -> _execute_step -> toolhost.execute ->
    classifier.classify_shell`. The classifier is fixed; this is the reason the
    next thing of that shape does not take the whole agent with it.
    """
    solver, plan = _step_loop(_RaisingToolhost(ValueError("the tool is broken")))
    solver.config.gsl.step_tokens = 1000

    solver._complete_with_cost = _scripted_step  # type: ignore[method-assign]

    class _Intention:
        id = "00000000-0000-0000-0000-000000000001"
        title = "write the file"
        commissioned_by = None
        origin = "self"
        desired_end_state = "the file exists"
        budget_usd = None
        spent_usd = 0.0

    class _Step:
        id = "s1"
        description = "write the file"

    class _WS:
        def volatile_text(self): return ""
        def stable_prefix_text(self): return ""

    solver._recall = _async([])                      # type: ignore[method-assign]
    solver._tool_catalog = _async("a catalog")        # type: ignore[method-assign]

    result = await solver._execute_step(_Intention(), {}, plan, _Step(), _WS())

    assert result.outcome.value == "step_failed", (
        "a tool that raised is a step that failed, which the loop knows how to handle"
    )
    assert len(result.actions) == 1
    assert result.actions[0]["ok"] is False
    assert "raised before returning" in str(result.actions[0]["result"])
    assert "ValueError" in str(result.actions[0]["result"]), \
        "and the type is named, so the next step can read what went wrong"


async def test_a_tool_that_answers_is_unaffected() -> None:
    """The guard is around the call, not around the loop."""
    toolhost = _SilentToolhost()
    solver, plan = _step_loop(toolhost)
    solver.config.gsl.step_tokens = 1000

    solver._complete_with_cost = _scripted_step  # type: ignore[method-assign]

    class _Intention:
        id = "00000000-0000-0000-0000-000000000001"
        title = "write the file"
        commissioned_by = None
        origin = "self"
        desired_end_state = "the file exists"
        budget_usd = None
        spent_usd = 0.0

    class _Step:
        id = "s1"
        description = "write the file"

    class _WS:
        def volatile_text(self): return ""
        def stable_prefix_text(self): return ""

    solver._recall = _async([])                      # type: ignore[method-assign]
    solver._tool_catalog = _async("a catalog")        # type: ignore[method-assign]

    result = await solver._execute_step(_Intention(), {}, plan, _Step(), _WS())

    assert result.outcome.value == "step_done"
    assert result.actions[0]["ok"] is True
    assert toolhost.calls == [("fs.write", {"path": "/tmp/x.md", "content": "hi"})]


def _async(value: Any) -> Any:
    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        return value
    return _call


# --------------------------------------------------------------------------
# The prompts that do the work were not told the work was asked for


def test_the_prompts_that_do_the_work_say_it_was_asked_for() -> None:
    """The licence to act stopped at the turn that decides.

    The deliberation prompt has carried "Do not ask permission for work the
    message already asked for" since the "I'll do it with nothing behind it"
    failure. That sentence only ever reached the turn that *decides*. Understand,
    plan and step are three separate prompts with no memory of the conversation
    and nothing in them saying a person is waiting, so the licence to act had to
    be re-earned at every one -- and was not.

    It showed up as a refusal rather than as a wrong file. Asked to "create a
    file named `Test.md` on your desktop", the agent resolved the path, then
    stopped twice to ask *what text should be in it* -- a question the request
    does not raise, about a detail where any reasonable answer serves -- and said
    it would make no filesystem changes until answered. It held the step open,
    replanned toward "read the file back", and read a file that did not exist.

    The cost is not the delay. It is that the agent cannot be handed anything
    underspecified, which is most things: a name, a path, a stub, a heading.

    Asserted on the prompts rather than left to the next edit to undo, and
    asserted on all three, because the gap was that each one was written
    separately and only one of them was told.
    """
    from clio.prompts.gsl import COMMISSIONED_ALREADY, PLAN_PROMPT, STEP_PROMPT

    for prompt in (PLAN_PROMPT, STEP_PROMPT):
        assert "{commissioned}" in prompt, (
            "the prompt does not even have a place for the statement"
        )

    assert "already decided" in COMMISSIONED_ALREADY
    assert "choose the obvious one" in COMMISSIONED_ALREADY, \
        "and is told what to do about the details the request leaves open"
    assert "irreversible" in COMMISSIONED_ALREADY, \
        "with the exception stated, so this is not 'never ask' in a new coat"
    assert "if there is nobody to ask" in COMMISSIONED_ALREADY, \
        "delegated work runs in an isolated thread where nobody is watching"


@pytest.mark.parametrize("template,fields", [
    ("plan", dict(name="Clio", commissioned="C", success_checks="S", delegation="D",
                  deliverable="V", approach="a", title="t", desired_end_state="d",
                  constraints="K", success_criteria="R")),
    ("step", dict(name="Clio", commissioned="C", editing="E", delegation="D",
                  deliverable="V", title="t",
                  intention_end_state="d", constraints="K",
                  step_id="s1", step_description="x", plan_state="[]", memories="(none)",
                  tools="(catalog)")),
])
def test_both_prompts_render_with_the_statement(template: str, fields: dict) -> None:
    """A new `.format()` key is a KeyError on the first real intention.

    Which is the worst time to find out: it is inside the plan call, it is caught
    as `PlanUnavailable`, and the goal gets no plan at all.
    """
    from clio.prompts.gsl import PLAN_PROMPT, STEP_PROMPT

    prompt = PLAN_PROMPT if template == "plan" else STEP_PROMPT
    body = prompt.format(**fields)
    assert "C" in body
    if template == "step":
        assert "E" in body, "the editing guidance did not reach the rendered prompt"
    else:
        assert "S" in body, "the success-check guidance did not reach the rendered prompt"
    for placeholder in ("{commissioned}", "{editing}", "{name}", "{title}", "{success_checks}"):
        assert placeholder not in body, f"{placeholder} was not substituted"


def test_the_step_prompt_says_to_edit_rather_than_rewrite() -> None:
    """The rule that was missing, and the sentence that was pointing the other way.

    `fs.edit` was implemented, correct, and in the catalog the whole time: exact
    search/replace, several operations at once, a unified diff option, a pre-image
    backup, and a diff in its response. It was used zero times across ninety-nine
    operations on files. Every one of them was a whole-file write through a shell
    heredoc, and one of those came back with a single closing brace mangled into
    `}n` -- which made the whole file a syntax error.

    Nothing was missing but the instruction. The step prompt actively invited the
    other behaviour: "Tool arguments may be long -- a file's entire contents go in
    `args.content`", which is true, and reads as a reason to write files whole.

    The argument that makes it stick is the blast radius, so it is in the text: a
    rewrite is one guess about every character, and one wrong character destroys
    everything that was right with nothing left to compare against.
    """
    from clio.prompts.gsl import EDIT_EXISTING_FILES, STEP_PROMPT

    assert "{editing}" in STEP_PROMPT, "the prompt has no place to put the guidance"
    assert "`fs.edit`" in EDIT_EXISTING_FILES, "and it names the tool to use"
    assert "Do not rewrite it" in EDIT_EXISTING_FILES, "as the instruction, not a footnote"
    assert "already on disk" in EDIT_EXISTING_FILES or "already exists" in EDIT_EXISTING_FILES
    assert "replace_all" in EDIT_EXISTING_FILES, \
        "including how to answer the refusal, or the model abandons the edit"
    assert "do not fall back to rewriting the file" in EDIT_EXISTING_FILES.lower(), \
        "the exact wrong turn this is meant to prevent"

    # The old invitation is gone, and the useful half of it -- do not abbreviate --
    # is still there, because it is true and it was not the problem.
    body = STEP_PROMPT.format(
        name="Clio", commissioned="C", editing=EDIT_EXISTING_FILES, delegation="D",
        deliverable="V", title="t", intention_end_state="d", constraints="K",
        step_id="s1", step_description="x", plan_state="[]",
        memories="(none)", tools="(catalog)",
    )
    assert "Do not abbreviate a file" in body
    assert "a file\'s entire contents go in" not in body, \
        "the sentence that taught it to write files whole"


# --------------------------------------------------------------------------
# Devise degrades with the plan request it shares a failure counter with


class _DeviseIntention:
    id = "00000000-0000-0000-0000-0000000000bb"
    title = "build the coding agent"
    commissioned_by = "owner"
    origin = "commission"
    desired_end_state = "the agent exists"
    budget_usd = None
    spent_usd = 0.0


async def test_devise_drops_to_t2_after_repeated_planning_failures(tmp_path, monkeypatch) -> None:
    """`plan_degraded` is not a note; it changes the request.

    The failure handler computed it, wrote it to the resume blob and said "tier
    dropped" in its own thoughts, while devise still asked at T3 because it read
    only `consequential`. On 2026-10-20 the only T3 model was returning 429, so
    the commissioned build never reached the T2 models that were answering, and
    the loop waited out the five-hour rung instead.
    """
    from clio.gsl import loop as loop_module
    from clio.gsl.loop import GeneralSolverLoop
    from clio.schemas.models import Tier

    solver = GeneralSolverLoop.__new__(GeneralSolverLoop)
    solver.config = make_config(tmp_path)
    solver.gateway = object()
    seen: dict[str, object] = {}

    async def fake_devise(_gateway, _config, _intention, _understanding, _knowhow,
                          _lessons, *, tier, knowhow_limit):
        seen["tier"] = tier
        return [{
            "id": "a1", "summary": "do it", "expected_value": 0.5,
            "estimated_cost_usd": 0.0, "reversible": True,
        }]

    monkeypatch.setattr(loop_module, "devise", fake_devise)

    await solver._devise_with_cost(
        _DeviseIntention(), {"consequential": True, "plan_attempts": 3}, [], [],
    )

    assert seen["tier"] == Tier.T2


def test_the_shipped_plan_floor_does_not_reject_a_one_step_plan() -> None:
    """A simple task can be one step; requiring three makes it unplannable.

    Beta 3 raised this floor from 1 to 3 while its own comment said plans must
    never be padded. The Docs task then returned a valid one-step plan and was
    rejected twice before it could start.
    """
    import yaml

    config = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "gsl.yaml").read_text()
    )
    assert config["min_real_plan_steps"] == 1


def test_the_plan_prompt_does_not_promise_a_fixed_minimum() -> None:
    from clio.prompts.gsl import PLAN_PROMPT

    assert "3 to 7 steps" not in PLAN_PROMPT
    assert "as many steps as the work needs" in PLAN_PROMPT


def test_a_failed_plan_is_revised_rather_than_abandoned() -> None:
    """A dead end in the plan is not the end of the task.

    The prompt described how to build a plan and said nothing about the plan not
    working, so a failed plan ended as an empty answer, a parked goal and a person
    told the work had stopped. A plan is a hypothesis: the prompt now says what a
    failure means, what a revision changes, and that an empty plan is giving up.
    """
    from clio.prompts.gsl import DEGRADED_PLAN, DEVISE_PROMPT, PLAN_PROMPT

    plan = " ".join(PLAN_PROMPT.split()).lower()
    assert "when a plan fails" in plan
    assert "do not give up on the intention because a plan did not work" in plan
    assert "legitimate stop" in plan
    assert "revise the plan" in plan
    assert "empty plan" in plan
    assert "information about the plan, not by itself the end" in plan

    degraded = " ".join(DEGRADED_PLAN.split()).lower()
    assert "return no steps rather than inventing them" not in degraded
    assert "revision" in degraded
    assert "never answer with an empty plan" in degraded

    devise = " ".join(DEVISE_PROMPT.split()).lower()
    assert "failed approach is not by itself the end" in devise
    assert "never answer with an empty list" in devise


def test_the_step_prompt_has_a_folder_action_that_is_not_fs_write() -> None:
    from clio.prompts.gsl import STEP_PROMPT

    flat = " ".join(STEP_PROMPT.split())
    assert "fs.mkdir" in flat
    assert "creates a folder" in flat


def test_a_desktop_deliverable_uses_a_visible_launcher_not_just_files() -> None:
    """A folder of source files is not an app the person can launch.

    The coding-agent build ended as a CLI package: visible on disk, but no
    launchable thing and no icon, and the report called it finished. The plan and
    the step now carry the delivery rule, and the verifier is told to judge the
    thing where the person will meet it.
    """
    from clio.prompts.gsl import (
        PLAN_PROMPT,
        STEP_PROMPT,
        VERIFIER_PROMPT,
        VISIBLE_DELIVERABLE,
    )

    deliverable = " ".join(VISIBLE_DELIVERABLE.split()).lower()
    assert "launchable entry point" in deliverable
    assert ".app" in deliverable
    assert "open -r" in deliverable
    assert "absolute path" in deliverable
    assert "folder of source files is not an app" in deliverable
    assert "never report the work as finished" in deliverable

    assert "{deliverable}" in PLAN_PROMPT
    assert "{deliverable}" in STEP_PROMPT

    verifier = " ".join(VERIFIER_PROMPT.split()).lower()
    assert "launcher" in verifier
    assert ".app" in verifier
    assert "source" in verifier and "not that thing" in verifier


def test_the_report_names_the_files_the_work_wrote() -> None:
    """`done` is not an address; the path is what the person can act on."""
    from clio.gsl.loop import _delivered_note, _delivered_paths

    resume = {"artifacts": [
        {"tool": "fs.mkdir", "ok": True, "args": {"path": "/Users/me/Desktop/agent"}},
        {"tool": "fs.write", "ok": True, "args": {"path": "/Users/me/Desktop/agent/README.md"}},
        {"tool": "fs.read", "ok": True, "args": {"path": "/Users/me/elsewhere.txt"}},
        {"tool": "fs.write", "ok": False, "args": {"path": "/Users/me/Desktop/broken.txt"}},
    ]}

    assert _delivered_paths(resume) == ["/Users/me/Desktop/agent"], (
        "nested files collapse into their folder, reads are not deliveries, and "
        "a failed write is not a file the person can see"
    )
    assert _delivered_note(resume) == "; files: /Users/me/Desktop/agent"


# --------------------------------------------------------------------------
# When strategies are unbounded, the switch schedules its own pacing


class _MemorySaved:
    """Captures what the loop wrote, and nothing else."""

    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None

    async def update_intention(self, intention_id: Any, **fields: Any) -> None:
        self.saved = fields.get("resume_state")


def _bare_loop(config, memory: Any) -> Any:
    from clio.gsl.loop import GeneralSolverLoop

    return GeneralSolverLoop(config, memory, gateway=None, toolhost=None, verification=None)


def test_an_unbounded_strategy_switch_schedules_its_own_pacing(tmp_path: Path) -> None:
    """No ceiling means pacing, not a tick-rate ladder of fresh approaches.

    The strategy cap used to be the only thing standing between a hopeless
    step and an infinite ladder of approaches; with the cap lifted
    (`gsl.max_strategies: 0`, the default), every installed strategy carries
    a backoff that grows with its number and caps at `plan_backoff_max_s`.
    The deadline rides `plan_retry_after`, which the loop's own gates
    already read and name.
    """
    import asyncio
    from datetime import UTC, datetime

    from clio.gsl.loop import PLAN_RETRY_AFTER

    config = make_config(tmp_path)
    memory = _MemorySaved()
    loop = _bare_loop(config, memory)

    asyncio.run(loop._pace_strategy_switch(_IntentionRow(), {}, strategy_number=2))
    raw = (memory.saved or {}).get(PLAN_RETRY_AFTER)
    assert raw, "the switch set a deadline"
    deadline = datetime.fromisoformat(str(raw))
    assert deadline > datetime.now(UTC)
    # Strategy 8 sits on the cap: later switches do not wait longer than
    # `plan_backoff_max_s`, whatever their number.
    asyncio.run(loop._pace_strategy_switch(_IntentionRow(), {}, strategy_number=8))
    capped = datetime.fromisoformat(str((memory.saved or {}).get(PLAN_RETRY_AFTER)))
    assert (capped - datetime.now(UTC)).total_seconds() <= config.gsl.plan_backoff_max_s + 5


def test_a_capped_strategy_switch_adds_no_pacing(tmp_path: Path) -> None:
    """With a ceiling configured, the old shape is unchanged: the park is the
    stop, and a switch that installs does not also delay the new plan."""
    import asyncio

    config = make_config(tmp_path)
    config.gsl.max_strategies = 3
    memory = _MemorySaved()
    loop = _bare_loop(config, memory)

    asyncio.run(loop._pace_strategy_switch(_IntentionRow(), {}, strategy_number=2))
    assert memory.saved is None, "a capped switch writes nothing, let alone a wait"


class _IntentionRow:
    id = "00000000-0000-0000-0000-000000000001"
