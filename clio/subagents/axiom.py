"""AXIOM as a sub-agent: the single-turn autonomous coding agent.

AXIOM takes a bounded coding task -- a feature, a fix, a refactor with its
tests -- and runs it through its own six-layer engine: the prompt is compiled
into a verified intent, the workspace is indexed into a semantic world model,
the work is planned as a DAG of waves, every mutation is applied inside a
sandboxed shadow workspace and committed back atomically, and the result goes
through an adversarial verification and self-healing gauntlet before the run
reports. What this adapter adds is only the seam: a delegated run's model
calls land on this agent's gateway, and the run's own verdict comes back in
`SubAgentResult` terms.

AXIOM is a sibling checkout (`AXIOM/`, package name `axiom-agent`). In-process
mode imports `axiom.orchestrator` from its `src/` tree and points AXIOM's own
OpenAI-compatible client (raw `httpx`, no SDK dependency) at the gateway's
OpenAI-compatible shim on loopback -- the shim streams, which AXIOM's client
expects. Subprocess mode drives the program the only way it now exposes
itself: `axiom serve`, the agent-link service, with the task handed over
`POST /api/message` and the verdict read back off the snapshot. The service's
dependencies (pydantic, fastapi, uvicorn, httpx, networkx) live in
`AXIOM/.venv`, so when that virtualenv is missing it is bootstrapped --
idempotently, non-interactively -- before the service starts, the same seam
repair APEX's adapter makes for its native dependencies.

One failure mode is guarded against explicitly: with no base model configured,
AXIOM silently routes to its built-in deterministic mock engine, which
exercises the full pipeline with rule-based synthesis. A delegation answered
by the mock engine would come back `done` with confident-sounding fiction, so
both modes refuse to report a run whose reasoning engine is the mock: the
bridge either engaged, or the delegation failed saying so.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from clio.observability.logger import get_logger
from clio.platform import venv_python
from clio.subagents.base import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    SubAgent,
    SubAgentResult,
)

logger = get_logger("clio.subagents.axiom")

# The agent-link service's own terminal vocabulary for a task intention,
# mapped once. `succeeded` is AXIOM's verified completion; `failed` is the
# pipeline reporting it could not deliver; `cancelled` is an operator stop --
# a safe halt awaiting a decision, not a crash.
_SERVICE_STATUS = {
    "succeeded": STATUS_DONE,
    "failed": STATUS_FAILED,
    "cancelled": STATUS_BLOCKED,
}


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _request_json(
    method: str, url: str, *, body: dict[str, Any] | None = None, timeout_s: float = 10.0,
) -> tuple[bool, dict[str, Any], str]:
    """One loopback HTTP call to the agent-link service, stdlib only.

    The adapter must not add a dependency to this agent for the sake of its
    sub-agent's control plane; `urllib` in a worker thread is enough for the
    four calls a delegation makes.
    """
    import json as _json
    import urllib.request

    def _do() -> dict[str, Any]:
        data = _json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = response.read()
        return _json.loads(payload) if payload else {}

    try:
        return True, await asyncio.to_thread(_do), ""
    except Exception as exc:
        return False, {}, str(exc)


class AxiomSubAgent(SubAgent):
    name = "axiom"
    dirname = "AXIOM"
    description = (
        "Single-turn autonomous coding agent: takes a bounded coding task -- a "
        "feature, a fix, a refactor with its tests -- compiles it into a "
        "verified intent, models the workspace, plans the work as a DAG, "
        "mutates only inside a sandboxed shadow workspace and puts the result "
        "through an adversarial verification gauntlet before anything lands in "
        "the workspace. Returns a synthesized report with the changed files."
    )
    input_contract = "task: a coding task in plain text; workdir: the workspace to act on"

    # ---- in-process -------------------------------------------------------

    def _import_engine(self):
        src = self.root / "src"
        if not (src / "axiom").is_dir():
            raise ImportError(f"no axiom package under {src}")
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from axiom.config import AxiomConfig
        from axiom.orchestrator import AxiomOrchestrator

        return AxiomConfig, AxiomOrchestrator

    async def _run_inprocess(
        self, task: str, workdir: str | None, started: float,
    ) -> SubAgentResult:
        from clio.subagents.bridge import axiom_engine_config

        AxiomConfig, AxiomOrchestrator = self._import_engine()
        repo = self.repo_root(workdir)
        config = axiom_engine_config(
            AxiomConfig,
            gateway_url=self.gateway_url(),
            model_name=self.model_name(),
            workspace=repo,
        )
        orchestrator = AxiomOrchestrator(config)
        # A run answered by the mock engine is not a delegation: it is the
        # program's no-key fallback synthesizing plausible-looking work. The
        # bridge either engaged -- base model set, provider off the mock --
        # or this delegation fails and says so.
        if getattr(orchestrator.llm, "provider", "") == "mock":
            raise RuntimeError(
                "the gateway bridge did not engage; axiom would run its mock engine"
            )
        response = await orchestrator.run(task, workspace=str(repo))
        return self._result_from_response(response, "inprocess", started)

    def _result_from_response(self, response: Any, mode: str, started: float) -> SubAgentResult:
        success = bool(getattr(response, "success", False))
        summary_text = str(getattr(response, "summary", "") or "").strip()
        changes = list(getattr(response, "changes", []) or [])
        deliverables = list(getattr(response, "deliverables", []) or [])
        verification = getattr(response, "verification", None)
        verdict = str(getattr(verification, "verdict", "") or "") if verification is not None else ""
        if success:
            line = summary_text or f"axiom completed the task; {len(changes)} file(s) changed"
        else:
            reason = summary_text or (
                f"verification {verdict}" if verdict else "the run reported failure"
            )
            line = f"axiom did not complete the task: {reason}"
        detail: dict[str, Any] = {
            "run_id": str(getattr(response, "run_id", "") or ""),
            "provider": str(getattr(response, "provider", "") or ""),
            "deliverables": [str(item) for item in deliverables][:20],
            "changes": [
                {"path": str(change.path), "action": str(change.action)}
                for change in changes
                if hasattr(change, "path")
            ][:40],
        }
        if verification is not None:
            detail["verification"] = {
                "verdict": verdict,
                "score": float(getattr(verification, "score", 0.0) or 0.0),
            }
        return self.timed(SubAgentResult(
            agent=self.name,
            status=STATUS_DONE if success else STATUS_FAILED,
            summary=line[:2000],
            detail=detail,
            mode=mode,
        ), started)

    # ---- subprocess -------------------------------------------------------

    async def _child_python(self, started: float) -> Path | None:
        """The interpreter the agent-link service runs under.

        AXIOM's dependencies live in its own virtualenv, not in this agent's:
        importing the engine here fails on `networkx` the way APEX's plane
        fails on pyobjc. When the virtualenv is missing it is bootstrapped --
        `python -m venv` then `pip install` of the checkout itself -- the same
        idempotent, non-interactive repair APEX's adapter makes. Handing the
        service this agent's own interpreter instead is how a missing
        dependency came back as "the coding task failed" there.
        """
        python = venv_python(self.root)
        if python is not None:
            return python
        remaining = self.timeout_s() - (time.monotonic() - started)
        budget = max(120.0, min(900.0, remaining / 2))
        try:
            code, out, _ = await self.run_command(
                [sys.executable, "-m", "venv", str(self.root / ".venv")],
                cwd=self.root, timeout_s=min(180.0, budget),
            )
        except TimeoutError:
            logger.warning("subagent.axiom_venv_timeout")
            return None
        python = venv_python(self.root)
        if code != 0 or python is None:
            logger.warning("subagent.axiom_venv_failed", exit=code, output=out[-500:])
            return None
        try:
            code, out, _ = await self.run_command(
                [str(python), "-m", "pip", "install", "--quiet", str(self.root)],
                cwd=self.root, timeout_s=budget,
            )
        except TimeoutError:
            logger.warning("subagent.axiom_install_timeout")
            return None
        if code != 0:
            logger.warning("subagent.axiom_install_failed", exit=code, output=out[-500:])
            return None
        logger.info("subagent.axiom_bootstrap_done")
        return python

    async def _run_subprocess(
        self, task: str, workdir: str | None, started: float,
    ) -> SubAgentResult:
        python = await self._child_python(started)
        if python is None:
            return self.timed(SubAgentResult(
                agent=self.name, status=STATUS_UNAVAILABLE,
                summary=(
                    "axiom could not run: AXIOM/.venv is missing and bootstrapping "
                    "it did not succeed; run `pip install -e AXIOM` into a "
                    "virtualenv at AXIOM/.venv"
                ),
                mode="subprocess",
            ), started)
        repo = self.repo_root(workdir)
        timeout = self.timeout_s()
        deadline = started + timeout
        # The service persists its identity and base-model state under its own
        # home; a delegation runs in a throwaway one, so this agent's runs
        # never write into the operator's `~/.axiom`.
        home = Path(tempfile.mkdtemp(prefix="axiom-agent-home-"))
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        proc: asyncio.subprocess.Process | None = None
        try:
            env = self.child_env({
                "PYTHONPATH": str(self.root / "src"),
                "AXIOM_AGENT_HOME": str(home),
                "AXIOM_WORKSPACE": str(repo),
            })
            proc = await asyncio.create_subprocess_exec(
                str(python), "-m", "axiom", "serve",
                "--host", "127.0.0.1", "--port", str(port),
                "--workspace", str(repo),
                cwd=str(self.root), env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            return await self._drive_service(proc, base_url, task, deadline, started)
        finally:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            shutil.rmtree(home, ignore_errors=True)

    async def _drive_service(
        self,
        proc: asyncio.subprocess.Process,
        base_url: str,
        task: str,
        deadline: float,
        started: float,
    ) -> SubAgentResult:
        # Ready is when the snapshot answers, not when the port accepts: the
        # service starts uvicorn last.
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                out = (await proc.stdout.read()).decode("utf-8", errors="replace")
                raise RuntimeError(f"axiom serve exited {proc.returncode}: {out[-1500:]}")
            ok, _snapshot, _err = await _request_json("GET", f"{base_url}/api/snapshot")
            if ok:
                break
            await asyncio.sleep(1.0)
        else:
            raise TimeoutError("axiom serve did not become ready")

        # The base model is set the way the interface sets it -- `POST
        # /api/model`, provider `ollama` being the catalogue's local-endpoint
        # entry, the only kind that accepts a plain-http gateway URL. Without
        # it the service routes to its mock engine, and a mock run is never a
        # verdict this agent may report.
        ok, _payload, err = await _request_json(
            "POST", f"{base_url}/api/model",
            body={
                "provider": "ollama",
                "model": self.model_name(),
                "base_url": f"{self.gateway_url()}/v1",
                "api_key": "clio-gateway",
            },
        )
        if not ok:
            return self.timed(SubAgentResult(
                agent=self.name, status=STATUS_FAILED,
                summary=f"axiom service refused the gateway model configuration: {err}",
                detail={"output": err[-2000:]}, mode="subprocess",
            ), started)

        ok, payload, err = await _request_json(
            "POST", f"{base_url}/api/message",
            body={"text": task, "person_id": "clio-agent", "channel": "subagent"},
        )
        if not ok:
            return self.timed(SubAgentResult(
                agent=self.name, status=STATUS_FAILED,
                summary=f"the task was not accepted by the axiom service: {err}",
                mode="subprocess",
            ), started)

        # Fresh service, one delegation: the first task intention is this
        # run's. Its status transitions are the run's own verdict.
        status = ""
        snapshot: dict[str, Any] = {}
        while time.monotonic() < deadline:
            ok, snapshot, _err = await _request_json("GET", f"{base_url}/api/snapshot")
            if ok:
                intentions = [
                    item for item in (snapshot.get("intentions") or [])
                    if isinstance(item, dict) and item.get("kind") == "task"
                ]
                if intentions:
                    status = str(intentions[0].get("status") or "")
                    if status in _SERVICE_STATUS:
                        break
            await asyncio.sleep(2.0)
        else:
            return self.timed(SubAgentResult(
                agent=self.name, status=STATUS_TIMEOUT,
                summary=f"axiom exceeded its {self.timeout_s():.0f}s ceiling for task: {task[:80]}",
                mode="subprocess",
            ), started)

        messages = snapshot.get("messages") or []
        reply = next(
            (
                str(message.get("text") or "").strip()
                for message in reversed(messages)
                if isinstance(message, dict) and message.get("direction") == "outbound"
            ),
            "",
        )
        mapped = _SERVICE_STATUS.get(status, STATUS_FAILED)
        if mapped is STATUS_DONE:
            summary = reply or "axiom completed the task"
        elif mapped is STATUS_BLOCKED:
            summary = reply or f"the axiom run was cancelled ({status})"
        else:
            summary = reply or f"the axiom run reported {status or 'an unknown status'}"
        return self.timed(SubAgentResult(
            agent=self.name,
            status=mapped,
            summary=summary[:2000],
            detail={"intention_status": status, "service": base_url},
            mode="subprocess",
        ), started)

    # ---- SubAgent interface ------------------------------------------------

    async def run(self, task: str, *, workdir: str | None = None, **opts: Any) -> SubAgentResult:
        started = time.monotonic()
        mode = self.settings.mode
        if mode in ("inprocess", "auto"):
            try:
                return await self._run_inprocess(task, workdir, started)
            except ImportError as exc:
                if mode == "inprocess":
                    return self.timed(SubAgentResult(
                        agent=self.name, status=STATUS_UNAVAILABLE,
                        summary=f"axiom cannot be imported: {exc}", mode="inprocess",
                    ), started)
                logger.info("subagent.axiom_import_failed", error=str(exc))
            except Exception as exc:
                logger.exception("subagent.axiom_inprocess_failed")
                if mode == "inprocess":
                    return self.timed(SubAgentResult(
                        agent=self.name, status=STATUS_FAILED,
                        summary="axiom run failed", error=str(exc), mode="inprocess",
                    ), started)
        return await self._run_subprocess(task, workdir, started)

    async def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {"root": str(self.root), "mode": self.settings.mode}
        try:
            self._import_engine()
            info["available"] = True
            info["import"] = "in-process"
        except Exception as exc:
            python = venv_python(self.root)
            info["available"] = (self.root / "src" / "axiom").is_dir()
            info["import"] = (
                f"unavailable ({exc}); subprocess via "
                + (
                    str(python) if python is not None
                    else "bootstrap of AXIOM/.venv (pip install -e AXIOM)"
                )
            )
        return info


__all__ = ["AxiomSubAgent"]

