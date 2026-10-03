from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ethos.gsl.loop import was_truncated
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
        from ethos.toolhost.server import ToolResult

        self.calls.append((name, args))
        return ToolResult(ok=True, content={"wrote": args.get("path", "")})


def _step_loop(toolhost: Any) -> Any:
    """A goal loop with one step left, driving the real `_execute_step`."""
    from ethos.gsl.loop import GeneralSolverLoop
    from ethos.schemas.gsl import Plan

    solver = GeneralSolverLoop.__new__(GeneralSolverLoop)
    solver.config = make_config(Path("/tmp/ethos-test-tool-raises"))
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
    from ethos.prompts.gsl import COMMISSIONED_ALREADY, PLAN_PROMPT, STEP_PROMPT

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
    ("plan", dict(name="Clio", commissioned="C", success_checks="S", approach="a",
                  title="t", desired_end_state="d")),
    ("step", dict(name="Clio", commissioned="C", editing="E", title="t", step_id="s1",
                  step_description="x", plan_state="[]", memories="(none)", tools="(catalog)")),
])
def test_both_prompts_render_with_the_statement(template: str, fields: dict) -> None:
    """A new `.format()` key is a KeyError on the first real intention.

    Which is the worst time to find out: it is inside the plan call, it is caught
    as `PlanUnavailable`, and the goal gets no plan at all.
    """
    from ethos.prompts.gsl import PLAN_PROMPT, STEP_PROMPT

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
    from ethos.prompts.gsl import EDIT_EXISTING_FILES, STEP_PROMPT

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
        name="Clio", commissioned="C", editing=EDIT_EXISTING_FILES, title="t",
        step_id="s1", step_description="x", plan_state="[]", memories="(none)",
        tools="(catalog)",
    )
    assert "Do not abbreviate a file" in body
    assert "a file\'s entire contents go in" not in body, \
        "the sentence that taught it to write files whole"
