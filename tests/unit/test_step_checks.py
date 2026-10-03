"""The success check every plan step has always carried, now run.

`PlanStep.success_check` has been in the schema since the solver loop was
written. The plan prompt asks for one on every step, in the example as well as in
the prose. It is parsed, stored in the intention's resume blob, shown to nobody,
and checked by nothing -- so a step that set the entire test suite red was
recorded as a *completed step*, the plan advanced, and the next step had no idea
why the code it was about to touch was broken. The failure surfaced several steps
later, attributed to whatever step happened to run next.

That is the shape of "it did not do what I asked and told me it had". The step is
the cheapest place to catch it: the change is one step old, and the plan's own
check is already written down, describing exactly what should be true.

These drive the real `_execute_step` with a scripted model, the way the
tool-raises tests in `test_gsl_budgets` do, so what is being tested is the
decision rather than a description of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.conftest import make_config


class _SilentToolhost:
    """Answers `ok: true` and does the thing, because `ok: true` is the problem.

    A tool that says it wrote the file without writing it would make every
    `file_exists` check here fail for the wrong reason. So this one writes, and
    the checks are then about whether the loop listens to them.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict, ctx) -> Any:
        from pathlib import Path as _Path

        from ethos.toolhost.server import ToolResult

        self.calls.append((name, args))
        if args.get("path") and args.get("content") is not None:
            _Path(str(args["path"])).write_text(str(args["content"]), encoding="utf-8")
        return ToolResult(ok=True, content={"path": args.get("path", "")})


class _Intention:
    id = "00000000-0000-0000-0000-0000000000aa"
    title = "add the function"
    commissioned_by = "owner"
    desired_end_state = "the function exists and the suite is green"
    budget_usd = None
    spent_usd = 0.0


class _WS:
    def volatile_text(self) -> str:
        return ""

    def stable_prefix_text(self) -> str:
        return ""


def _solver(tmp_path: Path) -> Any:
    from ethos.gsl.loop import GeneralSolverLoop

    solver = GeneralSolverLoop.__new__(GeneralSolverLoop)
    solver.config = make_config(tmp_path)
    solver.config.gsl.step_tokens = 1000
    solver.gateway = None
    solver.toolhost = _SilentToolhost()
    solver.thread_id = "self"
    solver.context = None
    solver.memory = None
    solver.audit = None

    async def _recall(*_a: Any, **_k: Any) -> list[Any]:
        return []

    async def _catalog(*_a: Any, **_k: Any) -> str:
        return "a catalog"

    async def _complete(request: Any) -> tuple[dict, float, bool]:
        return ({"thought": "adding the function",
                 "actions": [{"tool": "fs.write",
                              "args": {"path": str(tmp_path / "thing.py"),
                                       "content": "def thing():\n    return 1\n"}}]},
                0.0, False)

    solver._recall = _recall
    solver._tool_catalog = _catalog
    solver._complete_with_cost = _complete
    return solver


async def _run(tmp_path: Path, success_check: dict[str, Any]) -> Any:
    from ethos.gsl.loop import GeneralSolverLoop  # noqa: F401
    from ethos.schemas.gsl import Plan, PlanStep

    solver = _solver(tmp_path)
    plan = Plan(approach_id="a1", approach="do it", strategy_number="1",
                steps=[PlanStep(id="s1", description="add the function",
                                success_check=success_check)])
    step = PlanStep(id="s1", description="add the function",
                    success_check=success_check)
    return await solver._execute_step(_Intention(), {}, plan, step, _WS())


async def test_a_step_whose_own_check_does_not_pass_is_not_a_finished_step(
    tmp_path: Path,
) -> None:
    """The rule.

    The tool reported `ok: true`, which says the call went through and not that
    the step achieved anything. Before this check existed there was nothing else
    in the step that could tell the difference.
    """
    missing = tmp_path / "never-written.txt"
    result = await _run(tmp_path, {"type": "file_exists", "path": str(missing)})

    assert result.outcome.value == "step_failed", (
        "a step that did not achieve what it said it was for is a failed step"
    )
    assert any("success check" in t for t in result.thoughts), result.thoughts
    assert result.actions and result.actions[0]["ok"] is True, \
        "the tool call itself still succeeded; it is the step that did not"


async def test_a_step_whose_own_check_passes_is_a_finished_step(tmp_path: Path) -> None:
    """The guard is around the check, not around the loop."""
    result = await _run(tmp_path, {"type": "file_exists",
                                   "path": str(tmp_path / "thing.py")})
    assert result.outcome.value == "step_done"


async def test_a_step_with_no_check_is_unaffected(tmp_path: Path) -> None:
    """Most steps have nothing mechanical about them, and must not be held to it."""
    assert (await _run(tmp_path, {})).outcome.value == "step_done"


async def test_a_failing_command_check_tells_the_model_what_failed(tmp_path: Path) -> None:
    """'It did not pass' is not actionable. The output is.

    The next step is where the recovery happens, and it can only recover if it
    knows which command failed and what it printed.
    """
    result = await _run(tmp_path, {
        "type": "command",
        "command": "echo 'assert 1 == 2' >&2; exit 1",
        "expect": "exit_zero",
        "timeout_s": 20,
    })
    assert result.outcome.value == "step_failed"
    joined = " ".join(result.thoughts)
    assert "check said" in joined
    assert "1 == 2" in joined, "with the check's own output, so the next step can act"


async def test_a_failing_test_command_is_a_failing_step(tmp_path: Path) -> None:
    """The case the whole change exists for, at the cheapest place to catch it.

    A real project, a real test file that fails, run by the real interpreter --
    because a step check that has only ever been asserted against a mock is a
    step check nobody has run.
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='p'\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_x.py").write_text(
        "def test_x():\n    assert 1 == 2\n", encoding="utf-8",
    )
    import sys

    result = await _run(tmp_path, {
        "type": "command",
        "command": f"{sys.executable} -m pytest -q -p no:cacheprovider {project}",
        "expect": "exit_zero",
        "timeout_s": 120,
    })
    assert result.outcome.value == "step_failed"
    assert "1 failed" in " ".join(result.thoughts), result.thoughts


async def test_the_step_check_can_be_turned_off_by_configuration(tmp_path: Path) -> None:
    """An operator whose plans carry checks that cannot pass needs a way out."""
    solver = _solver(tmp_path)
    solver.config.gsl.verify_step_checks = False
    from ethos.schemas.gsl import Plan, PlanStep

    plan = Plan(approach_id="a1", approach="", strategy_number="1",
                steps=[PlanStep(id="s1", description="x")])
    step = PlanStep(id="s1", description="x",
                    success_check={"type": "file_exists", "path": str(tmp_path / "nope")})
    result = await solver._execute_step(_Intention(), {}, plan, step, _WS())
    assert result.outcome.value == "step_done"


async def test_a_check_of_a_kind_nothing_can_run_is_not_a_step_failure(
    tmp_path: Path,
) -> None:
    """A malformed check in a model's plan is not a reason to discard real work.

    `http_status` and `metric_gte` need a URL and a number this loop does not
    have. The step's file is written either way, and failing the step would send
    a plan that is working into three strategies of guessing.
    """
    result = await _run(tmp_path, {"type": "http_status", "url": "http://127.0.0.1:9/"})
    assert result.outcome.value == "step_done"


async def test_the_thought_names_the_check_that_failed_not_just_that_something_did(
    tmp_path: Path,
) -> None:
    """It is read by a model deciding what to do next, not by a person."""
    result = await _run(tmp_path, {"type": "file_exists",
                                   "path": str(tmp_path / "absent")})
    check_named = [t for t in result.thoughts if "file_exists" in t]
    assert check_named, result.thoughts
    assert json.dumps(result.actions), "and the actions that were taken are still there"
