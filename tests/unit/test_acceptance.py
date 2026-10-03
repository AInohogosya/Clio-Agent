"""Asking a project whether the change works, and finding out what it cannot say.

The loop could finish a coding task without ever running a test. Not because
running one was hard -- `ObjectiveChecker` has run arbitrary commands since it
was written -- but because nothing ever derived one: a commission arrives with no
success criteria, an empty list of checks was scored as a pass, and the thing
that actually decided "done" was a model reading a summary of the plan's own step
descriptions. Nothing in the system ever asked a file whether it parsed.

These tests are about the derivation and the asking, and they run against real
files in a real temporary directory rather than a mock of one. A check that cannot
be exercised by a test that actually parses a broken file is a check nobody has
run either.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ethos.gsl.acceptance import (
    changed_paths,
    derive_checks,
    is_code,
    project_root,
    uncovered_paths,
)
from ethos.gsl.evaluator import ObjectiveChecker, evaluate_objective


def _python_project(root: Path, *, passing: bool = True, with_tests: bool = True) -> Path:
    """A project small enough to read in one breath and real enough to run."""
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1'\n", encoding="utf-8",
    )
    (root / "pkg" / "adder.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8",
    )
    if with_tests:
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_adder.py").write_text(
            "from pkg.adder import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n",
            encoding="utf-8",
        )
        if not passing:
            (root / "pkg" / "adder.py").write_text(
                "def add(a, b):\n    return a - b\n", encoding="utf-8",
            )
    return root / "pkg" / "adder.py"


# ------------------------------------------------------------- what was touched


def test_a_files_path_comes_from_the_actions_the_work_recorded() -> None:
    """The record is the only description of the work there is.

    Everything the loop does is written into `resume_state` as tool, arguments,
    result. A check derived from anything but that is a check derived from what
    the agent said it did, which is the thing being verified.
    """
    paths = changed_paths([
        {"tool": "fs.edit", "args": {"path": "/tmp/pkg/mod.py"}, "ok": True},
        {"tool": "fs.write", "args": {"path": "/tmp/pkg/mod.py"}, "ok": True},
    ])
    assert [str(p) for p in paths] == ["/tmp/pkg/mod.py"], "and it is deduplicated"


def test_a_shell_rewrite_still_names_the_file_it_rewrote(tmp_path: Path) -> None:
    """422 `shell.run`s against 58 `fs.write`s is the shape of this agent's file work.

    A command is a string, so it carries no `path` argument, and a derivation
    that only understood the tool arguments would see almost none of what this
    agent actually writes. Absolute paths in the command that exist and are code
    count as touched -- a guess, and deliberately a guess towards checking more.
    """
    target = tmp_path / "script.py"
    target.write_text("x = 1\n", encoding="utf-8")
    paths = changed_paths([
        {"tool": "shell.run", "args": {"command": f"cat > {target} <<'EOF'\nx = 2\nEOF"}},
    ])
    assert target in paths, "the file a heredoc just rewrote is a changed file"


def test_a_relative_path_in_a_command_is_not_attributed_to_guesswork() -> None:
    """A relative token is relative to a working directory nobody recorded.

    Attributing it to the agent's home directory would file the work under a
    project it has nothing to do with, and run that project's tests.
    """
    paths = changed_paths([{"tool": "shell.run", "args": {"command": "python -m pytest tests"}}])
    assert paths == []


def test_a_note_is_not_code() -> None:
    """Which decides whether an unrunnable check is a failure or a shrug."""
    assert is_code(Path("a.py")) and is_code(Path("a.tsx"))
    assert not is_code(Path("README.md")) and not is_code(Path("notes.txt"))
    assert not is_code(Path("data.json"))


def test_the_nearest_marker_wins_and_no_project_is_a_real_answer(tmp_path: Path) -> None:
    (tmp_path / "outer").mkdir()
    (tmp_path / "outer" / "package.json").write_text("{}", encoding="utf-8")
    inner = tmp_path / "outer" / "packages" / "core"
    inner.mkdir(parents=True)
    (inner / "package.json").write_text("{}", encoding="utf-8")
    source = inner / "index.ts"
    source.write_text("export const x = 1\n", encoding="utf-8")
    assert project_root(source) == inner, "a monorepo package is its own project"

    loose = tmp_path / "loose.py"
    loose.write_text("x = 1\n", encoding="utf-8")
    assert project_root(loose) is None


def test_a_marker_in_the_home_directory_never_becomes_a_project_root(tmp_path: Path) -> None:
    """The machine this was written on has a `package.json` in `$HOME`.

    Everything unrelated accumulates there, so a marker in `$HOME` makes every
    file beneath it a member of that "project" -- and the derived check becomes
    whatever its `test` script says, executed in the person's home directory.
    Found by running it: the walk reached `$HOME` and offered to run `npm test`
    there, for a file four levels below a `pyproject.toml` it had passed on the
    way up.
    """
    from pathlib import Path as _Path

    home = _Path.home()
    stray = home / "package.json"
    existed = stray.exists()
    created = not existed
    if created:
        stray.write_text('{"scripts": {"test": "echo should never run"}}', encoding="utf-8")
    try:
        assert project_root(home / "notes" / "deep" / "x.py") is None, (
            "a file in a directory of the home that is not a project is not "
            "a member of the project that happens to sit in $HOME"
        )
    finally:
        if created:
            stray.unlink(missing_ok=True)


# ------------------------------------------------------------ what to check


async def test_a_python_project_is_asked_to_run_its_own_tests(tmp_path: Path) -> None:
    """The check that matters is the one the project's maintainers run."""
    source = _python_project(tmp_path / "demo")
    derived = derive_checks([source])
    commands = [c for c in derived.checks if c["type"] == "command"]
    assert len(commands) == 1, commands
    assert "-m pytest" in commands[0]["command"]
    assert Path(commands[0]["cwd"]) == source.parents[1], \
        "in the project, not in the agent's home directory"
    assert "no:cacheprovider" in commands[0]["command"], \
        "and it does not leave a .pytest_cache in someone's repository"


async def test_the_derived_tests_actually_run_and_report_what_they_found(tmp_path: Path) -> None:
    """Not "it built a command" -- it ran one, and the answer is the answer."""
    passing = _python_project(tmp_path / "good", passing=True)
    result = await evaluate_objective(derive_checks([passing]).checks)
    assert result.passed is True, result.evidence
    assert result.verified is True
    assert result.checks_run >= 2, "the syntax check and the test suite"

    broken = _python_project(tmp_path / "bad", passing=False)
    failed = await evaluate_objective(derive_checks([broken]).checks)
    assert failed.passed is False, (
        "a project whose own test fails is not verified, whatever else is true"
    )
    assert failed.verified is False
    evidence = list(failed.evidence.values())
    assert any("1 failed" in json.dumps(e.get("evidence", {})) for e in evidence), (
        "and the failure output is kept, because 'it did not pass' is not actionable"
    )


async def test_a_syntax_error_is_caught_by_the_cheapest_check_there_is(tmp_path: Path) -> None:
    """One wrong character in a whole-file rewrite, and nothing else loads.

    A Snake game written in one shot came back with a closing brace mangled into
    `}n`. An `fs.edit` of one line would have left the other 416 lines alone; what
    neither would have done is load the file. This is the check that does.
    """
    target = tmp_path / "game.py"
    target.write_text(
        "const head = {\n  x: 1\n}\nreturn head;\n", encoding="utf-8",
    )
    result = await ObjectiveChecker(tmp_path).check({"type": "syntax", "paths": [str(target)]})
    assert result["passed"] is False
    assert result["observed"] is True
    assert "game.py" in result["evidence"]["errors"][0]


async def test_a_good_file_passes_the_syntax_check_and_reports_which(tmp_path: Path) -> None:
    target = tmp_path / "ok.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    result = await ObjectiveChecker(tmp_path).check({"type": "syntax", "paths": [str(target)]})
    assert result["passed"] is True
    assert result["evidence"]["checked"] == [str(target)]


async def test_a_file_with_no_parser_is_skipped_rather_than_counted(tmp_path: Path) -> None:
    """Three answers, not two.

    Passing it silently is how a broken `.ts` file gets through in an
    environment with no `tsc`; failing it is failing the work for a reason that
    is not the work's. It is reported, and it is not evidence.
    """
    target = tmp_path / "view.ts"
    target.write_text("export const x = ", encoding="utf-8")  # a syntax error
    result = await ObjectiveChecker(tmp_path).check({"type": "syntax", "paths": [str(target)]})
    assert result["evidence"]["checked"] == [], "no parser here, so nothing was examined"
    assert result["observed"] is False
    assert result["evidence"]["skipped"], "and the file is named, so the gap is visible"


async def test_json_yaml_and_toml_are_parsed_in_process(tmp_path: Path) -> None:
    """No interpreter, no subprocess, no excuse."""
    bad = tmp_path / "broken.json"
    bad.write_text('{"a": 1,,}', encoding="utf-8")
    result = await ObjectiveChecker(tmp_path).check({"type": "syntax", "paths": [str(bad)]})
    assert result["passed"] is False
    assert "broken.json" in result["evidence"]["errors"][0]


async def test_a_file_that_is_gone_is_not_a_syntax_failure(tmp_path: Path) -> None:
    """A step that moved or deleted a file did not break it."""
    result = await ObjectiveChecker(tmp_path).check(
        {"type": "syntax", "paths": [str(tmp_path / "moved.py")]},
    )
    assert result["passed"] is True
    assert result["evidence"]["checked"] == []


def test_a_node_project_is_asked_to_run_its_own_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8",
    )
    source = tmp_path / "index.js"
    source.write_text("module.exports = 1\n", encoding="utf-8")
    derived = derive_checks([source])
    commands = [c for c in derived.checks if c["type"] == "command"]
    assert commands and "npm test" in commands[0]["command"]


def test_a_node_project_without_a_test_script_says_so_rather_than_inventing_one(
    tmp_path: Path,
) -> None:
    """A fabricated check is worse than none: it is a check that cannot fail."""
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}), encoding="utf-8")
    source = tmp_path / "index.js"
    source.write_text("module.exports = 1\n", encoding="utf-8")
    derived = derive_checks([source])
    assert not [c for c in derived.checks if c["type"] == "command"]
    assert any("no test script" in note for note in derived.notes), derived.notes


def test_a_go_project_is_asked_to_build_and_a_rust_one_to_test(tmp_path: Path) -> None:
    go_root = tmp_path / "goapp"
    go_root.mkdir()
    (go_root / "go.mod").write_text("module demo\n", encoding="utf-8")
    go_source = go_root / "main.go"
    go_source.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
    go_checks = derive_checks([go_source]).checks
    assert any("go test" in c.get("command", "") for c in go_checks), go_checks

    rs_root = tmp_path / "rsapp"
    rs_root.mkdir()
    (rs_root / "Cargo.toml").write_text("[package]\nname = \"demo\"\n", encoding="utf-8")
    rs_source = rs_root / "src" / "lib.rs"
    rs_source.parent.mkdir()
    rs_source.write_text("pub fn f() -> i32 { 1 }\n", encoding="utf-8")
    rs_checks = derive_checks([rs_source]).checks
    assert any("cargo test" in c.get("command", "") for c in rs_checks), rs_checks


def test_a_file_outside_every_project_is_still_syntax_checked(tmp_path: Path) -> None:
    """No project means no test suite to ask about, not no check at all."""
    source = tmp_path / "scratch.py"
    source.write_text("def f(:\n", encoding="utf-8")  # broken on purpose
    derived = derive_checks([source])
    assert [c["type"] for c in derived.checks] == ["syntax"]
    assert derived.roots == []
    assert any("not inside a project" in note for note in derived.notes), derived.notes


def test_tests_are_found_one_directory_deeper_than_tests_slash(tmp_path: Path) -> None:
    """`tests/unit/test_thing.py` is two levels down, and most repos do it this way.

    Found by running the derivation against this repository: a search that only
    looked at `tests/*.py` reported "no tests, so there was nothing to run" for a
    project with 788 of them, and offered to verify the work with nothing.
    """
    root = tmp_path / "proj"
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "test_deep.py").write_text(
        "def test_deep():\n    assert True\n", encoding="utf-8",
    )
    (root / "pyproject.toml").write_text("[project]\nname='p'\n", encoding="utf-8")
    source = root / "mod.py"
    source.write_text("x = 1\n", encoding="utf-8")
    commands = [c for c in derive_checks([source]).checks if c["type"] == "command"]
    assert commands, "a project with tests in tests/unit has tests"
    assert "-m pytest" in commands[0]["command"]


def test_the_test_search_does_not_walk_into_things_that_are_not_source(tmp_path: Path) -> None:
    """A bounded search that finds `.venv` is not a bounded search."""
    root = tmp_path / "proj"
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "test_vendored.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8",
    )
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "test_published.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8",
    )
    (root / "pyproject.toml").write_text("[project]\nname='p'\n", encoding="utf-8")
    source = root / "mod.py"
    source.write_text("x = 1\n", encoding="utf-8")
    derived = derive_checks([source])
    assert not [c for c in derived.checks if c["type"] == "command"], (
        "the only test files in this project belong to other projects"
    )


def test_the_linter_is_run_on_what_changed_rather_than_on_the_repository(
    tmp_path: Path,
) -> None:
    """This repository lints `ethos tests` and deliberately not `migrations`.

    Running the linter over the project root instead reports four pre-existing
    errors in a generated Alembic file, so every coding task in the repository
    would fail verification for something it did not do.
    """
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname='p'\n[tool.ruff]\nline-length = 100\n", encoding="utf-8",
    )
    (root / "migrations").mkdir()
    (root / "migrations" / "generated.py").write_text("import os\n", encoding="utf-8")
    source = root / "mod.py"
    source.write_text("x = 1\n", encoding="utf-8")
    commands = [c for c in derive_checks([source]).checks if c["type"] == "command"]
    lint = [c for c in commands if "ruff" in c["command"]]
    assert lint, commands
    assert str(source) in lint[0]["command"], "the changed file, not the root"
    assert str(root / "migrations") not in lint[0]["command"]


def test_a_project_with_no_tests_offers_no_test_command(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='d'\n", encoding="utf-8")
    source = tmp_path / "mod.py"
    source.write_text("x = 1\n", encoding="utf-8")
    derived = derive_checks([source])
    assert not [c for c in derived.checks if c["type"] == "command"]
    assert any("no tests" in note for note in derived.notes), derived.notes


def test_prose_gets_no_checks_at_all(tmp_path: Path) -> None:
    note = tmp_path / "NOTES.md"
    note.write_text("# notes\n", encoding="utf-8")
    assert derive_checks([note]).checks == []


def test_the_check_count_is_capped_per_goal(tmp_path: Path) -> None:
    """A verification that takes longer than the work it verifies is an outage."""
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='d'\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n",
                                                  encoding="utf-8")
        (root / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
        (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
    derived = derive_checks(
        [tmp_path / "a" / "mod.py", tmp_path / "b" / "mod.py"], max_commands=2,
    )
    commands = [c for c in derived.checks if c["type"] == "command"]
    assert len(commands) == 2, commands
    assert any("ran out" in note or "budget" in note for note in derived.notes), derived.notes


# ------------------------------------------------------------------- coverage


async def test_a_file_no_check_could_look_at_is_reported_as_uncovered(tmp_path: Path) -> None:
    """`verified` asks whether *a* check observed *something*. This asks the
    other question: was every changed file looked at?

    Five files changed, four parsable and one not, and the syntax check reports a
    pass while the fifth file is unchecked. A person asking "did you test it"
    means the second question.
    """
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    unparsable = tmp_path / "view.ts"
    unparsable.write_text("export const x = \n", encoding="utf-8")

    evidence = (await evaluate_objective(
        derive_checks([good, unparsable]).checks,
    )).evidence
    uncovered = uncovered_paths([good, unparsable], evidence)
    assert uncovered in ([unparsable], [good]), \
        "whatever was not examined is what is reported; the parsable one was checked"
    assert (unparsable in uncovered) or (good in uncovered)


async def test_a_project_command_covers_the_files_it_imported(tmp_path: Path) -> None:
    """A suite that passes has already imported what it tests."""
    source = _python_project(tmp_path / "demo")
    evidence = (await evaluate_objective(derive_checks([source]).checks)).evidence
    assert uncovered_paths([source], evidence) == []


async def test_a_deleted_file_is_not_uncovered(tmp_path: Path) -> None:
    gone = tmp_path / "removed.py"
    assert uncovered_paths([gone], {}) == []


# --------------------------------------------------- nothing checked is not pass


async def test_no_checks_pass_nothing_and_verify_nothing() -> None:
    """Nothing ran, and both answers say so.

    An empty list of checks raises no objection, so it is not a pass either --
    which is why every commissioning path in this system ends up at rung 2 with
    a model holding the only vote. "Nothing objected" and "the work has been
    checked" used to be the same value, and they are the two halves of "it claims
    it finished".
    """
    result = await evaluate_objective([])
    assert result.passed is False, "no check passed, because none ran"
    assert result.verified is False, "and nothing about the work was examined"
    assert result.checks_run == 0


async def test_an_unknown_check_type_is_not_evidence() -> None:
    """It passes -- refusing to pass it would fail work for an unfixable reason."""
    result = await evaluate_objective([{"type": "telepathy", "question": "does it work"}])
    assert result.passed is True
    assert result.verified is False


async def test_a_check_that_skipped_is_not_evidence() -> None:
    result = await evaluate_objective([{"type": "human", "note": "ask somebody"}])
    assert result.passed is True
    assert result.verified is False


def test_the_interpreter_running_the_agent_is_the_one_that_runs_the_tests(tmp_path: Path) -> None:
    """A bare `pytest` would find whatever is on PATH.

    Which can pass a suite by collecting nothing at all, and report that as a
    green run of a project whose dependencies are not even installed.
    """
    source = _python_project(tmp_path / "demo")
    command = [c for c in derive_checks([source]).checks if c["type"] == "command"][0]
    assert command["command"].startswith(sys.executable), command["command"]
