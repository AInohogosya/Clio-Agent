from __future__ import annotations

from typing import Any

from clio.social.conversation import DELIVERED
from clio.toolhost.server import Tool, ToolContext, Toolhost
from clio.util import clip_text

# How far back one call may reach, and how many rows it may return. The tool is
# a window over the whole transcript, so the bounds are what keep a casual
# "what did we say last week" from turning into a dump of every row the store
# has ever held. A year is the practical limit of "as far back as needed";
# beyond that the question is a retrieval problem, not a history problem.
MAX_HOURS_BACK = 24.0 * 366.0
MAX_LIMIT = 200
_DEFAULT_HOURS_BACK = 24.0
_DEFAULT_LIMIT = 50


class HistoryBrowseTool(Tool):
    name = "history.browse"
    description = (
        "Read past conversation history, including what is no longer in your "
        "context. hours_back: how far back."
    )
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "hours_back": {"type": "number", "description": "How far back, in hours."},
            "person_id": {"type": "string"},
            "channel": {"type": "string"},
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        db = host.service("memory").db
        if db is None:
            return {"error": "no_store", "messages": []}
        hours = _bounded_number(args.get("hours_back"), _DEFAULT_HOURS_BACK, MAX_HOURS_BACK)
        limit = int(_bounded_number(args.get("limit"), _DEFAULT_LIMIT, MAX_LIMIT))
        person = str(args.get("person_id") or "").strip()[:128]
        channel = str(args.get("channel") or "").strip()[:64]
        query = str(args.get("query") or "").strip()[:256]

        clauses = ["ts >= now() - make_interval(secs => $1)", DELIVERED]
        params: list[Any] = [hours * 3600.0]
        if person:
            params.append(person)
            clauses.append(f"person_id = ${len(params)}")
        if channel:
            params.append(channel)
            clauses.append(f"channel = ${len(params)}")
        if query:
            params.append(query)
            # A history question is usually a fragment the person used, not a
            # stem to be inflected, so the match is literal and case-blind.
            clauses.append(f"text ILIKE '%' || ${len(params)} || '%'")
        params.append(limit + 1)
        rows = await db.fetch(
            "SELECT id::text AS id, ts, direction, channel, conversation_key, "
            "person_id, sender_name, sender_username, text, delivery "
            f"FROM messages WHERE {' AND '.join(clauses)} "
            f"ORDER BY ts DESC LIMIT ${len(params)}",
            *params,
        )
        # One more row than the limit was asked for, so hasMore is a fact about
        # the store rather than a guess; the extra row is not returned. Oldest
        # first, because a transcript is read in the order it was said.
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows = list(reversed(rows))
        return {
            "hours_back": hours,
            "count": len(rows),
            "has_more": has_more,
            "messages": [
                {
                    "id": row["id"],
                    "ts": row["ts"].isoformat() if row["ts"] else None,
                    "direction": row["direction"],
                    "channel": row["channel"] or "",
                    "conversation_key": row["conversation_key"] or "",
                    "person_id": row["person_id"],
                    "sender_name": row["sender_name"],
                    "text": clip_text(row["text"] or "", 4000),
                }
                for row in rows
            ],
        }


def _bounded_number(value: Any, default: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number <= 0:
        return default
    return min(number, maximum)


__all__ = ["HistoryBrowseTool"]
