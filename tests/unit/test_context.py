from __future__ import annotations

from typing import Any

import pytest

from ethos.config import load_config
from ethos.memory.context import ContextLayer
from ethos.schemas.context import TurnKind
from ethos.schemas.models import NormalizedResponse, Usage


class NarrationGateway:
    """Returns a scripted narration, and records what it was asked to narrate.

    Falls back to a distinct line per call so a test that rolls repeatedly gets
    a genuinely different thread rather than the same sentence repeated.
    """

    def __init__(self, lines: list[str] | None = None):
        self.lines = list(lines or [])
        self.requests: list[Any] = []
        self.calls = 0

    async def complete(self, request: Any) -> NormalizedResponse:
        self.requests.append(request)
        if self.lines:
            line = self.lines.pop(0)
        else:
            line = f"did some work, pass {self.calls}"
        self.calls += 1
        return NormalizedResponse(
            text=f'{{"line": "{line}", "open_loops": []}}',
            cost_usd=0.002,
            usage=Usage(input_tokens=50, output_tokens=5),
        )


class FailingGateway:
    async def complete(self, request: Any) -> NormalizedResponse:
        raise RuntimeError("gateway down")


class RecordingMemory:
    def __init__(self) -> None:
        self.episodes: list[dict[str, Any]] = []
        self.db = None

    async def record_episode(self, kind: str, summary: str, body: dict | None = None, **kw: Any):
        self.episodes.append({"kind": kind, "summary": summary, "body": body, **kw})
        return None


@pytest.fixture()
def context_config():
    return load_config("config").memory.context


def make_layer(context_config, gateway=None, memory=None) -> ContextLayer:
    from ethos.config import EthosConfig

    config = EthosConfig()
    config.memory.context = context_config
    return ContextLayer(config, memory=memory, gateway=gateway)


# --------------------------------------------------------------------- buffer


async def test_append_keeps_turns_in_order(context_config):
    layer = make_layer(context_config)
    await layer.append(TurnKind.action.value, "first")
    await layer.append(TurnKind.action.value, "second")
    assert [t.summary for t in layer.turns] == ["first", "second"]


async def test_append_collapses_whitespace_and_caps_length(context_config):
    layer = make_layer(context_config)
    await layer.append(TurnKind.action.value, "a  b\n\t c  " * 200)
    assert "\n" not in layer.turns[0].summary
    assert len(layer.turns[0].summary) <= 300


async def test_append_ignores_empty_summary(context_config):
    layer = make_layer(context_config)
    assert await layer.append(TurnKind.action.value, "   ") is None
    assert layer.turns == []


async def test_social_turns_outrank_thoughts(context_config):
    """A person speaking is worth more of the budget than the agent musing."""
    layer = make_layer(context_config)
    await layer.append(TurnKind.thought.value, "a passing thought")
    await layer.append(TurnKind.inbound.value, "owner asked something")
    ordered = layer._ordered_turns()
    assert ordered[0].kind == TurnKind.inbound.value


async def test_a_message_is_not_buried_by_the_cycles_that_follow_it(context_config):
    """The amnesia this layer exists to remove, reproduced inside the buffer.

    A person asks something, twenty routine cycles go by, and the budget runs
    out. Sorting on recency alone drops the question unread — which is how an
    agent answers "as I said" with no idea what was said.
    """
    layer = make_layer(context_config)
    await layer.append(
        TurnKind.inbound.value, "owner asked any news on the deploy",
        detail={"text": "any news on the deploy?"},
    )
    for i in range(20):
        await layer.append(TurnKind.event.value, f"routine heartbeat {i}")

    _, recent = layer.render_blocks(budget_chars=400)
    assert "any news on the deploy" in recent, \
        "a message must outrank the routine cycles that followed it"


async def test_failures_outrank_the_routine(context_config):
    layer = make_layer(context_config)
    await layer.append(TurnKind.failure.value, "the credential expired and the deploy failed")
    for i in range(20):
        await layer.append(TurnKind.event.value, f"routine heartbeat {i}")
    _, recent = layer.render_blocks(budget_chars=400)
    assert "credential expired" in recent


async def test_among_equally_salient_turns_the_newest_comes_first(context_config):
    layer = make_layer(context_config)
    for i in range(5):
        await layer.append(TurnKind.event.value, f"event {i}")
    ordered = layer._ordered_turns()
    assert [t.summary for t in ordered] == [f"event {i}" for i in reversed(range(5))]


async def test_buffer_never_grows_past_max_turns(context_config):
    context_config.max_turns = 10
    context_config.narrate_every = 4
    layer = make_layer(context_config, gateway=NarrationGateway())
    for i in range(40):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert len(layer.turns) <= context_config.max_turns


# ------------------------------------------------------------------ narrative


async def test_overflow_rolls_oldest_turns_into_a_narrative_line(context_config):
    context_config.max_turns = 6
    context_config.narrate_every = 3
    layer = make_layer(context_config, gateway=NarrationGateway(["worked on the thing"]))
    for i in range(8):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert layer.narrative, "a full buffer must produce a line, not just drop turns"
    assert layer.narrative[0].text == "worked on the thing"
    assert layer.narrative[0].turn_count == 3
    # The rolled turns are gone from the buffer, but accounted for in the line.
    assert all("turn" not in t.summary or int(t.summary.split()[1]) >= 3 for t in layer.turns)


async def test_roll_is_a_noop_below_the_batch_size(context_config):
    context_config.narrate_every = 10
    layer = make_layer(context_config, gateway=NarrationGateway())
    for i in range(5):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert await layer.roll("focus") is None
    assert layer.narrative == []


async def test_narration_falls_back_to_a_mechanical_rollup(context_config):
    """Losing context because the model was down is the failure this prevents."""
    context_config.max_turns = 4
    context_config.narrate_every = 2
    layer = make_layer(context_config, gateway=FailingGateway())
    for i in range(6):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert len(layer.narrative) == 1
    assert "2 turns" in layer.narrative[0].text


async def test_narration_falls_back_when_gateway_is_absent(context_config):
    context_config.max_turns = 4
    context_config.narrate_every = 2
    layer = make_layer(context_config, gateway=None)
    for i in range(6):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert len(layer.narrative) == 1


async def test_open_loops_are_kept_from_narration(context_config):
    context_config.max_turns = 4
    context_config.narrate_every = 2

    class LoopGateway:
        async def complete(self, request):
            return NormalizedResponse(
                text='{"line": "left something unfinished", "open_loops": ["still owe owner an answer"]}',
                cost_usd=0.0, usage=Usage(input_tokens=1, output_tokens=1),
            )

    layer = make_layer(context_config, gateway=LoopGateway())
    for i in range(6):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert layer.open_loops == ["still owe owner an answer"]


async def test_dropped_narrative_lines_are_offered_to_the_retriever(context_config):
    """A line that scrolls off the thread must not become unreachable."""
    context_config.narrate_every = 2
    context_config.narrative_max_lines = 2
    memory = RecordingMemory()
    layer = make_layer(context_config, gateway=NarrationGateway(), memory=memory)
    for i in range(20):
        await layer.append(TurnKind.action.value, f"turn {i}")
        await layer.roll("focus")
    assert len(layer.narrative) == 2, "the thread must stay bounded"
    assert memory.episodes, "narrated lines must reach durable memory"
    assert any(e["kind"] == "narrative" for e in memory.episodes)


async def test_a_line_on_the_thread_is_not_also_written_to_memory(context_config):
    """While a line is on the thread it is already in front of the agent every
    cycle; storing it too would put the same line in two places and let the two
    disagree."""
    context_config.narrate_every = 1
    context_config.narrative_max_lines = 5
    memory = RecordingMemory()
    layer = make_layer(context_config, gateway=NarrationGateway(), memory=memory)
    await layer.append(TurnKind.action.value, "a thing happened")
    await layer.roll("focus")
    assert len(layer.narrative) == 1
    assert memory.episodes == []


async def test_a_line_is_written_to_memory_exactly_once(context_config):
    context_config.narrate_every = 1
    context_config.narrative_max_lines = 2
    memory = RecordingMemory()
    layer = make_layer(context_config, gateway=NarrationGateway(), memory=memory)
    for i in range(12):
        await layer.append(TurnKind.action.value, f"turn {i}")
        await layer.roll("focus")
    texts = [e["summary"] for e in memory.episodes]
    assert len(texts) == len(set(texts)), f"a line was stored twice: {texts}"


async def test_narration_cost_is_tracked(context_config):
    context_config.max_turns = 4
    context_config.narrate_every = 2
    layer = make_layer(context_config, gateway=NarrationGateway())
    for i in range(6):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert layer.cost_usd > 0


async def test_narrator_is_sent_the_actual_turns(context_config):
    context_config.max_turns = 4
    context_config.narrate_every = 2
    gateway = NarrationGateway()
    layer = make_layer(context_config, gateway=gateway)
    await layer.append(TurnKind.inbound.value, "owner asked about the migration")
    await layer.append(TurnKind.action.value, "ran the tool")
    await layer.roll("fix the migration")
    assert gateway.requests, "narration must actually reach the model"
    sent = "\n".join(str(m.content) for m in gateway.requests[0].messages)
    assert "owner asked about the migration" in sent
    assert "fix the migration" in sent


# --------------------------------------------------------------------- render


async def test_render_blocks_splits_thread_from_recent(context_config):
    layer = make_layer(context_config, gateway=NarrationGateway())
    await layer.append(TurnKind.action.value, "a recent thing")
    thread, recent = layer.render_blocks()
    assert thread == ""
    assert "a recent thing" in recent


async def test_rendered_context_respects_its_budget(context_config):
    layer = make_layer(context_config)
    for i in range(200):
        await layer.append(TurnKind.action.value, f"turn number {i} happened")
    rendered = layer.render()
    assert len(rendered) <= context_config.max_chars


async def test_tiny_budget_still_returns_the_newest_turn(context_config):
    layer = make_layer(context_config)
    await layer.append(TurnKind.action.value, "old")
    await layer.append(TurnKind.action.value, "newest thing")
    _, recent = layer.render_blocks(budget_chars=60)
    assert "newest" in recent


async def test_zero_budget_renders_nothing(context_config):
    layer = make_layer(context_config)
    await layer.append(TurnKind.action.value, "something")
    assert layer.render_blocks(budget_chars=0) == ("", "")


async def test_empty_layer_renders_nothing(context_config):
    layer = make_layer(context_config)
    assert layer.render() == ""


async def test_message_detail_is_rendered_verbatim(context_config):
    """A person is quoted, not paraphrased: the details that matter later are
    the ones a summary drops."""
    layer = make_layer(context_config)
    await layer.append(
        TurnKind.inbound.value, "owner asked something",
        detail={"text": "did you finish the migration or not"},
    )
    _, recent = layer.render_blocks()
    assert "did you finish the migration or not" in recent


async def test_long_tool_result_cannot_starve_the_summary(context_config):
    """One big result must not be able to consume the whole context."""
    layer = make_layer(context_config)
    await layer.append(
        TurnKind.action.value, "ran the exporter",
        detail={"result": "x" * 20_000},
    )
    await layer.append(TurnKind.failure.value, "the exporter broke", detail={"error": "boom"})
    _, recent = layer.render_blocks(budget_chars=400)
    assert len(recent) <= 400
    assert "ran the exporter" in recent


async def test_render_says_when_turns_were_left_out(context_config):
    layer = make_layer(context_config)
    for i in range(60):
        await layer.append(TurnKind.action.value, f"turn {i} happened today")
    _, recent = layer.render_blocks(budget_chars=300)
    assert "did not fit" in recent


async def test_thread_is_preferred_over_buffer_when_budget_is_tight(context_config):
    """When the budget runs out, the compressed thread outranks the raw buffer."""
    context_config.narrate_every = 1
    layer = make_layer(context_config, gateway=NarrationGateway(["was fixing the deploy"]))
    await layer.append(TurnKind.action.value, "old turn")
    await layer.roll("focus")
    await layer.append(TurnKind.action.value, "brand new turn")
    thread, recent = layer.render_blocks(budget_chars=200)
    assert "was fixing the deploy" in thread, \
        "the compressed half is kept whole before the raw half is spent"
    assert thread.startswith("## What I have been doing")


# ----------------------------------------------------------------- action log


async def test_note_actions_records_success(context_config):
    layer = make_layer(context_config)
    await layer.note_actions([
        {"tool": "fs.read", "args": {"path": "a.txt"}, "ok": True, "result": "hello"},
    ])
    assert any(t.kind == TurnKind.result.value for t in layer.turns)
    assert "hello" in layer.turns[0].summary


async def test_note_actions_records_failure_as_a_failure(context_config):
    """A failure is the thing a retry most needs to see, so it gets its own kind."""
    layer = make_layer(context_config)
    await layer.note_actions([
        {"tool": "shell.run", "args": {}, "ok": False, "result": "permission denied"},
    ])
    assert layer.turns[0].kind == TurnKind.failure.value
    assert "permission denied" in layer.turns[0].summary


async def test_note_actions_ignores_malformed_entries(context_config):
    layer = make_layer(context_config)
    await layer.note_actions([None, "not a dict", {"no_tool": True}])
    assert layer.turns == []


# ------------------------------------------------------------------ durability


async def test_snapshot_round_trips(context_config):
    layer = make_layer(context_config, gateway=NarrationGateway())
    await layer.append(TurnKind.action.value, "a thing that happened")
    await layer.roll("focus")
    await layer.append(TurnKind.inbound.value, "owner said hello")
    payload = layer.snapshot()

    revived = make_layer(context_config)
    revived.restore(payload)
    assert len(revived.narrative) == len(layer.narrative)
    assert len(revived.turns) == len(layer.turns)
    assert revived.render() == layer.render()


async def test_snapshot_keeps_only_the_newest_turns(context_config):
    context_config.persist_turns = 3
    layer = make_layer(context_config)
    for i in range(20):
        await layer.append(TurnKind.action.value, f"turn {i}")
    assert len(layer.snapshot()["turns"]) == 3


async def test_restore_tolerates_junk(context_config):
    layer = make_layer(context_config)
    layer.restore(None)
    layer.restore({"narrative": "not a list", "turns": 3})
    assert layer.turns == [] and layer.narrative == []


async def test_restore_keeps_open_loops(context_config):
    layer = make_layer(context_config)
    layer.restore({"narrative": [], "turns": [], "open_loops": ["owed an answer"]})
    assert layer.open_loops == ["owed an answer"]


async def test_snapshot_is_json_serialisable(context_config):
    import json

    context_config.narrate_every = 1
    layer = make_layer(context_config, gateway=NarrationGateway())
    await layer.append(TurnKind.inbound.value, "said something")
    await layer.roll("focus")
    assert json.loads(json.dumps(layer.snapshot()))["narrative"]
