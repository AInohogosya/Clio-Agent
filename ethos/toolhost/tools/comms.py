from __future__ import annotations

from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost

#: The channels an outbound send may name. The same set the web bridge checks
#: `POST /api/message` against, restated here for the same reason: `channel`
#: decides where the reply is delivered and which conversation the row is filed
#: under, so a free-text one is a way to file the agent's own words under a
#: conversation nobody can find.
SEND_CHANNELS = ("web", "cli", "telegram", "whatsapp", "slack", "discord", "email")


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
            "channel": {"type": "string", "enum": list(SEND_CHANNELS)},
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
            channel=_send_channel(args.get("channel")),
            text=str(args["text"]),
            urgency=str(args.get("urgency", "normal")),
            conversation_key=args.get("conversation_key"),
            in_reply_to=None,
            intention_id=ctx.intention_id,
        )


def _send_channel(value: Any) -> str:
    """The channel a send goes out of, or the local line for anything else.

    The model names the channel, and a model that answers with `None`, an
    urgency, or a phrase it read out of the conversation is not naming a door.
    The local line is the fallback rather than a refusal, because the row is
    still recorded either way — a refusal here would file the words under a
    channel nobody can find, which is how a transcript fills with conversations
    that do not exist.
    """
    name = str(value if value is not None else "").strip().lower()
    return name if name in SEND_CHANNELS else "web"


__all__ = ["CommSendTool", "SEND_CHANNELS"]
