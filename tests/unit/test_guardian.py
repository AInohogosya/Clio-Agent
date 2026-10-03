from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ethos.guardian.classifier import classify_shell, classify_tool, extract_paths
from ethos.guardian.gate import GatePath, GuardianGate, Verdict
from ethos.guardian.journal import ActionJournal, DuplicateAction
from ethos.guardian.trash import TrashStore
from ethos.observability.audit import GENESIS_HASH, FileAuditSink, canonical_record, compute_hash
from tests.conftest import FakeRegistry, FakeVerifier, ListAudit, NoopControl, NoopSnapshots


def test_classify_rm_is_destructive():
    effects = classify_shell("rm -rf /tmp/build")
    assert "delete" in effects.categories
    assert "irreversible" in effects.categories
    assert effects.is_destructive()
    assert "/tmp/build" in effects.paths


def test_classify_admin_and_network():
    effects = classify_shell("sudo apt install -y curl")
    assert "admin" in effects.categories
    assert "network" in effects.categories


def test_classify_force_push_is_irreversible():
    effects = classify_shell("git push --force origin main")
    assert "irreversible" in effects.categories


def test_classify_hard_limits_block():
    effects = classify_shell("nmap -sS 10.0.0.5")
    assert "blocked" in effects.categories
    assert any("H3" in note for note in effects.notes)


def test_extract_paths_from_redirect():
    paths = extract_paths("echo hi > /tmp/out.txt")
    assert "/tmp/out.txt" in paths


# The command an agent writes when it is working on code, and the one this used
# to refuse to classify at all.


def test_a_file_descriptor_redirect_is_not_a_path_and_is_not_a_crash():
    """`bashlex` puts an int here, and this used to read `.word` off it.

    The idiom is `cmd > out 2>&1`: stderr folded into stdout, the tail end of
    nearly every build, test and lint invocation a person or an agent writes.
    `bashlex` represents the target of a descriptor-duplicating redirect as the
    descriptor number itself -- an `int`, not a `Word` node -- so the
    unconditional `.word` raised `AttributeError` on all of them.

    It raised inside `classify_shell`, which runs on the toolhost's path before a
    tool is allowed to do anything, and the exception was never caught on the way
    out: it unwound the goal loop's step and then the whole life cycle. The agent
    re-ran the same failing step on every cycle after that and produced neither
    work nor words, which from the outside is an agent that cannot do the thing
    it promised. It is not a malformed command; it is the normal way to run one.
    """
    for command in (
        "pytest -q 2>&1 | tail -5",
        "make build >/dev/null 2>&1",
        "npm test 2>&1 | grep -i fail",
        "python3 -m build 2>&1",
        "ls >&2",
        "echo x 1>&2",
        "ruff check . 2>&1",
    ):
        effects = classify_shell(command)  # must not raise
        assert isinstance(effects.categories, set), command


def test_a_descriptor_is_not_recorded_as_a_path():
    """The list is a decision input, not decoration.

    `GuardianGate._review_destructive` walks `effects.paths` looking for an
    artifact somebody commissioned and is relying on, and a descriptor in it is
    a non-answer. The redirect regex reads `>&1` as the string `&1`; the real
    target of the command is what matters, and it is still there.
    """
    assert extract_paths("python3 x.py > out.txt 2>&1") == ["out.txt"]
    assert extract_paths("make build >/dev/null 2>&1") == ["/dev/null"]
    assert extract_paths("ls 2>&1 1>&2") == []
    assert extract_paths("cmd 2>&1 > out.txt") == ["out.txt"]


def test_a_redirect_target_is_still_found_under_a_descriptor_redirect():
    """The fix must not cost the real path it was already finding."""
    paths = extract_paths("cat a.txt > /tmp/b.txt 2>&1")
    assert "/tmp/b.txt" in paths


def test_a_command_that_cannot_be_parsed_still_yields_its_paths():
    """The fallback the parse failure already had, unchanged."""
    paths = extract_paths(">> /tmp/out.txt")
    assert "/tmp/out.txt" in paths


def test_classify_tool_fs_delete():
    effects = classify_tool("fs.delete", {"path": "/tmp/x.txt"})
    assert effects.is_destructive()


def test_classify_tool_http_money():
    effects = classify_tool("http.request", {"url": "https://api.example.com", "spend_usd": 3.5})
    assert effects.money_usd == 3.5


@pytest.fixture()
def trash(tmp_path: Path):
    return TrashStore(tmp_path / "trash", fast_retention_days=30, due_retention_days=180)


async def test_trash_move_and_restore(tmp_path: Path, trash: TrashStore):
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    entry = await trash.move(victim, kind="fast_path", reason="cleanup")
    assert not victim.exists()
    assert entry.origin == str(victim)
    assert entry.kind == "fast_path"
    assert entry.path.exists()
    assert (entry.path.parent / "manifest.json").exists()
    restored = await trash.restore(entry.id)
    assert restored.exists()
    assert restored.read_text(encoding="utf-8") == "important"


async def test_trash_purge_expired(tmp_path: Path, trash: TrashStore):
    victim = tmp_path / "old.txt"
    victim.write_text("x", encoding="utf-8")
    entry = await trash.move(victim, kind="fast_path", reason="old")
    assert await trash.purge_expired(now=datetime.now(UTC) + timedelta(days=400)) == 1
    assert await trash.get(entry.id) is None


async def test_trash_retention_kinds(trash: TrashStore):
    assert trash.retention_for("fast_path") == 30
    assert trash.retention_for("due_process") == 180


@pytest.fixture()
def gate(config, tmp_path: Path):
    trash = TrashStore(tmp_path / "trash2")
    registry = FakeRegistry()
    audit = ListAudit()
    gate = GuardianGate(
        config, registry=registry, trash=trash, snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(supported=True), control=NoopControl(),
    )
    gate.trash_dir = tmp_path / "trash2"
    return gate


async def test_gate_allows_normal_actions(gate: GuardianGate, tmp_path: Path):
    target = tmp_path / "ok.txt"
    target.write_text("data", encoding="utf-8")
    decision = await gate.review(
        "fs.read", {"path": str(target)}, reason="inspect file",
    )
    assert decision.verdict == Verdict.ALLOW
    assert decision.path == GatePath.NORMAL


async def test_gate_fast_path_moves_to_trash(gate: GuardianGate, tmp_path: Path):
    victim = tmp_path / "agent-file.txt"
    victim.write_text("disposable", encoding="utf-8")
    decision = await gate.review(
        "fs.delete", {"path": str(victim), "reason": "obsolete scratch file"},
        reason="obsolete scratch file",
    )
    assert decision.verdict == Verdict.TRANSFORM
    assert decision.path == GatePath.FAST
    assert not victim.exists()
    assert decision.undo_ref is not None
    audit_summaries = [e["summary"] for e in gate.audit.entries]
    assert any("fast path" in s for s in audit_summaries)


async def test_gate_due_process_rejects_unsupported_reason(config, tmp_path: Path):
    """A refusal is recorded, not announced.

    The gate used to compose a first-person sentence about what it had
    declined to destroy and hand it to the owner as an outbound message, so a
    refusal read in the transcript as the agent having said so in words it
    never chose. What has to survive is the record of the refusal — that it
    happened, against what, and why — and nothing else.
    """
    victim = tmp_path / "commissioned.txt"
    victim.write_text("deliverable", encoding="utf-8")
    registry = FakeRegistry(protected={
        str(victim): {"uri": str(victim), "commissioned_by": "owner", "protection": "commissioned"},
    })
    audit = ListAudit()
    trash = TrashStore(tmp_path / "trash3")
    gate = GuardianGate(
        config, registry=registry, trash=trash, snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(supported=False), control=NoopControl(),
    )
    decision = await gate.review(
        "fs.delete", {"path": str(victim), "reason": "tidying up"},
        reason="tidying up",
    )
    assert decision.verdict == Verdict.REJECT
    assert decision.path == GatePath.DUE_PROCESS
    assert victim.exists()
    assert decision.notify is None, \
        "the gate composes nothing to put in the owner's conversation"
    assert any("due process" in s for s in [e["summary"] for e in audit.entries]), \
        "and the refusal is still on the record, with its reason"


async def test_gate_due_process_moves_to_trash_without_announcing_it(config, tmp_path: Path):
    """The allowed case is the same shape: the move happens, nothing is said.

    A completed due process used to be reported to the owner as a composed
    first-person sentence. The trash entry and the audit record are what make
    the move recoverable and explainable; the sentence added a claim on the
    agent's voice and no information that those two do not already carry.
    """
    victim = tmp_path / "commissioned3.txt"
    victim.write_text("deliverable", encoding="utf-8")
    registry = FakeRegistry(protected={
        str(victim): {"uri": str(victim), "commissioned_by": "owner", "protection": "commissioned"},
    })
    audit = ListAudit()
    trash = TrashStore(tmp_path / "trash5")
    gate = GuardianGate(
        config, registry=registry, trash=trash, snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(supported=True), control=NoopControl(),
    )
    decision = await gate.review(
        "fs.delete", {"path": str(victim), "reason": "owner asked for a rewrite"},
        reason="owner asked for a rewrite",
    )
    assert decision.verdict == Verdict.TRANSFORM
    assert decision.notify is None, "the gate has no voice to speak with"
    assert any("due process" in s for s in [e["summary"] for e in audit.entries])


async def test_gate_due_process_allows_supported_reason(config, tmp_path: Path):
    victim = tmp_path / "commissioned2.txt"
    victim.write_text("deliverable", encoding="utf-8")
    registry = FakeRegistry(protected={
        str(victim): {"uri": str(victim), "commissioned_by": "owner", "protection": "commissioned"},
    })
    audit = ListAudit()
    trash = TrashStore(tmp_path / "trash4")
    gate = GuardianGate(
        config, registry=registry, trash=trash, snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(supported=True), control=NoopControl(),
    )
    decision = await gate.review(
        "fs.delete", {"path": str(victim), "reason": "owner asked for a rewrite"},
        reason="owner asked for a rewrite",
    )
    assert decision.verdict == Verdict.TRANSFORM
    assert decision.path == GatePath.DUE_PROCESS
    assert not victim.exists()
    assert trash.retention_for("due_process") == 180
    assert decision.undo_ref is not None


async def test_gate_blocks_hard_limit_commands(gate: GuardianGate):
    decision = await gate.review(
        "shell.run", {"command": "rm -rf /"}, reason="reset everything",
    )
    assert decision.verdict == Verdict.REJECT
    assert decision.path == GatePath.BLOCKED


async def test_gate_money_cap_blocks(config, tmp_path: Path):
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "t5"),
        snapshots=NoopSnapshots(), audit=ListAudit(),
        verifier=FakeVerifier(), control=NoopControl(),
    )
    decision = await gate.review(
        "http.request",
        {"url": "https://broker.example.com", "spend_usd": 500.0},
        reason="buy dip",
    )
    assert decision.verdict == Verdict.REJECT
    assert "H2" in decision.reason


async def test_gate_pause_actions_blocks_everything(config, tmp_path: Path):
    control = NoopControl()
    control.pause_actions = True
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "t6"),
        snapshots=NoopSnapshots(), audit=ListAudit(),
        verifier=FakeVerifier(), control=control,
    )
    decision = await gate.review(
        "fs.read", {"path": "/tmp/x"}, reason="paused world",
    )
    assert decision.verdict == Verdict.REJECT
    assert "H1" in decision.reason


# Preview mode, which is the same gate reached for a different reason.


def _preview_gate(config, tmp_path: Path) -> GuardianGate:
    control = NoopControl()
    control.preview = True
    return GuardianGate(
        config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "t-preview"),
        snapshots=NoopSnapshots(), audit=ListAudit(),
        verifier=FakeVerifier(), control=control,
    )


@pytest.mark.parametrize("tool,args", [
    ("shell.run", {"command": "ls -la"}),
    ("shell.job", {"command": "make build"}),
    ("code.exec", {"code": "print(1)"}),
    ("fs.read", {"path": "/tmp/x"}),
    ("fs.write", {"path": "/tmp/x", "content": "hello"}),
    ("fs.edit", {"path": "/tmp/x", "search": "a", "replace": "b"}),
    ("fs.delete", {"path": "/tmp/x"}),
    ("fs.move", {"from_path": "/tmp/a", "to_path": "/tmp/b"}),
    ("http.request", {"url": "https://example.com"}),
])
async def test_preview_mode_refuses_every_tool(config, tmp_path: Path, tool, args):
    """The claim on the settings screen is *all* of them, so this asks about all of them.

    A gate that only stopped the shell tools would leave an agent that cannot run
    a command still able to write a file, and the box would be answering a
    narrower question than the one printed beside it. The list is here to be
    extended: a tool added to the host later is refused by the same line of code
    these all go through, and this is what notices if that ever stops being true.
    """
    gate = _preview_gate(config, tmp_path)
    decision = await gate.review(tool, args, reason="look before you leap")
    assert decision.verdict == Verdict.REJECT, tool
    assert decision.path == GatePath.BLOCKED, tool
    assert "preview" in decision.reason.lower(), tool


async def test_preview_mode_is_not_reported_as_oversight(config, tmp_path: Path):
    """It is journalled as what it is.

    The pause and preview refusals gate the same tools, so a gate that reused the
    pause's words would write "paused by oversight (H1)" into the audit trail for
    an action nobody had stopped — and the record is what an owner reads to find
    out why the agent did not do the thing it said it would.
    """
    audit = ListAudit()
    control = NoopControl()
    control.preview = True
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "t-preview-audit"),
        snapshots=NoopSnapshots(), audit=audit, verifier=FakeVerifier(), control=control,
    )
    await gate.review("shell.run", {"command": "ls"}, reason="look")
    assert len(audit.entries) == 1
    summary = audit.entries[0]["summary"]
    assert "preview" in summary
    assert "H1" not in summary, "it was not an oversight hold"


async def test_a_pause_still_outranks_preview(config, tmp_path: Path):
    """Both gate every tool, and the one somebody stopped the agent for is said.

    Checked second for a reason: with both flags set there is one refusal to make
    and one reason to give for it, and an agent somebody has actually stopped is
    the more urgent fact. It also keeps the H1 behaviour exactly as it was — the
    preview check is additive, and a run with preview off cannot tell the
    difference.
    """
    control = NoopControl()
    control.preview = True
    control.pause_actions = True
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "t-both"),
        snapshots=NoopSnapshots(), audit=ListAudit(),
        verifier=FakeVerifier(), control=control,
    )
    decision = await gate.review("fs.read", {"path": "/tmp/x"}, reason="both")
    assert decision.verdict == Verdict.REJECT
    assert "H1" in decision.reason


async def test_preview_off_changes_nothing_about_an_ordinary_action(gate: GuardianGate, tmp_path: Path):
    target = tmp_path / "fine.txt"
    target.write_text("data", encoding="utf-8")
    decision = await gate.review("fs.read", {"path": str(target)}, reason="read it")
    assert decision.verdict == Verdict.ALLOW


def test_hash_chain_math():
    record = canonical_record(datetime.now(UTC), "self", "lifecycle", "woke up", {"k": 1})
    digest = compute_hash(GENESIS_HASH, record)
    assert len(digest) == 32
    assert digest != compute_hash(b"\x01" * 32, record)


async def test_file_audit_chain_verifies_and_detects_tampering(tmp_path: Path):
    sink = FileAuditSink(tmp_path / "audit.jsonl")
    from ethos.observability.audit import AuditLog

    audit = AuditLog(sink)
    await audit.append(actor="self", category="test", summary="first", details={})
    await audit.append(actor="self", category="test", summary="second", details={})
    assert await audit.verify_chain()
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    tampered = json.loads(lines[0])
    tampered["summary"] = "hacked"
    lines[0] = json.dumps(tampered)
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n")
    audit2 = AuditLog(sink)
    assert not await audit2.verify_chain()


async def test_journal_write_ahead_and_idempotency(tmp_path: Path):
    journal = ActionJournal(file_path=tmp_path / "journal.jsonl")
    action_id = await journal.begin(
        thread_id="self", intention_id=None, tool="fs.write",
        args_redacted={"path": "x", "content": "y"}, reason="test",
        idempotency_key="key-1", side_effects=["write"],
    )
    with pytest.raises(DuplicateAction):
        await journal.begin(
            thread_id="self", intention_id=None, tool="fs.write",
            args_redacted={"path": "x"}, reason="test",
            idempotency_key="key-1", side_effects=["write"],
        )
    records = await journal.list(limit=10)
    assert records[0]["id"] == action_id
    assert records[0]["status"] == "running"
    await journal.end(action_id, status="ok", result_digest={"bytes": 1}, undo_ref=None)
    records = await journal.list(limit=10)
    assert records[0]["status"] == "ok"
    assert await journal.reconcile_stale() == 0
