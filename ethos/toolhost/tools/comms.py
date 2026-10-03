from __future__ import annotations

from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


class CommSendTool(Tool):
    name = "comm.send"
    description = (
        "Send a message to a person on a channel. Passes through outbound "
        "etiquette (interruption budgets, quiet hours, digest batching). "
        "Use this to start a conversation, not to answer one — a reply to a "
        "message that was actually sent to the agent is delivered for you. "
        "This one can come back saying it was held: nothing is sent when the "
        "last word on that conversation was the agent's own and has not been "
        "answered, or when the same words were already said there recently. "
        "Being told 'held' means nobody heard it — read the conversation block "
        "in your context before composing another."
    )
    timeout_s = 60.0
    input_schema = {
        "type": "object",
        "properties": {
            "person_id": {"type": "string"},
            "channel": {"type": "string"},
            "text": {"type": "string"},
            "urgency": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
            "conversation_key": {"type": "string"},
        },
        "required": ["person_id", "channel", "text"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        sender = host.services.get("comms_send")
        if sender is None:
            raise RuntimeError("comms outbound pipeline is not available in this toolhost")
        return await sender(
            person_id=str(args["person_id"]),
            channel=str(args.get("channel", "web")),
            text=str(args["text"]),
            urgency=str(args.get("urgency", "normal")),
            conversation_key=args.get("conversation_key"),
            in_reply_to=None,
            intention_id=ctx.intention_id,
        )


__all__ = ["CommSendTool"]
