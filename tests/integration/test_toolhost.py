from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ethos.gateway.embeddings import HashingEmbedder
from ethos.guardian.gate import GuardianGate
from ethos.guardian.journal import ActionJournal
from ethos.guardian.trash import TrashStore
from ethos.memory.store import MemoryStore
from ethos.toolhost.server import ToolContext, Toolhost
from tests.conftest import FakeRegistry, FakeVerifier, ListAudit, NoopControl, NoopSnapshots

pytestmark = pytest.mark.integration


def ctx(**overrides) -> ToolContext:
    base = dict(thread_id="test", reason="integration test")
    base.update(overrides)
    return ToolContext(**base)


@pytest.fixture()
async def harness(db, config, tmp_path: Path):
    from tests.conftest import make_config

    config = make_config(tmp_path)
    config.paths.ensure()
    config.permissions.network.deny_private_ranges = False
    memory = MemoryStore(db, HashingEmbedder(1024), config)
    trash = TrashStore(tmp_path / "trash")
    journal = ActionJournal(file_path=tmp_path / "journal.jsonl")
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=trash, snapshots=NoopSnapshots(),
        audit=ListAudit(), verifier=FakeVerifier(supported=True), control=NoopControl(),
    )
    host = Toolhost(config, gate=gate, journal=journal,
                    services={"trash": trash, "memory": memory})
    yield {"host": host, "memory": memory, "trash": trash, "journal": journal, "gate": gate}


async def test_fs_write_read_roundtrip(harness, tmp_path: Path):
    host = harness["host"]
    target = tmp_path / "note.md"
    result = await host.execute("fs.write", {"path": str(target), "content": "# hello\n"}, ctx())
    assert result.ok
    assert result.content["bytes"] == len("# hello\n")
    read = await host.execute("fs.read", {"path": str(target)}, ctx())
    assert read.ok
    assert read.content["text"] == "# hello\n"
    assert read.content["total_lines"] == 1


async def test_fs_append_and_lines(harness, tmp_path: Path):
    host = harness["host"]
    target = tmp_path / "log.txt"
    await host.execute("fs.write", {"path": str(target), "content": "a\n"}, ctx())
    await host.execute("fs.write", {"path": str(target), "content": "b\nc\n", "append": True}, ctx())
    read = await host.execute("fs.read", {"path": str(target), "offset": 1, "limit": 2}, ctx())
    assert read.content["text"] == "b\nc\n"
    assert read.content["total_lines"] == 3


async def test_fs_edit_search_replace_and_backup(harness, tmp_path: Path):
    host = harness["host"]
    target = tmp_path / "code.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    result = await host.execute(
        "fs.edit",
        {"path": str(target), "search": "return 1", "replace": "return 2"},
        ctx(),
    )
    assert result.ok
    assert target.read_text(encoding="utf-8") == "def foo():\n    return 2\n"
    assert result.content["backup"] is not None
    missing = await host.execute(
        "fs.edit",
        {"path": str(target), "search": "nope", "replace": "x"},
        ctx(),
    )
    assert not missing.ok


async def test_fs_edit_unified_diff(harness, tmp_path: Path):
    host = harness["host"]
    target = tmp_path / "data.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    diff = "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    result = await host.execute("fs.edit", {"path": str(target), "diff": diff}, ctx())
    assert result.ok
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


async def test_fs_delete_transforms_to_trash(harness, tmp_path: Path):
    host = harness["host"]
    victim = tmp_path / "scratch.txt"
    victim.write_text("temp", encoding="utf-8")
    result = await host.execute("fs.delete", {"path": str(victim)}, ctx())
    assert result.ok
    assert not victim.exists()
    assert result.undo_ref is not None
    entry = await harness["trash"].get(result.undo_ref)
    assert entry is not None
    assert entry.kind == "fast_path"


async def test_shell_run_and_failure(harness):
    host = harness["host"]
    ok = await host.execute("shell.run", {"command": "echo hello-ethos"}, ctx())
    assert ok.ok
    assert "hello-ethos" in ok.content["stdout"]
    assert ok.content["returncode"] == 0
    failed = await host.execute("shell.run", {"command": "exit 41"}, ctx())
    assert failed.ok
    assert failed.content["returncode"] == 41


async def test_code_kernel_persists_state(harness):
    host = harness["host"]
    first = await host.execute("code.exec", {"code": "magic_number = 41", "session_id": "k1"}, ctx())
    assert first.ok
    second = await host.execute("code.exec", {"code": "magic_number + 1", "session_id": "k1"}, ctx())
    assert second.ok
    assert second.content["value"] == "42"
    errored = await host.execute("code.exec", {"code": "raise ValueError('boom')", "session_id": "k1"}, ctx())
    assert errored.ok
    assert "ValueError" in errored.content["error"]


async def test_http_request_against_local_server(harness):
    received: dict = {}

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        received["line"] = request_line.decode()
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", None):
                break
            if line.decode(errors="replace").lower().startswith("x-secret:"):
                received["secret"] = line.decode().strip()
        body = b"hello"
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/plain\r\n\r\n" + body)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        host = harness["host"]
        result = await host.execute(
            "http.request", {"url": f"http://127.0.0.1:{port}/ping"}, ctx(),
        )
        assert result.ok
        assert result.content["status"] == 200
        assert result.content["body"] == "hello"
    finally:
        server.close()
        await server.wait_closed()


async def test_http_blocks_private_ranges_by_default(db, config, tmp_path: Path):
    from tests.conftest import make_config

    config = make_config(tmp_path)
    config.permissions.network.deny_private_ranges = True
    trash = TrashStore(tmp_path / "trash2")
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=trash, snapshots=NoopSnapshots(),
        audit=ListAudit(), verifier=FakeVerifier(), control=NoopControl(),
    )
    host = Toolhost(config, gate=gate, journal=ActionJournal(file_path=tmp_path / "j.jsonl"),
                    services={"trash": trash})
    result = await host.execute("http.request", {"url": "http://127.0.0.1:9/"}, ctx())
    assert not result.ok


async def test_idempotency_key_executes_once(harness, tmp_path: Path):
    host = harness["host"]
    target = tmp_path / "idem.txt"
    args = {"path": str(target), "content": "v1"}
    first = await host.execute("fs.write", args, ctx(idempotency_key="idem-1"))
    assert first.ok
    second = await host.execute("fs.write", {"path": str(target), "content": "v2"},
                                ctx(idempotency_key="idem-1"))
    assert second.ok
    assert second.content.get("idempotent") is True
    assert target.read_text(encoding="utf-8") == "v1"
    records = await harness["journal"].list(limit=20)
    writes = [r for r in records if r["tool"] == "fs.write" and r.get("idempotency_key") == "idem-1"]
    assert len(writes) == 1


async def test_pause_actions_rejects_tools(db, config, tmp_path: Path):
    from tests.conftest import make_config

    config = make_config(tmp_path)

    class Paused:
        pause_actions = True

    trash = TrashStore(tmp_path / "trash3")
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=trash, snapshots=NoopSnapshots(),
        audit=ListAudit(), verifier=FakeVerifier(), control=Paused(),
    )
    host = Toolhost(config, gate=gate, journal=ActionJournal(file_path=tmp_path / "j3.jsonl"),
                    services={"trash": trash})
    result = await host.execute("shell.run", {"command": "echo no"}, ctx())
    assert not result.ok
    assert result.error_code == "guardian_reject"
    assert "H1" in result.error


async def test_tool_catalog_matches_primitive_spec(harness):
    catalog = harness["host"].catalog()
    names = {t["name"] for t in catalog}
    required = {
        "shell.run", "shell.job", "shell.session_open", "shell.session_write",
        "shell.session_read", "shell.session_close",
        "fs.read", "fs.write", "fs.edit", "fs.move", "fs.delete", "fs.list", "fs.stat",
        "code.exec", "code.reset",
        "browser.visit", "browser.click", "browser.type", "browser.extract",
        "browser.screenshot", "browser.close",
        "desktop.key", "desktop.type", "desktop.click", "desktop.move",
        "desktop.screenshot", "desktop.windows",
        "http.request", "comm.send",
        "memory.record", "memory.search", "memory.remember_fact", "memory.open_question",
        "memory.random",
        "intent.create", "intent.get", "intent.list", "intent.update", "intent.close",
        "thread.spawn",
        "schedule.at", "schedule.every", "schedule.cancel",
        "tools.register", "tools.run", "tools.list",
        "secrets.set", "secrets.get", "secrets.list",
    }
    missing = required - names
    assert not missing, f"missing primitive tools: {missing}"
    for tool in catalog:
        assert tool["description"]
        assert isinstance(tool["input_schema"], dict)


async def test_memory_and_intent_tools(harness):
    host = harness["host"]
    recorded = await host.execute("memory.record", {"kind": "experience", "summary": "did a thing",
                                                    "importance": 0.7}, ctx())
    assert recorded.ok
    found = await host.execute("memory.search", {"query": "did a thing"}, ctx())
    assert found.ok
    assert any(h["summary"] == "did a thing" for h in found.content)
    created = await host.execute("intent.create", {"title": "tool-made goal"}, ctx())
    assert created.ok
    listed = await host.execute("intent.list", {}, ctx())
    assert listed.ok
    assert any(i["title"] == "tool-made goal" for i in listed.content)
    closed = await host.execute(
        "intent.close",
        {"intention_id": created.content["intention_id"], "status": "done", "reason": "t"},
        ctx(),
    )
    assert closed.ok


async def test_secrets_tools_never_leak(harness):
    host = harness["host"]
    memory = harness["memory"]
    from ethos.gateway.secrets import SecretVault

    vault = SecretVault(keyfile=memory.config.paths.data_dir / "keys" / "age.key",
                        store_path=memory.config.paths.data_dir / "secrets.age",
                        allowed_domains=[])
    host.services["vault"] = vault
    set_result = await host.execute("secrets.set", {"key": "API", "value": "s3cr3t-value"}, ctx())
    assert set_result.ok
    get_result = await host.execute("secrets.get", {"key": "API"}, ctx())
    assert get_result.ok
    assert get_result.content == {"key": "API", "exists": True}
    listed = await host.execute("secrets.list", {}, ctx())
    assert listed.ok
    assert listed.content == {"keys": ["API"]}


async def test_schedule_tools_register_and_cancel(harness):
    host = harness["host"]
    from ethos.core.scheduler import Scheduler

    async def noop_bus():
        return None

    class FakeBus:
        def __init__(self):
            self.events = []

        async def publish(self, kind, payload, **kwargs):
            self.events.append((kind, payload))

    bus = FakeBus()
    scheduler = Scheduler(host.config, None, bus=bus)
    await scheduler.start()
    host.services["scheduler"] = scheduler
    try:
        created = await host.execute(
            "schedule.every", {"name": "test-every", "interval_s": 3600}, ctx(),
        )
        assert created.ok
        timer_id = created.content["timer_id"]
        cancelled = await host.execute("schedule.cancel", {"timer_id": timer_id}, ctx())
        assert cancelled.ok
    finally:
        await scheduler.stop()
