from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from clio.toolhost.server import ToolContext
from tests.unit.test_history_tool import FakeHost

pytestmark = pytest.mark.integration


def host_for(db):
    """A host standing in for the real one, whose `memory` service owns the db."""
    return FakeHost(SimpleNamespace(db=db))


async def _seed(db, *, minutes_ago: int, text: str, direction: str = "inbound",
                person: str = "owner", channel: str = "web",
                delivered: bool = True) -> None:
    delivery = "null" if delivered else '\'{"status": "held"}\'::jsonb'
    # `social_decision IS NULL` is what marks an inbound row as unprocessed inbox,
    # and this suite runs against the database the scenario tests run their life
    # loops against. These rows are history, not a queue: a message left
    # undeliberated here would be answered by the next agent that ran, which is
    # why every row is marked handled on the way in.
    await db.execute(
        "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
        f"text, delivery, social_decision) VALUES (gen_random_uuid(), "
        f"now() - make_interval(mins => $1::int), $2, $3, $4, $5, $6, {delivery}, "
        "'\"handled\"'::jsonb)",
        minutes_ago, direction, channel, f"{channel}:{person}", person, text,
    )


async def test_history_browse_reads_the_transcript(db, tmp_path: Path):
    """The tool the agent reaches for when its own context no longer holds what
    was said, against the real table the interface scrolls back through."""
    from clio.toolhost.tools.history import HistoryBrowseTool

    await _seed(db, minutes_ago=10, text="did the deploy finish?", person="hist-alpha")
    await _seed(db, minutes_ago=5, text="yes — it finished at noon",
                direction="outbound", person="hist-alpha")
    await _seed(db, minutes_ago=3, text="a held note nobody received",
                direction="outbound", person="hist-alpha", delivered=False)
    await _seed(db, minutes_ago=60 * 24 * 40, text="ancient history", person="hist-alpha")

    tool = HistoryBrowseTool()
    result = await tool.run({"hours_back": 24, "person_id": "hist-alpha"}, ToolContext(),
                            host_for(db))
    texts = [m["text"] for m in result["messages"]]
    assert texts == ["did the deploy finish?", "yes — it finished at noon"], \
        "oldest first, delivered rows only, within the window"
    assert result["has_more"] is False


async def test_history_browse_reports_more_beyond_the_limit(db, tmp_path: Path):
    from clio.toolhost.tools.history import HistoryBrowseTool

    for i in range(5):
        await _seed(db, minutes_ago=10 - i, text=f"beta message {i}", person="hist-beta")

    tool = HistoryBrowseTool()
    page = await tool.run({"hours_back": 24, "limit": 3, "person_id": "hist-beta"},
                          ToolContext(), host_for(db))
    assert [m["text"] for m in page["messages"]] == [
        "beta message 2", "beta message 3", "beta message 4",
    ]
    assert page["has_more"] is True, "five rows in the window, three returned"

    narrowed = await tool.run(
        {"hours_back": 24, "person_id": "hist-beta", "query": "beta message 0"},
        ToolContext(), host_for(db),
    )
    assert [m["text"] for m in narrowed["messages"]] == ["beta message 0"]
    assert narrowed["has_more"] is False


async def test_history_browse_filters_by_person(db, tmp_path: Path):
    from clio.toolhost.tools.history import HistoryBrowseTool

    await _seed(db, minutes_ago=10, text="for gamma owner", person="hist-gamma")
    await _seed(db, minutes_ago=9, text="for ada", person="tg:819012")

    tool = HistoryBrowseTool()
    result = await tool.run({"hours_back": 24, "person_id": "tg:819012"}, ToolContext(),
                            host_for(db))
    assert [m["text"] for m in result["messages"]] == ["for ada"]
