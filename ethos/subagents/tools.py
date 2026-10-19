"""The model-facing tools for delegation to Foundry and APEX.

These sit beside `thread.spawn` in the catalog: where a thread is a delegate
frame of this agent's own mind, a sub-agent is a whole other program with its
own state machine — called here synchronously, with the caller's intention
carried along so the result lands in the same memory the rest of the work
uses. The registry and the gateway both come in as toolhost services, so a
toolhost without them (a worker process, a test) reports rather than crashes.
"""

from __future__ import annotations

from typing import Any

from ethos.schemas.tools import ToolErrorCode, ToolResult
from ethos.toolhost.server import Tool, ToolContext, Toolhost


def _registry(host: Toolhost) -> Any:
    try:
        return host.service("subagents")
    except RuntimeError:
        return None


# A delegation's own status vocabulary is richer than ok/failed, and the two
# the caller most needs told apart are the ones a bare `ok=False` would merge:
# `blocked` is a safe stop awaiting a human, `unknown` is an outcome that may or
# may not have happened and must never be retried blind. Both are failures of
# *this* step -- the work is not done -- so both are reported as one, with the
# real status carried in the payload so the loop's reflection can read it.
_SUBAGENT_ERROR_CODE = {
    "timeout": ToolErrorCode.TIMEOUT,
    "unavailable": ToolErrorCode.ENVIRONMENT,
}


def _as_result(payload: dict[str, Any]) -> ToolResult:
    """A sub-agent's outcome as an action outcome.

    Only `done` is success. Everything else -- failed, blocked, unknown,
    timeout, unavailable -- means the step's work was not accomplished, and
    reporting it as anything but a failure lets the caller mark the step done
    over a run that failed, was blocked, or whose result is genuinely unknown.
    """
    status = str(payload.get("status") or "")
    if status == "done":
        return ToolResult(ok=True, content=payload)
    summary = payload.get("summary") or payload.get("error") or ""
    return ToolResult(
        ok=False,
        content=payload,
        error_code=_SUBAGENT_ERROR_CODE.get(status, ToolErrorCode.EXECUTION),
        error=f"{payload.get('agent', 'sub-agent')} reported {status or 'an unknown status'}"
              + (f": {summary}" if summary else ""),
    )


class SubagentStatusTool(Tool):
    name = "subagent.status"
    description = (
        "List the external agent programs wired in as sub-agents "
        "(Foundry, APEX), what each is for, and whether each is reachable."
    )
    timeout_s = 15.0
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        registry = _registry(host)
        if registry is None:
            return {"available": [], "note": "sub-agent registry not available in this toolhost"}
        return {"available": registry.describe(), "health": await registry.health()}


class FoundryRunTool(Tool):
    name = "subagent.foundry"
    description = (
        "Delegate a software objective to Foundry, the gate-verified software "
        "factory. It designs and plans the change, then runs every task "
        "through a deterministic state machine with sandboxed gates and a "
        "merge train; merged branches land in the target repository. "
        "This call blocks until the objective is done, blocked or failed, and "
        "the agent does nothing else meanwhile -- for work expected to outlast "
        "a few minutes, spawn it as a thread (role 'foundry') and carry on."
    )
    # A ceiling, not a budget: the sub-agent stops itself at its own configured
    # `timeout_s` and reports `timeout`, which is a far better answer than this
    # layer cutting the call off with no result. It only exists so a wedged
    # child cannot hold the step open forever, and it is deliberately above the
    # default 3600s budget so the sub-agent's own accounting is what reports.
    timeout_s = 86400.0
    input_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "the product objective, in plain text"},
            "workdir": {"type": "string", "description": "repository to work on; default: the thread's workdir"},
            "max_cycles": {"type": "integer", "description": "cap on orchestrator cycles (debugging only)"},
        },
        "required": ["task"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        registry = _registry(host)
        if registry is None or not registry.has("foundry"):
            return _as_result({
                "agent": "foundry", "status": "unavailable",
                "error": "foundry sub-agent is not available",
            })
        result = await registry.run(
            "foundry",
            str(args["task"]),
            workdir=args.get("workdir") or ctx.cwd,
            max_cycles=args.get("max_cycles"),
        )
        if isinstance(result, dict):
            return _as_result(result)
        return _as_result(result.to_dict())


class ApexRunTool(Tool):
    name = "subagent.apex"
    description = (
        "Delegate a desktop goal to APEX, the revision-aware desktop "
        "controller. It observes the real desktop and executes one verified, "
        "fenced step at a time until the goal is done, blocked, or the "
        "outcome is unknowable. Drives real input on this machine. "
        "This call blocks until the goal resolves and the agent does nothing "
        "else meanwhile -- for longer work, spawn it as a thread (role 'apex')."
    )
    # Same relationship to the sub-agent's own budget as `subagent.foundry`:
    # above its 900s default, so APEX reports its own `blocked`/`failed`
    # verdict rather than being cut off here with nothing to say.
    timeout_s = 86400.0
    input_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "the desktop goal, in natural language"},
            "mode": {"type": "string", "enum": ["visible", "hidden"], "description": "default hidden"},
            "max_steps": {"type": "integer", "description": "step ceiling (default 60)"},
        },
        "required": ["task"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        registry = _registry(host)
        if registry is None or not registry.has("apex"):
            return _as_result({
                "agent": "apex", "status": "unavailable",
                "error": "apex sub-agent is not available",
            })
        result = await registry.run(
            "apex",
            str(args["task"]),
            mode=args.get("mode"),
            max_steps=args.get("max_steps"),
        )
        if isinstance(result, dict):
            return _as_result(result)
        return _as_result(result.to_dict())


__all__ = ["ApexRunTool", "FoundryRunTool", "SubagentStatusTool"]
