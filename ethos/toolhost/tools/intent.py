from __future__ import annotations

from typing import Any
from uuid import UUID

from ethos.toolhost.server import Tool, ToolContext, Toolhost


def _parse_intention_id(raw: Any, *, what: str = "intention_id") -> UUID:
    """An intention id as the tool means it, or a message that can be acted on.

    `UUID(...)` answers a malformed id with `badly formed hexadecimal UUID
    string` -- true, and useless: it says nothing about where a usable id comes
    from, and it arrived 39 times in the logs as the whole error text. The
    caller is the agent, and the one useful thing to tell it is the same thing
    a person would be told: list the intentions and copy an id from there.
    """
    text = str(raw or "").strip()
    try:
        return UUID(text)
    except (ValueError, AttributeError):
        raise ValueError(
            f"{what} {text[:60]!r} is not an intention id; ids look like "
            "uuid4 strings and come from intent.list — call it and copy one"
        ) from None


class IntentCreateTool(Tool):
    name = "intent.create"
    description = "Create an intention in the intention tree."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "desired_end_state": {"type": "string"},
            "kind": {"type": "string", "enum": ["goal", "task", "commitment", "venture", "habit"]},
            "parent_id": {"type": "string"},
            "priority": {"type": "number"},
            "budget_usd": {"type": "number"},
        },
        "required": ["title"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")

        parent_id = (
            _parse_intention_id(args["parent_id"], what="parent_id")
            if args.get("parent_id") else None
        )
        intention_id = await memory.create_intention(
            title=str(args["title"]),
            desired_end_state=str(args.get("desired_end_state", "")),
            kind=str(args.get("kind", "task")),
            parent_id=parent_id,
            priority=float(args.get("priority", 0.5)),
            budget_usd=args.get("budget_usd"),
        )
        return {"intention_id": str(intention_id)}


class IntentGetTool(Tool):
    name = "intent.get"
    description = "Fetch an intention by id."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {"intention_id": {"type": "string"}},
        "required": ["intention_id"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")

        intention = await memory.get_intention(_parse_intention_id(args["intention_id"]))
        if intention is None:
            raise KeyError("intention not found")
        return intention.model_dump(mode="json")


class IntentListTool(Tool):
    name = "intent.list"
    description = "List open intentions."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "statuses": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        intentions = await memory.list_intentions(
            statuses=args.get("statuses"), limit=int(args.get("limit", 50)),
        )
        return [i.model_dump(mode="json") for i in intentions]


class IntentUpdateTool(Tool):
    name = "intent.update"
    description = "Update intention fields (status, priority, bookmark, ...)."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "intention_id": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "number"},
            "resume_state": {"type": "object"},
        },
        "required": ["intention_id"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")

        fields = {k: v for k, v in args.items() if k != "intention_id"}
        await memory.update_intention(_parse_intention_id(args["intention_id"]), **fields)
        return {"updated": True}


class IntentCloseTool(Tool):
    name = "intent.close"
    description = "Close an intention with a status and reason."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "intention_id": {"type": "string"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["intention_id", "status", "reason"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")

        await memory.close_intention(
            _parse_intention_id(args["intention_id"]), str(args["status"]), str(args["reason"]),
        )
        return {"closed": str(args["intention_id"])}


__all__ = [
    "IntentCreateTool", "IntentGetTool", "IntentListTool", "IntentUpdateTool",
    "IntentCloseTool",
]
