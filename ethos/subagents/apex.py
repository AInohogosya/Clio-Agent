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
back to subprocess rather than failing a delegation over an import. The
fallback is only real if the child can run at all: APEX's native dependencies
(pyobjc, mss, pyautogui) live in `APEX/.venv`, so when that virtualenv is
missing it is bootstrapped — idempotently, non-interactively — before the
subprocess starts. Handing the child this agent's own interpreter instead is
how a missing dependency came back as "the desktop task failed": the child
crashed on the same import that broke in-process mode, before it had touched
the desktop, and its exit code was read as a verdict on a task it never ran.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from ethos.observability.logger import get_logger
from ethos.platform import venv_python
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

# APEX's own terminal vocabulary, mapped once. `main.py` returns 0 for a
# verified completion, 2 for an outcome-unknown stop, 3 for a safe block and
# 130 for a failed or operator-stopped task. Exit 1 is different: it is the
# crash path — an exception that escaped `run_controller` before any task
# reached a terminal state — so it means the controller never delivered a
# verdict, which is "unavailable" and not "failed". Reading it as a failed
# task is how a missing pyobjc import became a report that a desktop change
# had been attempted and had failed.
_EXIT_CODES = {
    0: STATUS_DONE,
    1: STATUS_UNAVAILABLE,
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

    # One bootstrap attempt per process: a failed pip install will not get
    # better by being retried inside the delegation that needs its result.
    _bootstrap_attempted = False
    BOOTSTRAP_TIMEOUT_S = 600.0

    async def _child_python(self, started: float) -> Path | None:
        """An interpreter that can actually run APEX, bootstrapping one if needed.

        In-process mode imports APEX into this process, so it inherits this
        agent's interpreter whatever that interpreter has. The subprocess
        fallback used to inherit the same interpreter when `APEX/.venv` was
        missing — which made it crash with the very dependency error that
        triggered the fallback, seconds before the child could have touched
        the desktop. `bootstrap.py` exists to create the virtualenv the child
        needs, so the fallback falls back to something that can run.
        """
        python = venv_python(self.root)
        if python is not None:
            return python
        bootstrap = self.root / "bootstrap.py"
        if not bootstrap.is_file() or ApexSubAgent._bootstrap_attempted:
            return None
        ApexSubAgent._bootstrap_attempted = True
        budget = max(
            60.0, min(self.BOOTSTRAP_TIMEOUT_S, self.timeout_s() - (time.monotonic() - started)),
        )
        logger.info("subagent.apex_bootstrap_started", root=str(self.root))
        # `--skip-permissions` keeps the native TCC dialogs for the real run,
        # and `--skip-api-key` because the child reaches the model through the
        # gateway bridge, not a credential of its own.
        try:
            code, out, _ = await self.run_command(
                [str(sys.executable), str(bootstrap),
                 "--skip-permissions", "--skip-api-key", "--no-dev"],
                cwd=self.root, timeout_s=budget,
            )
        except TimeoutError:
            logger.warning("subagent.apex_bootstrap_timeout", seconds=budget)
            return None
        python = venv_python(self.root)
        if code == 0 and python is not None:
            logger.info("subagent.apex_bootstrap_done")
            return python
        logger.warning("subagent.apex_bootstrap_failed", exit=code, output=out[-800:])
        return None

    async def _run_subprocess(
        self, task: str, mode: str, max_steps: int, started: float,
    ) -> SubAgentResult:
        python = await self._child_python(started)
        if python is None:
            return self.timed(SubAgentResult(
                agent=self.name, status=STATUS_UNAVAILABLE,
                summary=(
                    "apex could not run: APEX/.venv is missing and bootstrapping "
                    "it did not succeed; run APEX/bootstrap.py to install its "
                    "native dependencies"
                ),
                mode="subprocess",
            ), started)
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
        detail = {"exit_code": code, "output": out[-4000:]}
        if status is STATUS_UNAVAILABLE:
            # The controller died before any task could be given a verified
            # verdict, so the honest sentence names the crash, not the task.
            # Quoting its own last line keeps the cause — a missing dependency,
            # a refused permission — attached to the failure it produced.
            last_line = next(
                (line for line in reversed(out.splitlines()) if line.strip()), "",
            )
            status_summary = (
                f"apex crashed before it could report a task outcome "
                f"(exit {code}): {last_line[-300:]}"
            )
        else:
            status_summary = f"desktop task ended with exit code {code} ({status})"
        return self.timed(SubAgentResult(
            agent=self.name,
            status=status,
            summary=status_summary,
            detail=detail,
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
            (_planner, _config_cls, _runtime, _safety, ScreenMode, _plane,
             create_screen_controller) = self._import_plane()
            # The planner modules importing is not the health the caller is
            # asking about. A delegation that cannot reach the desktop is the
            # failure that was observed: the planner imports cleanly, the run
            # starts, and `create_screen_controller` raises on the native
            # pyobjc dependency the planner never touches — so a registry that
            # checked only the import plane reported APEX available while
            # every actual run died on contact. Constructing the hidden-mode
            # controller here is the same import chain a real run takes, and
            # the constructor only imports and initializes state — nothing is
            # put on the display until `install()`.
            create_screen_controller(ScreenMode.HIDDEN)
            info["available"] = True
            info["import"] = "in-process"
        except Exception as exc:
            python = venv_python(self.root)
            info["available"] = (self.root / "main.py").is_file()
            info["import"] = (
                f"unavailable ({exc}); subprocess via "
                + (
                    str(python) if python is not None
                    else "bootstrap of APEX/.venv (run APEX/bootstrap.py)"
                )
            )
        return info


__all__ = ["ApexSubAgent"]
