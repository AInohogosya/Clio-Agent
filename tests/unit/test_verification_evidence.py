"""A verdict, and the evidence behind it.

Two failures, and they are different failures.

**It said it was finished.** A commissioning path (`Life._act_social`) creates an
intention with no success criteria, because nothing in the deliberation writes
them. Rung 1 then has no criteria to run, and the thing that decides the outcome
is rung 2: another model, asked to judge the work from an artifact that was a
list of step descriptions and the last few tool results. A description of a
finished piece of software reads exactly like one. And rung 2 could overturn a
failing check outright -- `verdict == "pass"` set `passed = True` whatever the
checks had said -- so a test suite that failed was overruled by a model that had
never seen it fail, and the goal was closed.

**It rebuilt the file and dropped the edits.** A whole-file rewrite through
`fs.write` or a shell heredoc replaced a file that had four hundred correct lines
in it, and the specific changes that were asked for were among the lines that did
not come back. Every tool reported success; a mangled brace was the only evidence
and nothing looked at it.

The tests here hold the two rules that close those: a check that ran outranks a
model that says so, and a file nobody examined is not a verified file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ethos.gsl.verification import VerificationLadder
from tests.conftest import ListAudit, ScriptedGateway, make_config


class _Intention:
    def __init__(self, *, commissioned: str | None = None, kind: str = "commitment") -> None:
        self.id = "00000000-0000-0000-0000-0000000000ff"
        self.title = "make the thing work"
        self.kind = kind
        self.commissioned_by = commissioned
        self.desired_end_state = "the tests pass"
        self.success_criteria: list[dict[str, Any]] = []
        self.constraints: dict[str, Any] = {}


def _ladder(verdict: str, *, tmp_path: Path, issues: list[str] | None = None) -> VerificationLadder:
    """A ladder whose judge is scripted, so the judge's opinion is not the variable."""
    gateway = ScriptedGateway([json.dumps(
        {"verdict": verdict, "issues": issues or [], "confidence": 0.99},
    )])
    config = make_config(tmp_path)
    return VerificationLadder(config, gateway, None, comms_out=None, audit=ListAudit())


def _demo_project(root: Path, *, passing: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_thing.py").write_text(
        "def test_thing():\n    assert True\n", encoding="utf-8",
    )
    source = root / "thing.py"
    source.write_text(
        "def thing():\n    return 1\n" if passing else "def thing(:\n", encoding="utf-8",
    )
    return source


# -------------------------------------------------- a judge cannot overrule


async def test_a_judge_cannot_pass_work_whose_own_check_failed(tmp_path: Path) -> None:
    """The rule, stated as the behaviour it replaces.

    Rung 2 was allowed to overturn rung 1 outright. So a project whose test suite
    fails -- a fact, printed by the interpreter -- could be passed by a model
    reading a sentence that said the file had been written. The check is the
    thing that ran; the judge is the thing that read.
    """
    source = _demo_project(tmp_path / "proj", passing=False)
    ladder = _ladder("pass", tmp_path=tmp_path)

    result = await ladder.verify(
        intention=_Intention(commissioned="owner"),
        criteria=[],
        artifact={"steps": [{"id": "s1", "description": "add the function"}]},
        cwd=tmp_path,
        changed_paths=[source],
    )

    assert result.passed is False, (
        "a model saying `pass` does not make a syntax error go away"
    )
    assert result.uncovered_files == []
    assert any("failing check" in i for i in result.issues), result.issues


async def test_the_contradiction_is_recorded_rather_than_dropped(tmp_path: Path) -> None:
    """Two answers that disagree is a fact worth having, not one to resolve quietly."""
    audit = ListAudit()
    ladder = _ladder("pass", tmp_path=tmp_path)
    ladder.audit = audit
    source = _demo_project(tmp_path / "proj", passing=False)

    await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[source],
    )
    entries = [e for e in audit.entries if "contradicted" in e["summary"]]
    assert entries, [e["summary"] for e in audit.entries]


async def test_a_judge_still_passes_work_where_there_was_nothing_to_contradict(
    tmp_path: Path,
) -> None:
    """The rule is about authority, not about silencing the judge.

    With no stated criteria and no changed code -- a note, a decision, a piece of
    writing -- rung 1 has nothing to say and rung 2 is the only voice there is.
    Refusing to let it speak would strand work that has nothing mechanical about
    it, which is most of what this agent does when nobody is asking.
    """
    ladder = _ladder("pass", tmp_path=tmp_path)
    result = await ladder.verify(
        intention=_Intention(commissioned="owner"),
        criteria=[],
        artifact={"steps": [{"id": "s1", "description": "write the note"}]},
        cwd=tmp_path,
        changed_paths=[],
    )
    assert result.passed is True
    assert result.rung_reached == 2


async def test_a_judge_passes_work_whose_checks_all_pass(tmp_path: Path) -> None:
    source = _demo_project(tmp_path / "proj", passing=True)
    ladder = _ladder("pass", tmp_path=tmp_path)
    result = await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[source],
    )
    assert result.passed is True
    assert result.objective is not None and result.objective.verified is True
    assert result.derived_checks, "and the result names what was checked"


# ------------------------------------------------ nothing checked is not done


async def test_code_changed_and_nothing_checked_is_not_a_verified_task(tmp_path: Path) -> None:
    """The case that has no check available at all.

    No project marker anywhere above the file, so there is no test suite to run;
    a `.ts` file in an environment with no `tsc`. The syntax check skips it. And
    the previous behaviour was to call that a pass -- an unknown check type, an
    empty criteria list and a skipped check all scored `passed: True` -- so the
    agent reported a coding task it had never executed a single line of.
    """
    target = tmp_path / "view.ts"
    target.write_text("export const x = 1\n", encoding="utf-8")
    ladder = _ladder("pass", tmp_path=tmp_path)

    result = await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[target],
    )

    assert result.passed is False
    assert str(target) in result.uncovered_files, (
        "and it says which file nobody looked at"
    )
    assert any("unverified" in i for i in result.issues), result.issues


async def test_the_switch_that_says_so_is_read_from_the_configuration(tmp_path: Path) -> None:
    """An operator who cannot get a check to run needs a way out of the deadlock.

    Parked, every time, with the same unverifiable work and three strategies spent
    on it. `unverified_is_failure: false` is the way to choose the other trade --
    unverified, and recorded as such, rather than blocked.
    """
    target = tmp_path / "view.ts"
    target.write_text("export const x = 1\n", encoding="utf-8")
    ladder = _ladder("pass", tmp_path=tmp_path)
    ladder.config.gsl.unverified_is_failure = False

    result = await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[target],
    )
    assert result.passed is True, "and with the switch off the judge decides again"
    assert result.uncovered_files, "while the gap is still reported"


async def test_work_that_changed_no_code_is_not_held_to_a_check_it_cannot_pass(
    tmp_path: Path,
) -> None:
    """A note, a spreadsheet, a config value: the rule is about code.

    Requiring a check for a `.md` file would park half of what this agent is for.
    """
    note = tmp_path / "NOTES.md"
    note.write_text("# done\n", encoding="utf-8")
    ladder = _ladder("pass", tmp_path=tmp_path)
    result = await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[note],
    )
    assert result.passed is True
    assert result.uncovered_files == []


# ----------------------------------------------------------- what it is shown


async def test_the_judge_is_shown_the_code_and_not_a_description_of_it(tmp_path: Path) -> None:
    """The artifact used to be the plan's step descriptions.

    Every judge this system has run has passed that, which is the expected result
    of asking a model whether a sentence saying the file was written is a true
    account of whether the file works. The diffs are in it now.
    """
    source = _demo_project(tmp_path / "proj")
    ladder = _ladder("fail", tmp_path=tmp_path)
    artifact = {
        "steps": [{"id": "s1", "description": "add the function"}],
        "changed_files": [str(source)],
        "diffs": [{"path": str(source), "diff": "--- a\n+++ b\n-OLD\n+NEW"}],
    }
    await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact=artifact,
        cwd=tmp_path, changed_paths=[source],
    )
    from ethos.gsl.verification import _artifact_text

    text = _artifact_text(artifact)
    assert "+NEW" in text and str(source) in text


def test_the_verifier_prompt_says_a_failing_test_is_a_failing_test() -> None:
    """The judge is told what the machine output means, in the judge's own prompt."""
    from ethos.prompts.gsl import VERIFIER_PROMPT

    # Prompts are wrapped for reading, so the assertion is about the sentences
    # rather than about the line breaks inside them.
    body = " ".join(VERIFIER_PROMPT.split())
    assert "failing test" in body
    assert "not in the diff" in body, \
        "a requirement absent from the change is a requirement not met"
    assert "description of it" in body, \
        "and it is told the artifact is the work, not a report of the work"


# --------------------------------------------------- what reaches the report


async def test_the_result_carries_the_evidence_so_the_answer_can_be_asked_for(
    tmp_path: Path,
) -> None:
    source = _demo_project(tmp_path / "proj")
    ladder = _ladder("pass", tmp_path=tmp_path)
    result = await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[source],
    )
    assert result.changed_files == [str(source)]
    assert result.derived_checks, "and says which checks were derived"
    assert result.objective is not None
    assert result.objective.checks_run >= 1, \
        "so 'how do you know?' has an answer that is not a promise"


async def test_the_audit_entry_records_what_was_checked(tmp_path: Path) -> None:
    audit = ListAudit()
    ladder = _ladder("pass", tmp_path=tmp_path)
    ladder.audit = audit
    source = _demo_project(tmp_path / "proj")
    await ladder.verify(
        intention=_Intention(commissioned="owner"), criteria=[], artifact={},
        cwd=tmp_path, changed_paths=[source],
    )
    verification_entries = [e for e in audit.entries if e["category"] == "verification"]
    assert verification_entries
    details = verification_entries[-1]["details"]
    assert details["checks_run"] >= 1
    assert details["derived_checks"], "which is what makes the entry worth keeping"


async def test_a_criterion_that_runs_its_own_command_is_not_derived_twice(tmp_path: Path) -> None:
    """A stated check knows more than a marker file does.

    `{"type": "command", "command": "pytest -q tests/unit"}` is this project's
    own expectation. Running the derived suite as well spends the cycle twice to
    learn the same thing. The syntax check is still derived -- no criterion states
    one, and it is the one that catches a file that will not load.
    """
    source = _demo_project(tmp_path / "proj")
    ladder = _ladder("pass", tmp_path=tmp_path)
    result = await ladder.verify(
        intention=_Intention(commissioned="owner"),
        criteria=[{"type": "command", "command": "pytest -q tests", "expect": "exit_zero"}],
        artifact={}, cwd=tmp_path, changed_paths=[source],
    )
    assert result.objective is not None
    derived = result.derived_checks
    assert derived, "the syntax check is derived even so: no criterion states one"
    assert all("syntax" in str(label) for label in derived), derived
    ran_commands = [
        detail.get("evidence", {}).get("command", "")
        for detail in result.objective.evidence.values()
        if isinstance(detail, dict)
        and str(detail.get("evidence", {}).get("command", ""))
    ]
    assert ran_commands == ["pytest -q tests"], (
        "the stated command is the only command run, rather than the derived "
        f"suite as well: {list(result.objective.evidence)}"
    )
