from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ethos.config import EthosConfig, clock_timezone
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind
from ethos.schemas.messages import Urgency
from ethos.social.conversation import (
    awaiting_reply,
    last_spoken,
    normalise,
    recent_words,
)
from ethos.social.conversation import conversation_key as key_for

logger = get_logger("ethos.social.etiquette")


def _parse_hhmm(value: str):
    hh, mm = value.strip().split(":")
    return int(hh), int(mm)


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    start_h, start_m = _parse_hhmm(start)
    end_h, end_m = _parse_hhmm(end)
    current = now.hour * 60 + now.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if start_minutes <= end_minutes:
        return start_minutes <= current < end_minutes
    return current >= start_minutes or current < end_minutes


class OutboundPipeline:
    """Outbound etiquette [D-31]: interruption budgets, quiet hours,
    2-hour update batching, and two gates on speaking first.

    Critical urgency bypasses quiet hours and is always audited."""

    def __init__(
        self,
        config: EthosConfig,
        db: Any = None,
        bus: Any = None,
        audit: Any = None,
        relationships: Any = None,
        sender: Any = None,
        now_fn: Any = lambda: datetime.now(UTC),
    ):
        self.config = config
        self.db = db
        self.bus = bus
        self.audit = audit
        self.relationships = relationships
        self.sender = sender
        self.now_fn = now_fn
        self.digests: dict[str, dict[str, Any]] = {}
        self.window_s = config.permissions.interruption.digest_window_min * 60

    def daily_budget(self, relation: str | None) -> int:
        budgets = self.config.permissions.interruption.daily_budgets
        return budgets.get(relation or "other", budgets.get("other", 1))

    async def _contact_count_today(self, person_id: str) -> int:
        if self.db is None:
            return 0
        # The budget's "day" is a local day, for the same reason the quiet
        # hours are local hours: both are written for a person's clock, and a
        # day that turns over at 9am in Tokyo is not a day anybody asked for.
        local = self.now_fn().astimezone(clock_timezone(self.config.timezone))
        start = datetime.combine(local.date(), datetime.min.time(),
                                  tzinfo=local.tzinfo)
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM messages WHERE direction = 'outbound' AND person_id = $1 "
            "AND ts >= $2 AND (delivery->>'status') = 'sent'",
            person_id, start,
        )
        return int(count or 0)

    async def can_contact(self, person_id: str | None, urgency: str) -> tuple[bool, str]:
        now = self.now_fn().astimezone(clock_timezone(self.config.timezone))
        inter = self.config.permissions.interruption
        if urgency in (Urgency.high.value, Urgency.critical.value):
            if urgency == Urgency.critical.value:
                if self.audit is not None:
                    await self.audit.append(
                        actor="self", category="social",
                        summary="critical message sent (bypasses quiet hours)",
                        details={"person_id": person_id},
                    )
                return True, "critical urgency bypasses etiquette"
            if in_quiet_hours(now, inter.quiet_hours["start"], inter.quiet_hours["end"]):
                return False, "quiet hours"
            return True, "high urgency within hours"
        if in_quiet_hours(now, inter.quiet_hours["start"], inter.quiet_hours["end"]):
            return False, "quiet hours"
        relation = None
        if self.relationships is not None and person_id:
            person = await self.relationships.get(person_id)
            relation = person.get("relation") if person else None
        budget = self.daily_budget(relation)
        if person_id:
            used = await self._contact_count_today(person_id)
            if used >= budget:
                return False, f"daily interruption budget exhausted ({used}/{budget})"
        return True, "ok"

    async def _why_not_speak_first(self, key: str, text: str) -> str | None:
        """Why the agent should not open this conversation, or None for no objection.

        The two things that make an unsolicited message read as noise are both
        about what the agent has already done here, and neither was asked before.

        It had already said these exact words. Without a check, a thought that
        resolves to the same sentence every cycle queues the same sentence every
        cycle, and the digest flushes them as a paragraph the person reads four
        times — from an agent that, as far as anyone can tell, has nothing else
        going on.

        And its own last word is still unanswered. This is the one the agent could
        not see coming: it had no record of having spoken last beyond a buffer of
        paraphrased turns, and the person had the whole transcript. So it treated
        a conversation it had already interrupted as a fresh one and told them
        something else, which is how a person ends up being sent unrelated news
        about work they asked about hours ago and never got an answer to.
        """
        inter = self.config.permissions.interruption
        said = await recent_words(self.db, key, within_h=inter.repeat_window_h)
        if normalise(text) in said:
            return "already said on this conversation; the person has it"
        if await self._held_back(key):
            return "the last word here was mine and it has not been answered"
        return None

    async def _held_back(self, key: str) -> bool:
        """Whether the agent spoke last on a conversation and has not been answered."""
        within_h = self.config.permissions.interruption.awaiting_reply_h
        return awaiting_reply(await last_spoken(self.db, key, within_h=within_h))

    async def send(
        self,
        *,
        person_id: str | None,
        channel: str,
        text: str,
        urgency: str = Urgency.normal.value,
        conversation_key: str | None = None,
        in_reply_to: Any = None,
        intention_id: Any = None,
    ) -> dict[str, Any]:
        if urgency not in (u.value for u in Urgency):
            urgency = Urgency.normal.value
        key = conversation_key or key_for(channel, person_id)
        # A reply to a named message is an answer, not an interruption.
        #
        # Every gate below this line — the digest window, quiet hours, the daily
        # budget, the two in `_why_not_speak_first` — exists to stop the agent from
        # *starting* a conversation that nobody is in. A person who wrote is already
        # in it, and this is the answer to what they wrote. Running a reply through
        # any of them is how a deliberate `reply_now` becomes two hours of silence,
        # or how a question is answered with the same words it was answered with an
        # hour ago, and then the surface reports a decision the agent made as a link
        # that broke.
        #
        # So a solicited reply is delivered. Nothing here decides *whether* to reply:
        # the agent has already deliberated and chosen, and the choice still stands.
        # What is fixed is that a chosen reply arrives.
        if in_reply_to is not None:
            return await self._deliver(
                person_id, channel, text, urgency, conversation_key, in_reply_to, intention_id,
            )
        if (
            intention_id is not None
            and person_id
            and self.config.permissions.interruption.midtask_sends_immediate
        ):
            return await self._send_midtask(person_id, key, channel, text, urgency, intention_id)
        if urgency in (Urgency.low.value, Urgency.normal.value) and person_id:
            objection = await self._why_not_speak_first(key, text)
            if objection is not None:
                return await self._hold(
                    person_id, key, channel, text, urgency, intention_id, objection,
                )
            return await self._enqueue_digest(person_id, channel, text, urgency, intention_id)
        allowed, why = await self.can_contact(person_id, urgency)
        if not allowed and urgency == Urgency.normal.value:
            return await self._enqueue_digest(person_id, channel, text, urgency, intention_id)
        if not allowed:
            await self._record(
                person_id, channel, text, urgency, conversation_key, in_reply_to, intention_id,
                status="suppressed", detail=why,
            )
            return {"status": "suppressed", "reason": why}
        return await self._deliver(
            person_id, channel, text, urgency, conversation_key, in_reply_to, intention_id,
        )

    async def _send_midtask(self, person_id: str, key: str, channel: str, text: str, urgency: str,
                            intention_id: Any) -> dict[str, Any]:
        """A note about work already under way, delivered when it is written.

        The two gates this skips are both questions about *starting* a
        conversation, and a progress note is not one. The digest window asks
        whether several small thoughts should arrive as one paragraph; the
        unanswered check asks whether the agent has already interrupted and is
        owed silence. Neither describes a step reporting on a task it was
        commissioned to do.

        The unanswered check was worse than useless here. Commissioning work is
        itself a turn in the conversation, and the agent's "I'll do it" is a
        solicited reply -- delivered at once, by the branch above -- so its own
        word was last and unanswered for the next `awaiting_reply_h` hours. Every
        progress note that followed was held, and `_hold` records the text and
        drops it rather than deferring it, so the updates went nowhere at all
        while the intention sat in the interface reading as work in progress.

        What stays is everything about *how often* rather than *when*. Quiet
        hours and the daily budget are asked at this moment, so a blocked send
        goes to the digest and is flushed when the window is up rather than
        vanishing. The repeat window still stands, so the same sentence is never
        delivered twice in `repeat_window_h`. And the daily budget is still a
        budget: a step that reports on every one of its four actions spends four
        of the day's six, which is a choice with a price and should stay one.
        """
        inter = self.config.permissions.interruption
        said = await recent_words(self.db, key, within_h=inter.repeat_window_h)
        if normalise(text) in said:
            return await self._hold(
                person_id, key, channel, text, urgency, intention_id,
                "already said on this conversation; the person has it",
            )
        allowed, why = await self.can_contact(person_id, urgency)
        if not allowed:
            return await self._enqueue_digest(person_id, channel, text, urgency, intention_id)
        return await self._deliver(person_id, channel, text, urgency, key, None, intention_id)

    async def _hold(self, person_id: str, key: str, channel: str, text: str, urgency: str,
                    intention_id: Any, reason: str) -> dict[str, Any]:
        """Something the agent chose to say that the transcript should not pretend
        it said.

        Recorded, not delivered, and returned to the caller with the reason rather
        than swallowed. The caller is usually a tool the agent called itself, so
        what it reads back is the only way it learns nobody heard it: a step that
        returns "held" can stop composing the message, where a step that returned
        success would go on composing it next cycle.

        The row is written with a status of its own, `held`, so the surfaces that
        already render `queued` and `suppressed` have something to say about it,
        and so the delivery filter — which is what "has this person heard from
        me" is asked about — does not count a held message as spoken.
        """
        if self.audit is not None:
            await self.audit.append(
                actor="self", category="social",
                summary=f"held an unsolicited message to {person_id}",
                details={"conversation_key": key, "reason": reason},
            )
        await self._record(
            person_id, channel, text, urgency, key, None, intention_id,
            status="held", detail=reason,
        )
        return {"status": "held", "reason": reason}

    async def _enqueue_digest(self, person_id: str, channel: str, text: str, urgency: str,
                              intention_id: Any) -> dict[str, Any]:
        entry = self.digests.setdefault(
            person_id,
            {"channel": channel, "key": key_for(channel, person_id), "texts": [],
             "queued_at": time.monotonic(), "urgency": urgency},
        )
        # Once, not once per call. Two cycles reaching the same conclusion inside
        # one window is one thing to tell somebody, and the flush joins these with
        # nothing between them — so without this the delivery is the same sentence
        # twice, which reads as the agent repeating itself and is.
        if normalise(text) in {normalise(written) for written in entry["texts"]}:
            return {"status": "duplicate", "digest_size": len(entry["texts"])}
        entry["texts"].append(text)
        entry["urgency"] = max(entry["urgency"], urgency, key=lambda u: Urgency(u).weight())
        await self._record(
            person_id, channel, text, urgency, entry["key"], None, intention_id,
            status="queued", detail="digest batching window",
        )
        return {"status": "queued", "digest_size": len(entry["texts"])}

    async def flush_due_digests(self) -> int:
        sent = 0
        now = time.monotonic()
        for person_id, entry in list(self.digests.items()):
            if now - entry["queued_at"] < self.window_s and len(entry["texts"]) < 4:
                continue
            # The same gate as at enqueue, asked again at the moment of delivery,
            # because the two can disagree: a note queued while the person had the
            # last word waits out its window, and by then the agent may have
            # answered something they wrote — which puts its own word last, with
            # the batched note now sitting behind an unanswered reply. So the entry
            # stays queued rather than being delivered on top of it, and goes out
            # when the person comes back or the window is long since up.
            if await self._held_back(entry["key"]):
                continue
            texts = entry["texts"]
            del self.digests[person_id]
            if not texts:
                continue
            if len(texts) == 1:
                merged = texts[0]
            else:
                # Several things the agent chose to say, batched into one
                # window's delivery. They go out as they were written, one
                # after another, with no preamble: the framing sentence this
                # used to prepend — "Updates since my last message:" — was
                # composed here rather than deliberated, and it described a
                # bundle the agent had not decided to present as a bundle.
                merged = "\n\n".join(texts)
            allowed, why = await self.can_contact(person_id, entry["urgency"])
            if not allowed:
                await self._record(person_id, entry["channel"], merged, entry["urgency"],
                                   entry["key"], None, None, status="suppressed", detail=why)
                continue
            await self._deliver(
                person_id, entry["channel"], merged, entry["urgency"], entry["key"], None, None,
            )
            sent += 1
        return sent

    async def _deliver(self, person_id: str | None, channel: str, text: str, urgency: str,
                       conversation_key: str | None, in_reply_to: Any, intention_id: Any) -> dict[str, Any]:
        detail = None
        status = "sent"
        if self.sender is not None:
            try:
                result = await self.sender(
                    person_id=person_id, channel=channel, text=text, urgency=urgency,
                    conversation_key=conversation_key, in_reply_to=in_reply_to,
                )
                detail = result if isinstance(result, dict) else {"note": str(result)}
                status = detail.get("status", "sent")
            except Exception as exc:
                logger.exception("etiquette.delivery_failed")
                status, detail = "failed", {"error": str(exc)}
        record = await self._record(
            person_id, channel, text, urgency, conversation_key, in_reply_to, intention_id,
            status=status, detail=detail,
        )
        if self.relationships is not None and person_id:
            await self.relationships.note_contact(person_id, direction="outbound")
        return {"status": status, "message_id": record, "detail": detail}

    async def _record(self, person_id: str | None, channel: str, text: str, urgency: str,
                      conversation_key: str | None, in_reply_to: Any, intention_id: Any,
                      status: str, detail: Any = None) -> str:
        message_id = uuid4()
        delivery = {"status": status, "urgency": urgency}
        if detail:
            delivery["detail"] = detail
        # One timestamp, written to the row and published with the event, rather
        # than a database `now()` and a separate clock read that disagree by the
        # time it takes to get there. A surface decides whether a message answers
        # the question it is holding by comparing timestamps, and a reply that
        # arrives as `0` because its event carried no time can never match — the
        # answer exists and is still read as silence.
        ts = datetime.now(UTC)
        if self.db is not None:
            try:
                await self.db.execute(
                    "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
                    "text, attachments, in_reply_to, intention_id, delivery) "
                    "VALUES ($1, $2, 'outbound', $3, $4, $5, $6, '[]'::jsonb, $7, $8, $9::jsonb)",
                    message_id, ts, channel, conversation_key or key_for(channel, person_id),
                    person_id, text, in_reply_to, intention_id, json.dumps(delivery),
                )
            except Exception:
                logger.exception("etiquette.record_failed")
        if self.bus is not None:
            try:
                await self.bus.publish(EventKind.MESSAGE_NEW, {
                    "id": str(message_id), "ts": ts.isoformat(), "direction": "outbound",
                    "channel": channel, "person_id": person_id, "text": text[:2000],
                    "urgency": urgency, "delivery": delivery,
                })
            except Exception:
                logger.exception("etiquette.publish_failed")
        return str(message_id)
