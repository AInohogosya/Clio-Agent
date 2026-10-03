from __future__ import annotations

from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


class ScheduleAtTool(Tool):
    name = "schedule.at"
    description = "Schedule a timer that fires a timer.fired event at a specific time."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "at": {"type": "string", "description": "ISO timestamp"},
            "payload": {"type": "object"},
        },
        "required": ["name", "at"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        scheduler = host.service("scheduler")
        timer_id = await scheduler.schedule_at(
            name=str(args["name"]), at=str(args["at"]), payload=args.get("payload") or {},
        )
        return {"timer_id": timer_id}


class ScheduleEveryTool(Tool):
    name = "schedule.every"
    description = "Schedule a recurring timer with a fixed interval."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "interval_s": {"type": "number"},
            "payload": {"type": "object"},
        },
        "required": ["name", "interval_s"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        scheduler = host.service("scheduler")
        timer_id = await scheduler.schedule_every(
            name=str(args["name"]), interval_s=float(args["interval_s"]),
            payload=args.get("payload") or {},
        )
        return {"timer_id": timer_id}


class ScheduleCancelTool(Tool):
    name = "schedule.cancel"
    description = "Cancel a scheduled timer."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {"timer_id": {"type": "string"}},
        "required": ["timer_id"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        scheduler = host.service("scheduler")
        await scheduler.cancel(str(args["timer_id"]))
        return {"cancelled": str(args["timer_id"])}


__all__ = ["ScheduleAtTool", "ScheduleEveryTool", "ScheduleCancelTool"]
