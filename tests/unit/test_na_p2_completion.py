"""A task is finished when it is verified, and it survives the world's bad days.

NA-P2 asked for three guarantees and this file holds each in place:

* *no premature done* (NA-P2.2) -- work whose own stated requirements were
  never checked by any rung cannot close as verified, however confident the
  independent verifier is;
* *no silent drop* (NA-P2.1) -- a planning loop that cannot reach a model
  backs off and stays open; it does not spend the world's outage as an excuse
  to park somebody's work;
* *the goal survives the interruption* (NA-P2.3) -- a bookmark carries the
  original goal, and resuming says so in the agent's own thread.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from clio.engine.life import Life, LifeDeps
from clio.gsl.loop import PLAN_ATTEMPTS, GeneralSolverLoop
from clio.gsl.planner import PlanUnavailable
from clio.gsl.verification import VerificationLadder
from clio.schemas.gsl import Bookmark, Focus
from tests.conftest import ListAudit, ScriptedGateway, make_config

# ------------------------------------------------------------------ NA-P2.2


def _ladder(tmp: str, verdict: str) -> VerificationLadder:
    config = make_config(Path(tmp))
    return VerificationLadder(
        config,
        gateway=ScriptedGateway([verdict]),
        threads=None,
        audit=ListAudit(),
    )


def _intention(**kwargs: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "title": "land the config rewrite",
        "desired_end_state": "the config loads and the suite is green",
        "commissioned_by": "mira",
        "kind": "commitment",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_stated_requirements_nobody_checked_block_a_done():
    """The verifier's confidence is not evidence.

    The criteria stated what would make this work done. No check that ran
    looked at any of it -- the work changed no file the ladder recognises, so
    rung 1 ran nothing -- and the rung-2 judge said pass. That pass used to
    close the goal (NA-P2.2): a verdict over requirements nobody checked is
    the exact shape of "finished" that NA-P2 forbids.
    """
    ladder = _ladder("/tmp/clio-test-na-p2-verified", '{"verdict": "pass", "issues": []}')
    result = asyncio.run(ladder.verify(
        intention=_intention(),
        criteria=[{"type": "manifest_present", "label": "the stated requirement"}],
        artifact={"steps": []},
        changed_paths=[],
    ))
    assert result.passed is False
    assert any("never checked" in issue for issue in result.issues)


def test_requirements_nobody_stated_are_not_blocked_by_the_same_rule():
    """The rule is about stated requirements, not about confidence itself.

    An intention with no stated criteria and no code touched has nothing the
    rung-1 ladder was asked to look at; the judge's pass is then the only
    verdict available, and refusing it would park every such goal forever.
    """
    ladder = _ladder("/tmp/clio-test-na-p2-unstated", '{"verdict": "pass", "issues": []}')
    result = asyncio.run(ladder.verify(
        intention=_intention(),
        criteria=[],
        artifact={"steps": []},
        changed_paths=[],
    ))
    assert result.passed is True


def test_the_block_holds_even_when_the_judge_passes_over_a_failing_check():
    ladder = _ladder("/tmp/clio-test-na-p2-contradiction", '{"verdict": "pass", "issues": []}')
    missing = "/definitely/not/a/real/path/xyz"
    result = asyncio.run(ladder.verify(
        intention=_intention(),
        criteria=[{"type": "file_exists", "path": missing}],
        artifact={"steps": []},
        changed_paths=[],
    ))
    assert result.passed is False
    assert any("failed check" in issue for issue in result.issues)


# ------------------------------------------------------------------ NA-P2.1


class _RecordingMemory:
    def __init__(self) -> None:
        self.closed: list[tuple[Any, str, str]] = []
        self.updated: list[dict[str, Any]] = []
        self.questions: list[str] = []
        self.notices: list[tuple[Any, str, str]] = []

    async def update_intention(self, intention_id: Any, **kwargs: Any) -> None:
        self.updated.append({"id": intention_id, **kwargs})

    async def close_intention(self, intention_id: Any, status: str, reason: str) -> None:
        self.closed.append((intention_id, status, reason))

    async def open_question(self, text: str, priority: float = 0.5) -> None:
        self.questions.append(text)

    async def record_stage_notice(self, intention: Any, stage: str, detail: str) -> None:
        self.notices.append((intention, stage, detail))
        return None


def _loop(tmp: str, memory: _RecordingMemory) -> GeneralSolverLoop:
    config = make_config(Path(tmp))
    return GeneralSolverLoop(
        config, memory=memory, gateway=None, toolhost=None,
        verification=None, audit=ListAudit(), context=None,
    )


def _intention_record() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), title="rewrite the importer", status="in_progress",
        resume_state={}, closed_reason=None, commissioned_by="mira",
        desired_end_state="the importer works", kind="commitment",
    )


def test_a_provider_outage_never_parks_a_task():
    """The world being unreachable is not the goal being impossible.

    A rate limit and a missing model clear on their own. v3.0 counted those
    failures toward the park threshold like any other, so an outage that
    outlasted the budget closed somebody's work as parked -- a task dropped
    because the provider had a bad afternoon (NA-P2.1). The failures now
    accumulate, back off, and the intention stays open no matter how many
    arrive.
    """
    memory = _RecordingMemory()
    loop = _loop("/tmp/clio-test-na-p2-nopark", memory)
    intention = _intention_record()
    resume: dict[str, Any] = {}
    for _ in range(12):
        result = asyncio.run(loop._planning_failed(
            intention, resume,
            PlanUnavailable("no eligible model could serve tier T2", kind="no_model"),
            stage="plan",
        ))
        assert result.outcome.value != "parked"
    assert not memory.closed, "a provider outage closed no task"
    assert resume[PLAN_ATTEMPTS] == 12


def test_a_rate_limited_goal_also_stays_open():
    memory = _RecordingMemory()
    loop = _loop("/tmp/clio-test-na-p2-ratelimit", memory)
    intention = _intention_record()
    resume: dict[str, Any] = {}
    for _ in range(12):
        result = asyncio.run(loop._planning_failed(
            intention, resume,
            PlanUnavailable("429; slow down", kind="rate_limited"),
            stage="plan",
        ))
        assert result.outcome.value != "parked"
    assert not memory.closed


def test_a_genuinely_unusable_request_still_parks_at_the_threshold():
    """The bounded park survives, for the kind of failure waiting does not fix.

    A model that answers and answers wrongly, after every degradation lever
    has been pulled, is a goal the loop cannot make progress on -- parking it
    with the reason recorded is the observable stop, and the stage notice
    tells whoever asked. The park threshold is configuration now
    (`gsl.plan_attempts_before_park`) and the field default is zero -- never
    park -- so this test restores the ceiling it is about.
    """
    memory = _RecordingMemory()
    loop = _loop("/tmp/clio-test-na-p2-park", memory)
    loop.config.gsl.plan_attempts_before_park = 6
    intention = _intention_record()
    resume: dict[str, Any] = {}
    result = None
    for _ in range(8):
        result = asyncio.run(loop._planning_failed(
            intention, resume,
            PlanUnavailable("the model answered in prose", kind="unusable"),
            stage="plan",
        ))
    assert result is not None and result.outcome.value == "parked"
    assert memory.closed, "the impossible goal is closed, with its reason"
    assert memory.notices, "the stop is owed an answer and the notice says so"


# ------------------------------------------------------------------ NA-P2.3


class _Quiet:
    def __getattr__(self, name: str):
        return _Quiet()

    def __call__(self, *args: Any, **kwargs: Any):
        return _Quiet()

    async def __call_async__(self, *args: Any, **kwargs: Any):  # pragma: no cover
        return _Quiet()


class _MemoryWithBookmarks:
    def __init__(self) -> None:
        self.bookmarks: dict[Any, Bookmark] = {}
        self.intention = SimpleNamespace(
            id=uuid4(), title="migrate the archive",
            desired_end_state="every object moved and verified",
            status="in_progress",
            is_open=lambda: True,
        )

    async def get_intention(self, intention_id: Any):
        return self.intention

    async def set_bookmark(self, intention_id: Any, bookmark: Bookmark) -> None:
        self.bookmarks[intention_id] = bookmark


class _RecordingTurns:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, dict[str, Any]]] = []

    async def append(self, kind: str, summary: str, **kwargs: Any) -> None:
        self.turns.append((kind, summary, kwargs))

    def snapshot(self) -> dict[str, Any]:
        return {}

    def render_blocks(self) -> tuple[str, str]:
        return "", ""

    def restore(self, payload: Any) -> None:
        return None


def test_a_bookmark_carries_the_original_goal():
    """NA-P2.3: the interruption loses the moment, never the aim."""
    memory = _MemoryWithBookmarks()
    config = make_config(Path("/tmp/clio-test-na-p2-bookmark"))
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=None, memory=memory, self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=_Quiet(), comms_out=_Quiet(), toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))
    intention_id = memory.intention.id
    asyncio.run(life.bookmark(Focus(
        intention_id=intention_id, title="migrate the archive", kind="commitment",
        why="every object moved and verified", bookmark={"next_planned_step": "s3"},
    )))
    saved = memory.bookmarks[intention_id]
    assert saved.next_planned_step == "s3"
    assert saved.goal["title"] == "migrate the archive"
    assert saved.goal["desired_end_state"] == "every object moved and verified"
    assert saved.goal["status"] == "in_progress"


def test_resuming_an_interrupted_task_is_said_in_the_agent_s_own_thread():
    """A restart that picks work back up says that it did.

    The focus was restored silently before: the work reappeared with nothing
    anywhere noting the interruption, so the agent could not report it and
    the person waiting could not be told the work had continued. The resume
    is now a turn in the agent's own thread, with the goal attached.
    """
    memory = _MemoryWithBookmarks()
    context = _RecordingTurns()
    config = make_config(Path("/tmp/clio-test-na-p2-resume"))
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=None, memory=memory, self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=_Quiet(), comms_out=_Quiet(), toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))
    life.context = context
    import json

    focus = Focus(
        intention_id=memory.intention.id, title="migrate the archive",
        kind="commitment", why="every object moved and verified",
    )
    report = {"snapshot": {"focus": json.dumps(focus.model_dump(mode="json"))}}
    asyncio.run(life._restore_focus_from_report(report))
    resumed = [t for t in context.turns if "resumed" in t[1]]
    assert resumed, "the resumption is a fact in the agent's own thread"
    assert "goal unchanged" in resumed[0][1]
    detail = resumed[0][2].get("detail") or {}
    assert detail["goal"]["desired_end_state"] == "every object moved and verified"


def test_a_focus_pointing_at_gone_work_is_recorded_not_dropped():
    """NA-P2.1: a task vanishing is a fact, not a silence."""
    memory = _MemoryWithBookmarks()
    memory.intention = None  # the work no longer exists
    context = _RecordingTurns()
    config = make_config(Path("/tmp/clio-test-na-p2-gone"))
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=None, memory=memory, self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=_Quiet(), comms_out=_Quiet(), toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))
    life.context = context
    import json

    focus = Focus(intention_id=uuid4(), title="lost work", kind="task", why="x")
    report = {"snapshot": {"focus": json.dumps(focus.model_dump(mode="json"))}}
    asyncio.run(life._restore_focus_from_report(report))
    assert any("could not resume" in t[1] for t in context.turns)
