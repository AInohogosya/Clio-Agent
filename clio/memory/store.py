from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from clio.bus import EventBus
from clio.config import ClioConfig
from clio.db import Database, decode_env_attrs, decode_json_value
from clio.gateway.embeddings import EmbeddingProvider
from clio.memory.retrieval import HybridRetriever, parse_pgvector, to_pgvector
from clio.observability.logger import get_logger
from clio.schemas.events import EventKind
from clio.schemas.gsl import (
    EDGE_TYPES,
    AttemptRecord,
    Bookmark,
    DecisionRecord,
    IntentionEdgeRecord,
    IntentionRecord,
    IntentionStatus,
    StageNotice,
)
from clio.schemas.memory import MemoryHit

logger = get_logger("clio.memory")


# Statuses that count as "still open work" everywhere this store looks.
_OPEN_INTENTION_STATUSES = (
    IntentionStatus.proposed.value, IntentionStatus.active.value,
    IntentionStatus.in_progress.value, IntentionStatus.waiting.value,
    IntentionStatus.awaiting_review.value, IntentionStatus.awaiting_human.value,
)

# How far along a goal each open status means, lowest first. Used only to pick
# which of several rows for the same work to keep (`find_open_intention`), where
# the wrong pick throws away a plan or a bookmark and the right one costs nothing.
_INTENTION_PROGRESS = {
    IntentionStatus.proposed.value: 0,
    IntentionStatus.active.value: 1,
    IntentionStatus.waiting.value: 2,
    IntentionStatus.awaiting_review.value: 2,
    IntentionStatus.awaiting_human.value: 2,
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


def _opt_uuid(value: Any) -> UUID | None:
    """A caller's id as a UUID, or None for anything that is not one."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


MEMORY_SCOPES = frozenset({"private", "project", "org"})


def _check_scope(scope: str) -> str:
    scope = (scope or "private").lower()
    if scope not in MEMORY_SCOPES:
        raise ValueError(f"unknown memory scope: {scope!r}")
    return scope


PROJECT_STATUSES = frozenset({
    "active", "paused_for_human", "preempted", "evolving_cooldown", "done",
})
PROJECT_LIFECYCLE_MODES = frozenset({"finite", "evergreen"})


@dataclass
class ProjectProgressLedger:
    """One project's verified standing, read all-time from Postgres.

    Every field here is a fact from a row, not an intention: nodes are counted
    intentions, artifacts are registered paths, actions are journal rows. A
    reply that reports progress is answerable from this alone, which is the
    point — a project that lost the focus an hour ago keeps its exact record.
    """

    project_id: str
    title: str
    lifecycle_mode: str
    status: str
    is_current_focus: bool
    nodes_done: int
    nodes_total: int
    verified_checks_passed: int
    artifacts_touched: list[str] = field(default_factory=list)
    recent_actions: list[str] = field(default_factory=list)
    blocking_human_tasks: list[dict] = field(default_factory=list)
    checkpoint_summary: str = ""
    budget_spent_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "lifecycle_mode": self.lifecycle_mode,
            "status": self.status,
            "is_current_focus": self.is_current_focus,
            "nodes_done": self.nodes_done,
            "nodes_total": self.nodes_total,
            "verified_checks_passed": self.verified_checks_passed,
            "artifacts_touched": list(self.artifacts_touched),
            "recent_actions": list(self.recent_actions),
            "blocking_human_tasks": [dict(t) for t in self.blocking_human_tasks],
            "checkpoint_summary": self.checkpoint_summary,
            "budget_spent_usd": self.budget_spent_usd,
        }


def _check_edge_type(edge_type: str) -> str:
    edge_type = (edge_type or "depends_on").lower()
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"unknown edge_type: {edge_type!r}")
    return edge_type


def dag_has_cycle(edges: list[tuple[str, str]]) -> bool:
    """Whether a set of (parent, child) pairs would form a cycle. Pure."""
    graph: dict[str, list[str]] = {}
    nodes: set[str] = set()
    for parent, child in edges:
        graph.setdefault(parent, []).append(child)
        nodes.add(parent)
        nodes.add(child)
    state: dict[str, int] = {n: 0 for n in nodes}

    def visit(node: str) -> bool:
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt, 0) == 1:
                return True
            if state.get(nxt, 0) == 0 and visit(nxt):
                return True
        state[node] = 2
        return False

    return any(state[n] == 0 and visit(n) for n in nodes)


def allocate_ev_budgets(
    total: float,
    children: list[dict[str, Any]],
    *,
    h2_cap: float | None = None,
) -> dict[str, float]:
    """Split `total` across children proportionally to EV/cost.

    Weight = expected_value (floored, default 0.5) * (1 / max(estimated_cost, eps))
    — high value per dollar earns the larger share. Capped at `h2_cap` when
    given. Returns {child_key: budget}. Pure: no I/O.
    """
    total = max(0.0, float(total))
    if h2_cap is not None:
        total = min(total, max(0.0, float(h2_cap)))
    weights: list[float] = []
    for child in children:
        ev = child.get("expected_value")
        ev = 0.5 if ev is None else max(0.0, float(ev))
        cost = child.get("estimated_cost_usd")
        cost = 0.05 if cost is None else max(1e-6, float(cost))
        weights.append(max(1e-9, ev / cost))
    weight_sum = sum(weights) or 1.0
    out: dict[str, float] = {}
    for child, weight in zip(children, weights, strict=True):
        key = str(child.get("key") or child.get("title") or child.get("id") or len(out))
        out[key] = round(total * weight / weight_sum, 4)
    return out


class CycleError(ValueError):
    """Adding this edge would close a cycle in the intention DAG."""


class MemoryStore:
    """Durable memory: episodes, facts, knowhow, questions, intentions,
    decisions and attempts. Postgres is the single source of truth [D-10]."""

    def __init__(
        self,
        db: Database,
        embedder: EmbeddingProvider,
        config: ClioConfig,
        bus: EventBus | None = None,
        envelope: Any = None,
    ):
        self.db = db
        self.embedder = embedder
        self.config = config
        self.bus = bus
        # The envelope, when the deployment configured a master key, seals the
        # sensitive payloads of memory at rest (episode bodies, fact
        # provenance). `None` is the plaintext mode everything ran in before.
        self.envelope = envelope
        self.retriever = HybridRetriever(db, embedder, config.memory)

    # ---- envelope sealing ---------------------------------------------------

    async def _seal(self, payload: dict[str, Any], space_id: str) -> tuple[dict[str, Any], Any]:
        """Seal a payload when an envelope is configured; else pass it through."""
        if self.envelope is None:
            return payload, None
        sealed = await self.envelope.seal(json.dumps(payload, default=str), space_id)
        return dict(self.envelope.stub()), sealed

    async def _unseal_row(self, row: Any) -> dict[str, Any]:
        """Give the caller the real payload of a row, sealed or not."""
        data = dict(row)
        body = data.get("body")
        is_stub = isinstance(body, (dict, str)) and (
            (isinstance(body, str) and '"sealed": true' in body) or (isinstance(body, dict) and body.get("sealed"))
        )
        if not (is_stub and data.get("sealed_payload") and self.envelope is not None):
            return data
        space_id = str(data.get("space_id") or "local")
        try:
            raw = await self.envelope.open(data["sealed_payload"], space_id)
            data["body"] = json.loads(raw)
        except Exception:
            logger.exception("memory.unseal_failed id=%s", data.get("id"))
        return data

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
        scope: str = "private",
        namespace_id: str | None = None,
    ) -> UUID:
        scope = _check_scope(scope)
        if scope == "private":
            namespace_id = None
        importance = self.config.memory.episode.default_importance if importance is None else importance
        half_life_h = self.config.memory.episode.default_half_life_h if half_life_h is None else half_life_h
        vec = self.embedder.embed([summary])[0]
        ep_id = _new_id()
        body_json, sealed = await self._seal(dict(body or {}), "local")
        await self.db.execute(
            "INSERT INTO episodes (id, ts, thread_id, kind, summary, body, intention_id, importance, "
            "strength, half_life_h, last_access, access_count, status, embedding, scope, namespace_id, "
            "sealed_payload) "
            "VALUES ($1, now(), $2, $3, $4, $5::jsonb, $6, $7, 0.5, $8, now(), 0, 'active', $9::vector, $10, $11, $12)",
            ep_id, thread_id, kind, summary, json.dumps(body_json), intention_id,
            float(importance), float(half_life_h), to_pgvector(vec), scope, namespace_id, sealed,
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
        return await self._unseal_row(row)

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
            "embedding::text AS emb, sealed_payload FROM episodes WHERE ts >= $1 AND status = 'active' "
            "ORDER BY ts ASC LIMIT $2", since, limit,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            unsealed = await self._unseal_row(r)
            body = unsealed.get("body")
            out.append(
                {
                    "id": r["id"], "ts": r["ts"], "kind": r["kind"], "summary": r["summary"],
                    "body": json.loads(body) if isinstance(body, str) and body else (body or {}),
                    "importance": float(r["importance"] or 0), "strength": float(r["strength"] or 0),
                    "embedding": parse_pgvector(r["emb"]),
                }
            )
        return out

    async def last_consolidation_at(self) -> datetime | None:
        """When the nightly pass last finished, so a restart does not repeat it.

        The life loop keeps the date in memory, which is enough for a process
        that stays up and no help at all for one that is restarted inside the
        same evening: the pass would run again on every start. The episode it
        writes on completion is the durable record that already exists.
        """
        row = await self.db.fetchrow(
            "SELECT max(ts) AS ts FROM episodes WHERE kind = 'consolidation'"
        )
        return row["ts"] if row is not None else None

    # --------------------------------------------------------------- facts

    async def record_fact(
        self,
        subject: str,
        statement: str,
        confidence: float,
        provenance: dict[str, Any],
        field: str | None = None,
        supersede_similar: bool = True,
        scope: str = "private",
        namespace_id: str | None = None,
    ) -> UUID:
        scope = _check_scope(scope)
        if scope == "private":
            namespace_id = None
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
                prov_json, sealed = await self._seal(dict(provenance), "local")
                await conn.execute(
                    "INSERT INTO facts (id, subject, statement, confidence, provenance, field, "
                    "valid_from, strength, embedding, updated_at, scope, namespace_id, sealed_payload) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), 0.5, $7::vector, now(), $8, $9, $10)",
                    fact_id, subject, statement, float(confidence),
                    json.dumps(prov_json), field, to_pgvector(vec), scope, namespace_id, sealed,
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
            "valid_from, valid_to, superseded_by, strength, sealed_payload FROM facts "
            "WHERE subject = $1 AND superseded_by IS NULL ORDER BY updated_at DESC LIMIT $2",
            subject, limit,
        )
        return [await self._unseal_row(r) for r in rows]

    # ------------------------------------------------------------ knowhow

    async def record_knowhow(
        self, situation: str, advice: str, evidence: dict[str, Any],
        scope: str = "private", namespace_id: str | None = None,
    ) -> UUID:
        scope = _check_scope(scope)
        if scope == "private":
            namespace_id = None
        kh_id = _new_id()
        vec = self.embedder.embed([f"{situation} -> {advice}"])[0]
        existing = await self.db.fetchrow(
            "SELECT id, uses, successes FROM knowhow WHERE situation = $1 AND advice = $2 AND retired = false",
            situation, advice,
        )
        if existing:
            await self.db.execute(
                "UPDATE knowhow SET evidence = $1::jsonb, uses = uses, embedding = $2::vector, "
                "scope = $4, namespace_id = $5 WHERE id = $3",
                json.dumps(evidence), to_pgvector(vec), existing["id"], scope, namespace_id,
            )
            return existing["id"]
        await self.db.execute(
            "INSERT INTO knowhow (id, situation, advice, evidence, uses, successes, embedding, retired, scope, namespace_id) "
            "VALUES ($1, $2, $3, $4::jsonb, 0, 0, $5::vector, false, $6, $7)",
            kh_id, situation, advice, json.dumps(evidence), to_pgvector(vec), scope, namespace_id,
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
        scope: str = "private", namespace_id: str | None = None,
    ) -> UUID:
        scope = _check_scope(scope)
        if scope == "private":
            namespace_id = None
        q_id = _new_id()
        vec = self.embedder.embed([text])[0]
        await self.db.execute(
            "INSERT INTO questions (id, text, origin_episode, priority, status, embedding, scope, namespace_id) "
            "VALUES ($1, $2, $3, $4, 'open', $5::vector, $6, $7)",
            q_id, text, origin_episode, float(priority), to_pgvector(vec), scope, namespace_id,
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
        expected_value: float | None = None,
        assigned_principal_id: str | None = None,
        lease_id: str | None = None,
        project_id: UUID | str | None = None,
        root_intention_id: UUID | str | None = None,
        executor_kind: str = "self",
        instruction_spec: dict[str, Any] | None = None,
    ) -> UUID:
        intent_id = _new_id()
        await self.db.execute(
            "INSERT INTO intentions (id, parent_id, kind, title, desired_end_state, success_criteria, "
            "constraints, origin, commissioned_by, status, priority, deadline, budget_usd, spent_usd, "
            "expected_value, lease_id, assigned_principal_id, project_id, root_intention_id, "
            "executor_kind, instruction_spec, "
            "created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, 'proposed', $10, $11, $12, 0, $13, $14, $15, $16, $17, $18, $19::jsonb, now(), now())",
            intent_id, parent_id, kind, title, desired_end_state,
            json.dumps(success_criteria or []), json.dumps(constraints or {}),
            origin, commissioned_by, float(priority), deadline, budget_usd,
            expected_value, lease_id, assigned_principal_id,
            _opt_uuid(project_id), _opt_uuid(root_intention_id),
            executor_kind or "self", json.dumps(instruction_spec) if instruction_spec else None,
        )
        if self.bus is not None:
            await self.bus.publish(EventKind.INTENTION_UPDATE, {
                "intention_id": str(intent_id), "status": "proposed", "title": title,
                "kind": kind, "commissioned_by": commissioned_by, "priority": priority,
                "deadline": deadline.isoformat() if deadline else None,
                "budget_usd": budget_usd, "parent_id": str(parent_id) if parent_id else None,
                "project_id": str(project_id) if project_id else None,
                "executor_kind": executor_kind or "self",
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
        try:
            budget_allocated = float(row["budget_allocated_usd"]) if row["budget_allocated_usd"] is not None else None
        except (KeyError, TypeError, ValueError):
            budget_allocated = None
        try:
            budget_spent = float(row["budget_spent_usd"] or 0)
        except (KeyError, TypeError, ValueError):
            budget_spent = 0.0
        try:
            expected_value = float(row["expected_value"]) if row["expected_value"] is not None else None
        except (KeyError, TypeError, ValueError):
            expected_value = None
        def _opt_text(key: str) -> str | None:
            try:
                value = row[key]
            except (KeyError, TypeError):
                return None
            return str(value) if value is not None else None

        def _opt_uuid(key: str) -> UUID | None:
            try:
                value = row[key]
            except (KeyError, TypeError):
                return None
            if value is None:
                return None
            try:
                return UUID(str(value))
            except ValueError:
                return None

        def _opt_json(key: str) -> dict[str, Any] | None:
            try:
                value = decode_json_value(row[key])
            except (KeyError, TypeError):
                return None
            return dict(value) if isinstance(value, dict) else None
        return IntentionRecord(
            id=row["id"], parent_id=row["parent_id"], kind=row["kind"], title=row["title"],
            desired_end_state=row["desired_end_state"] or "", success_criteria=list(criteria or []),
            constraints=dict(constraints or {}), origin=row["origin"] or "self",
            commissioned_by=row["commissioned_by"], status=row["status"],
            priority=float(row["priority"] or 0.5), deadline=row["deadline"],
            budget_usd=float(row["budget_usd"]) if row["budget_usd"] is not None else None,
            spent_usd=float(row["spent_usd"] or 0),
            budget_allocated_usd=budget_allocated,
            budget_spent_usd=budget_spent,
            expected_value=expected_value,
            lease_id=_opt_text("lease_id"),
            assigned_principal_id=_opt_text("assigned_principal_id"),
            project_id=_opt_uuid("project_id"),
            root_intention_id=_opt_uuid("root_intention_id"),
            executor_kind=_opt_text("executor_kind") or "self",
            instruction_spec=_opt_json("instruction_spec"),
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

    async def next_open_commission(self) -> IntentionRecord | None:
        """The open intention a person is waiting on, most urgent first.

        The commission bypass in `choose_focus` takes the pass outright -- but
        it only ever runs when the focus is empty. A focus held by lesser work
        (an idle impulse, a drive's own housekeeping) never empties on its own:
        `_settled_focus` returns the held focus for as long as its goal has a
        pending step, so the bypass is unreachable and a person's request sits
        open behind work nobody asked for. Observed live on 2026-10-25: the
        focus had landed on a placeholder intention and stayed there all
        morning -- `advance_goal` cycles every tick -- while the commissioned
        task the owner had asked to see resumed sat open one priority band
        above it, and every reply said "picking up the Snake work" while no
        cycle ever read that intention.

        This is the same selection the bypass makes, callable mid-cycle: the
        oldest open commission with a deadline beats an undated one, priority
        breaks ties, recency after that -- the same ordering
        `selection_key` gives the drive layer.
        """
        rows = await self.db.fetch(
            "SELECT * FROM intentions WHERE status = ANY($1::text[]) "
            "AND origin = 'commission' "
            "ORDER BY deadline ASC NULLS LAST, priority DESC, created_at ASC LIMIT 1",
            list(_OPEN_INTENTION_STATUSES),
        )
        if not rows:
            return None
        return self._row_to_intention(rows[0])

    async def record_owner_decision_request(
        self, intention: IntentionRecord, stage: str, detail: str = "",
    ) -> StageNotice | None:
        """Record that a decision only the owner can make is owed, at an address.

        `record_stage_notice` answers for commissioned work: the commissioner
        is on the intention, and their newest inbound row is the address. Work
        the agent chose for itself has no commissioner, so the same park that a
        commission would announce otherwise arrives in silence -- and the
        repeat parks of 2026-11-08..10 (CodeForge, five times) arrived as
        nothing at all. The owner's address is resolved the same durable way:
        the newest inbound row in the store, whoever it is from, is the
        conversation the owner is actually reading. Nobody inbound at all
        means no address, and a notice with no address is a debt paid into a
        void, so `None`.

        Idempotent on `(intention_id, stage)` like every stage notice, so a
        park counted twice writes one debt; a later park at a higher count
        carries a new stage string and can ask again.
        """
        row = await self.db.fetchrow(
            "SELECT person_id, channel, conversation_key FROM messages "
            "WHERE direction = 'inbound' "
            "ORDER BY ts DESC LIMIT 1",
        )
        if row is None:
            return None
        person_id = str(row["person_id"] or "owner")
        notice = StageNotice(
            intention_id=intention.id, stage=stage, title=intention.title or "",
            detail=detail[:600], person_id=person_id,
            channel=row["channel"], conversation_key=row["conversation_key"] or None,
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
            logger.warning(
                "memory.owner_decision_request_failed", intention=str(intention.id)
            )
            return None
        return notice

    async def list_parked_intentions(self, older_than_s: float, limit: int = 50) -> list[IntentionRecord]:
        """Parked intentions whose parking has aged past `older_than_s`.

        For the reopen sweep: parking is a pause with a reason, and this is
        the query that finds the pauses old enough to be owed another chance.
        `updated_at` is written when the intention is closed, so for a parked
        row it *is* the parking time until something else touches the row --
        and nothing else is allowed to, while it is closed.
        """
        rows = await self.db.fetch(
            "SELECT * FROM intentions WHERE status = 'parked' "
            "AND updated_at <= now() - make_interval(secs => $1) "
            "ORDER BY updated_at ASC LIMIT $2",
            float(older_than_s), limit,
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
            "budget_allocated_usd", "budget_spent_usd", "expected_value",
            "lease_id", "assigned_principal_id",
            "project_id", "root_intention_id", "executor_kind", "instruction_spec",
        }
        sets: list[str] = []
        args: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            idx = len(args) + 1
            if key in ("resume_state", "hypothesis", "success_criteria", "constraints", "instruction_spec"):
                sets.append(f"{key} = ${idx}::jsonb")
                args.append(json.dumps(value))
            elif key in ("project_id", "root_intention_id"):
                sets.append(f"{key} = ${idx}")
                args.append(_opt_uuid(value))
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
            "UPDATE intentions SET spent_usd = COALESCE(spent_usd, 0) + $1, "
            "budget_spent_usd = COALESCE(budget_spent_usd, 0) + $1, updated_at = now() "
            "WHERE id = $2 RETURNING spent_usd, budget_spent_usd",
            float(amount_usd), intention_id,
        )
        if row is None:
            return 0.0
        try:
            return float(row["budget_spent_usd"] or row["spent_usd"] or 0)
        except (TypeError, ValueError, KeyError):
            return float(row["spent_usd"] or 0)

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

    # ------------------------------------------- recursive intention graphs

    async def add_intention_edge(
        self, parent_id: UUID, child_id: UUID, edge_type: str = "depends_on",
        gate_policy: dict[str, Any] | None = None,
    ) -> IntentionEdgeRecord:
        """Link two intentions into a DAG, rejecting cycles."""
        edge_type = _check_edge_type(edge_type)
        if parent_id == child_id:
            raise CycleError("an intention cannot depend on itself")
        # Cycle check: the child must not already reach the parent.
        existing = await self.db.fetch(
            "SELECT parent_id, child_id FROM intention_edges LIMIT 10000",
        )
        pairs = [(str(r["parent_id"]), str(r["child_id"])) for r in existing]
        pairs.append((str(parent_id), str(child_id)))
        if dag_has_cycle(pairs):
            raise CycleError(f"edge {parent_id} -> {child_id} ({edge_type}) would create a cycle")
        await self.db.execute(
            "INSERT INTO intention_edges (parent_id, child_id, edge_type, gate_policy, created_at) "
            "VALUES ($1, $2, $3, $4::jsonb, now()) "
            "ON CONFLICT (parent_id, child_id, edge_type) DO UPDATE SET gate_policy = $4::jsonb",
            parent_id, child_id, edge_type, json.dumps(gate_policy or {}),
        )
        return IntentionEdgeRecord(
            parent_id=parent_id, child_id=child_id, edge_type=edge_type,
            gate_policy=dict(gate_policy or {}),
        )

    async def list_intention_edges(
        self, intention_id: UUID | None = None, *, as_parent: bool = True,
    ) -> list[IntentionEdgeRecord]:
        if intention_id is None:
            rows = await self.db.fetch(
                "SELECT * FROM intention_edges ORDER BY created_at ASC LIMIT 10000",
            )
        elif as_parent:
            rows = await self.db.fetch(
                "SELECT * FROM intention_edges WHERE parent_id = $1 ORDER BY created_at ASC",
                intention_id,
            )
        else:
            rows = await self.db.fetch(
                "SELECT * FROM intention_edges WHERE child_id = $1 ORDER BY created_at ASC",
                intention_id,
            )
        out: list[IntentionEdgeRecord] = []
        for r in rows:
            out.append(IntentionEdgeRecord(
                parent_id=r["parent_id"], child_id=r["child_id"],
                edge_type=r["edge_type"],
                gate_policy=dict(decode_json_value(r["gate_policy"]) or {}),
                created_at=r["created_at"],
            ))
        return out

    async def get_ready_intentions(
        self, limit: int = 50,
    ) -> list[IntentionRecord]:
        """Agent-executable intentions whose `depends_on` predecessors are done.

        `executor_kind = 'human'` rows are excluded on purpose: a node only a
        person can complete must never take a solver pass — it waits in
        `get_ready_human_intentions` for the reply that closes it.
        """
        rows = await self.db.fetch(
            "SELECT * FROM intentions i WHERE i.status IN ('active','proposed','in_progress','waiting') "
            "AND COALESCE(i.executor_kind, 'self') <> 'human' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM intention_edges e JOIN intentions pred ON pred.id = e.parent_id "
            "  WHERE e.child_id = i.id AND e.edge_type = 'depends_on' "
            "  AND pred.status <> 'done'"
            ") ORDER BY i.priority DESC, i.created_at ASC LIMIT $1",
            limit,
        )
        return [self._row_to_intention(r) for r in rows]

    async def get_ready_human_intentions(
        self, limit: int = 50,
    ) -> list[IntentionRecord]:
        """Human-executable nodes whose predecessors are all done.

        These are the asks: the moment one lands here, someone is being asked
        to do something only they can do, and the project it belongs to runs
        on their reply rather than on the solver.
        """
        rows = await self.db.fetch(
            "SELECT * FROM intentions i WHERE i.status IN ('active','proposed','in_progress','waiting') "
            "AND COALESCE(i.executor_kind, 'self') = 'human' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM intention_edges e JOIN intentions pred ON pred.id = e.parent_id "
            "  WHERE e.child_id = i.id AND e.edge_type = 'depends_on' "
            "  AND pred.status <> 'done'"
            ") ORDER BY i.priority DESC, i.created_at ASC LIMIT $1",
            limit,
        )
        return [self._row_to_intention(r) for r in rows]

    async def get_intention_subgraph(
        self, root_id: UUID, *, max_depth: int = 8,
    ) -> dict[str, Any]:
        """All intentions reachable from `root_id` plus their edges."""
        seen: set[UUID] = {root_id}
        frontier: list[UUID] = [root_id]
        edges: list[IntentionEdgeRecord] = []
        for _ in range(max_depth):
            if not frontier:
                break
            next_frontier: list[UUID] = []
            for node in frontier:
                for edge in await self.list_intention_edges(node, as_parent=True):
                    edges.append(edge)
                    if edge.child_id not in seen:
                        seen.add(edge.child_id)
                        next_frontier.append(edge.child_id)
            frontier = next_frontier
        intentions: list[IntentionRecord] = []
        for node in seen:
            record = await self.get_intention(node)
            if record is not None:
                intentions.append(record)
        return {"root_id": str(root_id), "intentions": intentions, "edges": edges}

    async def dag_telemetry(self, root_id: UUID | None = None) -> dict[str, Any]:
        """Completion ratio, blockers, burn and ready count for the drives."""
        if root_id is not None:
            subgraph = await self.get_intention_subgraph(root_id)
            nodes = subgraph["intentions"]
            edges = subgraph["edges"]
        else:
            nodes = await self.list_intentions(limit=500)
            edges = await self.list_intention_edges()
        total = len(nodes)
        done = sum(1 for n in nodes if n.status == "done")
        blocked = 0
        by_id = {n.id: n for n in nodes}
        for edge in edges:
            if edge.edge_type != "depends_on":
                continue
            child = by_id.get(edge.child_id)
            parent = by_id.get(edge.parent_id)
            if child is not None and child.status in _OPEN_INTENTION_STATUSES and (
                parent is None or parent.status != "done"
            ):
                blocked += 1
        burn = sum(float(n.spent_usd or 0) + float(n.budget_spent_usd or 0) for n in nodes)
        allocated = sum(float(n.budget_usd or 0) for n in nodes)
        ready = await self.get_ready_intentions(limit=500)
        return {
            "total": total,
            "done": done,
            "completion_ratio": (done / total) if total else 0.0,
            "blocked_edges": blocked,
            "budget_burn_usd": burn,
            "budget_allocated_usd": allocated,
            "ready_count": len(ready),
            "root_id": str(root_id) if root_id else None,
        }

    # ------------------------------------------------------------- projects

    async def create_project(
        self,
        title: str,
        owner_principal_id: str,
        root_intention_id: UUID | str | None = None,
        *,
        lifecycle_mode: str = "finite",
        status: str = "active",
        leases: Any = None,
        db: Any = None,
    ) -> dict[str, Any]:
        """One project container: its row, its root intention's attribution,
        and its own isolated workspace lease.

        The lease is the isolation guarantee: two projects assigned from two
        conversations get two lease roots, so Project 1 and Project 2 never
        write into each other's files. A git repository gets its own worktree;
        anything else gets a snapshot directory under `~/work/.projects/<id>/`.

        Returns the project row as a dict, with `project_id` and
        `workspace_lease_id` set — the caller binds its conversation to it.
        """
        lifecycle_mode = (lifecycle_mode or "finite").lower()
        if lifecycle_mode not in PROJECT_LIFECYCLE_MODES:
            lifecycle_mode = "finite"
        status = (status or "active").lower()
        if status not in PROJECT_STATUSES:
            status = "active"
        project_id = _new_id()
        root_uuid = _opt_uuid(root_intention_id)
        lease_id: str | None = None
        lease_root: str | None = None
        sandbox_id: str | None = None
        if leases is not None:
            try:
                projects_root = self.config.paths.work / ".projects" / str(project_id)
                projects_root.mkdir(parents=True, exist_ok=True)
                base = Path(str(self.config.paths.workspace)).expanduser()
                is_git = (base / ".git").exists() or (base / ".git").is_file()
                if is_git:
                    # A real git worktree: the project's work integrates
                    # through a commit and two projects never share one.
                    lease = leases.acquire(
                        intention_id=root_uuid, target_path=base, kind="git_worktree",
                        allowed_paths=[],
                    )
                else:
                    # No repository to branch: the project gets its own
                    # isolated directory under ~/work/.projects/<project_id>/,
                    # which is both its workspace and its fence.
                    lease = leases.acquire(
                        intention_id=root_uuid, target_path=projects_root,
                        kind="snapshot", allowed_paths=[],
                    )
                    lease.root_path = projects_root
                lease_id = lease.id
                lease_root = str(lease.root_path)
                # The container: the lease's root goes inside a sandbox
                # mounted at /workspace, one sandbox per project. A runtime
                # without a sandbox manager provisions nothing here and the
                # lease root remains the boundary.
                provision = getattr(leases, "provision_sandbox", None)
                if provision is not None:
                    try:
                        report = await provision(lease, str(project_id))
                        sandbox_id = report.get("sandbox_id")
                    except Exception:
                        logger.exception("memory.project_sandbox_failed", project=str(project_id))
                persist = getattr(leases, "persist_lease", None)
                if persist is not None:
                    await persist(lease, db if db is not None else self.db)
            except Exception:
                logger.exception("memory.project_lease_failed", project=str(project_id))
                lease_id = None
        await self.db.execute(
            "INSERT INTO projects (project_id, title, owner_principal_id, root_intention_id, "
            "workspace_lease_id, sandbox_id, lifecycle_mode, status, focus_seconds_accumulated, "
            "checkpoint_json, evolution_config_json, last_active_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0.0, '{}'::jsonb, '{}'::jsonb, now(), now())",
            project_id, title, owner_principal_id, root_uuid, lease_id, sandbox_id,
            lifecycle_mode, status,
        )
        if root_uuid is not None:
            # `root_intention_id` is the *root intention's own id* — the anchor
            # every descendant's attribution points back to; `project_id` is
            # the container. They are two different columns precisely so a
            # journal row can name both.
            await self.db.execute(
                "UPDATE intentions SET project_id = $2, root_intention_id = $1, updated_at = now() "
                "WHERE id = $1 AND project_id IS NULL",
                root_uuid, project_id,
            )
        logger.info(
            "memory.project_created", project=str(project_id), title=title[:120],
            lease=lease_id, lease_root=lease_root, sandbox=sandbox_id, status=status,
        )
        return {
            "project_id": str(project_id),
            "title": title,
            "owner_principal_id": owner_principal_id,
            "root_intention_id": str(root_uuid) if root_uuid else None,
            "workspace_lease_id": lease_id,
            "workspace_root": lease_root,
            "sandbox_id": sandbox_id,
            "lifecycle_mode": lifecycle_mode,
            "status": status,
        }

    async def get_project(self, project_id: UUID | str) -> dict[str, Any] | None:
        row = await self.db.fetchrow("SELECT * FROM projects WHERE project_id = $1", _opt_uuid(project_id))
        return self._project_row_to_dict(row) if row is not None else None

    async def update_project(self, project_id: UUID | str, **fields: Any) -> None:
        allowed = {
            "status", "lifecycle_mode", "checkpoint_json", "evolution_config_json",
            "focus_seconds_accumulated", "next_evolution_at", "last_active_at",
            "workspace_lease_id", "sandbox_id", "title",
        }
        sets: list[str] = []
        args: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            idx = len(args) + 1
            if key in ("checkpoint_json", "evolution_config_json"):
                sets.append(f"{key} = ${idx}::jsonb")
                args.append(json.dumps(value or {}))
            else:
                sets.append(f"{key} = ${idx}")
                args.append(value)
        if not sets:
            return
        args.append(_opt_uuid(project_id))
        await self.db.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE project_id = ${len(args)}",
            *args,
        )

    async def list_projects(self, statuses: Sequence[str] | None = None) -> list[dict[str, Any]]:
        if statuses:
            rows = await self.db.fetch(
                "SELECT * FROM projects WHERE status = ANY($1::text[]) ORDER BY last_active_at DESC",
                list(statuses),
            )
        else:
            rows = await self.db.fetch("SELECT * FROM projects ORDER BY last_active_at DESC")
        return [self._project_row_to_dict(r) for r in rows]

    @staticmethod
    def _project_row_to_dict(row: Any) -> dict[str, Any]:
        def _uuid_text(key: str) -> str | None:
            value = row[key] if key in row.keys() else None
            return str(value) if value is not None else None
        checkpoint = decode_json_value(row["checkpoint_json"], {}) or {}
        evolution = decode_json_value(row["evolution_config_json"], {}) or {}
        return {
            "project_id": str(row["project_id"]),
            "title": row["title"],
            "owner_principal_id": row["owner_principal_id"],
            "root_intention_id": _uuid_text("root_intention_id"),
            "workspace_lease_id": _uuid_text("workspace_lease_id"),
            "lifecycle_mode": row["lifecycle_mode"],
            "status": row["status"],
            "focus_seconds_accumulated": float(row["focus_seconds_accumulated"] or 0.0),
            "checkpoint_json": dict(checkpoint),
            "evolution_config_json": dict(evolution),
            "last_active_at": row["last_active_at"],
            "next_evolution_at": row["next_evolution_at"],
            "created_at": row["created_at"],
        }

    async def bind_conversation_project(self, conversation_key: str, project_id: str | None) -> None:
        """Upsert the conversation -> project binding (`channel:person_id` key)."""
        await self.db.execute(
            "INSERT INTO conversation_project_bindings (conversation_key, active_project_id, updated_at) "
            "VALUES ($1, $2, now()) "
            "ON CONFLICT (conversation_key) DO UPDATE SET "
            "active_project_id = EXCLUDED.active_project_id, updated_at = now()",
            conversation_key, _opt_uuid(project_id),
        )

    async def get_conversation_project(self, conversation_key: str) -> dict[str, Any] | None:
        """The project a conversation is bound to, or None."""
        row = await self.db.fetchrow(
            "SELECT p.* FROM conversation_project_bindings b "
            "JOIN projects p ON p.project_id = b.active_project_id "
            "WHERE b.conversation_key = $1",
            conversation_key,
        )
        return self._project_row_to_dict(row) if row is not None else None

    async def get_project_progress_ledger(
        self,
        project_id: str | None = None,
        current_focus_project_id: str | None = None,
    ) -> list[ProjectProgressLedger]:
        """Every project's verified standing, read straight from Postgres.

        No model call anywhere in here — this is the record the reply prompt
        and the workspace ledger render from. One `project_id` answers for
        that project alone; `None` answers for every project that is not
        `done`, plus recently completed ones, so "what is the status of
        everything" is one query set rather than one per project.
        """
        if project_id is not None:
            rows = await self.db.fetch(
                "SELECT * FROM projects WHERE project_id = $1", _opt_uuid(project_id),
            )
        else:
            rows = await self.db.fetch(
                "SELECT * FROM projects WHERE status <> 'done' "
                "OR (status = 'done' AND last_active_at >= now() - interval '24 hours') "
                "ORDER BY last_active_at DESC",
            )
        ledgers: list[ProjectProgressLedger] = []
        for row in rows:
            pid = str(row["project_id"])
            nodes = await self.db.fetchrow(
                "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status = 'done') AS done, "
                "COALESCE(SUM(COALESCE(budget_spent_usd, spent_usd, 0)), 0) AS spent "
                "FROM intentions WHERE project_id = $1",
                _opt_uuid(pid),
            )
            artifact_rows = await self.db.fetch(
                "SELECT DISTINCT uri FROM artifacts WHERE project_id = $1 ORDER BY uri LIMIT 20",
                _opt_uuid(pid),
            )
            action_rows = await self.db.fetch(
                "SELECT tool, status, COALESCE(result_digest->>'summary', '') AS summary "
                "FROM action_journal WHERE project_id = $1 AND status = 'ok' "
                "ORDER BY ts_start DESC LIMIT 5",
                _opt_uuid(pid),
            )
            human_rows = await self.db.fetch(
                "SELECT id::text, title, instruction_spec FROM intentions "
                "WHERE project_id = $1 AND executor_kind = 'human' "
                "AND status NOT IN ('done','cancelled') ORDER BY created_at ASC",
                _opt_uuid(pid),
            )
            checkpoint = decode_json_value(row["checkpoint_json"], {}) or {}
            ledgers.append(ProjectProgressLedger(
                project_id=pid,
                title=row["title"],
                lifecycle_mode=row["lifecycle_mode"],
                status=row["status"],
                is_current_focus=(
                    current_focus_project_id is not None and pid == str(current_focus_project_id)
                ),
                nodes_done=int(nodes["done"] or 0) if nodes else 0,
                nodes_total=int(nodes["total"] or 0) if nodes else 0,
                verified_checks_passed=int(nodes["done"] or 0) if nodes else 0,
                artifacts_touched=[str(r["uri"]) for r in artifact_rows],
                recent_actions=[
                    f"{r['tool']} -> {r['status']}: {' '.join(str(r['summary'] or '').split())[:140]}"
                    for r in action_rows
                ],
                blocking_human_tasks=[
                    {
                        "id": str(r["id"]),
                        "title": r["title"],
                        "instruction_spec": decode_json_value(r["instruction_spec"], {}) or {},
                    }
                    for r in human_rows
                ],
                checkpoint_summary=self.format_checkpoint_summary(checkpoint),
                budget_spent_usd=float(nodes["spent"] or 0) if nodes else 0.0,
            ))
        return ledgers

    @staticmethod
    def format_checkpoint_summary(checkpoint: dict[str, Any]) -> str:
        """A checkpoint dict as the one line a ledger or a reply needs."""
        if not checkpoint:
            return ""
        parts: list[str] = []
        if checkpoint.get("completed_nodes") is not None:
            parts.append(
                f"{checkpoint.get('completed_nodes')}/{checkpoint.get('total_nodes', '?')} nodes done"
            )
        if checkpoint.get("active_node_id"):
            parts.append(f"paused at node {str(checkpoint['active_node_id'])[:8]}")
        files = checkpoint.get("modified_files") or []
        if files:
            parts.append(f"files: {', '.join(str(f) for f in files[:3])}")
        if checkpoint.get("next_planned_step"):
            parts.append(f"next: {str(checkpoint['next_planned_step'])[:120]}")
        if checkpoint.get("reason"):
            parts.append(f"why paused: {str(checkpoint['reason'])[:120]}")
        return "; ".join(parts)

    async def record_project_milestone(
        self,
        project_id: UUID | str,
        *,
        version_tag: str,
        summary: str,
        metrics: dict[str, Any] | None = None,
        git_commit_sha: str | None = None,
    ) -> str:
        milestone_id = _new_id()
        await self.db.execute(
            "INSERT INTO project_milestones (milestone_id, project_id, version_tag, summary, "
            "metrics_json, git_commit_sha, created_at) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6, now())",
            milestone_id, _opt_uuid(project_id), version_tag, summary,
            json.dumps(metrics or {}), git_commit_sha,
        )
        return str(milestone_id)

    async def list_project_milestones(self, project_id: UUID | str) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT * FROM project_milestones WHERE project_id = $1 ORDER BY created_at ASC",
            _opt_uuid(project_id),
        )
        return [
            {
                "milestone_id": str(r["milestone_id"]),
                "project_id": str(r["project_id"]),
                "version_tag": r["version_tag"],
                "summary": r["summary"],
                "metrics": decode_json_value(r["metrics_json"], {}) or {},
                "git_commit_sha": r["git_commit_sha"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

