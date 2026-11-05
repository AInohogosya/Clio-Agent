from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ethos.guardian.classifier import classify_tool
from ethos.toolhost.server import ToolContext
from ethos.toolhost.tools.history import HistoryBrowseTool


class FakeDB:
    """Records the query and returns rows shaped like the messages table's.

    Real rows come back as asyncpg `Record`s, which answer both `row["col"]`
    and attribute access; this stub supports the two the tool uses.
    """

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.queries.append((sql, args))
        return [Row(**row) for row in self.rows]


class Row(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class FakeMemory:
    def __init__(self, db: FakeDB):
        self.db = db


class FakeHost:
    def __init__(self, memory: FakeMemory):
        self._memory = memory

    def service(self, name: str) -> Any:
        if name != "memory":
            raise RuntimeError(f"service '{name}' is not available in this toolhost")
        return self._memory


def _row(ts: datetime, text: str, direction: str = "inbound",
         person_id: str = "owner", channel: str = "web") -> dict[str, Any]:
    return {
        "id": f"m-{text[:6]}", "ts": ts, "direction": direction, "channel": channel,
        "conversation_key": f"{channel}:{person_id}", "person_id": person_id,
        "sender_name": "Ada" if direction == "inbound" else None,
        "sender_username": None, "text": text,
        "delivery": {"status": "sent"},
    }


async def test_returns_history_oldest_first() -> None:
    now = datetime.now(UTC)
    db = FakeDB([
        _row(now - timedelta(hours=2), "newest message"),
        _row(now - timedelta(hours=3), "older message"),
    ])
    tool = HistoryBrowseTool()
    result = await tool.run({"hours_back": 24}, ToolContext(), FakeHost(FakeMemory(db)))
    texts = [m["text"] for m in result["messages"]]
    assert texts == ["older message", "newest message"]
    assert result["count"] == 2
    assert result["has_more"] is False


async def test_hours_back_reaches_the_query() -> None:
    db = FakeDB([])
    tool = HistoryBrowseTool()
    await tool.run({"hours_back": 72}, ToolContext(), FakeHost(FakeMemory(db)))
    sql, args = db.queries[0]
    assert args[0] == 72 * 3600.0


async def test_hours_back_defaults_and_caps() -> None:
    db = FakeDB([])
    tool = HistoryBrowseTool()
    await tool.run({}, ToolContext(), FakeHost(FakeMemory(db)))
    assert db.queries[0][1][0] == 24 * 3600.0
    await tool.run({"hours_back": 10_000_000}, ToolContext(), FakeHost(FakeMemory(db)))
    assert db.queries[1][1][0] == 24 * 366 * 3600.0, "a year is the window's ceiling"


async def test_filters_are_parameterised() -> None:
    db = FakeDB([])
    tool = HistoryBrowseTool()
    await tool.run(
        {"hours_back": 48, "person_id": "owner", "channel": "telegram",
         "query": "deploy"},
        ToolContext(), FakeHost(FakeMemory(db)),
    )
    sql, args = db.queries[0]
    assert "person_id = $2" in sql
    assert "channel = $3" in sql
    assert "ILIKE" in sql
    assert args[1] == "owner"
    assert args[2] == "telegram"
    assert args[3] == "deploy"


async def test_limit_is_capped_and_has_more_is_reported() -> None:
    now = datetime.now(UTC)
    db = FakeDB([_row(now - timedelta(hours=i), f"message {i}") for i in range(4)])
    tool = HistoryBrowseTool()
    result = await tool.run({"hours_back": 24, "limit": 1000}, ToolContext(),
                            FakeHost(FakeMemory(db)))
    assert result["count"] == 4, "the cap may not exceed what the store returned"
    db_more = FakeDB([_row(now - timedelta(hours=i), f"message {i}") for i in range(3)])
    result = await tool.run({"hours_back": 24, "limit": 2}, ToolContext(),
                            FakeHost(FakeMemory(db_more)))
    assert result["count"] == 2
    assert result["has_more"] is True


async def test_undelivered_outbound_rows_are_not_history() -> None:
    db = FakeDB([])
    tool = HistoryBrowseTool()
    await tool.run({}, ToolContext(), FakeHost(FakeMemory(db)))
    sql = db.queries[0][0]
    assert "COALESCE(delivery->>'status', 'sent') = 'sent'" in sql, \
        "a message the agent wrote but never delivered is not something that was said"


async def test_tool_is_classified_read_only() -> None:
    effects = classify_tool("history.browse", {"hours_back": 24})
    assert effects.categories == set()
