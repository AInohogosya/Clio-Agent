from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ethos.config import MemoryConfig
from ethos.db import Database
from ethos.gateway.embeddings import EmbeddingProvider
from ethos.schemas.memory import MemoryHit

_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", (text or "").lower())


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def bm25_scores(query_terms: list[str], docs: list[list[str]]) -> list[float]:
    """Plain BM25 over the candidate set, returned in corpus order."""
    n_docs = len(docs)
    if n_docs == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n_docs or 1.0
    df: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    scores: list[float] = []
    for doc in docs:
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            if term not in tf or term not in df:
                continue
            idf = math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
            denom = tf[term] * (_K1 + 1)
            norm = tf[term] + _K1 * (1 - _B + _B * len(doc) / avgdl)
            score += idf * denom / norm
        scores.append(score)
    return scores


def normalize_bm25(scores: list[float]) -> list[float]:
    top = max(scores) if scores else 0.0
    if top <= 0:
        return [0.0] * len(scores)
    return [s / top for s in scores]


def recency_component(ts: datetime | None, now: datetime, half_life_h: float) -> float:
    if ts is None:
        return 0.0
    delta_h = max(0.0, (now - ts).total_seconds() / 3600.0)
    return 0.5 ** (delta_h / max(half_life_h, 1e-9))


def score_memory(
    query_vec: Sequence[float],
    embedding: Sequence[float] | None,
    ts: datetime | None,
    importance: float,
    strength: float,
    weights: Any,
    recency_half_life_h: float,
    bm25hat: float,
    now: datetime | None = None,
) -> tuple[float, dict[str, float]]:
    """S(m|q) = 0.40 cos + 0.15 bm25hat + 0.15 recency + 0.20 iota + 0.10 sigma."""
    now = now or datetime.now(UTC)
    cos = cosine(query_vec, embedding) if embedding is not None else 0.0
    rec = recency_component(ts, now, recency_half_life_h)
    components = {
        "cosine": cos,
        "bm25": max(0.0, min(1.0, bm25hat)),
        "recency": rec,
        "importance": max(0.0, min(1.0, importance)),
        "strength": max(0.0, min(1.0, strength)),
    }
    total = (
        weights.cosine * components["cosine"]
        + weights.bm25 * components["bm25"]
        + weights.recency * components["recency"]
        + weights.importance * components["importance"]
        + weights.strength * components["strength"]
    )
    return total, components


def mmr_select(candidates: list[MemoryHit], k: int, lambda_: float = 0.7) -> list[MemoryHit]:
    """Maximal marginal relevance re-ranking (diversity-aware top-k)."""
    selected: list[MemoryHit] = []
    pool = list(candidates)
    while pool and len(selected) < k:
        best: MemoryHit | None = None
        best_value = float("-inf")
        for hit in pool:
            if not selected:
                diversity = 0.0
            else:
                diversity = max(
                    (cosine(hit.embedding or [], s.embedding or []) if hit.embedding and s.embedding else 0.0)
                    for s in selected
                )
            value = lambda_ * hit.score - (1 - lambda_) * diversity
            if value > best_value:
                best_value = value
                best = hit
        if best is None:
            break
        selected.append(best)
        pool.remove(best)
    return selected


def parse_pgvector(text: str | None) -> list[float] | None:
    if not text:
        return None
    try:
        return [float(x) for x in json.loads(text)]
    except (ValueError, TypeError):
        return None


def to_pgvector(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def _as_body(raw: Any) -> dict[str, Any] | None:
    """A memory's body, whatever shape the column turns out to be holding.

    `episodes.body`, `facts.provenance` and `knowhow.evidence` are all declared
    to hold objects, and every writer in the codebase puts an object in. A
    column can still hold a bare JSON string — one seeded episode did, carrying
    a paragraph of text — and the trap is that `json.loads` on a JSON string
    *succeeds*, returning a `str`. So a `try`/`except` around the parse catches
    the unreadable bodies and lets through exactly the one that mattered: a body
    that parsed perfectly and was not a dict. `MemoryHit` then rejected it.

    That was not one bad memory. `retrieve()` runs inside the goal loop on every
    step, so a single offending row anywhere in the top-k raised out of
    `_cycle()` — past the focus-advance at the end of the pass, which then never
    ran. The agent kept the focus it crashed on, re-ran the same failing step
    every cycle, and produced no work and no words for as long as the row
    stayed. It ran 1,524 consecutive cycles that way.

    So the read is total. Anything that is not an object is carried through
    under `raw`, which also keeps it useful: a memory whose body is prose is
    still worth retrieving, because the prose is context. One malformed row is
    worth one slightly odd memory, not the end of the agent's afternoon.
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    parsed: Any = raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {"raw": raw if isinstance(raw, str) else raw.decode("utf-8", "replace")}
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, (list, tuple)):
        return {"items": list(parsed)}
    if isinstance(parsed, (str, int, float, bool)):
        return {"raw": parsed}
    return {"raw": str(parsed)}


class HybridRetriever:
    """Hybrid vector + BM25 + recency + importance + strength retrieval [D-18]."""

    def __init__(self, db: Database, embedder: EmbeddingProvider, cfg: MemoryConfig):
        self.db = db
        self.embedder = embedder
        self.cfg = cfg

    async def _candidates(self, query_vec: list[float], k: int) -> list[MemoryHit]:
        vec = to_pgvector(query_vec)
        hits: list[MemoryHit] = []
        rows = await self.db.fetch(
            "SELECT id::text AS id, kind, summary, body::text AS body, ts, importance, strength, "
            "embedding::text AS emb, 'episode' AS source FROM episodes "
            "WHERE status = 'active' AND embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT $2",
            vec, k,
        )
        hits.extend(self._row_to_hit(r) for r in rows)
        fact_rows = await self.db.fetch(
            "SELECT id::text AS id, 'fact' AS kind, subject || ': ' || statement AS summary, "
            "provenance::text AS body, updated_at AS ts, confidence AS importance, strength, "
            "embedding::text AS emb, 'fact' AS source FROM facts "
            "WHERE superseded_by IS NULL AND embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT $2",
            vec, k,
        )
        hits.extend(self._row_to_hit(r) for r in fact_rows)
        how_rows = await self.db.fetch(
            "SELECT id::text AS id, 'knowhow' AS kind, situation AS summary, evidence::text AS body, "
            "NULL::timestamptz AS ts, CASE WHEN uses > 0 THEN successes::real / uses::real ELSE 0.5 END "
            "AS importance, 0.5::real AS strength, embedding::text AS emb, 'knowhow' AS source "
            "FROM knowhow WHERE retired = false AND embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT $2",
            vec, k,
        )
        hits.extend(self._row_to_hit(r) for r in how_rows)
        question_rows = await self.db.fetch(
            "SELECT id::text AS id, 'question' AS kind, text AS summary, NULL::text AS body, "
            "NULL::timestamptz AS ts, priority AS importance, 0.5::real AS strength, "
            "embedding::text AS emb, 'question' AS source FROM questions "
            "WHERE status = 'open' AND embedding IS NOT NULL ORDER BY embedding <=> $1::vector LIMIT $2",
            vec, k,
        )
        hits.extend(self._row_to_hit(r) for r in question_rows)
        return hits

    @staticmethod
    def _row_to_hit(row: Any) -> MemoryHit:
        return MemoryHit(
            id=row["id"],
            kind=row["kind"] or "episode",
            summary=row["summary"] or "",
            body=_as_body(row["body"]),
            ts=row["ts"],
            importance=float(row["importance"] or 0.0),
            strength=float(row["strength"] or 0.0),
            embedding=parse_pgvector(row["emb"]),
            extra={"source": row["source"]},
        )

    async def retrieve(self, query: str, k: int | None = None, kinds: set[str] | None = None) -> list[MemoryHit]:
        k = k or self.cfg.retrieval.top_k
        candidate_k = max(k * 2, self.cfg.retrieval.candidate_k)
        query_vec = self.embedder.embed([query])[0]
        query_terms = tokenize(query)
        candidates = await self._candidates(query_vec, candidate_k)
        if kinds:
            candidates = [c for c in candidates if c.kind in kinds]
        if not candidates:
            return []
        doc_terms = [tokenize(f"{c.summary} {json.dumps(c.body) if c.body else ''}") for c in candidates]
        raw_bm25 = bm25_scores(query_terms, doc_terms)
        bm25_norm = max(raw_bm25) if raw_bm25 else 1.0
        now = datetime.now(UTC)
        w = self.cfg.retrieval.weights
        for hit, b_raw in zip(candidates, raw_bm25, strict=True):
            score, components = score_memory(
                query_vec, hit.embedding, hit.ts,
                hit.importance, hit.strength, w, self.cfg.retrieval.recency_half_life_h,
                bm25hat=b_raw / bm25_norm if bm25_norm > 0 else 0.0,
                now=now,
            )
            hit.score = score
            hit.components = components
        hits = sorted(candidates, key=lambda h: h.score, reverse=True)
        return mmr_select(hits, k, self.cfg.retrieval.mmr_lambda)
