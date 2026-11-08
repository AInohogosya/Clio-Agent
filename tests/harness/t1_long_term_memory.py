"""T1: plant F1-F10, dilute >=100 turns, quiz; restart/compaction variant."""
from __future__ import annotations
import asyncio
from ethos.config import EthosConfig
from ethos.memory.context import ContextLayer
from ethos.schemas.context import TurnKind
from tests.harness.facts import FACTS, INTERFERENCE

async def run_t1(*, turns: int = 120, restart: bool = False) -> dict:
    cfg = EthosConfig()
    cfg.memory.context.narrate_every = 12
    cfg.memory.context.max_turns = 48
    ctx = ContextLayer(cfg, gateway=None)  # no narrator => mechanical fallback
    # Plant facts in first ~12 turns (Appendix A.1)
    for i, f in enumerate(FACTS[:10]):
        await ctx.append(TurnKind.inbound.value, f["text"],
                         detail={"text": f["text"]}, conversation_key="web:owner")
    # Dilute with unrelated conversation
    for i in range(turns):
        await ctx.append(TurnKind.event.value, f"routine chatter {i} about weather and lunch")
    if restart:
        snap = ctx.snapshot()
        ctx2 = ContextLayer(cfg, gateway=None)
        ctx2.restore(snap)
        ctx = ctx2
        for i in range(40):
            await ctx.append(TurnKind.event.value, f"post-restart chatter {i}")
    rendered = ctx.render()
    # Recall = fact text still present verbatim in rendered window
    results = {}
    for f in FACTS:
        needle = f["expect_contains"][0].lower()
        results[f["id"]] = needle in rendered.lower()
    recall = sum(results.values())
    return {"recall": recall, "recall_per_fact": results,
            "render_chars": len(rendered), "anchors": len(ctx.anchors),
            "narrative_lines": len(ctx.narrative), "turns_left": len(ctx.turns),
            "render": rendered[:4000]}
