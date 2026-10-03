from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger
from ethos.paths import resolve
from ethos.schemas.gsl import EvaluationResult

logger = get_logger("ethos.gsl.evaluator")


class ObjectiveChecker:
    """Rung 1 of the verification ladder: objective checks (linters, tests,
    types, schema validation) executed mechanically [D-06, 6.2].

    Every result carries `observed`: whether the check actually looked at
    something. It is not the same question as `passed`, and the difference is
    the whole of the "claims it is finished" failure. A check that skipped
    because the tool it wanted was not installed passes trivially; a check that
    ran a test suite and watched it fail does not. Scoring both as `passed:
    True` is how a project with no runnable tests in the agent's environment
    came to be as verified as one whose tests all pass, and `observed` is how
    the caller can tell them apart.
    """

    def __init__(self, cwd: Path | None = None):
        self.cwd = cwd

    async def check(self, check: dict[str, Any], artifact: Any = None) -> dict[str, Any]:
        ctype = str(check.get("type", "none"))
        if ctype == "command":
            return await self._check_command(check)
        if ctype == "syntax":
            return await self._check_syntax(check)
        if ctype == "file_exists":
            path = resolve(str(check.get("path", "")))
            return {"passed": path.exists(), "observed": True,
                    "evidence": {"path": str(path), "exists": path.exists()}}
        if ctype == "file_contains":
            return self._check_contains(check)
        if ctype == "json_field":
            return self._check_json_field(check, artifact)
        if ctype == "metric_gte":
            try:
                value = float(check.get("value", 0))
                target = float(check.get("target", 0))
            except (TypeError, ValueError):
                return {"passed": False, "observed": False,
                        "evidence": {"error": "non-numeric metric"}}
            return {"passed": value >= target, "observed": True,
                    "evidence": {"value": value, "target": target}}
        if ctype == "http_status":
            return await self._check_http(check)
        if ctype in ("none", "human", ""):
            return {"passed": True, "observed": False,
                    "evidence": {"note": "no objective check defined"}}
        # An unknown check type passes, because refusing to pass it would fail
        # the work for a reason nobody can act on -- but it observes nothing,
        # and `observed: False` is what stops it counting as evidence.
        return {"passed": True, "observed": False,
                "evidence": {"note": f"unknown check type {ctype}; skipped"}}

    async def _check_command(self, check: dict[str, Any]) -> dict[str, Any]:
        command = str(check.get("command", ""))
        if not command:
            return {"passed": False, "observed": False, "evidence": {"error": "empty command"}}
        expect = str(check.get("expect", "exit_zero"))
        # A check may name its own working directory. Without this a derived
        # command can only run in the agent's home directory, which is the one
        # place a project's own test suite is guaranteed not to be.
        cwd = str(check["cwd"]) if check.get("cwd") else (str(self.cwd) if self.cwd else None)
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(os.environ),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=float(check.get("timeout_s", 120)),
            )
        except TimeoutError:
            return {"passed": False, "observed": True, "evidence": {
                "command": command, "cwd": cwd, "error": "timeout",
                "label": check.get("label", ""),
            }}
        except OSError as exc:
            # A `cwd` that has gone away, a permission bit, an interpreter that is
            # not installed. All three are common, none of them is this loop's
            # problem, and letting them out of here would unwind through
            # `_finish_goal`, `advance`, `act` and `_cycle` -- one derived check
            # with a stale working directory taking the whole agent with it.
            return {"passed": False, "observed": False, "evidence": {
                "command": command, "cwd": cwd, "error": str(exc)[:200],
                "label": check.get("label", ""),
            }}
        stdout_text = stdout.decode("utf-8", errors="replace")[:4000]
        stderr_text = stderr.decode("utf-8", errors="replace")[:4000]
        if expect == "exit_zero":
            passed = process.returncode == 0
        elif expect.startswith("output_contains:"):
            needle = expect.split(":", 1)[1]
            passed = needle in stdout_text
        elif expect.startswith("exit_nonzero"):
            passed = process.returncode != 0
        else:
            passed = process.returncode == 0
        return {
            "passed": passed,
            "observed": True,
            "evidence": {
                "command": command, "cwd": cwd, "returncode": process.returncode,
                "stdout": stdout_text, "stderr": stderr_text, "expect": expect,
                "label": check.get("label", ""),
            },
        }

    async def _check_syntax(self, check: dict[str, Any]) -> dict[str, Any]:
        """Does every one of these files parse?

        The cheapest check there is and the one that catches the most. A
        whole-file rewrite is a guess about every character in the file, and the
        way that guess fails is not a subtly wrong program -- it is a file that
        cannot be loaded at all, from a single mangled brace. Nothing else in
        this ladder can see that, because a file that will not parse fails
        everything downstream with a message about something else.

        In-process wherever a parser is already imported, and by asking the
        interpreter where one is not. A file that cannot be read is skipped
        rather than failed: a step that moved or deleted it did not break it.
        """
        errors: list[str] = []
        checked: list[str] = []
        skipped: list[str] = []
        for raw in check.get("paths", []) or []:
            path = Path(str(raw))
            if not path.exists() or not path.is_file():
                skipped.append(f"{path}: not there to check")
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                skipped.append(f"{path}: {exc}")
                continue
            verdict, detail = _syntax_of(path, source)
            if verdict == "ok":
                checked.append(str(path))
            elif verdict == "error":
                errors.append(f"{path}: {detail}")
            else:
                skipped.append(f"{path}: {detail}")
        return {
            "passed": not errors,
            # Observed means "a parser was run against these files", which
            # includes a parser that found something wrong. A file that fails to
            # parse has been looked at more thoroughly than one that passes.
            "observed": bool(checked or errors),
            "evidence": {
                "checked": checked,
                "errors": errors,
                "skipped": skipped,
                "label": check.get("label", "syntax of the changed files"),
            },
        }

    @staticmethod
    def _check_contains(check: dict[str, Any]) -> dict[str, Any]:
        path = resolve(str(check.get("path", "")))
        needle = str(check.get("text", ""))
        if not path.exists():
            return {"passed": False, "observed": True,
                    "evidence": {"error": f"missing file {path}"}}
        content = path.read_text(encoding="utf-8", errors="replace")
        return {"passed": needle in content, "observed": True,
                "evidence": {"path": str(path), "contains": needle in content}}

    @staticmethod
    def _check_json_field(check: dict[str, Any], artifact: Any) -> dict[str, Any]:
        try:
            data = artifact if isinstance(artifact, dict) else json.loads(str(artifact))
            keys = str(check.get("key", "")).split(".")
            value = data
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    value = None
                    break
            return {"passed": value is not None, "observed": True,
                    "evidence": {"key": check.get("key"), "value": str(value)[:200]}}
        except (json.JSONDecodeError, TypeError):
            return {"passed": False, "observed": True,
                    "evidence": {"error": "invalid JSON artifact"}}

    @staticmethod
    async def _check_http(check: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = str(check.get("url", ""))
        expected = int(check.get("status", 200))
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
            return {"passed": response.status_code == expected, "observed": True,
                    "evidence": {"url": url, "status": response.status_code}}
        except httpx.HTTPError as exc:
            return {"passed": False, "observed": True,
                    "evidence": {"url": url, "error": str(exc)}}


def _syntax_of(path: Path, source: str) -> tuple[str, str]:
    """Whether this file parses, and what to say about it.

    Three answers, not two. "skip" exists because the interesting case here is
    the file whose language has no parser installed: a `.ts` file with no
    `tsc`, a `.sh` file on a machine with no bash. Treating that as a pass would
    let a broken file through silently, and treating it as a failure would fail
    work for a reason that is not the work's. It is reported, and it does not
    count as evidence.
    """
    suffix = path.suffix.lower()
    if suffix in (".py", ".pyi"):
        try:
            compile(source, str(path), "exec")
        except (SyntaxError, ValueError) as exc:
            line = getattr(exc, "lineno", "?")
            offset = getattr(exc, "offset", "?")
            return "error", f"line {line}, column {offset}: {getattr(exc, 'msg', exc)}"
        return "ok", ""
    if suffix == ".json":
        try:
            json.loads(source)
        except json.JSONDecodeError as exc:
            return "error", f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        return "ok", ""
    if suffix in (".yaml", ".yml"):
        import yaml

        try:
            yaml.safe_load(source)
        except yaml.YAMLError as exc:
            return "error", str(exc).replace("\n", " ")[:200]
        return "ok", ""
    if suffix == ".toml":
        import tomllib

        try:
            tomllib.loads(source)
        except tomllib.TOMLDecodeError as exc:
            return "error", str(exc)[:200]
        return "ok", ""
    if suffix in (".js", ".mjs", ".cjs"):
        return _syntax_by_interpreter(["node", "--check", str(path)])
    if suffix in (".sh", ".bash", ".zsh"):
        return _syntax_by_interpreter(["bash", "-n", str(path)])
    return "skip", "no parser for this kind of file"


def _syntax_by_interpreter(argv: list[str]) -> tuple[str, str]:
    """Parse the file with the interpreter that owns the language, if it is here."""
    import shutil
    import subprocess

    if shutil.which(argv[0]) is None:
        return "skip", f"{argv[0]} is not installed here"
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "skip", f"could not be checked: {exc}"
    if result.returncode == 0:
        return "ok", ""
    detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")
    return "error", (detail or f"{argv[0]} exited {result.returncode}")[:200]


async def evaluate_objective(
    checks: list[dict[str, Any]], artifact: Any = None, cwd: Path | None = None,
) -> EvaluationResult:
    """Run the checks, and say how many of them actually looked at anything.

    `passed` and `verified` are different answers and the caller needs both. An
    empty list of checks passes -- there is nothing to object to -- and verifies
    nothing, and the difference between those two facts is the difference
    between "the work has been checked" and "there was no check to run".
    """
    checker = ObjectiveChecker(cwd)
    evidence: dict[str, Any] = {}
    notes: list[str] = []
    ran = 0
    passed_all = bool(checks)
    for index, check in enumerate(checks or [{"type": "none"}]):
        result = await checker.check(check, artifact)
        label = str(check.get("label") or check.get("type", "none"))
        key = label if label not in evidence else f"{label}#{index}"
        evidence[key] = result
        if result.get("observed"):
            ran += 1
        if not result.get("passed", False):
            passed_all = False
            notes.append(
                f"failed check: {label} {check.get('command', check.get('path', ''))}".strip()
            )
    difficulty = 0.2 if passed_all else 0.6
    return EvaluationResult(
        passed=passed_all, evidence=evidence, difficulty=difficulty, notes=notes,
        checks_run=ran, verified=passed_all and ran > 0,
    )


def difficulty_from_criteria(criteria: list[dict[str, Any]], objective: EvaluationResult) -> float:
    base = objective.difficulty
    if any(c.get("type") == "human" for c in criteria):
        base = max(base, 0.7)
    if any(c.get("type") == "metric_gte" for c in criteria):
        base = max(base, 0.5)
    return min(1.0, base)
