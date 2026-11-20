"""Live mid-turn steering: the person's word reaches the step in progress.

A GSL run can hold the loop for minutes — model calls, tool dispatches, up to
six actions a step. A message typed into that project's conversation while the
run is under way used to wait at the head of the inbound queue for the cycle to
end, which is the one moment the answer it needed was least able to use it: the
run finished whatever it was already doing, sometimes for hours, before the
guidance arrived.

Steering fixes the plumbing rather than the pacing. A message bound to the
active project's conversation, arriving while that project's solver is under
way, becomes a row in `steering_messages` (published on the bus as
`steering.inject`, an out-of-band lane). `GeneralSolverLoop.prepare_next_step`
— invoked before every tool dispatch and at every step transition — claims the
pending rows atomically and injects them into the step context at high
priority, so the model reads them before it chooses its next action. The
adjustment is acknowledged inline: the step's own reply, not a separate turn.

The queue is claim-based, never fire-and-forget: a claimed row is marked
`claimed` with the step that took it, and only `mark_applied` after the step's
context actually carries it. A run that dies between claim and apply leaves the
row claimed by a dead step, and `expire_stale` hands those back.
"""

from __future__ import annotations

from typing import Any

from clio.db import Database
from clio.observability.logger import get_logger

logger = get_logger("clio.gsl.steering")

#: Rows claimed longer than this by a step that never applied them are stale.
STALE_CLAIM_S = 300


class SteeringQueue:
    """The mid-turn guidance channel, backed by `steering_messages`."""

    def __init__(self, db: Database, bus: Any = None):
        self.db = db
        self.bus = bus

    # ---- enqueue -----------------------------------------------------------------

    async def enqueue(
        self,
        project_id: str,
        conversation_key: str,
        person_id: str,
        text: str,
        *,
        intention_id: str | None = None,
        thread_id: str | None = None,
        priority: str = "normal",
    ) -> str:
        """Record one steering message and announce it out-of-band."""
        row = await self.db.fetchrow(
            "INSERT INTO steering_messages (project_id, intention_id, thread_id, conversation_key, "
            "person_id, text, priority) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7) RETURNING id",
            project_id, intention_id, thread_id, conversation_key, person_id, text, priority,
        )
        steering_id = str(row["id"])
        if self.bus is not None:
            from clio.bus.event_bus import LANE_STEERING

            await self.bus.publish(
                "steering.inject",
                {
                    "steering_id": steering_id,
                    "project_id": str(project_id),
                    "intention_id": str(intention_id) if intention_id else None,
                    "conversation_key": conversation_key,
                    "person_id": person_id,
                    "text": text,
                    "priority": priority,
                },
                lane=LANE_STEERING,
            )
        logger.info("steering.enqueued", steering_id=steering_id, project_id=str(project_id))
        return steering_id

    # ---- claim / apply -------------------------------------------------------------

    async def claim(
        self,
        project_id: str,
        *,
        intention_id: str | None = None,
        step_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Claim pending messages atomically: nobody else gets the same row.

        The claim is one statement — subselect with `FOR UPDATE SKIP LOCKED`
        inside the same transaction as the update — so two workers stepping the
        same project in parallel never both take one message, and a claim
        survives a concurrent enqueue.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    "UPDATE steering_messages SET status = 'claimed', claimed_by_step = $4, "
                    "claimed_at = now() WHERE id IN ("
                    "  SELECT id FROM steering_messages WHERE project_id = $1::uuid "
                    "  AND status = 'pending' "
                    "  AND ($2::uuid IS NULL OR intention_id = $2::uuid)"
                    "  ORDER BY (priority = 'high') DESC, created_at ASC LIMIT $3"
                    "  FOR UPDATE SKIP LOCKED"
                    ") RETURNING id, conversation_key, person_id, text, priority, intention_id, created_at",
                    project_id, intention_id, limit, step_id,
                )
        # `RETURNING` has no guaranteed order, and the caller reads the claimed
        # list from the top, so the order the subselect asked for is applied
        # here: high priority first, then oldest first.
        claimed = sorted(
            (dict(r) for r in rows),
            key=lambda r: (str(r.get("priority") or "normal") != "high", r.get("created_at")),
        )
        if claimed:
            logger.info("steering.claimed", count=len(claimed), project_id=str(project_id))
        return claimed

    async def mark_applied(self, steering_ids: list[str]) -> None:
        if not steering_ids:
            return
        await self.db.execute(
            "UPDATE steering_messages SET status = 'applied', acknowledged_at = now() "
            "WHERE id = ANY($1::uuid[])",
            steering_ids,
        )

    async def expire_stale(self, *, stale_s: int = STALE_CLAIM_S) -> int:
        """Return dead claims to the pending queue."""
        row = await self.db.fetchrow(
            "UPDATE steering_messages SET status = 'pending', claimed_by_step = NULL, "
            "claimed_at = NULL WHERE status = 'claimed' "
            "AND claimed_at < now() - make_interval(secs => $1) RETURNING id",
            stale_s,
        )
        return 1 if row is not None else 0

    async def pending_count(self, project_id: str) -> int:
        row = await self.db.fetchrow(
            "SELECT COUNT(*) AS n FROM steering_messages WHERE project_id = $1::uuid AND status = 'pending'",
            project_id,
        )
        return int(row["n"]) if row else 0

    # ---- from the inbound queue -------------------------------------------------------

    async def drain_conversation_inbound(
        self,
        social: Any,
        project_id: str,
        conversation_keys: set[str],
        *,
        intention_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Convert pending inbound messages of one project's conversations.

        Only the bound conversations steer: a message from a conversation that
        is not bound to the active project is somebody else's thread, and it
        stays a message. Each converted message is marked handled as steering —
        it is not ignored and it is not deliberated; it is injected at the next
        step boundary and acknowledged there.
        """
        if not conversation_keys or not hasattr(social, "pending_inbound"):
            return []
        pending = await social.pending_inbound(limit=limit * 3)
        drained: list[dict[str, Any]] = []
        for message in pending:
            key = str(getattr(message, "conversation_key", "") or "")
            if key not in conversation_keys:
                continue
            steering_id = await self.enqueue(
                project_id,
                key,
                str(getattr(message, "person_id", "") or ""),
                str(getattr(message, "text", "") or ""),
                intention_id=intention_id,
                thread_id=str(getattr(message, "id", "") or "") or None,
                priority="high",
            )
            if hasattr(social, "mark_handled"):
                await social.mark_handled(message, {"action": "steering", "steering_id": steering_id})
            drained.append({"steering_id": steering_id, "text": str(getattr(message, "text", "") or ""),
                            "conversation_key": key, "person_id": str(getattr(message, "person_id", "") or "")})
        return drained

    # ---- rendering ----------------------------------------------------------------------

    @staticmethod
    def render_for_step(claimed: list[dict[str, Any]]) -> str:
        """The step-context block for claimed steering, high priority by position."""
        if not claimed:
            return ""
        lines = ["## Steering — from the person, while this run was under way",
                 "",
                 "These messages arrived mid-run. They are guidance for the work in",
                 "progress: read them before choosing the next action, adjust the plan",
                 "if they change what the person wants, and acknowledge the adjustment",
                 "in this step's reply. They outrank the plan's own next step where the",
                 "two disagree, and they do not suspend the run.",
                 ""]
        for item in claimed:
            priority = str(item.get("priority") or "normal")
            marker = "**(high priority)** " if priority == "high" else ""
            person = str(item.get("person_id") or "the person")
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(f"- {marker}{person}: {text}")
        return "\n".join(lines)


class SteeringBatch:
    """What one step boundary claimed, and what it did with it."""

    def __init__(self, claimed: list[dict[str, Any]], rendered: str) -> None:
        self.claimed = claimed
        self.rendered = rendered

    @property
    def ids(self) -> list[str]:
        return [str(item["id"]) for item in self.claimed]

    def __bool__(self) -> bool:
        return bool(self.claimed)


def steering_from_event(event: Any) -> dict[str, Any] | None:
    """The steering payload of a `steering.inject` event, if it is one."""
    if str(event.kind) != "steering.inject":
        return None
    payload = dict(event.payload or {})
    if not payload.get("project_id") or not payload.get("text"):
        return None
    return payload
