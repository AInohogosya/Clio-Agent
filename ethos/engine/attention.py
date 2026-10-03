from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ethos.config import EthosConfig
from ethos.gateway.embeddings import EmbeddingProvider
from ethos.observability.logger import get_logger
from ethos.schemas.events import Appraisal, Percept, ScoredPercept
from ethos.schemas.gsl import Focus
from ethos.schemas.messages import Message, sender_label
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.attention")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

INTERRUPTIBLE_KINDS = {
    "msg.inbound", "timer.fired", "control.update", "guardian.undo", "thread.result",
}


def clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def estimate_salience(
    percept: Percept,
    focus: Focus | None,
    weights,
    sender_weights: dict[str, float],
    embedder: EmbeddingProvider | None,
    focus_vec: list[float] | None,
    novelty_centroid: list[float] | None,
) -> tuple[float, dict[str, float]]:
    """T0 salience estimate: sal = clip(ws*src + wu*urg + wr*rel + wn*nov)."""
    src = sender_weights.get(percept.sender_class, sender_weights.get("unknown", 0.3))
    urg = clip(percept.urgency)
    summary_tokens = _TOKEN_RE.findall(percept.summary.lower())
    if focus_vec is not None and embedder is not None:
        p_vec = embedder.embed([percept.summary or percept.kind])[0]
        rel = clip(sum(a * b for a, b in zip(p_vec, focus_vec, strict=False)))
    else:
        focus_tokens = _TOKEN_RE.findall((focus.title + " " + (focus.why or "")).lower()) if focus else []
        rel = jaccard(summary_tokens, focus_tokens)
    if novelty_centroid is not None and embedder is not None:
        p_vec = embedder.embed([percept.summary or percept.kind])[0]
        nov = clip(1.0 - sum(a * b for a, b in zip(p_vec, novelty_centroid, strict=False)))
    else:
        nov = 0.5
    components = {"src": src, "urg": urg, "rel": rel, "nov": nov}
    sal = clip(
        weights.src * src + weights.urg * urg + weights.rel * rel + weights.nov * nov,
        0.0,
        1.0,
    )
    return sal, components


REFINE_PROMPT = """You are the fast-glance tier (T1) of {name}'s attention. Score this percept
against the current focus. Reply with strict JSON only:
{{"urgency": 0.0, "relevance": 0.0, "novelty": 0.0}}

CURRENT FOCUS: {focus}
PERCEPT [{kind}] from {source}: {summary}"""


class Attention:
    """Salience appraisal with T0 rules + T1 refinement in the [0.3, 0.8] band [D-03]."""

    def __init__(
        self,
        config: EthosConfig,
        embedder: EmbeddingProvider | None = None,
        gateway: Any | None = None,
        self_model: Any | None = None,
    ):
        self.config = config
        self.embedder = embedder
        self.gateway = gateway
        self.self_model = self_model
        self.band = config.rhythm.attention.refine_band
        self.max_refines_per_cycle = 3

    async def refine_salience(self, percept: Percept, focus: Focus | None) -> float | None:
        if self.gateway is None:
            return None
        name = self.config.self_name
        if self.self_model is not None:
            try:
                name = await self.self_model.get("name", self.config.self_name)
            except Exception:
                pass
        request = ModelRequest(
            tier=Tier.T1,
            purpose="attention.refine",
            messages=[
                ModelMessage(role=Role.system, content=REFINE_PROMPT.format(
                    name=name, focus=focus.title if focus else "(none)",
                    kind=percept.kind, source=percept.source or "internal",
                    summary=percept.summary[:400],
                )),
            ],
            max_tokens=120,
        )
        try:
            response = await self.gateway.complete(request)
            text = response.text or ""
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            import json

            data = json.loads(text[start:end + 1])
            w = self.config.rhythm.attention.weights
            sender = self.config.rhythm.attention.sender
            src = sender.get(percept.sender_class, sender.get("unknown", 0.3))
            return clip(
                w.src * src
                + w.urg * clip(float(data.get("urgency", percept.urgency)))
                + w.rel * clip(float(data.get("relevance", 0.3)))
                + w.nov * clip(float(data.get("novelty", 0.5)))
            )
        except Exception:
            logger.exception("attention.refine_failed")
            return None

    @staticmethod
    def dedupe(percepts: list[Percept], window_s: float = 10.0) -> list[Percept]:
        seen: dict[tuple[str, str | None], datetime] = {}
        out: list[Percept] = []
        for p in percepts:
            key = (p.kind, p.source)
            last = seen.get(key)
            if last is not None and (p.ts - last).total_seconds() < window_s:
                continue
            seen[key] = p.ts
            out.append(p)
        return out

    async def appraise(
        self,
        percepts: list[Percept],
        focus: Focus | None,
        recent_centroid: list[float] | None = None,
        thresholds_by_state: dict[str, float] | None = None,
        state_name: str | None = None,
    ) -> Appraisal:
        percepts = self.dedupe(percepts)
        if not percepts:
            return Appraisal()
        cfg_att = self.config.rhythm.attention
        focus_vec = None
        if self.embedder is not None and focus is not None:
            focus_vec = self.embedder.embed([f"{focus.title} {focus.why}"])[0]
        scored: list[ScoredPercept] = []
        for p in percepts:
            sal, components = estimate_salience(
                p, focus, cfg_att.weights, cfg_att.sender, self.embedder, focus_vec, recent_centroid,
            )
            p.salience = sal
            scored.append(ScoredPercept(percept=p, salience=sal, components=components))
        scored.sort(key=lambda s: s.salience, reverse=True)

        thresholds = thresholds_by_state or {}
        threshold = thresholds.get(state_name or "", thresholds.get("default", 0.65))

        refined = 0
        for scored_percept in scored:
            if refined >= self.max_refines_per_cycle:
                break
            lo, hi = self.band
            if lo <= scored_percept.salience <= hi:
                new_sal = await self.refine_salience(scored_percept.percept, focus)
                if new_sal is not None:
                    scored_percept.salience = new_sal
                    scored_percept.percept.salience = new_sal
                    refined += 1
        if refined:
            scored.sort(key=lambda s: s.salience, reverse=True)

        top = scored[0]
        interrupt = (
            top.salience >= threshold
            and top.percept.kind in INTERRUPTIBLE_KINDS
        )
        appraisal = Appraisal(
            interrupt=interrupt,
            new_focus=None,
            top=scored,
            refined=refined > 0,
        )
        if interrupt:
            appraisal.new_focus = focus_from_percept(top.percept)
        return appraisal

    @staticmethod
    def percept_from_message(message: Message) -> Percept:
        known = message.person_id is not None
        return Percept(
            kind="msg.inbound",
            source=f"{message.channel}:{message.person_id or 'unknown'}",
            payload={
                "message_id": str(message.id),
                "text": message.text[:2000],
                # The same two keys `percept_from_event` leaves in the payload for
                # the bus path, so `focus_from_percept` reads a sender the same way
                # whichever of the two produced this percept. A focus titled
                # "Respond to tg:819012345678" and one titled "Respond to alice" are
                # the same moment, and only one of them tells the agent who it is
                # about to talk to.
                "sender_name": message.sender_name,
                "sender_username": message.sender_username,
            },
            sender_class="known" if known else "unknown",
            sender_person=message.person_id,
            urgency=0.3 if known else 0.2,
            summary=f"Message from {message.sender_who()}: {message.text[:200]}",
            trust="known" if known else "untrusted",
            is_untrusted=not known,
        )


def percept_sender(percept: Percept) -> str:
    """Whoever a percept came from, named if the channel said so.

    `sender_person` alone is an address — `tg:819012345678` — and it stays that
    because that is what a reply is sent to. It is a poor title for the thing the
    agent is about to attend to, though, so the name the adapters now carry is
    read out of the payload and put first.
    """
    return sender_label(
        percept.payload.get("sender_name"),
        percept.payload.get("sender_username"),
        percept.sender_person,
    )


def focus_from_percept(percept: Percept) -> Focus:
    if percept.kind == "msg.inbound":
        return Focus(
            title=f"Respond to {percept_sender(percept)}",
            kind="social",
            why=f"inbound message on {percept.source}",
            bookmark={"percept": percept.model_dump(mode="json")},
        )
    if percept.kind == "timer.fired":
        return Focus(
            title="Handle timer",
            kind="timer",
            why=percept.summary,
            bookmark={"percept": percept.model_dump(mode="json")},
        )
    if percept.kind == "thread.result":
        return Focus(
            title="Integrate thread result",
            kind="thread_result",
            why=percept.summary,
            bookmark={"percept": percept.model_dump(mode="json")},
        )
    return Focus(
        title=percept.summary[:80] or "Unhandled percept",
        kind="event",
        why=f"percept {percept.kind} from {percept.source}",
        bookmark={"percept": percept.model_dump(mode="json")},
    )


def centroid_of(vectors: list[list[float]] | None) -> list[float] | None:
    if not vectors:
        return None
    dim = len(vectors[0])
    centroid = [0.0] * dim
    for vec in vectors:
        for i, x in enumerate(vec):
            centroid[i] += x
    norm = math.sqrt(sum(x * x for x in centroid)) or 1.0
    return [x / norm for x in centroid]
