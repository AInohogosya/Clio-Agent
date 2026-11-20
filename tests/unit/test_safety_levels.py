from __future__ import annotations

import asyncio

from clio.guardian.approval import ApprovalManager
from clio.guardian.gate import GatePath, GuardianGate, Verdict
from clio.guardian.safety import (
    decide,
    describe_level,
    effective_level,
    host_allowlisted,
    is_dialogue_tool,
    is_network_command,
    request_host,
    usable_tool_names,
)
from clio.guardian.trash import TrashStore
from tests.conftest import FakeRegistry, FakeVerifier, ListAudit, NoopControl, NoopSnapshots


def test_default_level_is_3(config):
    assert config.permissions.safety_level == 3
    assert effective_level(config, None) == 3


def test_parse_and_env_override(config, monkeypatch):
    monkeypatch.setenv("CLIO_SAFETY_LEVEL", "1")
    assert effective_level(config, None) == 1
    monkeypatch.setenv("CLIO_SAFETY_LEVEL", "99")
    assert effective_level(config, None) == 3


def test_control_overrides_env(config, monkeypatch):
    monkeypatch.setenv("CLIO_SAFETY_LEVEL", "1")
    control = NoopControl()
    control.safety_level = 4
    assert effective_level(config, control) == 4


def test_level_descriptions():
    assert "dialogue-only" in describe_level(1)
    assert "approval" in describe_level(2)
    assert "internet" in describe_level(3)
    assert "without approval" in describe_level(4)


def test_dialogue_set():
    assert is_dialogue_tool("memory.search")
    assert is_dialogue_tool("history.browse")
    assert not is_dialogue_tool("shell.run")
    assert not is_dialogue_tool("subagent.apex")


def test_network_detection():
    assert is_network_command("shell.run", {"command": "curl https://example.com"})
    assert is_network_command("shell.run", {"command": "pip install requests"})
    assert is_network_command("http.request", {"url": "https://example.com"})
    assert is_network_command("browser.visit", {"url": "https://example.com"})
    assert not is_network_command("shell.run", {"command": "ls -la"})
    assert not is_network_command("fs.write", {"path": "/tmp/x", "content": "hi"})


def test_download_detection():
    # Downloads are network commands: they cannot have run without Wi-Fi,
    # so level 3 gates them like every other network command.
    for command in (
        "pip3 install requests",
        "python -m pip install requests",
        "uv pip install requests",
        "uv add pydantic",
        "uv sync",
        "pipx install black",
        "conda install numpy",
        "mamba install numpy",
        "cargo install ripgrep",
        "go install example.com/tool",
        "brew install wget",
        "yarn add typescript",
        "pnpm add tsx",
        "npm install react",
        "docker pull nginx",
        "git clone https://github.com/org/repo",
        "git fetch",
        "ftp example.com",
        "sftp user@host",
    ):
        assert is_network_command("shell.run", {"command": command}), command
    assert is_network_command("code.exec", {"code": "uv sync"})
    # Local spellings and local operations stay non-network.
    assert not is_network_command("shell.run", {"command": "make install"})
    assert not is_network_command("shell.run", {"command": "git status"})
    assert not is_network_command("shell.run", {"command": "ls -la"})
    assert not is_network_command("code.exec", {"code": "print('hi')"})


def test_decide_level1():
    assert decide(1, "memory.search", {}).action == "allow"
    rejected = decide(1, "shell.run", {"command": "ls"})
    assert rejected.action == "reject"
    rejected_sub = decide(1, "subagent.foundry", {"task": "x"})
    assert rejected_sub.action == "reject"


def test_decide_level2():
    assert decide(2, "memory.search", {}).action == "allow"
    assert decide(2, "shell.run", {"command": "ls"}).action == "require_approval"
    assert decide(2, "fs.write", {"path": "/tmp/x"}).action == "require_approval"
    assert decide(2, "subagent.foundry", {"task": "x"}).action == "require_approval"


def test_decide_level3():
    assert decide(3, "memory.search", {}).action == "allow"
    assert decide(3, "shell.run", {"command": "ls"}).action == "allow"
    assert decide(3, "fs.write", {"path": "/tmp/x"}).action == "allow"
    assert decide(3, "shell.run", {"command": "curl https://example.com"}).action == "require_approval"
    assert decide(3, "shell.run", {"command": "pip3 install requests"}).action == "require_approval"
    assert decide(3, "shell.run", {"command": "uv sync"}).action == "require_approval"
    assert decide(3, "http.request", {"url": "https://example.com"}).action == "require_approval"
    assert decide(3, "subagent.apex", {"task": "x"}).action == "require_approval"
    assert decide(3, "subagent.foundry", {"task": "x"}).action == "allow"


def test_decide_level4():
    assert decide(4, "shell.run", {"command": "ls"}).action == "allow"
    assert decide(4, "http.request", {"url": "https://example.com"}).action == "allow"
    assert decide(4, "subagent.apex", {"task": "x"}).action == "allow"


def test_inside_subagent_apex_only():
    for level in (2, 3):
        assert decide(level, "shell.run", {"command": "ls"}, inside_subagent="apex").action == "require_approval"
        assert decide(level, "shell.run", {"command": "ls"}, inside_subagent="foundry").action == "allow"
        assert decide(level, "shell.run", {"command": "ls"}, inside_subagent="axiom").action == "allow"
        assert decide(level, "http.request", {"url": "https://example.com"}, inside_subagent="foundry").action == "allow"
    assert decide(4, "shell.run", {"command": "ls"}, inside_subagent="apex").action == "allow"


def test_usable_tools_level1():
    names = ["memory.search", "shell.run", "subagent.apex", "intent.list", "fs.read"]
    assert usable_tool_names(names, 1) == ["memory.search", "intent.list"]
    assert usable_tool_names(names, 2) == names
    assert usable_tool_names(names, 4) == names


def _gate(config, tmp_path, level, control=None, approvals=None):
    config.permissions.safety_level = level
    control = control or NoopControl()
    return GuardianGate(
        config, registry=FakeRegistry(), trash=TrashStore(tmp_path / "trash"),
        snapshots=NoopSnapshots(), audit=ListAudit(), verifier=FakeVerifier(),
        control=control, approvals=approvals,
    )


async def test_gate_level1_dialogue_allowed_command_rejected(config, tmp_path):
    gate = _gate(config, tmp_path, 1)
    dialogue = await gate.review("memory.search", {"query": "hi"}, reason="talk")
    assert dialogue.verdict == Verdict.ALLOW
    command = await gate.review("shell.run", {"command": "ls"}, reason="run")
    assert command.verdict == Verdict.REJECT
    assert command.path == GatePath.BLOCKED
    subagent = await gate.review("subagent.apex", {"task": "click"}, reason="delegate")
    assert subagent.verdict == Verdict.REJECT


async def test_gate_level2_denies_without_channel(config, tmp_path):
    gate = _gate(config, tmp_path, 2)
    decision = await gate.review("shell.run", {"command": "ls"}, reason="run")
    assert decision.verdict == Verdict.REJECT
    assert "approval" in decision.reason.lower()


async def test_gate_level3_network_needs_approval_local_runs(config, tmp_path):
    gate = _gate(config, tmp_path, 3)
    local = await gate.review("shell.run", {"command": "ls -la"}, reason="list")
    assert local.verdict == Verdict.ALLOW
    network = await gate.review("http.request", {"url": "https://example.com"}, reason="fetch")
    assert network.verdict == Verdict.REJECT
    assert "approval" in network.reason.lower()


async def test_gate_level3_downloads_need_approval(config, tmp_path):
    gate = _gate(config, tmp_path, 3)
    download = await gate.review(
        "shell.run", {"command": "pip3 install requests"}, reason="install deps",
    )
    assert download.verdict == Verdict.REJECT
    assert "approval" in download.reason.lower()
    install = await gate.review("shell.run", {"command": "uv sync"}, reason="sync env")
    assert install.verdict == Verdict.REJECT


async def test_gate_level4_runs_without_channel(config, tmp_path):
    gate = _gate(config, tmp_path, 4)
    decision = await gate.review("shell.run", {"command": "ls -la"}, reason="list")
    assert decision.verdict == Verdict.ALLOW
    network = await gate.review("http.request", {"url": "https://example.com"}, reason="fetch")
    assert network.verdict == Verdict.ALLOW


async def test_gate_level2_approved_runs(config, tmp_path):
    approvals = ApprovalManager()
    gate = _gate(config, tmp_path, 2, approvals=approvals)

    async def approve_soon():
        for _ in range(100):
            if approvals.pending:
                approvals.approve(next(iter(approvals.pending)), by="owner")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(approve_soon())
    try:
        decision = await gate.review("shell.run", {"command": "ls -la"}, reason="list")
    finally:
        await task
    assert decision.verdict == Verdict.ALLOW


async def test_gate_inside_foundry_auto_apex_waits(config, tmp_path):
    config.permissions.approval_timeout_s = 0.2
    approvals = ApprovalManager()
    gate = _gate(config, tmp_path, 2, approvals=approvals)
    inside_foundry = await gate.review(
        "shell.run", {"command": "ls"}, reason="x", inside_subagent="foundry"
    )
    assert inside_foundry.verdict == Verdict.ALLOW
    denied = await gate.review(
        "shell.run", {"command": "ls"}, reason="x", inside_subagent="apex"
    )
    assert denied.verdict == Verdict.REJECT
    assert "approval" in denied.reason.lower()


async def test_approval_manager_deny_and_timeout():
    manager = ApprovalManager(default_timeout_s=0.05)
    request = await manager.request_and_wait("shell.run", {}, reason="r")
    assert request.status == "timeout"
    manager2 = ApprovalManager()
    pending = manager2.create_pending("shell.run", {}, reason="r")
    manager2.deny(pending.id, by="owner")
    assert pending.status == "denied"
    assert manager2.list_pending()[0]["id"] == pending.id


# ------------------------------------------------------------------ allowlist


def test_request_host_reads_only_what_the_gate_can_vouch_for():
    assert request_host("http.request", {"url": "https://GitHub.com/x/y"}) == "https://github.com"
    assert request_host("browser.visit", {"url": "http://docs.python.org/3/"}) == "http://docs.python.org"
    assert request_host("http.request", {"url": "ftp://example.com"}) is None
    assert request_host("http.request", {}) is None
    # A shell command names no host the gate can read before the fact.
    assert request_host("shell.run", {"command": "curl https://github.com"}) is None


def test_host_allowlisted_matches_prefix_on_origin():
    allowed = ["https://github.com", "https://pypi.org"]
    assert host_allowlisted("http.request", {"url": "https://github.com/a/b"}, allowed)
    assert host_allowlisted("browser.visit", {"url": "https://pypi.org/project/x"}, allowed)
    # Scheme matters: an http:// request never satisfies an https:// entry.
    assert not host_allowlisted("http.request", {"url": "http://github.com/a"}, allowed)
    assert not host_allowlisted("http.request", {"url": "https://github.com.evil.com/a"}, allowed)
    assert not host_allowlisted("http.request", {"url": "https://example.com"}, allowed)
    # An empty list allows nothing, the way an empty channel allowlist admits nobody.
    assert not host_allowlisted("http.request", {"url": "https://github.com"}, [])
    # A non-URL tool never qualifies, whatever the list says.
    assert not host_allowlisted("shell.run", {"command": "curl https://github.com"}, allowed)


async def test_gate_level3_waives_approval_for_allowlisted_hosts(config, tmp_path):
    config.permissions.network.allowed_hosts = ["https://github.com"]
    config.permissions.approval_timeout_s = 0.2
    approvals = ApprovalManager()
    gate = _gate(config, tmp_path, 3, approvals=approvals)
    allowed = await gate.review(
        "http.request", {"url": "https://github.com/opencode-ai/opencode"}, reason="read docs",
    )
    assert allowed.verdict == Verdict.ALLOW
    elsewhere = await gate.review("http.request", {"url": "https://example.com"}, reason="fetch")
    assert elsewhere.verdict == Verdict.REJECT
    assert "approval" in elsewhere.reason.lower()


async def test_gate_level2_still_waives_allowlisted_hosts(config, tmp_path):
    config.permissions.network.allowed_hosts = ["https://pypi.org"]
    gate = _gate(config, tmp_path, 2)
    decision = await gate.review("http.request", {"url": "https://pypi.org/p/r"}, reason="fetch")
    assert decision.verdict == Verdict.ALLOW


async def test_catalog_filters_at_level1(config, tmp_path):
    from clio.guardian.journal import ActionJournal
    from clio.toolhost.server import Toolhost

    gate = _gate(config, tmp_path, 1)
    host = Toolhost(config, gate=gate, journal=ActionJournal(file_path=tmp_path / "j.jsonl"))
    names = [entry["name"] for entry in host.catalog()]
    assert "memory.search" in names
    assert "shell.run" not in names
    assert "subagent.apex" not in names
    assert "subagent.foundry" not in names
    config.permissions.safety_level = 3
    names2 = [entry["name"] for entry in host.catalog()]
    assert "shell.run" in names2
    assert "subagent.apex" in names2
