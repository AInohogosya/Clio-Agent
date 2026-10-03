from __future__ import annotations

import json
from typing import Any

from ethos.config import EthosConfig
from ethos.observability.logger import get_logger
from ethos.prompts.context import narrate_messages
from ethos.schemas.context import (
    ContextSnapshot,
    ContextTurn,
    NarrativeLine,
    TurnKind,
)
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.memory.context")

# How a turn is written for the model to read. Short, present, past-tense-free:
# these lines are read by the agent about itself, so they read like a log of its
# own awareness rather than like a stack trace.
_KIND_LABEL: dict[str, str] = {
    TurnKind.inbound.value: "said to me",
    TurnKind.outbound.value: "I said",
    TurnKind.action.value: "did",
    TurnKind.result.value: "got back",
    TurnKind.failure.value: "failed",
    TurnKind.thought.value: "noted",
    TurnKind.decision.value: "decided",
    TurnKind.event.value: "happened",
}

# Turns whose detail is worth re-reading verbatim rather than as a one-line
# summary: what a person actually said, and what actually went wrong.
_DETAIL_KINDS: frozenset[str] = frozenset({
    TurnKind.inbound.value, TurnKind.outbound.value, TurnKind.failure.value,
})

_SUMMARY_CAP = 300

# Stands in whenever the narrator is unavailable or unhelpful. It is deliberately
# dull: the job is to keep the turns accounted for, and a line that admits it is
# only a tally is more honest than one that invents significance.
_MECHANICAL_LINE = "{n} turns: {kinds}"

# Share of the character budget given to the compressed thread rather than the raw
# buffer. The thread is what makes context a thread rather than a queue, so it
# gets the larger share despite holding far less text.
_THREAD_BUDGET_SHARE = 0.6


class ContextLayer:
    """The context the agent carries from one cycle into the next.

    Durable memory answers "what do I know about X" by retrieval, which is the
    wrong shape for "what was I just doing". Retrieval over a stream that is still
    arriving returns whatever happened to score well, so the thread gets dropped
    exactly when it is most recent and most load-bearing — and the cost is paid
    every cycle to assemble a block that, as shipped, was never sent.

    So this keeps the thread the way it is actually lived, in three tiers:

    1. A **working buffer** of the turns that just happened — what tools ran,
       what came back, what a person said and whether it was answered, what
       failed. Appended, never recomputed, so it survives from one cycle to the
       next by construction.
    2. A **narrative thread** the agent writes about itself. When the buffer
       fills, the oldest stretch is handed to a cheap model that distills it into
       one line in the agent's own voice, and that line is what is kept. What
       rolls off the buffer is a detail; the meaning of it survives.
    3. The retriever, left alone — this layer feeds it (narrated stretches become
       episodes) rather than replacing it.

    Narration failing is not allowed to lose a turn: the mechanical rollup below
    stands in. A context system that forgets whenever the model is unavailable
    is worse than no context system, because the failure looks like the agent
    having no mind.
    """

    def __init__(
        self,
        config: EthosConfig,
        memory: Any = None,
        gateway: Any = None,
        thread_id: str = "self",
    ):
        self.config = config
        self.cfg = config.memory.context
        self.memory = memory
        self.gateway = gateway
        self.thread_id = thread_id
        self.turns: list[ContextTurn] = []
        self.narrative: list[NarrativeLine] = []
        self.open_loops: list[str] = []
        self.cost_usd = 0.0
        # Narration is a write to the agent's own mind, so it must not race
        # itself: a slow roll triggered by an append would interleave with one
        # triggered by a focus change and interleave the lines.
        self._narrating = False

    # ------------------------------------------------------------- writing

    def default_salience(self, kind: str) -> float:
        return {
            TurnKind.inbound.value: self.cfg.social_salience,
            TurnKind.outbound.value: self.cfg.social_salience,
            TurnKind.failure.value: self.cfg.failure_salience,
            TurnKind.action.value: self.cfg.action_salience,
            TurnKind.result.value: self.cfg.action_salience * 0.8,
            TurnKind.thought.value: self.cfg.thought_salience,
            TurnKind.event.value: self.cfg.event_salience,
            TurnKind.decision.value: self.cfg.action_salience,
        }.get(kind, 0.5)

    async def append(
        self,
        kind: str,
        summary: str,
        *,
        detail: dict[str, Any] | None = None,
        salience: float | None = None,
        intention_id: Any = None,
        conversation_key: str | None = None,
        focus: str | None = None,
    ) -> ContextTurn | None:
        """Record one turn. Rolls the buffer when it fills."""
        summary = " ".join(str(summary).split())[:_SUMMARY_CAP]
        if not summary:
            return None
        turn = ContextTurn(
            kind=kind,
            summary=summary,
            detail=detail or {},
            salience=self.default_salience(kind) if salience is None else float(salience),
            intention_id=str(intention_id) if intention_id is not None else None,
            conversation_key=conversation_key,
        )
        self.turns.append(turn)
        await self._enforce_capacity(focus)
        return turn

    async def note_actions(
        self,
        actions: list[dict[str, Any]],
        *,
        intention_id: Any = None,
        focus: str | None = None,
    ) -> None:
        """Record what the agent did and what came back.

        This is the loop closing on itself. The substance of a step — which tool
        ran, what it returned, which call failed — used to be written only into
        the intention's `resume_state` blob, where nothing reads it and no
        retrieval can reach it. A step that failed twice was invisible to the
        agent's own context at the moment it needed to know.
        """
        for action in actions:
            if not isinstance(action, dict) or not action.get("tool"):
                continue
            tool = str(action["tool"])
            ok = bool(action.get("ok"))
            result = action.get("result")
            text = _clip(_stringify(result), self.cfg.detail_chars)
            if ok:
                turn = await self.append(
                    TurnKind.result.value,
                    f"{tool} -> {_clip(_stringify(result), 120)}",
                    detail={"tool": tool, "args": action.get("args") or {}, "result": result},
                    intention_id=intention_id,
                )
                # A result substantial enough to have been worth reading is
                # worth carrying forward too, however routine the call was.
                if turn is not None and len(text) > 80:
                    turn.salience = max(turn.salience, 0.6)
            else:
                await self.append(
                    TurnKind.failure.value,
                    f"{tool} failed: {_clip(_stringify(result), 120)}",
                    detail={"tool": tool, "args": action.get("args") or {}, "error": result},
                    intention_id=intention_id,
                )

    async def _enforce_capacity(self, focus: str | None = None) -> None:
        if len(self.turns) > self.cfg.max_turns:
            await self.roll(focus)

    async def roll(self, focus: str | None = None) -> NarrativeLine | None:
        """Turn the oldest stretch of buffer into a line of narrative.

        Guarded against reentry: `append` can reach this from inside a cycle
        while a focus-change roll is already in flight, and two interleaved
        rolls would interleave their lines and drop turns from the middle.
        """
        if self._narrating:
            return None
        if len(self.turns) < self.cfg.narrate_every:
            return None
        self._narrating = True
        try:
            batch = self.turns[: self.cfg.narrate_every]
            del self.turns[: self.cfg.narrate_every]
            line = await self._narrate(batch, focus)
            self.narrative.append(line)
            if len(self.narrative) > self.cfg.narrative_max_lines:
                # A line leaves the thread, so it goes to the retriever. Not
                # before: while it is on the thread it is already in front of
                # the agent every cycle, and writing it to memory too would put
                # the same line in two places and let the two disagree.
                await self._remember_narrative(self.narrative.pop(0))
            return line
        except Exception:
            logger.exception("context.roll_failed")
            return None
        finally:
            self._narrating = False

    async def _narrate(self, batch: list[ContextTurn], focus: str | None) -> NarrativeLine:
        kinds = sorted({t.kind for t in batch})
        rendered = [_render_for_narration(t) for t in batch]
        line = await self._ask_narrator(rendered, focus)
        if not line:
            line = _MECHANICAL_LINE.format(n=len(batch), kinds=", ".join(kinds))
        return NarrativeLine(
            text=line,
            turn_count=len(batch),
            kinds=kinds,
        )

    async def _ask_narrator(
        self, rendered: list[str], focus: str | None
    ) -> str | None:
        if self.gateway is None:
            return None
        try:
            tier = Tier(self.cfg.narration_tier)
        except ValueError:
            tier = Tier.T1
        name = self.config.self_name
        messages = narrate_messages(name, rendered, focus)
        try:
            response = await self.gateway.complete(ModelRequest(
                tier=tier,
                purpose="context.narrate",
                budget_bucket="discretionary",
                messages=[
                    ModelMessage(role=Role.system, content=messages[0]["content"]),
                    ModelMessage(role=Role.user, content=messages[1]["content"]),
                ],
                max_tokens=300,
            ))
        except Exception:
            logger.warning("context.narrate_call_failed")
            return None
        self.cost_usd += float(response.cost_usd or 0.0)
        data = _parse_json(response.text or "")
        text = str(data.get("line") or "").strip()
        if not text:
            return None
        loops = data.get("open_loops")
        if isinstance(loops, list):
            for loop in loops[:3]:
                text_line = " ".join(str(loop).split())[:_SUMMARY_CAP]
                if text_line and text_line not in self.open_loops:
                    self.open_loops.append(text_line)
        return text

    async def _remember_narrative(self, line: NarrativeLine) -> None:
        if self.memory is None:
            return
        try:
            await self.memory.record_episode(
                kind="narrative",
                summary=line.text,
                body={"turn_count": line.turn_count, "kinds": line.kinds, "ts": line.ts.isoformat()},
                thread_id=self.thread_id,
                importance=0.6,
                half_life_h=336.0,
            )
        except Exception:
            logger.warning("context.remember_narrative_failed")

    # ------------------------------------------------------------- reading

    def _ordered_turns(self) -> list[ContextTurn]:
        """Newest first, except that importance buys its way forward.

        Sorting on recency alone buries exactly what matters: a person asks a
        question, then a handful of routine cycles go by, and the budget runs
        out before the question is ever read. That is the amnesia this layer
        exists to remove, reproduced inside the buffer meant to prevent it.

        So salience is added to age rather than used as a tie-break -- the two
        quantities a reader weighs together, and the only honest way to let a
        message outrank the twenty routine cycles that followed it. The factor is
        `2 * len(turns)` rather than `len(turns)` deliberately: it makes a fully
        salient turn outrank the whole buffer, which is what "this matters more
        than how long ago it was" has to mean for it to be true of anything.
        Monotone in both terms, so there is no tier boundary where relevance
        suddenly stops.
        """
        if not self.turns:
            return []
        boost = float(len(self.turns)) * 2.0
        indexed = sorted(
            enumerate(self.turns),
            key=lambda pair: pair[0] + pair[1].salience * boost,
            reverse=True,
        )
        return [turn for _, turn in indexed]

    def render(self, *, budget_chars: int | None = None) -> str:
        """The whole thread as one block, for callers that just want text."""
        parts = self.render_blocks(budget_chars=budget_chars)
        return "\n\n".join(p for p in parts if p)

    def render_blocks(self, *, budget_chars: int | None = None) -> tuple[str, str]:
        """Render into (thread, recent), budget shared across both.

        One place decides what fits, in priority order: the narrative (cheapest
        per character and already compressed once), then the newest turns, then
        the verbatim detail of the few turns carrying a person's words or a
        failure. The detail pass runs last and cannot starve the summary pass, so
        one large tool result cannot quietly consume the whole context.
        """
        total = budget_chars if budget_chars is not None else self.cfg.max_chars
        total = max(total, 0)
        if total == 0 or not (self.narrative or self.turns):
            return "", ""
        # The thread is the compressed half and the cheapest to keep whole; the
        # buffer is the volatile half. Give the thread a floor so a busy cycle
        # cannot leave the agent with a list of events and no sense of what they
        # added up to.
        thread_budget = max(int(total * _THREAD_BUDGET_SHARE), 0)

        thread = self.render_thread(budget_chars=thread_budget)
        recent = self.render_recent(budget_chars=max(total - len(thread), 0))
        return thread, recent

    def render_thread(self, *, budget_chars: int | None = None) -> str:
        budget = budget_chars if budget_chars is not None else int(
            self.cfg.max_chars * _THREAD_BUDGET_SHARE
        )
        budget = max(budget, 0)
        sections: list[str] = []
        used = 0

        def _take(fragment: str) -> bool:
            nonlocal used
            if used + len(fragment) > budget:
                return False
            sections.append(fragment)
            used += len(fragment)
            return True

        if self.open_loops:
            loops = "\n".join(f"- {loop}" for loop in self.open_loops[:3])
            _take(f"## Still open\n{loops}\n")

        if self.narrative:
            lines = "\n".join(f"- {line.text}" for line in self.narrative)
            _take(f"## What I have been doing\n{lines}\n")
        return "".join(sections).rstrip()

    def render_recent(self, *, budget_chars: int | None = None) -> str:
        budget = budget_chars if budget_chars is not None else int(
            self.cfg.max_chars * (1 - _THREAD_BUDGET_SHARE)
        )
        budget = max(budget, 0)
        if not self.turns or budget == 0:
            return ""
        sections: list[str] = ["## Just now\n"]
        used = 0

        summary_lines: list[str] = []
        detail_blocks: list[str] = []
        for turn in self._ordered_turns():
            label = _KIND_LABEL.get(turn.kind, turn.kind)
            summary_lines.append(f"- {label}: {turn.summary}")
            if turn.kind in _DETAIL_KINDS:
                text = turn.detail.get("text")
                if text:
                    detail_blocks.append(
                        f"  <{label}>\n  {_clip(str(text), self.cfg.detail_chars)}\n  </{label}>"
                    )

        for line in summary_lines:
            fragment = line + "\n"
            if used + len(fragment) > budget:
                break
            sections.append(fragment)
            used += len(fragment)
        shown = len(sections) - 1

        for block in detail_blocks:
            fragment = block + "\n"
            if used + len(fragment) > budget:
                continue
            sections.append(fragment)
            used += len(fragment)

        if shown == 0:
            return ""
        if shown < len(summary_lines):
            sections.append(
                f"({len(summary_lines) - shown} older turns did not fit; "
                "the thread above is what they added up to)"
            )
        return "".join(sections).rstrip()

        return "\n".join(sections).rstrip()

    # ---------------------------------------------------------- durability

    def snapshot(self) -> dict[str, Any]:
        """State, not transcript [P3]: the thread as written, plus only the
        newest raw turns, which is what resuming mid-thread actually requires."""
        snapshot = ContextSnapshot(
            narrative=self.narrative[-self.cfg.narrative_max_lines:],
            turns=self.turns[-self.cfg.persist_turns:],
        )
        payload = snapshot.to_payload()
        payload["open_loops"] = self.open_loops[:3]
        return payload

    def restore(self, payload: dict[str, Any] | None) -> None:
        snapshot = ContextSnapshot.from_payload(payload)
        if snapshot.narrative:
            self.narrative = snapshot.narrative
        if snapshot.turns:
            self.turns = snapshot.turns
        loops = (payload or {}).get("open_loops") if isinstance(payload, dict) else None
        if isinstance(loops, list):
            self.open_loops = [str(item) for item in loops[:3]]
        if self.narrative or self.turns:
            logger.info(
                "context.restored", narrative=len(self.narrative), turns=len(self.turns),
            )


def _render_for_narration(turn: ContextTurn) -> str:
    if turn.kind in _DETAIL_KINDS and turn.detail.get("text"):
        return f"[{turn.kind}] {turn.summary} :: {_one_line(str(turn.detail['text']))}"
    return f"[{turn.kind}] {turn.summary}"


def _stringify(value: Any) -> str:
    if value is None:
        return "ok"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _parse_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
