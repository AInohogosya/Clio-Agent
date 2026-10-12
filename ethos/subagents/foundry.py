"""Foundry as a sub-agent: the deterministic, gate-verified software factory.

Foundry takes a product objective in plain text and runs it through its own
fixed state machine — LLM-designed architecture and planning, then sandboxed
gates, merge trains and signed artifacts, with authority living in non-LLM
code. What this adapter adds is only the seam: a Foundry run under this
agent's delegation speaks to the model through the gateway bridge, and its
outcome comes back in `SubAgentResult` terms.

Foundry is a sibling checkout (`Foundry/`, package name `foundry-factory`).
In-process mode imports `foundry.orchestrator` from its `src/` tree and
injects the gateway-backed provider; subprocess mode drives the same code
through `python -m foundry` with the child pointed at the gateway's
OpenAI-compatible shim. Both use Foundry's SQLite/LocalQueue defaults, so a
delegation needs no database, no redis and no keys of its own.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger
from ethos.subagents.base import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    SubAgent,
    SubAgentResult,
    extract_json_object,
)

logger = get_logger("ethos.subagents.foundry")

_MERGED = "MERGED"


class FoundrySubAgent(SubAgent):
    name = "foundry"
    dirname = "Foundry"
    description = (
        "Gate-verified software factory: takes a product objective in plain "
        "text, designs and plans it with LLM roles, then runs every task "
        "through a deterministic state machine with sandboxed gates and a "
        "merge train. Returns merged branches in the target repository."
    )
    input_contract = "task: a product objective in plain text; workdir: the repository to work on"

    # ---- in-process -------------------------------------------------------

    def _import_orchestrator(self):
        src = self.root / "src"
        if not (src / "foundry").is_dir():
            raise ImportError(f"no foundry package under {src}")
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from foundry.config import FoundryConfig
        from foundry.orchestrator.core import Orchestrator

        return FoundryConfig, Orchestrator

    def _orchestrate(
        self,
        orchestrator_cls: Any,
        config: Any,
        provider: Any,
        task: str,
        max_cycles: int | None,
    ) -> dict[str, Any]:
        orch = orchestrator_cls(config, provider=provider)
        objective_id = orch.submit_objective(task)
        summary = orch.run(max_cycles=max_cycles)
        summary["objective_id"] = objective_id
        return summary

    async def _run_inprocess(
        self, task: str, workdir: str | None, max_cycles: int | None, started: float,
    ) -> SubAgentResult:
        from ethos.subagents.bridge import FoundryGatewayProvider

        FoundryConfig, Orchestrator = self._import_orchestrator()
        config = FoundryConfig.load(repo_root=self.repo_root(workdir))
        # The loop is captured here, on the event-loop thread: `_orchestrate`
        # runs in a worker thread (Orchestrator is synchronous), and its
        # provider hops back to this loop for every model call.
        loop = asyncio.get_running_loop()
        provider = FoundryGatewayProvider(
            self.gateway, loop,
            agent_name=self.name,
            tier=self.tier(),
            purpose=self.purpose(),
            timeout_s=self.timeout_s(),
            metadata={"thread_id": str(self.repo_root(workdir))},
        )
        summary = await asyncio.to_thread(
            self._orchestrate, Orchestrator, config, provider, task, max_cycles,
        )
        return self.timed(self._result_from_summary(summary, "inprocess", started), started)

    def _result_from_summary(
        self, summary: dict[str, Any], mode: str, started: float,
    ) -> SubAgentResult:
        by_status = summary.get("tasks_by_status") or {}
        merged = int(by_status.get(_MERGED, 0))
        total = sum(int(v) for v in by_status.values())
        objectives = summary.get("objectives") or []
        objective_done = bool(objectives) and all(
            str(obj.get("status", "")).lower() in ("done", "complete", "merged", "archived")
            for obj in objectives
        )
        if total == 0:
            if objective_done or not objectives:
                status, detail_line = STATUS_DONE, "objective accepted; no tasks were produced"
            else:
                status, detail_line = STATUS_FAILED, "no tasks were produced"
        elif merged == total:
            status, detail_line = STATUS_DONE, f"{merged}/{total} tasks merged"
        elif merged > 0:
            status, detail_line = STATUS_BLOCKED, f"{merged}/{total} tasks merged; the rest need attention"
        else:
            status, detail_line = STATUS_BLOCKED, f"0/{total} tasks merged"
        # Full task records are the sub-agent's own ledger; the headline counts
        # travel, the payloads stay queryable where the program keeps them.
        detail = {k: v for k, v in summary.items() if k != "tasks"}
        return SubAgentResult(
            agent=self.name,
            status=status,
            summary=detail_line,
            detail=detail,
            mode=mode,
        )

    # ---- subprocess -------------------------------------------------------

    def _python_and_env(self, workdir: str | None) -> tuple[Path, dict[str, str], Path]:
        repo = self.repo_root(workdir)
        venv_python = self.root / ".venv" / "bin" / "python"
        python = venv_python if venv_python.exists() else Path(sys.executable)
        env = self.child_env({
            "FOUNDRY_LLM_PROVIDER": "openai",
            "FOUNDRY_LLM_ENDPOINT": f"{self.gateway_url()}/v1",
            "FOUNDRY_LLM_MODEL": self.model_name(),
            "FOUNDRY_LLM_API_KEY": "clio-gateway",
            "FOUNDRY_REPO_ROOT": str(repo),
            "PYTHONPATH": str(self.root / "src"),
        })
        return python, env, repo

    async def _run_subprocess(
        self, task: str, workdir: str | None, max_cycles: int | None, started: float,
    ) -> SubAgentResult:
        python, env, repo = self._python_and_env(workdir)
        timeout = self.timeout_s()
        argv_create = [str(python), "-m", "foundry", "objective", "create", "--text", task]
        argv_run = [str(python), "-m", "foundry", "run"]
        if max_cycles is not None:
            argv_run += ["--max-cycles", str(max_cycles)]
        argv_status = [str(python), "-m", "foundry", "status"]
        for argv in (argv_create, argv_run, argv_status):
            try:
                code, out, _ = await self.run_command(
                    argv, cwd=repo, env=env, timeout_s=max(60.0, timeout - (time.monotonic() - started)),
                )
            except TimeoutError:
                return self.timed(SubAgentResult(
                    agent=self.name, status=STATUS_TIMEOUT,
                    summary=f"foundry exceeded its {timeout:.0f}s ceiling", mode="subprocess",
                ), started)
            if code != 0:
                return self.timed(SubAgentResult(
                    agent=self.name, status=STATUS_FAILED,
                    summary=f"foundry {' '.join(argv[2:4])} exited {code}",
                    detail={"output": out[-4000:]}, mode="subprocess",
                ), started)
        status_summary = extract_json_object(out) or {}
        return self._result_from_summary(status_summary, "subprocess", started)

    # ---- SubAgent interface ------------------------------------------------

    async def run(self, task: str, *, workdir: str | None = None, **opts: Any) -> SubAgentResult:
        started = time.monotonic()
        max_cycles = opts.get("max_cycles")
        mode = self.settings.mode
        if mode in ("inprocess", "auto") and self.gateway is not None:
            try:
                return await self._run_inprocess(task, workdir, max_cycles, started)
            except ImportError as exc:
                if mode == "inprocess":
                    return self.timed(SubAgentResult(
                        agent=self.name, status=STATUS_UNAVAILABLE,
                        summary=f"foundry cannot be imported: {exc}", mode="inprocess",
                    ), started)
                logger.info("subagent.foundry_import_failed", error=str(exc))
            except Exception as exc:
                logger.exception("subagent.foundry_inprocess_failed")
                if mode == "inprocess":
                    return self.timed(SubAgentResult(
                        agent=self.name, status=STATUS_FAILED,
                        summary="foundry run failed", error=str(exc), mode="inprocess",
                    ), started)
        return await self._run_subprocess(task, workdir, max_cycles, started)

    async def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {"root": str(self.root), "mode": self.settings.mode}
        try:
            self._import_orchestrator()
            info["available"] = True
            info["import"] = "in-process"
        except Exception as exc:
            cli = self.root / ".venv" / "bin" / "python"
            fallback = cli if cli.exists() else Path(sys.executable)
            info["available"] = (self.root / "src" / "foundry").is_dir()
            info["import"] = f"unavailable ({exc}); subprocess via {fallback}"
        return info


__all__ = ["FoundrySubAgent"]
