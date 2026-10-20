from __future__ import annotations

import asyncio
from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


class CodeExecTool(Tool):
    name = "code.exec"
    description = "Execute Python in a persistent in-memory kernel session (state kept per session_id)."
    timeout_s = 120.0
    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "session_id": {"type": "string"},
            "reset": {"type": "boolean"},
        },
        "required": ["code"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        code = str(args.get("code", ""))
        if not code.strip():
            raise ValueError("code must not be empty")
        session_id = str(args.get("session_id", ctx.thread_id or "self"))
        if args.get("reset"):
            session = host.kernel_manager.reset(session_id)
        else:
            session = host.kernel_manager.get_or_create(session_id)
        result = await asyncio.to_thread(session.exec, code)
        return {"session_id": session_id, **result}


class CodeResetTool(Tool):
    name = "code.reset"
    description = "Reset a kernel session (clears all variables)."
    timeout_s = 15.0
    input_schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session_id = str(args["session_id"])
        host.kernel_manager.reset(session_id)
        return {"session_id": session_id, "reset": True}


__all__ = ["CodeExecTool", "CodeResetTool"]
