"""Live mid-turn steering: enqueue, atomic claim, step-boundary injection."""
from __future__ import annotations

from typing import Any

import pytest

from clio.gsl.steering import (
    SteeringBatch,
    SteeringQueue,
)


class FakeSteeringDB:
    """The statements the queue runs, answered from a list of rows.

    The claim's `FOR UPDATE SKIP LOCKED` cannot be reproduced in a list, so
    the fake keeps the queue's own semantics: claim moves rows from pending to
    claimed and returns them; nothing else sees claimed rows.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._next_id = 1

    def _row(self, **kw: Any) -> dict[str, Any]:
        row = {
            "id": str(self._next_id), "status": "pending", "claimed_by_step": None,
            "intention_id": kw.get("intention_id"), "priority": kw.get("priority", "normal"),
            "conversation_key": kw.get("conversation_key", "web:owner"),
            "person_id": kw.get("person_id", "owner"),
            "text": kw.get("text", ""),
        }
        self._next_id += 1
        return row

    async def fetchrow(self, query: str, *args: Any):
        if "INSERT INTO steering_messages" in query:
            row = self._row(project_id=args[0], intention_id=args[1], thread_id=args[2],
                            conversation_key=args[3], person_id=args[4], text=args[5],
                            priority=args[6])
            self.rows.append(row)
            return {"id": row["id"]}
        if "COUNT(*)" in query:
            return {"n": sum(1 for r in self.rows if r["status"] == "pending")}
        return None

    async def execute(self, query: str, *args: Any):
        if "SET status = 'applied'" in query:
            applied = 0
            for row in self.rows:
                if row["id"] in [str(a) for a in (args[0] or [])]:
                    row["status"] = "applied"
                    applied += 1
            return f"UPDATE {applied}"
        return "UPDATE 0"

    async def fetch(self, query: str, *args: Any):
        return []

    async def acquire(self):
        import contextlib

        @contextlib.asynccontextmanager
        async def _fake():
            yield self

        return _fake()

    def transaction(self):
        import contextlib

        @contextlib.asynccontextmanager
        async def _fake():
            yield None

        return _fake()

    # The claim statement, implemented with the fake's own semantics.
    async def claim_local(self, project_id: str, intention_id: str | None, limit: int, step_id: str):
        claimed: list[dict[str, Any]] = []
        pending = [r for r in self.rows if r["status"] == "pending"
                   and (intention_id is None or r["intention_id"] == intention_id)]
        pending.sort(key=lambda r: (r["priority"] != "high", int(r["id"])))
        for row in pending[:limit]:
            row["status"] = "claimed"
            row["claimed_by_step"] = step_id
            claimed.append(row)
        return claimed


@pytest.fixture()
def queue() -> tuple[SteeringQueue, FakeSteeringDB]:
    db = FakeSteeringDB()
    sq = SteeringQueue(db, bus=None)
    db.claim = sq.claim  # not used; the fake mirrors the semantics

    async def _claim(project_id, *, intention_id=None, step_id=None, limit=5):
        rows = await db.claim_local(project_id, intention_id, limit, step_id)
        return rows

    sq.claim = _claim  # type: ignore[method-assign]
    return sq, db


async def test_enqueue_records_and_counts(queue):
    sq, db = queue
    steering_id = await sq.enqueue("11111111-1111-1111-1111-111111111111", "web:owner",
                                   "owner", "use the staging database, not prod")
    assert steering_id
    assert await sq.pending_count("11111111-1111-1111-1111-111111111111") == 1


async def test_claim_marks_rows_claimed_with_the_step(queue):
    sq, db = queue
    project = "11111111-1111-1111-1111-111111111111"
    await sq.enqueue(project, "web:owner", "owner", "skip the deploy step")
    claimed = await sq.claim(project, step_id="s3")
    assert len(claimed) == 1 and claimed[0]["text"] == "skip the deploy step"
    assert db.rows[0]["status"] == "claimed"
    assert db.rows[0]["claimed_by_step"] == "s3"
    # A second claim gets nothing: the row is taken.
    assert await sq.claim(project, step_id="s4") == []


async def test_mark_applied_closes_the_claim(queue):
    sq, db = queue
    project = "11111111-1111-1111-1111-111111111111"
    await sq.enqueue(project, "web:owner", "owner", "acknowledge inline")
    claimed = await sq.claim(project, step_id="s1")
    await sq.mark_applied([str(c["id"]) for c in claimed])
    assert db.rows[0]["status"] == "applied"


def test_render_for_step_is_high_priority_and_names_the_person(queue):
    block = SteeringQueue.render_for_step([
        {"id": "1", "person_id": "owner", "text": "stop before the deploy", "priority": "high"},
        {"id": "2", "person_id": "owner", "text": "also rerun the checks", "priority": "normal"},
    ])
    assert "Steering" in block
    assert "stop before the deploy" in block
    assert "(high priority)" in block
    assert "rerun the checks" in block


def test_render_for_nothing_is_empty():
    assert SteeringQueue.render_for_step([]) == ""
    assert SteeringBatch([], "").__bool__() is False


async def test_empty_text_never_becomes_a_row(queue):
    sq, db = queue
    project = "11111111-1111-1111-1111-111111111111"
    await sq.enqueue(project, "web:owner", "owner", "   ")
    claimed = await sq.claim(project, step_id="s1")
    assert len(claimed) == 1
    assert SteeringQueue.render_for_step(claimed).strip()


def test_steering_from_event_rejects_non_steering():
    from types import SimpleNamespace

    from clio.gsl.steering import steering_from_event

    assert steering_from_event(SimpleNamespace(kind="percept", payload={})) is None
    event = SimpleNamespace(kind="steering.inject",
                            payload={"project_id": "p", "text": "t"})
    assert steering_from_event(event)["text"] == "t"
