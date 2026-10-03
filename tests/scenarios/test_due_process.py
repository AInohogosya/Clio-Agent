from __future__ import annotations

from pathlib import Path

import pytest

from ethos.core.control import ControlPlane
from ethos.gateway.embeddings import HashingEmbedder
from ethos.guardian.gate import GuardianGate
from ethos.guardian.journal import ActionJournal
from ethos.guardian.registry import ArtifactRegistry
from ethos.guardian.trash import TrashStore
from ethos.memory.store import MemoryStore
from ethos.observability.audit import AuditLog, DbAuditSink
from ethos.toolhost.server import ToolContext, Toolhost
from tests.conftest import FakeVerifier, NoopSnapshots, make_config

pytestmark = pytest.mark.scenario


@pytest.fixture(autouse=True)
async def _reset_control_state(db):
    yield
    try:
        await db.execute(
            "UPDATE env_entities SET attrs = '{\"paused\": false, \"pause_actions\": false, "
            "\"stopped\": false, \"emergency\": false}' WHERE kind = 'control' AND key = 'lifecycle'"
        )
    except Exception:
        pass


async def build_stack(db, tmp_path: Path, verifier):
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = MemoryStore(db, HashingEmbedder(1024), config)
    registry = ArtifactRegistry(db)
    trash = TrashStore(config.paths.trash)
    journal = ActionJournal(db=db)
    audit = AuditLog(DbAuditSink(db))
    control = ControlPlane(db, str(config.paths.run_dir))
    await control.load()
    await control.apply_command("resume", by="test-fixture")
    gate = GuardianGate(config, registry=registry, trash=trash,
                        snapshots=NoopSnapshots(), audit=audit,
                        verifier=verifier, control=control)
    host = Toolhost(config, gate=gate, journal=journal,
                    services={"memory": memory, "registry": registry, "trash": trash})
    return {
        "config": config, "memory": memory, "registry": registry, "trash": trash,
        "journal": journal, "audit": audit, "control": control, "gate": gate, "host": host,
    }


def ctx(intention_id, commissioned_by="owner", reason="") -> ToolContext:
    return ToolContext(
        thread_id="self", intention_id=intention_id, commissioned_by=commissioned_by,
        reason=reason, idempotency_key=None,
    )


async def test_due_process_end_to_end(db, tmp_path: Path):
    verifier = FakeVerifier(supported=False, why="no evidence supports deletion")
    stack = await build_stack(db, tmp_path, verifier)
    host, memory, registry, trash = stack["host"], stack["memory"], stack["registry"], stack["trash"]
    # What the log held before this turn. The chain assertion below is about the
    # links *this* turn added, so it has to be counted from a baseline: a fixed
    # number is only ever true against a database that happens to hold that many
    # rows already, which is how this came to pass or fail with whatever the
    # database had been used for.
    audit_before = await db.fetchval("SELECT count(*) FROM audit_log")

    commission_id = await memory.create_intention(
        title="Write the client report",
        desired_end_state="report.pdf exists in the workspace",
        kind="commitment", origin="commission", commissioned_by="owner",
        priority=0.95,
    )
    report = tmp_path / "report.pdf"
    written = await host.execute(
        "fs.write",
        {"path": str(report), "content": "CLIENT REPORT v1"},
        ctx(str(commission_id), reason="drafting the commissioned report"),
    )
    assert written.ok

    protected = await registry.is_protected(report)
    assert protected is not None
    assert protected["commissioned_by"] == "owner"
    assert protected["protection"] == "commissioned"

    rejected = await host.execute(
        "fs.delete", {"path": str(report), "reason": "tidying the workspace"},
        ctx(str(commission_id), reason="tidying the workspace"),
    )
    assert not rejected.ok
    assert rejected.error_code == "guardian_reject"
    assert "due process" in rejected.error.lower()
    assert report.exists()
    row = await db.fetchrow(
        "SELECT status FROM action_journal WHERE id = $1", rejected.journal_id,
    )
    assert row["status"] == "rejected"
    due_entries = await db.fetch(
        "SELECT summary, details::text AS details FROM audit_log WHERE category = 'guardian' "
        "AND summary LIKE '%%due process%%' ORDER BY seq"
    )
    assert any("REJECTED" in e["summary"] for e in due_entries)

    verifier.supported = True
    verifier.why = "owner asked for a rewrite; prior deliverable archived in git"
    accepted = await host.execute(
        "fs.delete", {"path": str(report), "reason": "owner asked for a rewrite"},
        ctx(str(commission_id), reason="owner asked for a rewrite"),
    )
    assert accepted.ok, accepted.error
    assert not report.exists()
    undo_ref = accepted.undo_ref
    assert undo_ref is not None
    entry = await trash.get(undo_ref)
    assert entry is not None
    assert entry.kind == "due_process"
    retention_days = (entry.retention_until - entry.trashed_at).days
    assert 179 <= retention_days <= 181

    protected_after = await registry.is_protected(report)
    assert protected_after is not None

    audit_rows = await db.fetch(
        "SELECT encode(hash, 'hex') AS h, encode(prev_hash, 'hex') AS p FROM audit_log ORDER BY seq"
    )
    # Every due-process decision this turn made is a link, and the count is
    # counted from a baseline rather than asserted as a constant: a constant is
    # only ever true against a database that already held that many rows, which
    # is how this assertion came to depend on whatever the agent had been doing.
    assert len(audit_rows) - audit_before >= len(due_entries)
    assert await stack["audit"].verify_chain()

    restored = await trash.restore(undo_ref)
    assert restored.exists()
    assert restored.read_text(encoding="utf-8") == "CLIENT REPORT v1"

    intention = await memory.get_intention(commission_id)
    assert intention.is_open()


async def test_hard_delete_is_impossible_through_gate(db, tmp_path: Path):
    stack = await build_stack(db, tmp_path, FakeVerifier(supported=True))
    host = stack["host"]
    target = tmp_path / "doomed.txt"
    target.write_text("x", encoding="utf-8")
    result = await host.execute(
        "fs.delete", {"path": str(target), "reason": "testing reversibility"},
        ctx(None, commissioned_by=None, reason="testing reversibility"),
    )
    assert result.ok
    entry = await stack["trash"].get(result.undo_ref)
    assert entry.path.exists()
    assert entry.path.read_bytes() == b"x"


async def test_pause_actions_stops_even_commissioned_work(db, tmp_path: Path):
    stack = await build_stack(db, tmp_path, FakeVerifier(supported=True))
    await stack["control"].apply_command("pause_actions", by="test")
    assert stack["control"].pause_actions is True
    target = tmp_path / "read-me.txt"
    target.write_text("y", encoding="utf-8")
    result = await stack["host"].execute(
        "fs.read", {"path": str(target)}, ctx(None, commissioned_by="owner", reason="work"),
    )
    assert not result.ok
    assert "H1" in result.error
    journal_rows = await db.fetch(
        "SELECT status FROM action_journal WHERE tool = 'fs.read' AND status = 'rejected' "
        "ORDER BY ts_start DESC LIMIT 1"
    )
    assert len(journal_rows) == 1
