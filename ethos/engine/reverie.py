from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ethos.config import EthosConfig
from ethos.engine.attention import jaccard
from ethos.memory.retrieval import tokenize
from ethos.observability.logger import get_logger
from ethos.prompts.reverie import reverie_messages
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier
from ethos.schemas.reverie import ReverieKind, ReverieResult, ReverieSubject

logger = get_logger("ethos.reverie")


def _parse_json(text: str) -> dict[str, Any]:
    import json

    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


class Reverie:
    """An idle mind choosing its own subject, and keeping it [D-19].

    The life loop has always run when nobody is talking to it, but it could only
    ever spend an idle mind on what it was already on the hook for: a focus
    re-derived from stored intentions, or a glance across a few random memories.
    An agent with no open commitments and no questions therefore had nothing to
    do but wait, which is the difference between an idle agent and a stopped
    one — it was not thinking, it was only not answering.

    This is the path that lets it start something of its own. Deliberating is
    half of it. The other half is that the chosen subject has to land somewhere
    durable, because a thought held only in the process's `_thoughts` list is
    gone at the next restart, and an agent that invents an interest every few
    minutes and cannot remember any of them is not living, it is twitching.
    """

    def __init__(
        self,
        config: EthosConfig,
        gateway: Any = None,
        memory: Any = None,
        self_model: Any = None,
        audit: Any = None,
    ):
        self.config = config
        self.gateway = gateway
        self.memory = memory
        self.self_model = self_model
        self.audit = audit

    async def muse(
        self,
        recent_thoughts: list[str] | None = None,
        energy: float = 0.8,
    ) -> ReverieResult:
        cfg = self.config.rhythm.reverie
        if self.gateway is None:
            return ReverieResult(refused="no gateway")
        name = self.config.self_name
        if self.self_model is not None:
            try:
                name = await self.self_model.get("name", self.config.self_name)
            except Exception:
                pass

        memories = await self._material("random_memories", cfg.sample_memories)
        questions = [str(q.get("text", "")) for q in await self._questions(3)]
        recent = [t for t in (recent_thoughts or []) if t.strip()]

        messages = reverie_messages(
            self_name=name,
            memories=memories,
            questions=questions,
            recent=recent,
            now=datetime.now(UTC).isoformat(),
            energy=energy,
        )
        request = ModelRequest(
            tier=Tier.T1,
            purpose="reverie.choose",
            budget_bucket="discretionary",
            consequence_class="routine",
            messages=[
                ModelMessage(role=Role.system, content=messages[0]["content"]),
                ModelMessage(role=Role.user, content=messages[1]["content"]),
            ],
            max_tokens=cfg.max_tokens,
        )
        try:
            response = await self.gateway.complete(request)
            text = response.text or ""
            cost = float(getattr(response, "cost_usd", 0.0) or 0.0)
        except Exception:
            logger.exception("reverie.choose_failed")
            return ReverieResult(refused="deliberation failed")

        subject = self._subject_from(text)
        result = ReverieResult(subject=subject, cost_usd=cost)
        if not subject.is_something:
            result.refused = "not drawn to anything"
            result.thought = f"reverie: nothing in particular ({subject.raw.get('why', '').strip()[:120]})"
            return result
        if subject.interest < cfg.min_interest:
            result.refused = f"interest {subject.interest:.2f} below floor"
            result.thought = f"reverie: passed on {subject.subject[:80]}"
            return result
        repeat = await self._repeat_of(subject.subject)
        if repeat is not None:
            result.refused = f"same thought as {repeat!r}"
            result.thought = f"reverie: {subject.subject[:80]} — already had that one"
            return result

        await self._commit(result, subject)
        if self.audit is not None and result.committed_id is not None:
            await self.audit.append(
                actor="self", category="reverie",
                summary=f"picked up {subject.subject[:80]}",
                details={
                    "kind": result.committed_kind,
                    "why": subject.why[:300],
                    "interest": subject.interest,
                    "id": str(result.committed_id),
                },
            )
        return result

    # --------------------------------------------------------- interpreting

    def _subject_from(self, text: str) -> ReverieSubject:
        """The model's answer, taken as advice rather than as instruction.

        A model that names an interest also names one it has no business having
        — a private key, a person's address, a task nobody asked for. So every
        field is clipped and nothing here trusts the shape it was handed, and an
        answer that cannot be read is treated exactly like an answer of "none":
        an idle mind that said nothing intelligible has not found a subject.
        """
        data = _parse_json(text)
        if not data:
            return ReverieSubject()
        try:
            kind = ReverieKind(str(data.get("kind", "none")).strip().lower())
        except ValueError:
            return ReverieSubject(raw=data)
        subject = str(data.get("subject", "") or "").strip()
        if kind is ReverieKind.none:
            return ReverieSubject(kind=kind, raw=data)
        if not subject:
            return ReverieSubject(raw=data)
        return ReverieSubject(
            kind=kind,
            subject=subject[:200],
            why=str(data.get("why", "") or "").strip()[:400],
            first_step=str(data.get("first_step", "") or "").strip()[:400],
            interest=_clip(data.get("interest")),
            raw=data,
        )

    # ------------------------------------------------------------ durability

    async def _commit(self, result: ReverieResult, subject: ReverieSubject) -> None:
        """Put the subject somewhere it can still be found tomorrow.

        A venture and a question are committed differently on purpose, because
        the drive system reads them differently: `creation_gain` counts open
        ventures and `curiosity_gain` counts open questions, so a subject filed
        under the right one raises the very pressure that will bring the agent
        back to it. Filing everything as a venture would have made curiosity
        unreachable, and the agent would never have wondered about anything.
        """
        if self.memory is None:
            result.refused = "no memory store"
            return
        if subject.kind is ReverieKind.question:
            try:
                result.committed_id = await self.memory.open_question(
                    subject.subject, priority=max(0.3, subject.interest),
                )
                result.committed_kind = "question"
            except Exception:
                logger.exception("reverie.question_commit_failed")
                result.refused = "could not open the question"
            return
        desired = subject.first_step or subject.why or subject.subject
        try:
            result.committed_id = await self.memory.create_intention(
                title=subject.subject[:200],
                desired_end_state=desired[:400],
                kind="venture",
                origin="reverie",
                priority=max(0.3, subject.interest),
            )
            result.committed_kind = "venture"
        except Exception:
            logger.exception("reverie.venture_commit_failed")
            result.refused = "could not open the venture"
            return
        result.thought = f"reverie: picked up {subject.subject} — {subject.why or desired}"

    async def _repeat_of(self, subject: str) -> str | None:
        """The earlier subject this one is too close to being a repeat of.

        Checked against what the agent actually took on, and only against what it
        chose for itself: a venture it was commissioned to pursue may well share
        its subject, and that is not the agent going round in circles. Threshold
        is token overlap, which is crude, but it fails toward repeating the
        subject — and a repeated subject is a cheaper mistake than a new one that
        quietly reopens work the agent already abandoned.
        """
        if self.memory is None:
            return None
        window = self.config.rhythm.reverie.recent_window
        try:
            rows = await self.memory.db.fetch(
                "SELECT title FROM intentions WHERE origin = 'reverie' "
                "ORDER BY created_at DESC LIMIT $1", window,
            )
        except Exception:
            logger.warning("reverie.repeat_check_failed")
            return None
        tokens = tokenize(subject)
        if not tokens:
            return None
        threshold = self.config.rhythm.reverie.repeat_threshold
        for row in rows:
            previous = str(row["title"] or "")
            if not previous:
                continue
            if jaccard(tokens, tokenize(previous)) >= threshold:
                return previous[:80]
        return None

    # -------------------------------------------------------------- material

    async def _material(self, method: str, n: int) -> list[str]:
        if self.memory is None:
            return []
        try:
            hits = await getattr(self.memory, method)(n)
        except Exception:
            logger.warning("reverie.material_unavailable", method=method)
            return []
        out: list[str] = []
        for hit in hits:
            summary = getattr(hit, "summary", None) or str(hit)
            text = " ".join(str(summary).split())
            if text:
                out.append(text[:240])
        return out

    async def _questions(self, n: int) -> list[dict[str, Any]]:
        if self.memory is None:
            return []
        try:
            return await self.memory.open_questions(limit=n)
        except Exception:
            logger.warning("reverie.questions_unavailable")
            return []


def _clip(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
