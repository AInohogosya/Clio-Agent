from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ethos.bus import EventBus
from ethos.config import EthosConfig
from ethos.db import Database
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
)
from ethos.schemas.memory import MemoryHit

logger = get_logger("ethos.memory")


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

    async def recent_thoughts(self, n: int, thread_id: str | None = None) -> list[MemoryHit]:
        if thread_id:
            rows = await self.db.fetch(
                "SELECT id::text AS id, kind, summary, body::text AS body, ts, importance, strength, "
                "NULL::text AS emb, 'episode' AS source FROM episodes "
                "WHERE kind = 'thought' AND ($1::text IS NULL OR thread_id = $1) "
                "ORDER BY ts DESC LIMIT $2", thread_id, n,
            )
        else:
            rows = await self.db.fetch(
                "SELECT id::text AS id, kind, summary, body::text AS body, ts, importance, strength, "
                "NULL::text AS emb, 'episode' AS source FROM episodes "
                "WHERE kind = 'thought' ORDER BY ts DESC LIMIT $1", n,
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

    async def search_facts(self, query: str, k: int = 10) -> list[MemoryHit]:
        hits = await self.retriever.retrieve(query, k=k, kinds={"fact"})
        return hits

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
        demoted = 0
        refreshed = 0
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
        criteria = row["success_criteria"]
        if isinstance(criteria, str):
            criteria = json.loads(criteria)
        constraints = row["constraints"]
        if isinstance(constraints, str):
            constraints = json.loads(constraints)
        resume = row["resume_state"]
        if isinstance(resume, str):
            resume = json.loads(resume)
        hypothesis = row["hypothesis"]
        if isinstance(hypothesis, str):
            hypothesis = json.loads(hypothesis)
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
        open_statuses = statuses or [
            IntentionStatus.proposed.value, IntentionStatus.active.value,
            IntentionStatus.in_progress.value, IntentionStatus.waiting.value,
            IntentionStatus.awaiting_review.value,
        ]
        rows = await self.db.fetch(
            "SELECT * FROM intentions WHERE status = ANY($1::text[]) "
            "AND ($2::uuid IS NULL OR parent_id = $2) "
            "AND (NOT $3 OR commissioned_by IS NOT NULL) "
            "ORDER BY priority DESC, created_at ASC LIMIT $4",
            open_statuses, parent_id, commissioned_only, limit,
        )
        return [self._row_to_intention(r) for r in rows]

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
        open_statuses = [
            IntentionStatus.proposed.value, IntentionStatus.active.value,
            IntentionStatus.in_progress.value, IntentionStatus.waiting.value,
            IntentionStatus.awaiting_review.value,
        ]
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
        await self.update_intention(intention_id, resume_state=bookmark.model_dump(mode="json"))

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
            evaluation = r["evaluation"]
            if isinstance(evaluation, str):
                evaluation = json.loads(evaluation)
            out.append(AttemptRecord(
                id=r["id"], intention_id=r["intention_id"], approach=r["approach"],
                started_at=r["started_at"], ended_at=r["ended_at"], outcome=r["outcome"],
                evaluation=dict(evaluation or {}), lessons=list(r["lessons"] or []),
                cost_usd=float(r["cost_usd"] or 0),
            ))
        return out

    # ------------------------------------------------------- experiments

    async def record_experiment(
        self, intention_id: UUID, hypothesis: dict[str, Any],
    ) -> UUID:
        exp_id = _new_id()
        await self.db.execute(
            "INSERT INTO experiments (id, intention_id, hypothesis, started_at, status) "
            "VALUES ($1, $2, $3::jsonb, now(), 'running')",
            exp_id, intention_id, json.dumps(hypothesis),
        )
        return exp_id

    async def end_experiment(
        self, experiment_id: UUID, status: str, observations: dict[str, Any],
        conclusion: str, lessons: list[str],
    ) -> None:
        await self.db.execute(
            "UPDATE experiments SET ended_at = now(), status = $1, observations = $2::jsonb, "
            "conclusion = $3, lessons = $4 WHERE id = $5",
            status, json.dumps(observations), conclusion, lessons, experiment_id,
        )

    async def record_value(
        self, metric: str, value: float, unit: str, source: str,
        intention_id: UUID | None = None, experiment_id: UUID | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO value_ledger (ts, intention_id, experiment_id, metric, value, unit, source, evidence) "
            "VALUES (now(), $1, $2, $3, $4, $5, $6, $7::jsonb)",
            intention_id, experiment_id, metric, float(value), unit, source,
            json.dumps(evidence or {}),
        )

    async def value_totals(self, metric: str | None = None) -> dict[str, float]:
        if metric:
            row = await self.db.fetchrow(
                "SELECT COALESCE(SUM(value), 0) AS total FROM value_ledger WHERE metric = $1", metric,
            )
            return {metric: float(row["total"]) if row else 0.0}
        rows = await self.db.fetch(
            "SELECT metric, COALESCE(SUM(value), 0) AS total FROM value_ledger GROUP BY metric"
        )
        return {r["metric"]: float(r["total"]) for r in rows}

    # -------------------------------------------------------- env entities

    async def upsert_env_entity(
        self, kind: str, key: str, attrs: dict[str, Any], owner: str | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health) "
            "VALUES ($1, $2, $3, $4::jsonb, $5, now(), 'ok') "
            "ON CONFLICT (key) DO UPDATE SET attrs = $4::jsonb, last_verified_at = now(), health = 'ok'",
            _new_id(), kind, key, json.dumps(attrs), owner,
        )

    async def get_env_entity(self, key: str) -> dict[str, Any] | None:
        row = await self.db.fetchrow("SELECT * FROM env_entities WHERE key = $1", key)
        if row is None:
            return None
        attrs = row["attrs"]
        if isinstance(attrs, str):
            attrs = json.loads(attrs)
        return {
            "id": str(row["id"]), "kind": row["kind"], "key": row["key"],
            "attrs": dict(attrs or {}), "owner": row["owner"],
            "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
            "health": row["health"],
        }

    async def list_env_entities(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = await self.db.fetch(
                "SELECT * FROM env_entities WHERE kind = $1 ORDER BY key", kind,
            )
        else:
            rows = await self.db.fetch("SELECT * FROM env_entities ORDER BY key")
        out = []
        for row in rows:
            attrs = row["attrs"]
            if isinstance(attrs, str):
                attrs = json.loads(attrs)
            out.append({
                "id": str(row["id"]), "kind": row["kind"], "key": row["key"],
                "attrs": dict(attrs or {}), "owner": row["owner"],
                "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
                "health": row["health"],
            })
        return out

    async def delete_env_entity(self, key: str) -> None:
        await self.db.execute("DELETE FROM env_entities WHERE key = $1", key)
