"""Steering against the real table: atomic claims, stale expiry, drain."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from clio.gsl.steering import SteeringQueue

pytestmark = pytest.mark.integration


async def _project(db) -> str:
    """A project row, because the steering table carries its FK."""
    pid = uuid.uuid4()
    await db.execute(
        "INSERT INTO projects (project_id, title, owner_principal_id, space_id, tenant_id) "
        "VALUES ($1, 'steering test', 'owner', 'primary', 'local')",
        pid,
    )
    return str(pid)


async def _cleanup(db, project_id: str) -> None:
    await db.execute("DELETE FROM steering_messages WHERE project_id = $1::uuid", project_id)
    await db.execute("DELETE FROM projects WHERE project_id = $1::uuid", project_id)


async def test_enqueue_claim_apply_round_trip(db):
    project = await _project(db)
    try:
        queue = SteeringQueue(db)
        await queue.enqueue(project, "web:owner", "owner", "first note", priority="normal")
        await queue.enqueue(project, "web:owner", "owner", "urgent correction", priority="high")
        assert await queue.pending_count(project) == 2

        claimed = await queue.claim(project, step_id="s1")
        assert [c["text"] for c in claimed][0] == "urgent correction", \
            "high priority is claimed first"
        assert len(claimed) == 2
        assert await queue.claim(project, step_id="s2") == [], "claims are exclusive"
        await queue.mark_applied([str(c["id"]) for c in claimed])
        assert await queue.pending_count(project) == 0
    finally:
        await _cleanup(db, project)


async def test_claim_is_scoped_to_the_intention(db):
    project = await _project(db)
    other = uuid.uuid4()
    try:
        queue = SteeringQueue(db)
        await queue.enqueue(project, "web:owner", "owner", "for the root",
                            intention_id=str(other))
        claimed = await queue.claim(project, step_id="s1", intention_id=str(uuid.uuid4()))
        assert claimed == [], "another intention's steering is not this step's"
        claimed = await queue.claim(project, step_id="s1", intention_id=str(other))
        assert len(claimed) == 1
    finally:
        await _cleanup(db, project)


async def test_stale_claims_are_handed_back(db):
    project = await _project(db)
    try:
        queue = SteeringQueue(db)
        await queue.enqueue(project, "web:owner", "owner", "take this")
        claimed = await queue.claim(project, step_id="s1")
        assert len(claimed) == 1
        # A run that died between claim and apply: the row comes back.
        await db.execute(
            "UPDATE steering_messages SET claimed_at = now() - interval '10 minutes' "
            "WHERE project_id = $1::uuid",
            project,
        )
        assert await queue.expire_stale() >= 1
        assert await queue.pending_count(project) == 1
    finally:
        await _cleanup(db, project)


async def test_drain_conversation_inbound_takes_only_bound_conversations(db):
    project = await _project(db)
    try:
        queue = SteeringQueue(db)

        class FakeSocial:
            def __init__(self) -> None:
                self.handled: list[SimpleNamespace] = []

            async def pending_inbound(self, limit: int = 5):
                return [
                    SimpleNamespace(id="m1", conversation_key="web:owner",
                                    person_id="owner", text="rerun the failing test first"),
                    SimpleNamespace(id="m2", conversation_key="slack:other",
                                    person_id="other", text="a different thread's message"),
                ]

            async def mark_handled(self, message, decision=None):
                self.handled.append(message)

        social = FakeSocial()
        drained = await queue.drain_conversation_inbound(
            social, project, {"web:owner"}, intention_id=None,
        )
        assert len(drained) == 1 and drained[0]["conversation_key"] == "web:owner"
        assert len(social.handled) == 1, "the drained message is marked handled"
        rows = await db.fetch(
            "SELECT text FROM steering_messages WHERE project_id = $1::uuid", project,
        )
        assert [r["text"] for r in rows] == ["rerun the failing test first"]
        # The other thread's message is untouched — it stays a message.
        assert social.handled[0].id == "m1"
    finally:
        await _cleanup(db, project)
