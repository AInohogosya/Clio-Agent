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

from ethos.toolhost.server import Tool, ToolContext, Toolhost


def _registry(host: Toolhost) -> Any:
    try:
        return host.service("subagents")
    except RuntimeError:
        return None


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
        "Long-running: call with a concrete, complete objective."
    )
    timeout_s = 7200.0
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
            return {"status": "unavailable", "error": "foundry sub-agent is not available"}
        result = await registry.run(
            "foundry",
            str(args["task"]),
            workdir=args.get("workdir") or ctx.cwd,
            max_cycles=args.get("max_cycles"),
        )
        if isinstance(result, dict) and result.get("status") == "unavailable":
            return result
        return result.to_dict()


class ApexRunTool(Tool):
    name = "subagent.apex"
    description = (
        "Delegate a desktop goal to APEX, the revision-aware desktop "
        "controller. It observes the real desktop and executes one verified, "
        "fenced step at a time until the goal is done, blocked, or the "
        "outcome is unknowable. Drives real input on this machine."
    )
    timeout_s = 1800.0
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
            return {"status": "unavailable", "error": "apex sub-agent is not available"}
        result = await registry.run(
            "apex",
            str(args["task"]),
            mode=args.get("mode"),
            max_steps=args.get("max_steps"),
        )
        if isinstance(result, dict) and result.get("status") == "unavailable":
            return result
        return result.to_dict()


__all__ = ["ApexRunTool", "FoundryRunTool", "SubagentStatusTool"]
