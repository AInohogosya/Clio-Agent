from __future__ import annotations

from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


class ThreadSpawnTool(Tool):
    name = "thread.spawn"
    description = (
        "Spawn a delegated Thread (same framework, same identity, isolated "
        "worktree, budget carved from the parent intention). Max 4 concurrent, "
        "max depth 2. Results arrive via the bus as thread.result events."
    )
    timeout_s = 60.0
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "framing": {"type": "string", "description": "role framing, e.g. 'verifier'"},
            "parent_intention_id": {"type": "string"},
            "budget_usd": {"type": "number"},
            "workdir": {"type": "string"},
            "role": {"type": "string", "enum": ["delegate", "verifier"]},
        },
        "required": ["goal"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        manager = host.service("threads")
        handle = await manager.spawn_spec({
            "goal": str(args["goal"]),
            "framing": str(args.get("framing", "")),
            "role": str(args.get("role", "delegate")),
            "parent_intention_id": args.get("parent_intention_id") or ctx.intention_id,
            "budget_usd": args.get("budget_usd"),
            "workdir": args.get("workdir"),
            "depth": 1,
            "spawner_thread_id": ctx.thread_id,
        })
        return {
            "thread_id": handle.thread_id,
            "status": handle.status,
            "note": "running in background; result will surface as a thread.result event",
        }


__all__ = ["ThreadSpawnTool"]
