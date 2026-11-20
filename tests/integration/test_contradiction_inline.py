"""Speed-1 contradictions against the real store: a correction lands at once."""
from __future__ import annotations

import pytest

from clio.memory.contradiction import InlineContradictionResolver

pytestmark = pytest.mark.integration


DIM = 1024


class StubEmbedder:
    """Deterministic vectors at the schema's width: same topic lands close,
    unrelated text lands orthogonal. The signal lives in the first three
    dimensions; the rest are zero."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "deadline" in lowered or "due" in lowered:
                head = [1.0, 0.0, 0.0]
            elif "office" in lowered:
                head = [0.0, 1.0, 0.0]
            else:
                head = [0.0, 0.0, 1.0]
            vectors.append(head + [0.0] * (DIM - 3))
        return vectors


async def test_a_correction_supersedes_the_stale_fact_immediately(db, config):
    from clio.memory.store import MemoryStore

    async def judge(new, neighbors):
        return {"verdict": "supersede_old", "reason": "the date changed"}

    memory = MemoryStore(db, StubEmbedder(), config)
    memory.contradiction = InlineContradictionResolver(judge=judge)

    subject = f"the-big-deadline-{id(db)}"
    old_id = await memory.record_fact(
        subject, "the deadline is Thursday", 0.8, {"source": "owner"}, supersede_similar=False,
    )
    new_id = await memory.record_fact(
        subject, "the deadline is Friday", 0.9, {"source": "owner"}, supersede_similar=False,
    )

    rows = await db.fetch(
        "SELECT id, statement, superseded_by FROM facts WHERE subject = $1 ORDER BY updated_at",
        subject,
    )
    by_id = {str(r["id"]): r for r in rows}
    assert by_id[str(old_id)]["superseded_by"] == new_id, \
        "the stale fact is superseded in the same write, not at 04:00"
    assert by_id[str(new_id)]["superseded_by"] is None
    live = await memory.facts_about(subject)
    assert [f["statement"] for f in live] == ["the deadline is Friday"]

    await db.execute("DELETE FROM facts WHERE subject = $1", subject)


async def test_an_unrelated_neighbor_is_left_alone(db, config):
    from clio.memory.store import MemoryStore

    async def judge(new, neighbors):
        raise AssertionError("a 0.82+ cosine pair of unrelated topics reached the judge")

    memory = MemoryStore(db, StubEmbedder(), config)
    memory.contradiction = InlineContradictionResolver(judge=judge)

    subject_a = f"office-thing-{id(db)}"
    subject_b = f"other-thing-{id(db)}"
    await memory.record_fact(subject_a, "the office is in Ebisu", 0.8, {"s": "owner"},
                             supersede_similar=False)
    # Orthogonal embeddings: cosine 0, below the band, judge never called.
    await memory.record_fact(subject_b, "the report is ready", 0.8, {"s": "owner"},
                             supersede_similar=False)
    await db.execute("DELETE FROM facts WHERE subject = ANY($1::text[])", [subject_a, subject_b])


async def test_a_merge_rewrites_the_new_statement_and_closes_the_old(db, config):
    from clio.memory.store import MemoryStore

    async def judge(new, neighbors):
        return {"verdict": "merge",
                "merged_statement": "the deadline is Friday, moved from Thursday"}

    memory = MemoryStore(db, StubEmbedder(), config)
    memory.contradiction = InlineContradictionResolver(judge=judge)

    subject = f"merge-deadline-{id(db)}"
    old_id = await memory.record_fact(subject, "the deadline is Thursday", 0.8, {},
                                      supersede_similar=False)
    new_id = await memory.record_fact(subject, "the deadline is Friday", 0.9, {},
                                      supersede_similar=False)

    live = await memory.facts_about(subject)
    assert live[0]["statement"] == "the deadline is Friday, moved from Thursday"
    row = await db.fetchrow("SELECT superseded_by FROM facts WHERE id = $1", old_id)
    assert row["superseded_by"] == new_id
    await db.execute("DELETE FROM facts WHERE subject = $1", subject)
