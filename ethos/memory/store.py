from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ethos.bus import EventBus
from ethos.config import EthosConfig
from ethos.db import Database, decode_env_attrs, decode_json_value
from ethos.gateway.embeddings import EmbeddingProvider
from ethos.memory.retrieval import HybridRetriever, parse_pgvector, to_pgvector
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind
from ethos.schemas.gsl import (
    AttemptRecord,
    Bookmark,
    DecisionRecord,
    IntentionRecord,
    IntentionStatus,
    StageNotice,
)
from ethos.schemas.memory import MemoryHit

logger = get_logger("ethos.memory")


# Statuses that count as "still open work" everywhere this store looks.
_OPEN_INTENTION_STATUSES = (
    IntentionStatus.proposed.value, IntentionStatus.active.value,
    IntentionStatus.in_progress.value, IntentionStatus.waiting.value,
    IntentionStatus.awaiting_review.value,
)

# How far along a goal each open status means, lowest first. Used only to pick
# which of several rows for the same work to keep (`find_open_intention`), where
# the wrong pick throws away a plan or a bookmark and the right one costs nothing.
_INTENTION_PROGRESS = {
    IntentionStatus.proposed.value: 0,
    IntentionStatus.active.value: 1,
    IntentionStatus.waiting.value: 2,
    IntentionStatus.awaiting_review.value: 2,
    IntentionStatus.in_progress.value: 3,
}


def _env_entity_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "kind": row["kind"], "key": row["key"],
        "attrs": decode_env_attrs(row), "owner": row["owner"],
        "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
        "health": row["health"],
    }


def _new_id() -> UUID:
    return uuid4()


class MemoryStore:
    """Durable memory: episodes, facts, knowhow, questions, intentions,
    decisions and attempts. Postgres is the single source of truth [D-10]."""

    def __init__(
        self,
        db: Database,
        embedder: EmbeddingProvider,
        config: EthosConfig,
        bus: EventBus | None = None,
    ):
        self.db = db
        self.embedder = embedder
        self.config = config
        self.bus = bus
        self.retriever = HybridRetriever(db, embedder, config.memory)

    # ------------------------------------------------------------ episodes

    async def record_episode(
        self,
        kind: str,
        summary: str,
        body: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
        intention_id: UUID | None = None,
        importance: float | None = None,
        half_life_h: float | None = None,
        publish: bool = False,
    ) -> UUID:
        importance = self.config.memory.episode.default_importance if importance is None else importance
        half_life_h = self.config.memory.episode.default_half_life_h if half_life_h is None else half_life_h
        vec = self.embedder.embed([summary])[0]
        ep_id = _new_id()
        await self.db.execute(
            "INSERT INTO episodes (id, ts, thread_id, kind, summary, body, intention_id, importance, "
            "strength, half_life_h, last_access, access_count, status, embedding) "
            "VALUES ($1, now(), $2, $3, $4, $5::jsonb, $6, $7, 0.5, $8, now(), 0, 'active', $9::vector)",
            ep_id, thread_id, kind, summary, json.dumps(body or {}), intention_id,
            float(importance), float(half_life_h), to_pgvector(vec),
        )
        if publish and self.bus is not None:
            await self.bus.publish(
                EventKind.THOUGHT_NEW if kind == "thought" else "episode.new",
                {"episode_id": str(ep_id), "kind": kind, "summary": summary, "thread_id": thread_id},
            )
        return ep_id

    async def get_episode(self, episode_id: UUID) -> dict[str, Any] | None:
        row = await self.db.fetchrow("SELECT * FROM episodes WHERE id = $1", episode_id)
        if row is None:
            return None
        return dict(row)

    async def search(self, query: str, k: int | None = None, kinds: set[str] | None = None) -> list[MemoryHit]:
        hits = await self.retriever.retrieve(query, k=k, kinds=kinds)
        await self.access(hits)
        return hits

    async def access(self, hits: Sequence[MemoryHit]) -> None:
        """Spacing effect: access boosts strength and extends half-life [D-18]."""
        boost = self.config.memory.spacing.strength_boost
        cap = self.config.memory.spacing.strength_cap
        mult = self.config.memory.spacing.half_life_multiplier
        for hit in hits:
            if hit.kind != "episode" or hit.extra.get("source") != "episode":
                continue
            await self.db.execute(
                "UPDATE episodes SET strength = LEAST($1, strength + $2), "
                "half_life_h = half_life_h * $3, last_access = now(), access_count = access_count + 1 "
                "WHERE id = $4",
                cap, boost, mult, UUID(hit.id),
            )

    async def random_memories(self, n: int) -> list[MemoryHit]:
        rows = await self.db.fetch(
            "SELECT id::text AS id, kind, summary, body::text AS body, ts, importance, strength, "
            "NULL::text AS emb, 'episode' AS source FROM episodes "
            "WHERE status = 'active' ORDER BY random() LIMIT $1", n,
        )
        return [HybridRetriever._row_to_hit(r) for r in rows]

    async def episodes_since(self, since: datetime, limit: int = 400) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT id, ts, kind, summary, body::text AS body, importance, strength, "
            "embedding::text AS emb FROM episodes WHERE ts >= $1 AND status = 'active' "
            "ORDER BY ts ASC LIMIT $2", since, limit,
        )
        return [
            {
                "id": r["id"], "ts": r["ts"], "kind": r["kind"], "summary": r["summary"],
                "body": json.loads(r["body"]) if r["body"] else {},
                "importance": float(r["importance"] or 0), "strength": float(r["strength"] or 0),
                "embedding": parse_pgvector(r["emb"]),
            }
            for r in rows
        ]

    # --------------------------------------------------------------- facts

    async def record_fact(
        self,
        subject: str,
        statement: str,
        confidence: float,
        provenance: dict[str, Any],
        field: str | None = None,
        supersede_similar: bool = True,
    ) -> UUID:
        vec = self.embedder.embed([f"{subject}: {statement}"])[0]
        fact_id = _new_id()
        similar: Any = None
        if supersede_similar:
            similar = await self.db.fetch(
                "SELECT id FROM facts WHERE subject = $1 AND superseded_by IS NULL AND valid_to IS NULL",
                subject,
            )
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO facts (id, subject, statement, confidence, provenance, field, "
                    "valid_from, strength, embedding, updated_at) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), 0.5, $7::vector, now())",
                    fact_id, subject, statement, float(confidence),
                    json.dumps(provenance), field, to_pgvector(vec),
                )
                if similar:
                    for row in similar:
                        await conn.execute(
                            "UPDATE facts SET superseded_by = $1, valid_to = now(), updated_at = now() "
                            "WHERE id = $2",
                            fact_id, row["id"],
                        )
        return fact_id

    async def facts_about(self, subject: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT id::text, subject, statement, confidence, provenance::text, field, "
            "valid_from, valid_to, superseded_by, strength FROM facts "
            "WHERE subject = $1 AND superseded_by IS NULL ORDER BY updated_at DESC LIMIT $2",
            subject, limit,
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ knowhow

    async def record_knowhow(
        self, situation: str, advice: str, evidence: dict[str, Any],
    ) -> UUID:
        kh_id = _new_id()
        vec = self.embedder.embed([f"{situation} -> {advice}"])[0]
        existing = await self.db.fetchrow(
            "SELECT id, uses, successes FROM knowhow WHERE situation = $1 AND advice = $2 AND retired = false",
            situation, advice,
        )
        if existing:
            await self.db.execute(
                "UPDATE knowhow SET evidence = $1::jsonb, uses = uses, embedding = $2::vector WHERE id = $3",
                json.dumps(evidence), to_pgvector(vec), existing["id"],
            )
            return existing["id"]
        await self.db.execute(
            "INSERT INTO knowhow (id, situation, advice, evidence, uses, successes, embedding, retired) "
            "VALUES ($1, $2, $3, $4::jsonb, 0, 0, $5::vector, false)",
            kh_id, situation, advice, json.dumps(evidence), to_pgvector(vec),
        )
        return kh_id

    async def find_knowhow(self, situation: str, k: int = 5) -> list[MemoryHit]:
        return await self.retriever.retrieve(situation, k=k, kinds={"knowhow"})

    async def bump_knowhow(self, knowhow_id: UUID, success: bool) -> None:
        await self.db.execute(
            "UPDATE knowhow SET uses = uses + 1, successes = successes + $1 WHERE id = $2",
            1 if success else 0, knowhow_id,
        )

    # ---------------------------------------------------------- questions

    async def open_question(
        self, text: str, priority: float = 0.5, origin_episode: UUID | None = None,
    ) -> UUID:
        q_id = _new_id()
        vec = self.embedder.embed([text])[0]
        await self.db.execute(
            "INSERT INTO questions (id, text, origin_episode, priority, status, embedding) "
            "VALUES ($1, $2, $3, $4, 'open', $5::vector)",
            q_id, text, origin_episode, float(priority), to_pgvector(vec),
        )
        return q_id

    async def close_question(self, question_id: UUID, status: str = "answered") -> None:
        await self.db.execute(
            "UPDATE questions SET status = $1 WHERE id = $2", status, question_id,
        )

    async def open_questions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT id::text, text, priority FROM questions WHERE status = 'open' "
            "ORDER BY priority DESC, RANDOM() LIMIT $1", limit,
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------- decay / demotion

    async def decay_pass(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        rows = await self.db.fetch(
            "SELECT id FROM episodes WHERE status = 'active'"
        )
        sigma_floor = self.config.memory.decay.demote_strength
        iota_floor = self.config.memory.decay.demote_importance
        await self.db.execute(
            "UPDATE episodes SET strength = strength * (0.5 ^ (EXTRACT(EPOCH FROM (now() - last_access)) / 3600.0 / NULLIF(half_life_h, 0))) "
            "WHERE status = 'active' AND half_life_h > 0"
        )
        refreshed = len(rows)
        if self.config.memory.decay.commitments_exempt:
            demoted_rows = await self.db.fetch(
                "UPDATE episodes SET status = 'dormant' WHERE id IN ( "
                "  SELECT e.id FROM episodes e WHERE e.status = 'active' AND e.strength < $1 "
                "  AND e.importance < $2 "
                "  AND (e.intention_id IS NULL OR e.intention_id NOT IN ( "
                "    SELECT id FROM intentions WHERE status IN ('proposed','active','in_progress','waiting','awaiting_review') "
                "  )) "
                ") RETURNING id",
                sigma_floor, iota_floor,
            )
        else:
            demoted_rows = await self.db.fetch(
                "UPDATE episodes SET status = 'dormant' WHERE status = 'active' "
                "AND strength < $1 AND importance < $2 RETURNING id",
                sigma_floor, iota_floor,
            )
        demoted = len(demoted_rows)
        return {"refreshed": refreshed, "demoted": demoted}

    async def archive_pass(self, now: datetime | None = None) -> int:
        """Archive old episodes to monthly Parquet. Rows are never deleted [D-19]."""
        now = now or datetime.now(UTC)
        import pyarrow as pa
        import pyarrow.parquet as pq

        cutoff = now.timestamp() - self.config.memory.decay.archive_after_days * 86400
        rows = await self.db.fetch(
            "SELECT id::text, ts, kind, summary, body::text AS body, importance, strength, status "
            "FROM episodes WHERE status IN ('active','dormant') AND ts < to_timestamp($1) LIMIT 5000",
            cutoff,
        )
        if not rows:
            return 0
        archive_dir = self.config.paths.data_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        month_key = rows[0]["ts"].strftime("%Y-%m")
        table = pa.table({
            "id": [r["id"] for r in rows],
            "ts": [r["ts"].isoformat() for r in rows],
            "kind": [r["kind"] for r in rows],
            "summary": [r["summary"] for r in rows],
            "body": [r["body"] for r in rows],
            "importance": [float(r["importance"] or 0) for r in rows],
            "strength": [float(r["strength"] or 0) for r in rows],
            "status": [r["status"] for r in rows],
        })
        path = archive_dir / f"episodes-{month_key}.parquet"
        pq.write_table(table, path)
        ids = [UUID(r["id"]) for r in rows]
        await self.db.execute(
            "UPDATE episodes SET status = 'archived' WHERE id = ANY($1::uuid[])", ids,
        )
        return len(ids)

    # -------------------------------------------------------- intentions

    async def create_intention(
        self,
        title: str,
        desired_end_state: str = "",
        *,
        kind: str = "task",
        parent_id: UUID | None = None,
        success_criteria: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
        origin: str = "self",
        commissioned_by: str | None = None,
        priority: float = 0.5,
        deadline: datetime | None = None,
        budget_usd: float | None = None,
        hypothesis: dict[str, Any] | None = None,
    ) -> UUID:
        intent_id = _new_id()
        await self.db.execute(
            "INSERT INTO intentions (id, parent_id, kind, title, desired_end_state, success_criteria, "
            "constraints, origin, commissioned_by, status, priority, deadline, budget_usd, spent_usd, "
            "created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, 'proposed', $10, $11, $12, 0, now(), now())",
            intent_id, parent_id, kind, title, desired_end_state,
            json.dumps(success_criteria or []), json.dumps(constraints or {}),
            origin, commissioned_by, float(priority), deadline, budget_usd,
        )
        if self.bus is not None:
            await self.bus.publish(EventKind.INTENTION_UPDATE, {
                "intention_id": str(intent_id), "status": "proposed", "title": title,
                "kind": kind, "commissioned_by": commissioned_by, "priority": priority,
                "deadline": deadline.isoformat() if deadline else None,
                "budget_usd": budget_usd, "parent_id": str(parent_id) if parent_id else None,
            })
        return intent_id

    async def get_intention(self, intention_id: UUID) -> IntentionRecord | None:
        row = await self.db.fetchrow("SELECT * FROM intentions WHERE id = $1", intention_id)
        if row is None:
            return None
        return self._row_to_intention(row)

    @staticmethod
    def _row_to_intention(row: Any) -> IntentionRecord:
        criteria = decode_json_value(row["success_criteria"])
        constraints = decode_json_value(row["constraints"])
        resume = decode_json_value(row["resume_state"])
        hypothesis = decode_json_value(row["hypothesis"])
        return IntentionRecord(
            id=row["id"], parent_id=row["parent_id"], kind=row["kind"], title=row["title"],
            desired_end_state=row["desired_end_state"] or "", success_criteria=list(criteria or []),
            constraints=dict(constraints or {}), origin=row["origin"] or "self",
            commissioned_by=row["commissioned_by"], status=row["status"],
            priority=float(row["priority"] or 0.5), deadline=row["deadline"],
            budget_usd=float(row["budget_usd"]) if row["budget_usd"] is not None else None,
            spent_usd=float(row["spent_usd"] or 0),
            resume_state=dict(resume or {}), hypothesis=hypothesis,
            created_at=row["created_at"], updated_at=row["updated_at"],
            closed_reason=row["closed_reason"],
        )

    async def list_intentions(
        self,
        statuses: list[str] | None = None,
        parent_id: UUID | None = None,
        commissioned_only: bool = False,
        limit: int = 200,
    ) -> list[IntentionRecord]:
        open_statuses = statuses or list(_OPEN_INTENTION_STATUSES)
        rows = await self.db.fetch(
            "SELECT * FROM intentions WHERE status = ANY($1::text[]) "
            "AND ($2::uuid IS NULL OR parent_id = $2) "
            "AND (NOT $3 OR commissioned_by IS NOT NULL) "
            "ORDER BY priority DESC, created_at ASC LIMIT $4",
            open_statuses, parent_id, commissioned_only, limit,
        )
        return [self._row_to_intention(r) for r in rows]

    async def find_open_intention(
        self,
        title: str,
        commissioned_by: str | None = None,
    ) -> IntentionRecord | None:
        """The open intention that already *is* this piece of work, if there is one.

        A person asks for the same thing twice because the first time did not
        visibly happen, and the right answer to the second request is to carry on
        with the first — not to open a second row for it. The two are the same
        promise, and an agent holding two rows for one promise has made two.

        The cost is not a cluttered table. Progress through a goal lives in
        `resume_state`, so a fresh intention starts again at `understand` however
        far the old one got, and the agent can promise the same work on every pass
        and get no closer to the step that does it. That is what was observed: one
        request to run a sub-agent, three intentions titled identically, no plan
        and no tool call, because each reply the agent volunteered reset the work
        it had just said it was doing.

        Matching is on the title, casefolded and whitespace-collapsed, and only
        among open work for the same commissioner — deliberately literal. Two
        different pieces of work do not share a title by accident, and a fuzzy
        match here would merge genuinely distinct commitments, which is a worse
        failure than the duplicate this prevents. Closed intentions are excluded
        for the same reason: work that is `done` or `parked` was concluded, and
        being asked for again is new work, not a resurrection.

        The furthest-along match wins, so adopting one never throws away progress.
        """
        wanted = " ".join(str(title or "").casefold().split())
        if not wanted:
            return None
        rows = await self.db.fetch(
            "SELECT * FROM intentions WHERE status = ANY($1::text[]) "
            "AND ($2::text IS NULL OR commissioned_by = $2::text) "
            "ORDER BY updated_at DESC",
            list(_OPEN_INTENTION_STATUSES), commissioned_by,
        )
        matches = [
            self._row_to_intention(r) for r in rows
            if " ".join(str(r["title"] or "").casefold().split()) == wanted
        ]
        if not matches:
            return None
        return max(matches, key=lambda i: (
            _INTENTION_PROGRESS.get(i.status, 99), i.updated_at.timestamp(),
        ))

    async def list_attentionable_intentions(self, limit: int = 50) -> list[IntentionRecord]:
        """The open work the drives are allowed to choose between.

        `list_intentions` answers a different question: the top N by priority,
        which is the right shape for a panel or a report. It is the wrong shape
        for deciding what to *work on*, because a deadline is not a matter of
        priority and a limit can hide one completely.

        That is not hypothetical. With 180 open intentions, the 50 highest by
        priority were all seeded filler, and a commissioned task with a deadline
        eleven minutes away sat at rank 168 — so it was never in the set the
        drive layer could see. No resolver, deadline bypass or urgency score
        helps an intention the selector is not shown. The agent weighed the work
        it could see, found the filler perfectly adequate, and spent its turns
        there, which is what "I will do it" followed by nothing looks like from
        the outside.

        So a deadline is never subject to the priority cut. Everything open with
        a live deadline comes first, soonest first, and the rest of the budget is
        filled by priority. An intention can now only be starved of attention by
        having no deadline, which is a statement about it rather than about the
        ranking.
        """
        open_statuses = list(_OPEN_INTENTION_STATUSES)
        rows = await self.db.fetch(
            "SELECT DISTINCT ON (id) * FROM intentions "
            "WHERE status = ANY($1::text[]) "
            # A dated intention outranks an undated one at equal priority, so a
            # real commitment is never displaced by filler that merely scored
            # high on a field it never set.
            "ORDER BY id, deadline ASC NULLS LAST, priority DESC, created_at ASC",
            open_statuses,
        )
        dated = [r for r in rows if r["deadline"] is not None]
        undated = [r for r in rows if r["deadline"] is None]
        undated.sort(key=lambda r: (-float(r["priority"] or 0.5), r["created_at"]))
        chosen = dated + undated[: max(0, limit - len(dated))]
        return [self._row_to_intention(r) for r in chosen]

    # ------------------------------------------------------- stage notices

    async def record_stage_notice(
        self, intention: IntentionRecord, stage: str, detail: str = "",
    ) -> StageNotice | None:
        """Record that commissioned work reached a stage somebody is owed an answer on.

        The channel and conversation are resolved here, from the last thing that
        person actually said, rather than stored on the intention and looked up
        later. Two reasons, and they are different. The first is durability: a row
        that carries its own address stays correct across a restart and does not
        depend on which conversation that person has used since. The second is that
        the person who commissioned the work and the conversation they commissioned
        it on are the same by construction -- the intention is created out of one
        inbound message -- so the newest inbound row from them is the address the
        answer belongs at.

        `None` for work nobody asked for. There is nobody to owe a word to, and a
        notice with no address would be a debt the loop keeps trying to pay into a
        void.

        Idempotent on `(intention_id, stage)`: the solver loop reaches the same
        terminal state on more than one path, and a stage that arrives once must
        not be announced once per path. The unique constraint enforces that, and
        the conflict is swallowed here so a re-entry reads as the row it already
        has rather than as a failure.
        """
        if intention.commissioned_by is None:
            return None
        person_id = str(intention.commissioned_by)
        channel: str | None = None
        conversation_key: str | None = None
        try:
            row = await self.db.fetchrow(
                "SELECT channel, conversation_key FROM messages "
                "WHERE direction = 'inbound' AND person_id = $1 "
                "ORDER BY ts DESC LIMIT 1",
                person_id,
            )
        except Exception:
            logger.warning("memory.stage_notice_address_failed", person=person_id)
            row = None
        if row is not None:
            channel = row["channel"]
            conversation_key = row["conversation_key"] or None
        notice = StageNotice(
            intention_id=intention.id, stage=stage, title=intention.title or "",
            detail=detail[:600], person_id=person_id, channel=channel,
            conversation_key=conversation_key,
        )
        try:
            await self.db.execute(
                "INSERT INTO stage_notices (id, intention_id, stage, title, detail, "
                "person_id, channel, conversation_key) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "ON CONFLICT (intention_id, stage) DO NOTHING",
                notice.id, notice.intention_id, notice.stage, notice.title,
                notice.detail, notice.person_id, notice.channel, notice.conversation_key,
            )
        except Exception:
            logger.warning("memory.stage_notice_write_failed", intention=str(intention.id))
            return None
        existing = await self.get_stage_notice(intention.id, stage)
        if existing is None:
            logger.warning(
                "memory.stage_notice_missing", intention=str(intention.id), stage=stage,
            )
        return existing or notice

    async def get_stage_notice(self, intention_id: UUID, stage: str) -> StageNotice | None:
        row = await self.db.fetchrow(
            "SELECT * FROM stage_notices WHERE intention_id = $1 AND stage = $2",
            intention_id, stage,
        )
        return self._row_to_stage_notice(row) if row is not None else None

    async def list_stage_notices(
        self, *, only_owed: bool = False, since: datetime | None = None, limit: int = 20,
    ) -> list[StageNotice]:
        """Stage notices, newest first.

        Three callers with three questions, and the parameters are the three
        questions rather than three methods: what is still owed, what finished
        recently enough to be worth quoting back to somebody asking about it, and
        nothing in particular.
        """
        rows = await self.db.fetch(
            "SELECT * FROM stage_notices "
            "WHERE (NOT $1 OR delivered_at IS NULL) "
            "AND ($2::timestamptz IS NULL OR created_at >= $2) "
            "ORDER BY created_at DESC LIMIT $3",
            only_owed, since, int(limit),
        )
        return [self._row_to_stage_notice(r) for r in rows]

    async def due_stage_notices(self, limit: int = 1) -> list[StageNotice]:
        """The debts that are owed *and* due to be tried again.

        `next_attempt_at` is what keeps a notice nobody can deliver -- a dead
        channel, a withdrawn address -- from being retried at the tick rate. It is
        null until the first failure, so a fresh notice is due immediately, and a
        notice whose wait has not elapsed is not returned at all rather than
        returned and skipped.
        """
        rows = await self.db.fetch(
            "SELECT * FROM stage_notices "
            "WHERE delivered_at IS NULL "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
            "ORDER BY created_at ASC LIMIT $1",
            int(limit),
        )
        return [self._row_to_stage_notice(r) for r in rows]

    async def mark_stage_notice(
        self, notice_id: UUID, *, delivered: bool,
        delivery: dict[str, Any] | None = None,
        next_attempt_at: datetime | None = None,
    ) -> None:
        """Settle a notice one way or the other, and record which.

        `delivered=False` does not discard the row. It counts the attempt, keeps
        the reason, and sets the next time to try -- a debt that survives a failed
        send is the whole point of keeping it in a table rather than in a log
        line. What stops the retrying is the caller, which passes
        `delivered=True` with the exhaustion recorded in `delivery`, so "stopped
        asking" is a decision with a record rather than a row quietly aging.
        """
        if delivered:
            await self.db.execute(
                "UPDATE stage_notices SET delivered_at = now(), delivery = $2::jsonb, "
                "attempts = attempts + 1, next_attempt_at = NULL WHERE id = $1",
                notice_id, json.dumps(delivery or {}),
            )
            return
        await self.db.execute(
            "UPDATE stage_notices SET attempts = attempts + 1, delivery = $2::jsonb, "
            "next_attempt_at = $3 WHERE id = $1",
            notice_id, json.dumps(delivery or {}), next_attempt_at,
        )

    @staticmethod
    def _row_to_stage_notice(row: Any) -> StageNotice:
        return StageNotice(
            id=row["id"], intention_id=row["intention_id"], stage=row["stage"],
            title=row["title"] or "", detail=row["detail"] or "",
            person_id=row["person_id"], channel=row["channel"],
            conversation_key=row["conversation_key"], created_at=row["created_at"],
            delivered_at=row["delivered_at"],
            delivery=decode_json_value(row["delivery"]) or {},
            attempts=int(row["attempts"] or 0), next_attempt_at=row["next_attempt_at"],
        )

    async def update_intention(self, intention_id: UUID, **fields: Any) -> None:
        allowed = {
            "status", "priority", "deadline", "budget_usd", "spent_usd", "resume_state",
            "hypothesis", "title", "desired_end_state", "closed_reason", "success_criteria", "constraints",
        }
        sets: list[str] = []
        args: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            idx = len(args) + 1
            if key in ("resume_state", "hypothesis", "success_criteria", "constraints"):
                sets.append(f"{key} = ${idx}::jsonb")
                args.append(json.dumps(value))
            else:
                sets.append(f"{key} = ${idx}")
                args.append(value)
        if not sets:
            return
        args.append(intention_id)
        await self.db.execute(
            f"UPDATE intentions SET {', '.join(sets)}, updated_at = now() WHERE id = ${len(args)}",
            *args,
        )
        if self.bus is not None and "status" in fields:
            await self.bus.publish(EventKind.INTENTION_UPDATE, {
                "intention_id": str(intention_id), "status": fields["status"],
                "title": fields.get("title"), "spent_usd": fields.get("spent_usd"),
            })

    async def add_spend(self, intention_id: UUID, amount_usd: float) -> float:
        row = await self.db.fetchrow(
            "UPDATE intentions SET spent_usd = COALESCE(spent_usd, 0) + $1, updated_at = now() "
            "WHERE id = $2 RETURNING spent_usd", float(amount_usd), intention_id,
        )
        return float(row["spent_usd"]) if row else 0.0

    async def close_intention(self, intention_id: UUID, status: str, reason: str) -> None:
        await self.update_intention(
            intention_id, status=status, closed_reason=reason,
        )

    async def set_bookmark(self, intention_id: UUID, bookmark: Bookmark) -> None:
        """Merge a bookmark into `resume_state`, never replace it.

        `resume_state` is one column holding two writers' state. The solver loop
        keeps the goal's whole working record in it -- the plan, the
        understanding, the per-step attempt counts, the artifacts, the planning
        backoff -- and this method writes eight fields of human-readable summary
        on top. Writing the bookmark as the *whole* value is what it used to do,
        and the two sets of keys are disjoint enough that it did not look like
        data loss: every assertion that a bookmark round-trips still passed.

        What it actually cost is the solver loop's entire state, on every
        interrupt. `Life.bookmark` is called whenever attention interrupts the
        focus and whenever a social turn suspends it, so a task the agent was
        part-way through had its plan replaced by a summary of having a plan.
        The next `advance` read that summary, found no `understanding` and no
        `plan` in it, and started the goal over from phase one. It did not freeze
        and it did not error -- it re-understood, re-planned and re-executed,
        over and over, at full cost, forever making the same first step. The task
        looked alive in every table that counted activity.

        So this merges: the bookmark's own fields are written over whatever is
        there, and every key the solver loop owns is left alone. The merge is in
        SQL rather than in Python because the read-modify-write it would replace
        is the same race that would lose the plan to a concurrent save.

        Empty fields are not written. A `Bookmark` built by `Life.bookmark` sets
        `step_id`, `last_action` and `partial_results` to nothing at all, and a
        blind merge would use those nulls to erase the solver's record of what it
        last did. A bookmark is a partial snapshot; it should fill in what it
        knows and stay quiet about what it does not.
        """
        patch = {
            key: value for key, value in bookmark.model_dump(mode="json").items()
            if value is not None and value != {} and value != []
        }
        if not patch:
            return
        await self.db.execute(
            "UPDATE intentions "
            "SET resume_state = COALESCE(resume_state, '{}'::jsonb) || $1::jsonb, "
            "updated_at = now() "
            "WHERE id = $2",
            json.dumps(patch), intention_id,
        )

    async def record_decision(self, decision: DecisionRecord) -> UUID:
        await self.db.execute(
            "INSERT INTO decisions (id, intention_id, statement, rationale, decided_by, decided_at, superseded_by, reversible) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            decision.id, decision.intention_id, decision.statement, decision.rationale,
            decision.decided_by, decision.decided_at, decision.superseded_by, decision.reversible,
        )
        return decision.id

    async def start_attempt(self, intention_id: UUID, approach: str) -> UUID:
        attempt_id = _new_id()
        await self.db.execute(
            "INSERT INTO attempts (id, intention_id, approach, started_at) VALUES ($1, $2, $3, now())",
            attempt_id, intention_id, approach,
        )
        return attempt_id

    async def end_attempt(
        self, attempt_id: UUID, outcome: str, evaluation: dict[str, Any],
        lessons: list[str], cost_usd: float,
    ) -> None:
        await self.db.execute(
            "UPDATE attempts SET ended_at = now(), outcome = $1, evaluation = $2::jsonb, "
            "lessons = $3, cost_usd = $4 WHERE id = $5",
            outcome, json.dumps(evaluation), lessons, float(cost_usd), attempt_id,
        )

    async def list_attempts(self, intention_id: UUID, limit: int = 20) -> list[AttemptRecord]:
        rows = await self.db.fetch(
            "SELECT * FROM attempts WHERE intention_id = $1 ORDER BY started_at DESC LIMIT $2",
            intention_id, limit,
        )
        out: list[AttemptRecord] = []
        for r in rows:
            evaluation = decode_json_value(r["evaluation"])
            out.append(AttemptRecord(
                id=r["id"], intention_id=r["intention_id"], approach=r["approach"],
                started_at=r["started_at"], ended_at=r["ended_at"], outcome=r["outcome"],
                evaluation=dict(evaluation or {}), lessons=list(r["lessons"] or []),
                cost_usd=float(r["cost_usd"] or 0),
            ))
        return out

    # -------------------------------------------------------- env entities

    async def upsert_env_entity(
        self, kind: str, key: str, attrs: dict[str, Any], owner: str | None = None,
    ) -> None:
        await self.db.upsert_env(kind, key, attrs, owner=owner, update_health=True)

    async def get_env_entity(self, key: str) -> dict[str, Any] | None:
        row = await self.db.fetchrow("SELECT * FROM env_entities WHERE key = $1", key)
        if row is None:
            return None
        return _env_entity_dict(row)

    async def list_env_entities(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = await self.db.fetch(
                "SELECT * FROM env_entities WHERE kind = $1 ORDER BY key", kind,
            )
        else:
            rows = await self.db.fetch("SELECT * FROM env_entities ORDER BY key")
        return [_env_entity_dict(row) for row in rows]

