from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ethos.observability.logger import get_logger
from ethos.prompts.identity import cross_talk_note
from ethos.schemas.context import ConversationExchange, ConversationState

logger = get_logger("ethos.social.conversation")

# What counts as having been said.
#
# `direction = 'outbound'` is a row, not a delivery: the outbound path records
# messages it suppressed, held, batched or recognised as a repeat, and a person
# holding the transcript can see every one of those rows and did not receive any
# of them. So the question "has this person heard from me" has to be asked about
# rows that were delivered, or an agent that was refused three times reads as
# having been answered three times and waits forever for a reply it never asked
# for. Inbound rows carry no delivery block at all, so a missing one is a
# delivered message.
DELIVERED = "COALESCE(delivery->>'status', 'sent') = 'sent'"


def conversation_key(channel: str, person_id: str | None) -> str:
    """The one key a door and a person make together.

    Written once, here, because this is what decides which turns count as the
    same conversation, and two writers guessing separately is how a reply ends up
    in a transcript nobody was looking at. Matches what the outbound path writes.
    """
    return f"{channel}:{person_id}"


async def open_conversations(
    db: Any,
    *,
    within_h: float = 72.0,
    limit: int = 8,
    turns: int = 6,
    detail_chars: int = 400,
) -> list[ConversationState]:
    """Every conversation still live, newest first, with the turns that made it.

    Scoped to doors actually used recently rather than to every conversation that
    has ever existed: a person who stopped talking three weeks ago is not a
    conversation in progress, and listing them spends the block on transcripts
    nobody is standing in.

    Two queries rather than one: the first asks the index on
    `(conversation_key, ts)` which doors are live, and only the second looks at
    their contents. The tail is ranked in SQL so a busy conversation costs the
    same to read as a quiet one, instead of arriving as every row it ever had and
    being truncated afterwards.
    """
    if db is None:
        return []
    try:
        heads = await db.fetch(
            "SELECT DISTINCT ON (conversation_key) conversation_key, ts, direction, "
            f"person_id, channel, text FROM messages WHERE conversation_key IS NOT NULL "
            f"AND conversation_key <> '' AND {DELIVERED} "
            "AND ts >= now() - make_interval(secs => $1) "
            "ORDER BY conversation_key, ts DESC",
            float(within_h) * 3600.0,
        )
        if not heads:
            return []
        keys = [row["conversation_key"] for row in heads]
        rows = await db.fetch(
            "SELECT conversation_key, ts, direction, person_id, channel, text FROM ("
            "  SELECT conversation_key, ts, direction, person_id, channel, text,"
            "         row_number() OVER (PARTITION BY conversation_key ORDER BY ts DESC) AS rank"
            "  FROM messages WHERE conversation_key = ANY($1::text[]) "
            f"    AND {DELIVERED}"
            ") ranked WHERE rank <= $2 ORDER BY conversation_key, ts DESC",
            keys, int(turns),
        )
    except Exception:
        # The block is context, not the work. A lookup that fails leaves the agent
        # with what it already has rather than ending the cycle.
        logger.exception("conversation.open_failed")
        return []

    tail: dict[str, list[ConversationExchange]] = {}
    for row in rows:
        tail.setdefault(row["conversation_key"], []).append(ConversationExchange(
            ts=row["ts"], direction=row["direction"], person_id=row["person_id"],
            text=_clip(row["text"] or "", detail_chars), channel=row["channel"] or "",
        ))

    states: list[ConversationState] = []
    for row in heads:
        exchanges = tail.get(row["conversation_key"], [])
        exchanges.reverse()
        states.append(ConversationState(
            key=row["conversation_key"],
            person_id=row["person_id"],
            channel=row["channel"] or "",
            last_ts=row["ts"],
            last_direction=row["direction"] or "",
            last_text=_clip(row["text"] or "", detail_chars),
            exchanges=exchanges,
        ))
    states.sort(key=lambda state: state.last_ts, reverse=True)
    return states[:limit]


def render_conversations(states: list[ConversationState], *, now: datetime | None = None,
                         preview: bool = False) -> str:
    """The conversations as one block, for the agent to read about itself.

    Written to be read by the agent deciding what to say next, so it names where
    each conversation stands before the words in it: whose turn it is, and how
    long that has been true. A transcript on its own is only a record; the block
    also has to answer "am I the one who owes the next message?".

    `preview` is asked here rather than assembled by the caller because this is
    the only place the agent sees several strangers' words on one page. The note
    below it tells each person they hold the whole of their own transcript, and
    that is true — and it implies the same of every other person listed, which is
    exactly the inference the cross-talk note exists to refuse. Rendered here so
    the two cannot be read apart.
    """
    now = now or datetime.now(UTC)
    lines: list[str] = []
    for state in states:
        lines.append(
            f"- {state.key} ({state.person_id or 'unknown'}, {state.channel or 'unknown'})"
            f" — {_standing(state, now)}"
        )
        for exchange in state.exchanges:
            who = "me" if exchange.direction == "outbound" else (exchange.person_id or "them")
            lines.append(f"  {who}: {_one_line(exchange.text)}")
    transcript = "\n".join(lines)
    closing = (
        "The person on each of these has the whole transcript, including every line "
        "above, and can scroll back through all of it. So: do not re-ask what was "
        "already asked, do not re-say what was already said, and do not open a new "
        "subject on a conversation where the last word was yours."
    )
    # Blank line between the two, and only there: the transcript above is one list
    # read line by line, and a rule about it reads as part of it unless it is
    # visibly a separate claim.
    return f"{transcript}\n\n{closing}\n\n{cross_talk_note(preview=preview)}"


async def last_spoken(db: Any, key: str, *, within_h: float) -> Any:
    """The last thing actually delivered on a conversation, or None.

    `within_h` is required because it is what makes the answer mean anything:
    "still unanswered" is a claim about the last day or so. A message from last
    month is not somebody the agent is waiting on, and reading it as one would
    make the agent hold its tongue at somebody indefinitely.
    """
    if db is None or not key:
        return None
    try:
        return await db.fetchrow(
            "SELECT ts, direction, person_id, text FROM messages "
            f"WHERE conversation_key = $1 AND {DELIVERED} "
            "AND ts >= now() - make_interval(secs => $2) "
            "ORDER BY ts DESC LIMIT 1",
            key, float(within_h) * 3600.0,
        )
    except Exception:
        logger.exception("conversation.last_spoken_failed", key=key)
        return None


def awaiting_reply(last: Any) -> bool:
    """Whether the agent spoke last on a conversation and has not been answered."""
    return last is not None and str(last["direction"]) == "outbound"


async def recent_words(db: Any, key: str, *, within_h: float) -> set[str]:
    """What the agent has already *said* here, as comparable text.

    Normalised rather than compared raw because the repeat that matters is the
    one a person reads as a repeat: the same sentence, re-sent with a different
    amount of whitespace or capitalisation.

    Filtered to delivered rows for the same reason everything else here is. A
    message sitting in the digest has not been said yet, and treating it as said
    would have the gate answer "the person has it" about something they have never
    received — while the queue in front of it still holds it, where the
    window-level check belongs anyway.
    """
    if db is None or not key:
        return set()
    try:
        rows = await db.fetch(
            "SELECT text FROM messages WHERE conversation_key = $1 "
            f"AND direction = 'outbound' AND {DELIVERED} "
            "AND ts >= now() - make_interval(secs => $2) LIMIT 50",
            key, float(within_h) * 3600.0,
        )
    except Exception:
        logger.exception("conversation.recent_words_failed", key=key)
        return set()
    return {normalise(row["text"] or "") for row in rows} - {""}


def normalise(text: str) -> str:
    """The form two messages have to match in to count as the same message."""
    return " ".join(str(text).split()).casefold()


def _standing(state: ConversationState, now: datetime) -> str:
    when = _ago(state.age_h(now))
    if state.awaiting_reply:
        return f"last word was mine, {when} ago, and it has not been answered"
    return f"last word was theirs, {when} ago"


def _ago(hours: float) -> str:
    if hours < 1.0:
        return f"{int(hours * 60)}m"
    if hours < 24.0:
        return f"{hours:.1f}h"
    return f"{int(hours // 24)}d"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
