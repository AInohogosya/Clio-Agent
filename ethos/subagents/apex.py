"""APEX as a sub-agent: the revision-aware hybrid desktop controller.

APEX takes a natural-language desktop goal, observes the real desktop through
native accessibility trees and screenshots, and loops one fenced,
machine-verified step at a time until the goal is done or safely blocked.

Its planner is the only remote-inference component, and it goes through the
gateway bridge: APEX's own `TacticalPlanner` prompt building and schema
validation stay untouched, while the model call lands on this agent's
catalogue (APEX gains an `ethos` provider for this — see `APEX/ai_planner.py`,
which points its chat-completions client at the gateway's OpenAI-compatible
shim on loopback).

In-process mode runs APEX's `ControlPlane` on this loop; subprocess mode
drives `python main.py --goal ...` and maps its documented exit codes. APEX
needs native desktop permissions (macOS TCC), which is why `auto` mode falls
back to subprocess rather than failing a delegation over an import.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger
from ethos.subagents.base import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_OUTCOME_UNKNOWN,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    SubAgent,
    SubAgentResult,
)

logger = get_logger("ethos.subagents.apex")

# APEX's own terminal vocabulary, mapped once. `main.py` documents these exit
# codes; `TaskMode` uses the same distinction between "safely stopped" and
# "outcome unknown, never retry blind".
_EXIT_CODES = {
    0: STATUS_DONE,
    1: STATUS_FAILED,
    2: STATUS_OUTCOME_UNKNOWN,
    3: STATUS_BLOCKED,
    130: STATUS_FAILED,
}
_TASK_MODES = {
    "COMPLETED": STATUS_DONE,
    "STOPPED": STATUS_BLOCKED,
    "BLOCKED": STATUS_BLOCKED,
    "BLOCKED_UNKNOWN": STATUS_OUTCOME_UNKNOWN,
    "FAILED": STATUS_FAILED,
}


class ApexSubAgent(SubAgent):
    name = "apex"
    dirname = "APEX"
    description = (
        "Desktop automation agent: takes a natural-language goal for the real "
        "desktop, observes it through accessibility trees and screenshots, and "
        "executes one verified, fenced step at a time until the goal is done. "
        "Refuses to guess when an outcome cannot be verified."
    )
    input_contract = "task: a desktop goal in natural language; mode: visible|hidden (default hidden)"

    # ---- in-process -------------------------------------------------------

    def _import_plane(self):
        root = str(self.root)
        if not (self.root / "core_runtime.py").is_file():
            raise ImportError(f"no APEX modules under {self.root}")
        if root not in sys.path:
            sys.path.insert(0, root)
        from ai_planner import LLMTacticalPlanner, PlannerConfig
        from contracts import RuntimeConfig, SafetyMode, ScreenMode
        from core_runtime import ControlPlane
        from screen_modes import create_screen_controller

        return (LLMTacticalPlanner, PlannerConfig, RuntimeConfig,
                SafetyMode, ScreenMode, ControlPlane, create_screen_controller)

    async def _run_inprocess(
        self, task: str, mode: str, max_steps: int, started: float,
    ) -> SubAgentResult:
        from ethos.subagents.bridge import apex_planner_config

        if self.gateway is None:
            raise RuntimeError("gateway unavailable for the in-process bridge")
        (LLMTacticalPlanner, _planner_config_cls, RuntimeConfig, SafetyMode,
         ScreenMode, ControlPlane, create_screen_controller) = self._import_plane()

        screen_mode = ScreenMode.VISIBLE if mode == "visible" else ScreenMode.HIDDEN
        planner = LLMTacticalPlanner(apex_planner_config(
            self.config, self.gateway_url(), self.model_name(),
        ))
        plane: Any | None = None
        try:
            adapter = create_screen_controller(screen_mode)
            plane = ControlPlane(
                adapter=adapter, planner=planner,
                config=RuntimeConfig(
                    safety_mode=SafetyMode.STRICT,
                    screen_mode=screen_mode,
                    max_steps=max_steps,
                ),
            )
            await plane.start()
            snapshot = await plane.create_task(task)
            await plane.run_task(snapshot.task_id)
            final = await plane.wait_for_task(snapshot.task_id)
            return self.timed(self._result_from_snapshot(final, "inprocess"), started)
        finally:
            if plane is not None:
                try:
                    await plane.aclose()
                except Exception:
                    logger.exception("subagent.apex_aclose_failed")
            try:
                await planner.aclose()
            except Exception:
                pass

    def _result_from_snapshot(self, snapshot: Any, mode: str) -> SubAgentResult:
        task_mode = str(getattr(snapshot, "mode", ""))
        status = _TASK_MODES.get(task_mode.upper(), STATUS_FAILED)
        actions = getattr(snapshot, "actions", ()) or ()
        last_action = actions[-1] if actions else None
        summary = f"desktop task {task_mode.lower()} after {getattr(snapshot, 'steps_used', len(actions))} steps"
        if last_action is not None and getattr(last_action, "error", None):
            summary += f"; last error: {str(last_action.error)[:200]}"
        return SubAgentResult(
            agent=self.name,
            status=status,
            summary=summary,
            detail={
                "task_id": getattr(snapshot, "task_id", ""),
                "mode": task_mode,
                "steps_used": getattr(snapshot, "steps_used", None),
                "last_error": getattr(snapshot, "last_error", None),
            },
            mode=mode,
        )

    # ---- subprocess -------------------------------------------------------

    async def _run_subprocess(
        self, task: str, mode: str, max_steps: int, started: float,
    ) -> SubAgentResult:
        venv_python = self.root / ".venv" / "bin" / "python"
        python = venv_python if venv_python.exists() else Path(sys.executable)
        env = self.child_env({
            "LLM_PROVIDER": "ethos",
            "ETHOS_GATEWAY_URL": self.gateway_url(),
            "LLM_MODEL": self.model_name(),
            "APEX_MODE": mode,
            "APEX_SAFETY": "strict",
            "APEX_MAX_STEPS": str(max_steps),
        })
        argv = [
            str(python), str(self.root / "main.py"),
            "--goal", task,
            "--mode", mode,
            "--safety", "strict",
            "--max-steps", str(max_steps),
        ]
        timeout = self.timeout_s()
        try:
            code, out, _ = await self.run_command(argv, cwd=self.root, env=env, timeout_s=timeout)
        except TimeoutError:
            return self.timed(SubAgentResult(
                agent=self.name, status=STATUS_TIMEOUT,
                summary=f"apex exceeded its {timeout:.0f}s ceiling", mode="subprocess",
            ), started)
        status = _EXIT_CODES.get(code, STATUS_FAILED)
        return self.timed(SubAgentResult(
            agent=self.name,
            status=status,
            summary=f"desktop task ended with exit code {code} ({status})",
            detail={"exit_code": code, "output": out[-4000:]},
            mode="subprocess",
        ), started)

    # ---- SubAgent interface ------------------------------------------------

    async def run(self, task: str, *, workdir: str | None = None, **opts: Any) -> SubAgentResult:
        started = time.monotonic()
        mode = str(opts.get("mode") or self.settings.env.get("APEX_MODE") or "hidden")
        max_steps = int(opts.get("max_steps") or self.settings.env.get("APEX_MAX_STEPS") or 60)
        configured = self.settings.mode
        if configured in ("inprocess", "auto") and self.gateway is not None:
            try:
                return await self._run_inprocess(task, mode, max_steps, started)
            except ImportError as exc:
                if configured == "inprocess":
                    return self.timed(SubAgentResult(
                        agent=self.name, status=STATUS_UNAVAILABLE,
                        summary=f"apex cannot be imported: {exc}", mode="inprocess",
                    ), started)
                logger.info("subagent.apex_import_failed", error=str(exc))
            except Exception as exc:
                logger.exception("subagent.apex_inprocess_failed")
                if configured == "inprocess":
                    return self.timed(SubAgentResult(
                        agent=self.name, status=STATUS_FAILED,
                        summary="apex run failed", error=str(exc), mode="inprocess",
                    ), started)
        return await self._run_subprocess(task, mode, max_steps, started)

    async def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {"root": str(self.root), "mode": self.settings.mode}
        try:
            self._import_plane()
            info["available"] = True
            info["import"] = "in-process"
        except Exception as exc:
            python = self.root / ".venv" / "bin" / "python"
            info["available"] = (self.root / "main.py").is_file()
            info["import"] = (
                f"unavailable ({exc}); subprocess via "
                f"{python if python.exists() else sys.executable}"
            )
        return info


__all__ = ["ApexSubAgent"]
