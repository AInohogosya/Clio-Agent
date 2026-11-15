"""Speed-1 contradiction resolution: corrections land the moment they are said.

The nightly consolidation pass resolves contradictions once a day. That is
fine for the slow accretion of understanding and useless for the case that
actually hurts: a person corrects a fact at 14:00 and the agent acts on the
stale version until 04:00. Speed-1 is the inline path. The moment a fact is
recorded, its semantic neighbors are read — top-5 by cosine, admitted at
>= 0.82 — and a fast Tier-2 check rules on each pair:

- **keep_new** — both facts stand. The new one refines or narrows the old
  without contradicting it ("the office is in Shibuya" / "the office moved
  to Shibuya from Ebisu").
- **merge** — one statement carries both; the old rows are superseded by the
  merged one ("led by Ana" + "led by Ana and Ken" → "led by Ana and Ken").
- **supersede_old** — the new fact replaces the old ("the deadline is
  Friday" over "the deadline is Thursday"); the old row is superseded
  immediately, so the very next retrieval sees one truth.

When no judge is available — a deployment with no model reachable, a unit
test — the resolver falls back to a conservative lexical rule: an explicit
negation or replacement marker ("no longer", "not anymore", "instead",
"actually") supersedes; anything else keeps both. The fallback never merges,
because a wrong merge is a silent rewrite of what somebody said, and
superseding on a marker is checkable against the words themselves.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.memory.contradiction")

#: The admission band: neighbors at or above this cosine are candidates.
NEIGHBOR_MIN_COSINE = 0.82
NEIGHBOR_LIMIT = 5

VERDICTS = ("keep_new", "merge", "supersede_old")

_REPLACEMENT_MARKERS = re.compile(
    r"\b(no longer|not anymore|instead|actually|correction|changed to|"
    r"rather than|moved to|switched to|replaced by)\b",
    re.IGNORECASE,
)


@dataclass
class Neighbor:
    id: str
    subject: str
    statement: str
    cosine: float

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "subject": self.subject,
                "statement": self.statement, "cosine": round(self.cosine, 4)}


@dataclass
class ContradictionVerdict:
    neighbor_id: str
    verdict: str                       # keep_new | merge | supersede_old
    merged_statement: str | None = None
    reason: str = ""
    basis: str = "model"               # model | lexical

    def as_dict(self) -> dict[str, Any]:
        return {"neighbor_id": self.neighbor_id, "verdict": self.verdict,
                "merged_statement": self.merged_statement,
                "reason": self.reason, "basis": self.basis}


Judge = Callable[[dict[str, Any], list[Neighbor]], Awaitable[dict[str, Any]]]


def lexical_verdict(new_statement: str, neighbor: Neighbor) -> ContradictionVerdict:
    """The no-model fallback: supersede on an explicit replacement marker."""
    if _REPLACEMENT_MARKERS.search(new_statement):
        return ContradictionVerdict(
            neighbor_id=neighbor.id, verdict="supersede_old",
            reason="the new statement carries an explicit replacement marker",
            basis="lexical",
        )
    return ContradictionVerdict(
        neighbor_id=neighbor.id, verdict="keep_new",
        reason="no replacement marker; both statements stand until the nightly pass",
        basis="lexical",
    )


def coerce_verdict(raw: dict[str, Any], neighbor: Neighbor) -> ContradictionVerdict:
    """A judge's answer, validated. An unreadable answer keeps both facts."""
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return ContradictionVerdict(
            neighbor_id=neighbor.id, verdict="keep_new",
            reason=f"judge answered {raw.get('verdict')!r}; keeping both",
        )
    merged = raw.get("merged_statement")
    if verdict == "merge" and not str(merged or "").strip():
        # A merge with nothing to merge into is a rewrite with no text; keep
        # both instead of inventing a statement nobody said.
        verdict = "keep_new"
        merged = None
    return ContradictionVerdict(
        neighbor_id=neighbor.id, verdict=verdict,
        merged_statement=str(merged).strip() if merged else None,
        reason=str(raw.get("reason") or ""),
    )


class InlineContradictionResolver:
    """Runs the fast check for one newly recorded fact."""

    def __init__(self, judge: Judge | None = None, *,
                 min_cosine: float = NEIGHBOR_MIN_COSINE,
                 limit: int = NEIGHBOR_LIMIT) -> None:
        self.judge = judge
        self.min_cosine = min_cosine
        self.limit = limit

    async def resolve(self, new_fact: dict[str, Any], neighbors: list[Neighbor]) -> list[ContradictionVerdict]:
        if not neighbors:
            return []
        verdicts: list[ContradictionVerdict] = []
        for neighbor in neighbors:
            if neighbor.cosine < self.min_cosine:
                continue
            if self.judge is None:
                verdicts.append(lexical_verdict(str(new_fact.get("statement") or ""), neighbor))
                continue
            try:
                raw = await self.judge(new_fact, [neighbor])
            except Exception:
                logger.exception("contradiction.judge_failed")
                raw = {}
            verdicts.append(coerce_verdict(raw or {}, neighbor))
        interesting = [v for v in verdicts if v.verdict != "keep_new"]
        if interesting:
            logger.info(
                "contradiction.reconciled", new_fact=new_fact.get("id"),
                verdicts=[v.as_dict() for v in interesting],
            )
        return verdicts


CONTRAST_PROMPT = """A new fact was just recorded. One of its existing semantic
neighbors may contradict it. Decide, for THIS PAIR only:

- keep_new: both statements stand (the new one refines or extends the old).
- merge: one statement can carry both; give it in `merged_statement`.
- supersede_old: the new fact replaces the old (the old is outdated).

Answer JSON only: {"verdict": "keep_new"|"merge"|"supersede_old",
"merged_statement": "..." (merge only, the person's own words where possible),
"reason": "one sentence"}.

Subject: {subject}
Existing: {existing}
New: {new}
"""


async def model_judge(gateway: Any, config: Any) -> Judge:
    """A fast Tier-2 judge over the gateway, shaped for `InlineContradictionResolver`."""
    async def judge(new_fact: dict[str, Any], neighbors: list[Neighbor]) -> dict[str, Any]:
        from clio.gateway.normalizer import parse_json_block
        from clio.schemas.models import ModelMessage, ModelRequest, Role, Tier

        neighbor = neighbors[0]
        request = ModelRequest(
            tier=Tier.T2,
            purpose="memory.contradiction",
            messages=[
                ModelMessage(role=Role.user, content=CONTRAST_PROMPT.format(
                    subject=new_fact.get("subject") or neighbor.subject,
                    existing=neighbor.statement,
                    new=new_fact.get("statement") or "",
                )),
            ],
            max_tokens=300,
        )
        response = await gateway.complete(request)
        return parse_json_block(response.text or "") or {}

    return judge


def build_resolver(gateway: Any = None, config: Any = None) -> InlineContradictionResolver:
    """The resolver a store gets: a model judge when a gateway is present,
    the conservative lexical rule otherwise. Never `None` — a store that
    writes facts without checking them is the behavior this module exists to
    replace."""
    if gateway is not None:
        return InlineContradictionResolver(model_judge(gateway, config))
    return InlineContradictionResolver(judge=None)


__all__ = [
    "CONTRAST_PROMPT",
    "ContradictionVerdict",
    "InlineContradictionResolver",
    "NEIGHBOR_MIN_COSINE",
    "Neighbor",
    "VERDICTS",
    "build_resolver",
    "coerce_verdict",
    "lexical_verdict",
    "model_judge",
]
