from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from ethos.engine.life import Life, LifeDeps
from ethos.engine.rhythm import RhythmController
from ethos.engine.states import State
from ethos.schemas.events import Percept
from tests.conftest import make_config

"""
Perceptions arrive when somebody sends them, not when the tick comes round.

The bus subscribed the core the moment a message was published, and the core put
the percept on a queue the loop drained at the top of each pass — but the loop
then slept out the rest of the tick, so a message that landed a moment after a
pass was answered on the tick's clock rather than the sender's. Eight seconds on
a waiting agent and sixty on a resting one, and from a phone that reads as the
agent taking its time. These tests keep the two clocks apart: the tick still
paces an agent nobody is talking to, and it is no longer what paces a channel.
"""


class _Rhythm:
    """A rhythm whose only tick is one long enough to notice if it is slept out."""

    def __init__(self, tick: float) -> None:
        self.tick = tick
        self.ticks_started = 0

    def wait_until_next(self, state: Any) -> Any:
        self.ticks_started += 1

        async def _sleep() -> None:
            await asyncio.sleep(self.tick)

        return _sleep()


def _life(config: Any, rhythm: Any) -> Life:
    return Life(LifeDeps(
        config=config, control=None, bus=None, memory=None, self_model=None,
        attention=None, motivation=None, rhythm=rhythm, gateway=None, gsl=None,
        social=None, comms_out=None, toolhost=None, audit=None, continuity=None,
        consolidation=None, embedder=None, context=SimpleNamespace(), thread_id="self",
    ))


@pytest.fixture()
def config(tmp_path: Any) -> Any:
    return make_config(tmp_path)


async def test_an_idle_agent_still_waits_out_its_tick(config: Any):
    """No percept means the tick is the pace, which is what keeps an idle agent idle."""
    rhythm = _Rhythm(tick=0.15)
    life = _life(config, rhythm)
    life.attach_queue(asyncio.Queue())

    started = time.monotonic()
    await life._wait_until_next()
    elapsed = time.monotonic() - started

    assert rhythm.ticks_started == 1
    assert elapsed >= 0.12, "the tick is still what an idle agent waits on"


async def test_a_percept_ends_the_wait_before_the_tick_is_over(config: Any):
    """A message is the sender's clock, not the tick's."""
    rhythm = _Rhythm(tick=5.0)
    life = _life(config, rhythm)
    life.attach_queue(asyncio.Queue())

    async def _send() -> None:
        await asyncio.sleep(0.02)
        life._percept_queue.put_nowait(Percept(kind="msg.inbound", summary="are you there?"))

    started = time.monotonic()
    await asyncio.gather(life._wait_until_next(), _send())
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "a percept ends the wait rather than sitting out a five-second tick"


async def test_the_percept_that_ended_the_wait_is_still_perceived(config: Any):
    """Waking early must not cost the message that did the waking."""
    rhythm = _Rhythm(tick=5.0)
    life = _life(config, rhythm)
    life.attach_queue(asyncio.Queue())
    sent = Percept(kind="msg.inbound", summary="hello")

    async def _send() -> None:
        await asyncio.sleep(0.02)
        life._percept_queue.put_nowait(sent)

    await asyncio.gather(life._wait_until_next(), _send())
    percepts = await life.perceive()

    assert [p.kind for p in percepts] == ["msg.inbound"]
    assert percepts[0] is sent, "the message read to end the sleep is delivered, not dropped"


async def test_a_real_rhythm_controller_is_interruptible(config: Any):
    """The pair under test wired the way the loop wires them, on a real controller.

    A fake rhythm proves the race works against *a* wait; this proves it against
    the one the agent actually runs, so a change to `RhythmController` that made
    the wait uncancellable would fail here rather than only in production.
    """
    config.rhythm.ticks["WAITING"] = 5.0
    life = _life(config, RhythmController(config))
    life.attach_queue(asyncio.Queue())
    life.states.transition(State.WAITING, "test")

    async def _send() -> None:
        await asyncio.sleep(0.02)
        life._percept_queue.put_nowait(Percept(kind="msg.inbound", summary="hi"))

    started = time.monotonic()
    await asyncio.gather(life._wait_until_next(), _send())

    assert time.monotonic() - started < 1.0
