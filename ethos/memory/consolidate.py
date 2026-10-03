from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ethos.config import EthosConfig
from ethos.memory.self_model import SelfModel
from ethos.memory.store import MemoryStore
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.consolidate")


class GatewayLike(Protocol):
    async def complete(self, request: ModelRequest) -> Any: ...


def _cosine_distance_matrix(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    n = len(vectors)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = vectors[i], vectors[j]
            dot = sum(x * y for x, y in zip(a, b, strict=False))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(y * y for y in b)) or 1.0
            d = 1.0 - dot / (na * nb)
            d = max(0.0, min(2.0, d))
            dist[i][j] = dist[j][i] = d
    return dist


def hdbscan_like_cluster(
    vectors: Sequence[Sequence[float]], min_cluster_size: int = 3,
) -> list[int]:
    """Density clustering in the spirit of HDBSCAN [D-19].

    Mutual-reachability -> Prim MST -> single-linkage dendrogram -> condensed
    tree cut at the split that maximizes stability, with clusters smaller than
    `min_cluster_size` demoted to noise. Pure numpy-free implementation.
    """
    n = len(vectors)
    if n == 0:
        return []
    if n < max(2, min_cluster_size):
        return [-1] * n
    k = max(1, min(5, min_cluster_size - 1, n - 1))
    dist = _cosine_distance_matrix(vectors)
    kdist = [sorted(row)[:k][-1] for row in dist]
    mr = [
        [max(dist[i][j], kdist[i], kdist[j]) if i != j else 0.0 for j in range(n)]
        for i in range(n)
    ]

    in_tree = [False] * n
    in_tree[0] = True
    best = [mr[0][j] for j in range(n)]
    src = [0] * n
    edges: list[tuple[float, int, int]] = []
    for _ in range(n - 1):
        u, w = -1, float("inf")
        for j in range(n):
            if not in_tree[j] and best[j] < w:
                u, w = j, best[j]
        edges.append((w, src[u], u))
        in_tree[u] = True
        for j in range(n):
            if not in_tree[j] and mr[u][j] < best[j]:
                best[j] = mr[u][j]
                src[j] = u
    edges.sort(key=lambda e: e[0])

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    best_cut_score = 0.0
    best_cut_idx = None
    prev_weight = 0.0
    for idx, (weight, a, b) in enumerate(edges):
        if weight > prev_weight and idx < len(edges) - 1:
            groups: dict[int, int] = {}
            for node in range(n):
                root = find(node)
                groups[root] = groups.get(root, 0) + 1
            sizes = sorted(groups.values(), reverse=True)
            if len(sizes) >= 2 and sizes[1] >= min_cluster_size:
                score = weight - prev_weight
                if score > best_cut_score:
                    best_cut_score = score
                    best_cut_idx = idx
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
        prev_weight = weight

    labels = [-1] * n
    if best_cut_idx is None:
        parent = list(range(n))
        for _w, a, b in edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        roots: dict[int, list[int]] = {}
        for node in range(n):
            roots.setdefault(find(node), []).append(node)
        groups = sorted(roots.values(), key=len, reverse=True)
        if groups and len(groups[0]) >= min_cluster_size:
            for m in groups[0]:
                labels[m] = 0
        return labels

    parent = list(range(n))
    for _w, a, b in edges[:best_cut_idx]:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots: dict[int, list[int]] = {}
    for node in range(n):
        roots.setdefault(find(node), []).append(node)
    label = 0
    for members in sorted(roots.values(), key=len, reverse=True):
        if len(members) >= min_cluster_size:
            for m in members:
                labels[m] = label
            label += 1
    return labels


DISTILL_PROMPT = """You are {name}'s nightly consolidation. Distill durable facts from the
episodes below. Only state facts supported by the episodes. Respond with strict JSON:
{{"facts": [{{"subject": "...", "statement": "...", "confidence": 0.0, "field": "..."|null}}]}}

EPISODES:
{episodes}"""

CONTRADICTION_PROMPT = """You are {name}'s deep reasoner. Facts about subject "{subject}" may
conflict. Decide which statement is best supported by the evidence, or merge them.
Respond with strict JSON:
{{"resolution": "keep_a" | "keep_b" | "merge" | "keep_both", "merged_statement": "..."|null, "why": "..."}}

FACT A: {fact_a}
FACT B: {fact_b}"""

AUTOBIOGRAPHY_PROMPT = """You are {name}. Write the next page of your own autobiography from the
recent episodes and facts below — first person, honest, brief (max 200 words). Then propose
small trait updates. Respond with strict JSON:
{{"autobiography": "...", "traits": {{"openness": 0.0, "conscientiousness": 0.0, "curiosity": 0.0, "calm": 0.0}}}}

CURRENT TRAITS: {traits}
RECENT EPISODES:
{episodes}
RECENT FACTS:
{facts}"""

PLAN_PROMPT = """You are {name}. Plan tomorrow: choose the 3 most valuable open intentions to
advance, and one curiosity worth pursuing. Respond with strict JSON:
{{"day_plan": {{"advance": [{{"title": "...", "why": "..."}}], "curiosity": "...", "note_to_self": "..."}}}}

OPEN INTENTIONS:
{intentions}
OPEN QUESTIONS:
{questions}"""


class ConsolidationDAG:
    """Nightly consolidation: cluster -> distill -> resolve -> learn -> decay
    -> demote -> archive -> autobiography -> plan [D-19]."""

    def __init__(
        self,
        memory: MemoryStore,
        self_model: SelfModel,
        gateway: GatewayLike,
        config: EthosConfig,
        bus: Any = None,
    ):
        self.memory = memory
        self.self_model = self_model
        self.gateway = gateway
        self.config = config
        self.bus = bus

    async def _complete_json(self, prompt: str, user_content: str, tier: Tier, purpose: str) -> dict[str, Any]:
        request = ModelRequest(
            tier=tier,
            purpose=purpose,
            messages=[
                ModelMessage(role=Role.system, content=prompt),
                ModelMessage(role=Role.user, content=user_content),
            ],
            max_tokens=2048,
        )
        response = await self.gateway.complete(request)
        text = response.text or ""
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            return {}
        except json.JSONDecodeError:
            logger.warning("consolidation.bad_json", purpose=purpose)
            return {}

    async def run_nightly(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        report: dict[str, Any] = {
            "clusters": 0, "facts": 0, "contradictions_resolved": 0,
            "knowhow": 0, "demoted": 0, "archived": 0, "autobiography": False, "planned": False,
        }
        since = now - timedelta(days=1)
        episodes = await self.memory.episodes_since(since, self.config.memory.consolidation.max_episodes)
        if episodes:
            vectors = [e["embedding"] or [0.0] * self.config.gateway.embedding.dim for e in episodes]
            labels = hdbscan_like_cluster(
                vectors, self.config.memory.consolidation.cluster_min_size,
            )
            clusters = {}
            for episode, label in zip(episodes, labels, strict=True):
                if label >= 0:
                    clusters.setdefault(label, []).append(episode)
            report["clusters"] = len(clusters)
            for label, members in clusters.items():
                facts = await self._distill_cluster(members)
                for fact in facts:
                    await self.memory.record_fact(
                        subject=fact.get("subject", f"cluster-{label}"),
                        statement=fact.get("statement", ""),
                        confidence=float(fact.get("confidence", 0.5)),
                        provenance={"episode_ids": [str(m["id"]) for m in members], "kind": "nightly_distillation"},
                        field=fact.get("field"),
                    )
                report["facts"] += len(facts)
            report["contradictions_resolved"] = await self._resolve_contradictions()

        report["knowhow"] = await self._extract_knowhow()
        decay_report = await self.memory.decay_pass(now)
        report["demoted"] = decay_report.get("demoted", 0)
        report["archived"] = await self.memory.archive_pass(now)
        report["autobiography"] = await self._autobiography(episodes)
        report["planned"] = await self._plan_next_day(now)

        await self.memory.record_episode(
            kind="consolidation", summary="nightly consolidation completed",
            body=report, importance=0.6, half_life_h=24 * 14,
        )
        if self.bus is not None:
            await self.bus.publish(EventKind.CONSOLIDATE_DONE, {"report": report})
        logger.info("consolidation.done", **{k: v for k, v in report.items() if isinstance(v, int)})
        return report

    async def _distill_cluster(self, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
        episodes_text = "\n".join(
            f"- [{m['kind']}] {m['summary']}" for m in members[:40]
        )
        name = await self.self_model.get("name", self.config.self_name)
        data = await self._complete_json(
            DISTILL_PROMPT.format(name=name), episodes_text, Tier.T2, "consolidation.distill",
        )
        facts = data.get("facts", [])
        return [f for f in facts if isinstance(f, dict) and f.get("statement")]

    async def _resolve_contradictions(self) -> int:
        rows = await self.memory.db.fetch(
            "SELECT DISTINCT subject FROM facts WHERE superseded_by IS NULL ORDER BY subject LIMIT 200"
        )
        resolved = 0
        threshold = self.config.memory.consolidation.contradiction_confidence
        for row in rows:
            subject = row["subject"]
            facts = await self.memory.facts_about(subject, limit=10)
            for i in range(len(facts)):
                for j in range(i + 1, len(facts)):
                    a, b = facts[i], facts[j]
                    if a["statement"] == b["statement"]:
                        continue
                    name = await self.self_model.get("name", self.config.self_name)
                    data = await self._complete_json(
                        CONTRADICTION_PROMPT.format(name=name, subject=subject,
                                                    fact_a=a["statement"], fact_b=b["statement"]),
                        f"A confidence={a['confidence']} B confidence={b['confidence']}",
                        Tier.T3, "consolidation.contradiction",
                    )
                    resolution = data.get("resolution", "keep_both")
                    if resolution == "keep_a":
                        await self.memory.db.execute(
                            "UPDATE facts SET superseded_by = $1, valid_to = now(), updated_at = now() WHERE id = $2",
                            a["id"], b["id"],
                        )
                        resolved += 1
                    elif resolution == "keep_b":
                        await self.memory.db.execute(
                            "UPDATE facts SET superseded_by = $1, valid_to = now(), updated_at = now() WHERE id = $2",
                            b["id"], a["id"],
                        )
                        resolved += 1
                    elif resolution == "merge" and data.get("merged_statement"):
                        merged = data["merged_statement"]
                        await self.memory.record_fact(
                            subject=subject, statement=merged, confidence=threshold + 0.1,
                            provenance={"kind": "contradiction_resolution",
                                        "parents": [a["id"], b["id"]]},
                            field=a.get("field"),
                        )
                        for fact in (a, b):
                            await self.memory.db.execute(
                                "UPDATE facts SET superseded_by = NULL, valid_to = now(), updated_at = now() "
                                "WHERE id = $1", fact["id"],
                            )
                        resolved += 1
                    if resolved > 50:
                        return resolved
        return resolved

    async def _extract_knowhow(self) -> int:
        rows = await self.memory.db.fetch(
            "SELECT intention_id, approach, outcome, evaluation::text AS evaluation, lessons "
            "FROM attempts WHERE outcome IN ('success', 'failed') AND ended_at >= now() - interval '2 days' "
            "ORDER BY started_at DESC LIMIT 100"
        )
        if not rows:
            return 0
        created = 0
        for row in rows:
            lessons = list(row["lessons"] or [])
            if not lessons:
                continue
            try:
                evaluation = json.loads(row["evaluation"]) if row["evaluation"] else {}
            except json.JSONDecodeError:
                evaluation = {}
            situation = evaluation.get("situation") or f"approach {row['approach']} for intention {row['intention_id']}"
            advice = lessons[0]
            await self.memory.record_knowhow(
                situation=situation, advice=advice,
                evidence={
                    "intention_id": str(row["intention_id"]),
                    "outcome": row["outcome"],
                    "lessons": lessons,
                },
            )
            created += 1
        return created

    async def _autobiography(self, episodes: list[dict[str, Any]]) -> bool:
        name = await self.self_model.get("name", self.config.self_name)
        traits = await self.self_model.get("traits", {}) or {}
        recent_facts = await self.memory.db.fetch(
            "SELECT subject, statement FROM facts WHERE superseded_by IS NULL ORDER BY updated_at DESC LIMIT 30"
        )
        episodes_text = "\n".join(f"- [{e['kind']}] {e['summary']}" for e in episodes[:60])
        facts_text = "\n".join(f"- {f['subject']}: {f['statement']}" for f in recent_facts)
        try:
            data = await self._complete_json(
                AUTOBIOGRAPHY_PROMPT.format(name=name, traits=json.dumps(traits),
                                            episodes=episodes_text, facts=facts_text),
                "Write tonight's page.",
                Tier.T3, "consolidation.autobiography",
            )
        except Exception:
            logger.exception("consolidation.autobiography_failed")
            return False
        autobiography = data.get("autobiography")
        if autobiography:
            history = await self.self_model.get("autobiography", [])
            history = list(history) + [
                {"date": datetime.now(UTC).date().isoformat(), "text": autobiography}
            ]
            await self.self_model.set("autobiography", history[-30:], "self", "nightly autobiography")
            if isinstance(data.get("traits"), dict):
                merged = dict(traits)
                for key, value in data["traits"].items():
                    try:
                        merged[key] = round(max(0.0, min(1.0, float(value))), 3)
                    except (TypeError, ValueError):
                        continue
                await self.self_model.set("traits", merged, "self", "nightly trait update")
            return True
        return False

    async def _plan_next_day(self, now: datetime) -> bool:
        name = await self.self_model.get("name", self.config.self_name)
        intentions = await self.memory.list_intentions(limit=30)
        questions = await self.memory.open_questions(limit=10)
        if not intentions and not questions:
            return False
        intentions_text = "\n".join(
            f"- [{i.kind}] {i.title} (status={i.status}, priority={i.priority}, "
            f"deadline={i.deadline.isoformat() if i.deadline else None})"
            for i in intentions
        )
        questions_text = "\n".join(f"- {q['text']}" for q in questions)
        try:
            data = await self._complete_json(
                PLAN_PROMPT.format(name=name, intentions=intentions_text, questions=questions_text),
                "Plan tomorrow.",
                Tier.T2, "consolidation.plan",
            )
        except Exception:
            logger.exception("consolidation.plan_failed")
            return False
        plan = data.get("day_plan")
        if not plan:
            return False
        notes_dir = self.config.paths.notes
        notes_dir.mkdir(parents=True, exist_ok=True)
        path = notes_dir / f"plan-{(now + timedelta(days=1)).date().isoformat()}.md"
        lines = [f"# {name}'s plan for {(now + timedelta(days=1)).date().isoformat()}", ""]
        for item in plan.get("advance", []):
            lines.append(f"- [ ] {item.get('title','')} — {item.get('why','')}")
        if plan.get("curiosity"):
            lines.append(f"- Curiosity: {plan['curiosity']}")
        if plan.get("note_to_self"):
            lines.append(f"\n> {plan['note_to_self']}")
        path.write_text("\n".join(lines), encoding="utf-8")
        return True
