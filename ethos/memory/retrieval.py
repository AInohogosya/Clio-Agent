from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

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
    *,
    cosine_value: float | None = None,
) -> tuple[float, dict[str, float]]:
    """S(m|q) = 0.40 cos + 0.15 bm25hat + 0.15 recency + 0.20 iota + 0.10 sigma.

    `cosine_value` is the escape hatch for the vectorized caller. The retriever
    already has every candidate's similarity as one row of a dot product, and
    making it hand each row back as a Python `list[float]` to be multiplied out
    again is the allocation this whole path is trying to stop making. Passing the
    number is exact, not an approximation: it is the same quotient of the same
    dot product and the same two norms.
    """
    now = now or datetime.now(UTC)
    cos = cosine_value if cosine_value is not None else (
        cosine(query_vec, embedding) if embedding is not None else 0.0
    )
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


def mmr_select(
    candidates: list[MemoryHit],
    k: int,
    lambda_: float = 0.7,
    vectors: np.ndarray | None = None,
    rows: Sequence[int] | None = None,
) -> list[MemoryHit]:
    """Maximal marginal relevance re-ranking (diversity-aware top-k).

    `vectors` is the candidate set's embedding matrix and `rows[i]` is the row
    belonging to `candidates[i]`. The retriever ranks before it re-ranks, so the
    two arrive in different orders and an index that silently assumed they agreed
    would compare every candidate against the wrong memory — which produces a
    plausible-looking, slightly wrong answer rather than an error.

    With neither argument this reads `hit.embedding`, exactly as before, which is
    what every other caller of this function has.

    The selection runs on positions rather than on the objects, so two memories
    that compare equal in every field pydantic looks at are still two candidates.
    """
    selected: list[MemoryHit] = []
    selected_rows: list[np.ndarray] = []
    pool = list(range(len(candidates)))

    def row_of(index: int) -> np.ndarray | None:
        if vectors is None:
            return None
        return vectors[index if rows is None else rows[index]]

    while pool and len(selected) < k:
        # The selected set is fixed for the whole sweep, so it is stacked once per
        # pick rather than once per candidate. Stacking inside the inner loop would
        # replace the allocation this function exists to remove with a slightly
        # different one of the same size.
        if selected_rows:
            stack = np.vstack(selected_rows)
            stack_norms = np.linalg.norm(stack, axis=1)
        else:
            stack = None
            stack_norms = None

        best_index = -1
        best_value = float("-inf")
        for index in pool:
            hit = candidates[index]
            if stack is None:
                diversity = 0.0
            elif vectors is None:
                diversity = max(
                    (cosine(hit.embedding or [], s.embedding or []) if hit.embedding and s.embedding else 0.0)
                    for s in selected
                )
            else:
                row = row_of(index)
                denom = stack_norms * np.linalg.norm(row)
                sims = np.divide(
                    stack @ row, denom,
                    out=np.zeros(stack.shape[0], dtype=np.float64),
                    where=denom > 0,
                )
                diversity = float(np.max(sims))
            value = lambda_ * hit.score - (1 - lambda_) * diversity
            if value > best_value:
                best_value = value
                best_index = index
        if best_index < 0:
            break
        selected.append(candidates[best_index])
        if vectors is not None:
            selected_rows.append(row_of(best_index))
        pool.remove(best_index)
    return selected


def parse_pgvector(text: str | None) -> list[float] | None:
    if not text:
        return None
    try:
        return [float(x) for x in json.loads(text)]
    except (ValueError, TypeError):
        return None


def parse_pgvector_row(text: str | None) -> np.ndarray | None:
    """One pgvector literal as a row, without materializing a list of floats.

    `[0.1,0.2,...]` is not the bracketed form `np.fromstring` accepts, so the
    brackets come off and the remainder is parsed as the comma-separated numbers
    it is. A row that will not parse is None and the caller substitutes zeros,
    which score 0.0 against everything — the same answer `cosine` gives for a
    missing vector.
    """
    if not text:
        return None
    try:
        row = np.fromstring(text.strip()[1:-1], dtype=np.float64, sep=",")
    except (ValueError, TypeError):
        return None
    return row if row.size else None


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


@dataclass
class _Candidates:
    """One retrieval's candidate set and the vectors that scored it.

    `vectors` is a single `(len(hits), dim)` float64 buffer, row-aligned with
    `hits`. It exists so the candidate set is not a few hundred separate Python
    lists: at 1024 dimensions a `list[float]` is ~32 KB of pointers *and* boxed
    floats, so `max(k*2, 48)` candidates from four tables was several megabytes
    of transient garbage on every call, and `retrieve` runs inside the goal loop.
    One buffer is the same numbers in a few hundred KB of contiguous memory, and
    the dot products and norms it is there for are one BLAS call each instead of
    a Python generator.

    `hits[i].embedding` is deliberately left unset. Nothing outside this module
    reads it, and `retrieve` fills it in for the hits it actually returns.
    """

    hits: list[MemoryHit]
    vectors: np.ndarray
    cosines: np.ndarray

    def __len__(self) -> int:
        return len(self.hits)


class HybridRetriever:
    """Hybrid vector + BM25 + recency + importance + strength retrieval [D-18]."""

    # The four searchable stores, keyed by the `MemoryHit.kind` they produce.
    # `episodes` is spelled `episode` on the row because that is the schema's
    # word for it, and the dict exists so `kinds` can decide which tables to ask
    # at all rather than asking four and throwing three away.
    _QUERIES: dict[str, str] = {
        "episode": (
            "SELECT id::text AS id, kind, summary, body::text AS body, ts, importance, strength, "
            "embedding::text AS emb, 'episode' AS source FROM episodes "
            "WHERE status = 'active' AND embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector LIMIT $2"
        ),
        "fact": (
            "SELECT id::text AS id, 'fact' AS kind, subject || ': ' || statement AS summary, "
            "provenance::text AS body, updated_at AS ts, confidence AS importance, strength, "
            "embedding::text AS emb, 'fact' AS source FROM facts "
            "WHERE superseded_by IS NULL AND embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector LIMIT $2"
        ),
        "knowhow": (
            "SELECT id::text AS id, 'knowhow' AS kind, situation AS summary, evidence::text AS body, "
            "NULL::timestamptz AS ts, "
            "CASE WHEN uses > 0 THEN successes::real / uses::real ELSE 0.5 END AS importance, "
            "0.5::real AS strength, embedding::text AS emb, 'knowhow' AS source "
            "FROM knowhow WHERE retired = false AND embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector LIMIT $2"
        ),
        "question": (
            "SELECT id::text AS id, 'question' AS kind, text AS summary, NULL::text AS body, "
            "NULL::timestamptz AS ts, priority AS importance, 0.5::real AS strength, "
            "embedding::text AS emb, 'question' AS source FROM questions "
            "WHERE status = 'open' AND embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector LIMIT $2"
        ),
    }

    def __init__(self, db: Database, embedder: EmbeddingProvider, cfg: MemoryConfig):
        self.db = db
        self.embedder = embedder
        self.cfg = cfg

    async def _candidates(
        self, query_vec: list[float], k: int, kinds: set[str] | None = None
    ) -> _Candidates:
        vec = to_pgvector(query_vec)
        wanted = self._QUERIES if not kinds else {
            kind: sql for kind, sql in self._QUERIES.items() if kind in kinds
        }
        hits: list[MemoryHit] = []
        raw: list[str] = []
        for sql in wanted.values():
            rows = await self.db.fetch(sql, vec, k)
            for row in rows:
                hits.append(self._row_to_hit(row, with_embedding=False))
                raw.append(row["emb"] or "")
        return self._pack(hits, raw, query_vec)

    @staticmethod
    def _pack(hits: list[MemoryHit], raw: list[str], query_vec: list[float]) -> _Candidates:
        """Stack the candidate vectors and take the cosine of each against the query."""
        empty = np.zeros((0, 1), dtype=np.float64)
        if not hits:
            return _Candidates(hits, empty, np.zeros(0, dtype=np.float64))
        dim = len(query_vec)
        vectors = np.zeros((len(hits), dim), dtype=np.float64)
        for i, text in enumerate(raw):
            row = parse_pgvector_row(text)
            if row is not None and row.size == dim:
                vectors[i] = row
        norms = np.linalg.norm(vectors, axis=1)
        qnorm = float(np.linalg.norm(np.asarray(query_vec, dtype=np.float64))) or 1.0
        denom = norms * qnorm
        cosines = np.divide(
            vectors @ np.asarray(query_vec, dtype=np.float64),
            denom,
            out=np.zeros(len(hits), dtype=np.float64),
            where=denom > 0,
        )
        return _Candidates(hits, vectors, cosines)

    @staticmethod
    def _row_to_hit(row: Any, with_embedding: bool = True) -> MemoryHit:
        return MemoryHit(
            id=row["id"],
            kind=row["kind"] or "episode",
            summary=row["summary"] or "",
            body=_as_body(row["body"]),
            ts=row["ts"],
            importance=float(row["importance"] or 0.0),
            strength=float(row["strength"] or 0.0),
            embedding=parse_pgvector(row["emb"]) if with_embedding else None,
            extra={"source": row["source"]},
        )

    async def retrieve(self, query: str, k: int | None = None, kinds: set[str] | None = None) -> list[MemoryHit]:
        k = k or self.cfg.retrieval.top_k
        # `k * 2` is a widening to give MMR and BM25 something to choose from, and
        # it was the only bound: a caller asking for 2,000 hits pulled 4,000 rows
        # from each of four tables, 16,000 vectors, to select 2,000. The ceiling
        # is `max(…, k)` so it can never starve the caller below the k they asked
        # for — it bounds the widening, not the request.
        candidate_k = min(
            max(k * 2, self.cfg.retrieval.candidate_k),
            max(self.cfg.retrieval.max_candidate_k, k),
        )
        query_vec = self.embedder.embed([query])[0]
        query_terms = tokenize(query)
        # `kinds` narrows the SQL rather than the result: `search_facts` used to
        # ask all four tables for 48 rows each and keep the ones that said
        # `fact`, which is three quarters of the transfer and the parse thrown
        # away on every call. Asking only the table that can answer gives the
        # same list.
        packed = await self._candidates(query_vec, candidate_k, kinds)
        candidates = packed.hits
        if not candidates:
            return []
        doc_terms = [tokenize(f"{c.summary} {json.dumps(c.body) if c.body else ''}") for c in candidates]
        raw_bm25 = bm25_scores(query_terms, doc_terms)
        bm25_norm = max(raw_bm25) if raw_bm25 else 1.0
        now = datetime.now(UTC)
        w = self.cfg.retrieval.weights
        for i, (hit, b_raw) in enumerate(zip(candidates, raw_bm25, strict=True)):
            score, components = score_memory(
                query_vec, None, hit.ts,
                hit.importance, hit.strength, w, self.cfg.retrieval.recency_half_life_h,
                bm25hat=b_raw / bm25_norm if bm25_norm > 0 else 0.0,
                now=now,
                cosine_value=float(packed.cosines[i]),
            )
            hit.score = score
            hit.components = components
        # `sorted` reorders the candidates, so the permutation that ranked them is
        # handed to `mmr_select` alongside the matrix. Without it the diversity
        # term would read row *n* of the buffer for whatever candidate landed in
        # position *n* — a re-ranking that is wrong by a shift and looks entirely
        # reasonable doing it.
        order = sorted(range(len(candidates)), key=lambda i: candidates[i].score, reverse=True)
        ranked = [candidates[i] for i in order]
        selected = mmr_select(ranked, k, self.cfg.retrieval.mmr_lambda,
                              vectors=packed.vectors, rows=order)
        # The buffer is dropped here and the vectors the caller gets are lists
        # again — for `k` rows rather than for the whole candidate set, which is
        # the only reason to pay for them at all. Keyed on identity rather than
        # looked up: two memories can be equal in every field pydantic compares,
        # and `list.index` would then find the wrong one.
        row_of = {id(hit): i for i, hit in enumerate(candidates)}
        for hit in selected:
            hit.embedding = packed.vectors[row_of[id(hit)]].tolist()
        return selected
