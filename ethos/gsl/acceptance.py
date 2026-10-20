"""What to check, derived from what the work actually touched.

The verification ladder has been able to run a command since `ObjectiveChecker`
was written. Nothing ever asked it to. A coding task would finish its plan, reach
the end of it, and call `evaluate_objective([])`, which scored an empty list of
checks as a pass -- so rung 1 had nothing to say, rung 2 was asked to judge a
dict of step descriptions, and it said `pass`. The tests were never run, the
compiler was never called, and a file with a mangled brace in it was as verified
as a file that worked.

This module is the missing question. Given the files a piece of work changed, it
answers: *what does this project have that could tell us whether the change
works?* and it answers in the check vocabulary `ObjectiveChecker` already
speaks, so the running of them needs no new machinery -- only the decision to
ask.

Three things it does, in order:

* **Find the project.** The nearest ancestor of each changed file carrying a
  marker (`pyproject.toml`, `package.json`, `go.mod`, ...). A file outside every
  project is still syntax-checked, because a syntax error is a syntax error
  wherever the file lives.
* **Syntax-check every file it can.** In-process where a parser exists (Python,
  JSON, YAML, TOML) and by asking the interpreter where one does not (`node
  --check`, `bash -n`). This is the check that catches the class of damage a
  whole-file rewrite does: one wrong character in eight thousand lines, which
  turns a working file into a file that cannot be loaded.
* **Ask the project to check itself.** Its test command, its linter, its type
  checker -- the ones it already configures for itself, run the way its own
  maintainers run them. A project that has a test suite is asked to run it.

Two things it deliberately does not do. It does not invent a check for a project
that has none: a `tsconfig.json` with no `tsc` installed produces a note, not a
fabricated pass. And it does not decide whether the work is *correct* -- that is
the independent verifier's rung, and it is a judge, not an oracle. This is the
rung that produces facts.

The heuristics here err towards checking more, never towards checking less. A
path mentioned in a shell command counts as touched if it exists and looks like
code, because a work step that rewrote a file through the shell is exactly the
case where the tool arguments record no path; the cost of a false positive is a
test run that passes, and the cost of a false negative is the thing this module
exists to prevent.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ethos.paths import resolve, user_home

# Files that make a directory a project worth asking questions of.
PROJECT_MARKERS: tuple[str, ...] = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile",
    "tox.ini", "noxfile.py",
    "package.json", "tsconfig.json", "deno.json", "bun.lockb",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "mix.exs", "Makefile", "CMakeLists.txt",
)

# Suffixes that mean "this file is a program", as opposed to prose, data or
# markup. Used for one question only: did this piece of work change code? The
# answer decides whether an unrunnable check is a failure or a shrug.
CODE_SUFFIXES: frozenset[str] = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".rb", ".php", ".pl", ".lua",
    ".ex", ".exs", ".hs", ".ml", ".clj", ".cljs", ".erl", ".dart", ".swift", ".cs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".mm", ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".r", ".jl", ".vim", ".tf", ".proto", ".graphql",
})

# A path in a shell command, absolute only. A relative token in a command is
# resolved against an unknown working directory, so guessing one would attribute
# a file to the wrong project; an absolute path is exactly what it says.
_ABS_PATH_RE = re.compile(r"(?<![\w/.-])(/[\w./@+-]+)")

_MAX_SHELL_PATHS = 12

# How deep a test search goes from the project root, and what it never descends
# into. `tests/unit/test_x.py` is two levels down in this repository and in most
# others; the directories below are never somebody's tests, and walking them is
# how a bounded search turns into an unbounded one.
_TEST_SCAN_DEPTH = 3
_NEVER_SOURCE_DIRS = frozenset({
    "node_modules", "venv", "site-packages", "dist", "build", "out", "target",
    "vendor", "third_party", "coverage", "htmlcov", "__pycache__",
})


def is_code(path: Path | str) -> bool:
    """Whether a path names a program rather than prose, data or markup."""
    return Path(path).suffix.lower() in CODE_SUFFIXES


def changed_paths(actions: Iterable[Any]) -> list[Path]:
    """The files a piece of work touched, read back out of its own record.

    The solver loop already writes every action it takes into the intention's
    `resume_state` -- tool, arguments, whether it worked, and the tool's own
    result. That record is the only place the work is described, so it is the
    only place this can read from; a check derived from anything else would be a
    check derived from what the agent said it did.

    `fs.*` calls name their path. `shell.run` does not, and cannot: a command is
    a string. So a shell action contributes the absolute paths its command
    mentions, filtered to those that exist and end in a code suffix. That is a
    guess, and it is deliberately a guess in the direction of checking more --
    422 `shell.run`s against 58 `fs.write`s is the shape of this agent's file
    work, and a derivation that only understood the tool arguments would see
    almost none of it.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    for action in actions or ():
        if not isinstance(action, dict):
            continue
        tool = str(action.get("tool") or "")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        candidates: list[str] = []
        if tool in ("fs.write", "fs.edit", "fs.read", "fs.stat", "fs.list"):
            raw = args.get("path")
            if raw:
                candidates.append(str(raw))
        elif tool == "fs.move":
            for key in ("to_path", "from_path"):
                if args.get(key):
                    candidates.append(str(args[key]))
        elif tool == "shell.run":
            candidates.extend(_paths_in_command(str(args.get("command") or "")))
        for raw in candidates:
            path = resolve(raw)
            if tool == "shell.run" and not (path.exists() and is_code(path)):
                continue
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _paths_in_command(command: str) -> list[str]:
    found: list[str] = []
    for match in _ABS_PATH_RE.findall(command):
        # Trailing punctuation is part of shell syntax, not of the path.
        cleaned = match.rstrip(".,;:'\")]}")
        if cleaned and cleaned not in found:
            found.append(cleaned)
        if len(found) >= _MAX_SHELL_PATHS:
            break
    return found


def project_root(path: Path) -> Path | None:
    """The nearest ancestor directory that looks like a project, or None.

    None is a real answer and not a failure: a file written into a home
    directory is still syntax-checked, it just has no test suite to be asked
    about. A `pyproject.toml` further up the tree would be a wrong answer more
    often than a right one, so the walk stops at the first marker.

    The user's own home directory is never a project root, which is the one
    exception and it is not a small one. Everything unrelated accumulates there:
    a stray `package.json` in `$HOME` makes every file beneath it a member of
    that "project", and the derived check is then whatever its `test` script
    says, executed in the person's home directory. That is not a hypothetical
    shape — the machine this was written on has a `package.json` in `$HOME`, and
    the walk reached it before the `pyproject.toml` four levels down.
    """
    directory = path if path.is_dir() else path.parent
    try:
        home = user_home().resolve()
    except OSError:
        home = None
    for candidate in [directory, *directory.parents]:
        if home is not None and _same_path(candidate, home):
            break
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right
    except OSError:
        return str(left) == str(right)


@dataclass
class Acceptance:
    """The checks derived for a piece of work, and what they could not cover."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    code_paths: list[Path] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def uncovered_paths(paths: Iterable[Path], evidence: dict[str, Any]) -> list[Path]:
    """Which of these files no check that ran actually looked at.

    `verified` answers whether *a* check observed *something*. That is not quite
    enough: five files changed, four of them parsable and one not, and the parse
    check reports a pass while the fifth file is unchecked. The loop needs the
    other question -- was every file looked at -- because that is the one a person
    asking "did you test it" means.

    Two things count as having looked. A check that names the file and examined
    it, which is what the syntax check reports. And a project command that ran in
    the project's own directory, because a test suite that imports the package has
    imported the changed files by definition -- that is what the project's own
    command means when it is run from the root.

    A file that is not there any more is not uncovered. A step that moved or
    deleted a file did not leave it unverified; it left it somewhere else, or on
    purpose.
    """
    existing = [p for p in paths if Path(p).exists()]
    if not existing:
        return []
    checked: set[str] = set()
    for result in (evidence or {}).values():
        if not isinstance(result, dict):
            continue
        detail = result.get("evidence")
        if not isinstance(detail, dict):
            continue
        checked.update(str(p) for p in detail.get("checked", []) or [])
        cwd = detail.get("cwd")
        if cwd:
            try:
                root = Path(str(cwd))
            except (TypeError, ValueError):
                continue
            for path in existing:
                try:
                    if path.is_relative_to(root):
                        checked.add(str(path))
                except ValueError:
                    continue
    return [p for p in existing if str(p) not in checked]


def derive_checks(
    paths: Iterable[Path | str],
    *,
    timeout_s: float = 180.0,
    max_commands: int = 3,
) -> Acceptance:
    """Objective checks for the files a piece of work changed.

    The syntax check always comes first and covers every code file in the list,
    because it is the only check that works with no project, no dependencies and
    no network. Project commands follow, in the order a maintainer would run
    them: the tests, then the linter, then the type checker -- and no more than
    `max_commands` of them, because a verification that takes longer than the
    work it is verifying is an outage wearing a check's clothes.
    """
    resolved = [resolve(str(p)) for p in (paths or ())]
    code_paths = [p for p in resolved if is_code(p)]
    out = Acceptance(code_paths=code_paths)
    if not code_paths:
        return out

    out.checks.append({
        "type": "syntax",
        "paths": [str(p) for p in code_paths],
        "label": "syntax of the changed files",
    })

    roots: list[Path] = []
    for path in code_paths:
        root = project_root(path)
        if root is not None and root not in roots:
            roots.append(root)
    out.roots = roots
    if not roots:
        out.notes.append(
            "the changed files are not inside a project, so only their syntax "
            "was checked -- nothing here can say whether the change works"
        )
        return out

    budget = max(0, int(max_commands))
    for root in roots:
        if budget <= 0:
            out.notes.append(
                f"more than one project was changed and the check budget of "
                f"{max_commands} ran out; {root} was not asked to check itself"
            )
            break
        commands = _project_commands(root, code_paths, timeout_s=timeout_s, notes=out.notes)
        for command in commands:
            if budget <= 0:
                out.notes.append(
                    f"the check budget of {max_commands} ran out, so {root} was "
                    "only partly checked"
                )
                break
            out.checks.append(command)
            budget -= 1
    return out


def _project_commands(
    root: Path, code_paths: list[Path], *, timeout_s: float, notes: list[str],
) -> list[dict[str, Any]]:
    """The commands this project already runs on itself, in its own idiom."""
    commands: list[dict[str, Any]] = []

    def add(label: str, argv: list[str]) -> None:
        commands.append({
            "type": "command",
            "command": " ".join(argv),
            "cwd": str(root),
            "expect": "exit_zero",
            "timeout_s": timeout_s,
            "label": label,
        })

    tests = _test_command(root, code_paths, notes)
    if tests:
        add(*tests)
    lint = _lint_command(root, code_paths, notes)
    if lint:
        add(*lint)
    return commands


def _test_command(
    root: Path, code_paths: list[Path], notes: list[str],
) -> tuple[str, list[str]] | None:
    """The project's tests, run the way its own configuration runs them.

    For Python: `sys.executable -m pytest`. The interpreter is the one running
    the agent, which is the one with the project's dependencies in it -- a bare
    `pytest` would find whatever is on `PATH` and could pass a suite by
    collecting nothing. `--cacheprovider` is disabled so verification does not
    leave a `.pytest_cache` in someone's repository.
    """
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() \
            or (root / "setup.cfg").exists() or (root / "requirements.txt").exists():
        if importlib.util.find_spec("pytest") is None:
            notes.append(f"no pytest in the interpreter running the agent, so {root} was not tested")
            return None
        found = _python_test_files(root)
        if not found:
            notes.append(f"{root.name} has no tests, so there was nothing to run")
            return None
        touched_tests = [p for p in code_paths if _looks_like_test(p)]
        if touched_tests:
            targets = [str(p) for p in touched_tests[:5]]
            return (f"pytest ({len(targets)} test file(s) the work touched)",
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets])
        return (f"the test suite in {root.name}",
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(root)])

    package_json = root / "package.json"
    if package_json.exists():
        script = _npm_script(package_json, "test")
        if script:
            return (f"npm test in {root.name}", ["npm", "test", "--silent"])
        if (root / "tsconfig.json").exists() and shutil.which("npx"):
            return ("tsc --noEmit", ["npx", "--no-install", "tsc", "--noEmit"])
        notes.append(f"{root.name} has no test script, so it was not tested")
        return None

    if (root / "go.mod").exists() and shutil.which("go"):
        return ("go test ./...", ["go", "test", "./..."])
    if (root / "Cargo.toml").exists() and shutil.which("cargo"):
        return ("cargo test", ["cargo", "test", "--quiet"])
    notes.append(f"no test command was found for {root}")
    return None


def _lint_command(
    root: Path, code_paths: list[Path], notes: list[str],
) -> tuple[str, list[str]] | None:
    """A linter the project has already chosen to have, if it is installed.

    Scoped to the files the work changed rather than to the project root. The
    root is wrong for a repository that is not just its source -- this one lints
    `ethos tests` and deliberately not `migrations`, whose initial schema is
    generated -- and it is also the slow way round. The files that were edited
    are the ones whose lint errors this piece of work could have caused.
    """
    configured = (
        (root / "pyproject.toml").exists() and "[tool.ruff" in _read(root / "pyproject.toml")
    ) or (root / "ruff.toml").exists()
    if not configured:
        return None
    targets = [str(p) for p in code_paths if p.suffix in (".py", ".pyi")] or [str(root)]
    if importlib.util.find_spec("ruff") is not None:  # type: ignore[attr-defined]
        return (f"ruff on {len(targets)} changed file(s)",
                [sys.executable, "-m", "ruff", "check", *targets])
    if shutil.which("ruff"):
        return ("ruff", ["ruff", "check", *targets])
    notes.append(f"{root.name} configures ruff but it is not installed here, so it did not run")
    return None


def _npm_script(package_json: Path, name: str) -> str | None:
    import json

    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return None
    value = scripts.get(name)
    return str(value) if value else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _python_test_files(root: Path, depth: int = _TEST_SCAN_DEPTH) -> list[Path]:
    """Python test files in a project, bounded so the walk cannot run away.

    Bounded in two ways, because an unbounded search over a repository finds
    `.venv`, `node_modules` and every vendored copy of anything, and a suite that
    runs those is not a suite. Depth from the project root, and a list of the
    directories that are never somebody's tests.

    The depth matters more than it looks: `tests/` is a common shape and
    `tests/unit/test_thing.py` is a common file inside it, and a search that
    only looked at `tests/*.py` reported "this project has no tests" for this
    repository -- 788 of them -- and offered nothing to run.
    """
    found: list[Path] = []
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        if len(here.parts) - base >= depth:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in _NEVER_SOURCE_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
                found.append(here / name)
    return sorted(found)


def _looks_like_test(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_") or name.endswith("_test.py") or name.endswith(".test.ts")
        or name.endswith(".test.js") or name.endswith(".spec.ts")
        or "test" in [part.lower() for part in path.parts[:-1]]
    )


__all__ = [
    "Acceptance",
    "CODE_SUFFIXES",
    "PROJECT_MARKERS",
    "changed_paths",
    "derive_checks",
    "is_code",
    "project_root",
    "uncovered_paths",
]
