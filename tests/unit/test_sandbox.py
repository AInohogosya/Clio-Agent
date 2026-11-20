"""Project sandboxes: backends, argv hardening, egress rules, ref semantics."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from clio.sandbox.computer import (
    RefBrowser,
    StaleRefError,
    TakeoverController,
    mask_secrets,
)
from clio.sandbox.egress import (
    METADATA_ADDRESSES,
    EgressGuard,
    EgressPolicy,
    EgressProxy,
    _address_blocked,
)
from clio.sandbox.provider import (
    SANDBOX_PIDS_LIMIT,
    AgentHomeStore,
    BubblewrapSandbox,
    FilesystemSandbox,
    RootlessDockerSandbox,
    SandboxManager,
    SandboxSpec,
    SeatbeltSandbox,
    select_provider,
)


def _spec(tmp_path: Path, **kw: Any) -> SandboxSpec:
    return SandboxSpec(
        sandbox_id=kw.pop("sandbox_id", "sbx-test"),
        project_id=kw.pop("project_id", "11111111-1111-1111-1111-111111111111"),
        root_path=kw.pop("root_path", tmp_path / "workspace"),
        **kw,
    )


# ---- backends -----------------------------------------------------------------


async def test_the_filesystem_backend_provisions_and_execs(tmp_path):
    provider = FilesystemSandbox()
    spec = await provider.provision(_spec(tmp_path))
    marker = spec.root_path / ".clio-sandbox.json"
    assert marker.exists(), "the sandbox records itself beside the work"
    report = json.loads(marker.read_text())
    assert report["backend"] == "filesystem"
    result = await provider.exec(spec, ["sh", "-c", "echo hello-sandbox"])
    assert result.exit_code == 0 and "hello-sandbox" in result.stdout
    await provider.destroy(spec)
    assert not spec.root_path.exists()


def test_docker_argv_carries_every_hardening_flag(tmp_path):
    provider = RootlessDockerSandbox(docker="docker", runtime="runsc")
    spec = _spec(tmp_path)
    argv = provider.run_argv(spec, ["python", "-c", "pass"])
    joined = " ".join(argv)
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined
    assert "seccomp=default" in joined
    assert f"--pids-limit {SANDBOX_PIDS_LIMIT}" in joined
    assert "--runtime runsc" in joined, "gVisor is used when the deployment names it"
    assert "target=/workspace" in joined
    assert argv[-3:] == ["python", "-c", "pass"]


def test_docker_network_off_is_network_none(tmp_path):
    provider = RootlessDockerSandbox(docker="docker")
    spec = _spec(tmp_path, network=False)
    argv = provider.run_argv(spec, ["true"])
    assert "--network none" in " ".join(argv)


def test_bubblewrap_binds_only_the_lease_root_writable(tmp_path):
    provider = BubblewrapSandbox()
    spec = _spec(tmp_path)
    argv = provider.wrap_argv(spec, ["ls"])
    joined = " ".join(argv)
    assert "--unshare-user" in joined and "--share-net" in joined
    assert f"--bind {spec.root_path} /workspace" in joined, \
        "the lease root is the only writable mount"
    assert "--ro-bind /usr /usr" in joined


def test_seatbelt_profile_is_deny_by_default(tmp_path):
    provider = SeatbeltSandbox()
    spec = _spec(tmp_path)
    spec.root_path.mkdir(parents=True, exist_ok=True)
    profile = provider.profile_text(spec)
    assert profile.startswith("(version 1)\n(deny default)")
    assert json.dumps(str(spec.root_path)) in profile
    argv = provider.wrap_argv(spec, ["ls"])
    assert argv[0] == "sandbox-exec" and argv[1] == "-f"


def test_provider_selection_prefers_capability_and_ends_at_filesystem(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIO_SANDBOX_BACKEND", "filesystem")
    provider = select_provider(None)
    assert provider.backend == "filesystem"


def test_naming_an_absent_backend_is_refused_loudly(monkeypatch):
    from clio.sandbox.provider import SandboxError

    monkeypatch.setenv("CLIO_SANDBOX_BACKEND", "bubblewrap")
    monkeypatch.setattr(BubblewrapSandbox, "available", classmethod(lambda cls: False))
    with pytest.raises(SandboxError):
        select_provider(None)


# ---- sandbox manager & lease fencing -------------------------------------------


async def test_the_manager_registers_and_fences_the_sandbox(tmp_path):
    class DB:
        def __init__(self) -> None:
            self.statements: list[tuple[str, tuple]] = []
            self.lease_owner: str | None = None
            self.lease_fence = 0

        async def execute(self, sql, *args):
            self.statements.append((sql, args))
            return "OK"

        async def fetchrow(self, sql, *args):
            if sql.startswith("UPDATE sandbox_registry SET lease_owner"):
                self.lease_owner = args[1]
                self.lease_fence += 1
                return {"lease_fence": self.lease_fence}
            if sql.startswith("SELECT 1 AS ok"):
                owner, fence = args[1], args[2]
                return {"ok": 1} if (owner == self.lease_owner and fence == self.lease_fence) else None
            return None

    db = DB()
    manager = SandboxManager(db, FilesystemSandbox())
    spec = await manager.provision_for_project("p1", tmp_path / "ws")
    assert spec.sandbox_id == "sbx-p1"
    assert any("sandbox_registry" in sql for sql, _ in db.statements)

    fence = await manager.claim_lease(spec.sandbox_id, "worker-a")
    assert fence == 1
    assert await manager.commit_guarded(spec.sandbox_id, "worker-a", fence, "UPDATE x")
    fence2 = await manager.claim_lease(spec.sandbox_id, "worker-b")
    assert fence2 == 2
    assert not await manager.commit_guarded(spec.sandbox_id, "worker-a", fence, "UPDATE x"), \
        "the old owner's fence is stale after a transfer"


async def test_destroy_marks_the_registry(tmp_path):
    class DB:
        def __init__(self) -> None:
            self.updates: list[tuple] = []

        async def execute(self, sql, *args):
            self.updates.append((sql, args))
            return "OK"

        async def fetchrow(self, sql, *args):
            return None

    db = DB()
    manager = SandboxManager(db, FilesystemSandbox())
    await manager.provision_for_project("p2", tmp_path / "ws2")
    await manager.destroy("sbx-p2")
    assert any("status = 'destroyed'" in sql for sql, _ in db.updates)


# ---- egress ---------------------------------------------------------------------


def test_metadata_and_private_ranges_are_never_destinations():
    policy = EgressPolicy(allowed_hosts=["example.com"])
    for address in sorted(METADATA_ADDRESSES):
        reason = _address_blocked(address, policy)
        assert reason is not None, address
    for address in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5", "169.254.0.1", "::1", "fd00::1"):
        assert _address_blocked(address, policy) is not None, address
    assert _address_blocked("93.184.216.34", policy) is None


def test_metadata_is_blocked_even_when_private_ranges_are_allowed():
    policy = EgressPolicy(allowed_hosts=["example.com"], allow_private=True)
    assert _address_blocked("10.0.0.1", policy) is None
    assert _address_blocked("169.254.169.254", policy) == "cloud metadata service"


def test_the_allowlist_matches_names_and_suffixes():
    policy = EgressPolicy(allowed_hosts=["example.com"])
    assert policy.host_allowed("example.com")
    assert policy.host_allowed("api.example.com")
    assert not policy.host_allowed("evil-example.com")
    assert not policy.host_allowed("example.com.evil.net")


def test_a_host_off_the_allowlist_is_refused_before_dns():
    guard = EgressGuard(EgressPolicy(allowed_hosts=["good.example"]))
    decision = guard.check("other.example")
    assert not decision.allowed
    assert "allowlist" in decision.reason


async def test_every_redirect_hop_is_checked():
    guard = EgressGuard(EgressPolicy(allowed_hosts=["start.example", "also.example"]))
    hops: list[str] = []

    async def follow(url: str):
        hops.append(url)
        if url.endswith("/first"):
            return 302, "https://also.example/second"
        if url.endswith("/second"):
            return 302, "https://evil.example/third"
        return 200, None

    decision = await guard.check_url_chain("https://start.example/first", follow=follow, resolve=False)
    assert not decision.allowed
    assert decision.host == "evil.example"
    assert len(hops) >= 2, "the proxy followed the allowed hop and then checked the next"


async def test_a_redirect_chain_that_never_ends_is_cut():
    guard = EgressGuard(EgressPolicy(allowed_hosts=["loop.example"]))

    async def follow(url: str):
        return 302, "https://loop.example/again"

    decision = await guard.check_url_chain("https://loop.example/start", follow=follow, resolve=False)
    assert not decision.allowed
    assert "too many redirect hops" in decision.reason


async def test_the_proxy_refuses_a_disallowed_connect():
    guard = EgressGuard(EgressPolicy(allowed_hosts=["good.example"]))
    proxy = await EgressProxy(guard).start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"CONNECT metadata.example:443 HTTP/1.1\r\nHost: metadata.example:443\r\n\r\n")
        await writer.drain()
        # Read to EOF: the status line and the body are separate TCP segments
        # often enough that a single read() sees only the first one.
        chunks: list[bytes] = []
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
            if not chunk:
                break
            chunks.append(chunk)
        response = b"".join(chunks).decode()
        assert response.startswith("HTTP/1.1 403")
        assert "allowlist" in response
        writer.close()
    finally:
        await proxy.stop()


# ---- ref-addressed browser -------------------------------------------------------


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com/"
        self.elements: list[dict[str, Any]] = [
            {"ref": "", "role": "button", "name": "Buy now", "masked": False, "type": "button"},
            {"ref": "", "role": "input", "name": "", "masked": True, "type": "password"},
        ]
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []

    async def evaluate(self, script: str, *args: Any):
        if "data-clio-ref" in script and "return el ?" in script:
            ref = args[0] if args else ""
            return {"tag": "button"} if "gone" not in ref else None
        generation = None
        import re

        match = re.search(r"'-' \+ (\d+)", script)
        if match:
            generation = int(match.group(1))
        out = []
        for i, element in enumerate(self.elements):
            ref = f"r{i + 1}-{generation}"
            element["ref"] = ref
            out.append({**element, "ref": ref})
        return out

    async def click(self, selector: str) -> None:
        self.clicked.append(selector)

    async def fill(self, selector: str, value: str) -> None:
        self.filled.append((selector, value))


async def test_a_fresh_snapshot_issues_refs_and_actions_resolve_them():
    page = FakePage()
    browser = RefBrowser(page, sandbox_id="sbx")
    snapshot = await browser.snapshot()
    ref = snapshot["elements"][0]["ref"]
    await browser.act(ref, "click")
    assert page.clicked == [f'[data-clio-ref="{ref}"]']


async def test_a_stale_reference_is_refused_with_409_semantics():
    page = FakePage()
    browser = RefBrowser(page, sandbox_id="sbx")
    first = await browser.snapshot()
    old_ref = first["elements"][0]["ref"]
    for _ in range(4):
        await browser.snapshot()
    with pytest.raises(StaleRefError) as excinfo:
        await browser.act(old_ref, "click")
    assert excinfo.value.status == 409
    assert "re-observe" in str(excinfo.value)


async def test_a_reference_from_before_a_navigation_is_refused():
    page = FakePage()
    browser = RefBrowser(page, sandbox_id="sbx")
    snapshot = await browser.snapshot()
    ref = snapshot["elements"][0]["ref"]
    page.url = "https://example.com/other"  # the page navigated
    with pytest.raises(StaleRefError) as excinfo:
        await browser.act(ref, "click")
    assert "navigated" in str(excinfo.value)


async def test_password_fields_are_masked_in_snapshots():
    page = FakePage()
    browser = RefBrowser(page, sandbox_id="sbx")
    snapshot = await browser.snapshot()
    password_entry = snapshot["elements"][1]
    assert password_entry["masked"] is True
    assert password_entry.get("value") in (None, "(masked)") or "value" not in password_entry


async def test_secret_placeholders_are_substituted_inside_the_dom_only():
    class Vault:
        def get_secret(self, key: str) -> str:
            return "hunter2"

        def list_keys(self) -> list[str]:
            return ["LOGIN_PW"]

    page = FakePage()
    browser = RefBrowser(page, sandbox_id="sbx", secrets=Vault())
    snapshot = await browser.snapshot()
    ref = snapshot["elements"][1]["ref"]
    result = await browser.act(ref, "fill", text="{{secret:LOGIN_PW}}")
    assert page.filled == [(f'[data-clio-ref="{ref}"]', "hunter2")]
    assert "hunter2" not in json.dumps(result), "the raw secret never returns to the caller"


async def test_an_unknown_secret_types_nothing_and_says_so():
    class Vault:
        def get_secret(self, key: str):
            raise KeyError(key)

        def list_keys(self):
            return []

    page = FakePage()
    browser = RefBrowser(page, sandbox_id="sbx", secrets=Vault())
    snapshot = await browser.snapshot()
    ref = snapshot["elements"][1]["ref"]
    with pytest.raises(ValueError):
        await browser.act(ref, "fill", text="{{secret:NOPE}}")
    assert page.filled == []


def test_mask_secrets_replaces_values_with_placeholders():
    class Vault:
        def get_secret(self, key: str) -> str:
            return "hunter2"

        def list_keys(self) -> list[str]:
            return ["LOGIN_PW"]

    masked = mask_secrets("password hunter2 typed", Vault())
    assert masked == "password {{secret:LOGIN_PW}} typed"


# ---- live takeover & action barrier ----------------------------------------------


async def test_takeover_drains_inflight_input_before_granting_control():
    controller = TakeoverController(drain_timeout_s=2.0)
    done: list[str] = []

    async def slow_input() -> None:
        await asyncio.sleep(0.2)
        done.append("finished")

    task = asyncio.create_task(controller.guarded_input("sbx", slow_input()))
    await asyncio.sleep(0.05)  # let the input start
    session = await controller.take("sbx", "owner")
    assert done == ["finished"], "the barrier waited for the in-flight event"
    assert session.drained_events >= 1
    await task


async def test_input_while_a_person_holds_control_is_refused_by_name():
    controller = TakeoverController()
    session = await controller.take("sbx", "owner")
    with pytest.raises(PermissionError) as excinfo:
        await controller.guarded_input("sbx", ("click", 10, 20))
    assert "owner" in str(excinfo.value)
    assert await controller.hand_back("sbx", session.token)
    assert await controller.guarded_input("sbx", "typed") == "typed"


async def test_a_wrong_token_cannot_hand_back():
    controller = TakeoverController()
    await controller.take("sbx", "owner")
    assert not await controller.hand_back("sbx", "not-the-token")
    assert controller.holder("sbx") == "owner"


# ---- portable checkpoints ----------------------------------------------------------


async def test_agent_home_store_round_trips_encrypted(tmp_path):
    from clio.security.envelope import EnvelopeCrypto, EnvMasterKey

    class DB:
        def __init__(self) -> None:
            self.deks: dict[tuple[str, str, int], bytes] = {}

        async def fetchrow(self, query, *args):
            if "max(key_version)" in query:
                space, tenant = args
                versions = [v for (s, t, v) in self.deks if s == space and t == tenant]
                return {"v": max(versions) if versions else None}
            if len(args) == 2:
                space, tenant = args
                versions = [v for (s, t, v) in self.deks if s == space and t == tenant]
                if not versions:
                    return None
                newest = max(versions)
                return {"wrapped_dek": self.deks[(space, tenant, newest)], "key_version": newest}
            space, tenant, version = args
            wrapped = self.deks.get((space, tenant, version))
            return {"wrapped_dek": wrapped, "key_version": version} if wrapped else None

        async def execute(self, query, *args):
            if query.startswith("DELETE"):
                return "DELETE 0"
            space, tenant, wrapped, version, _ = args
            self.deks[(space, tenant, version)] = bytes(wrapped)
            return "INSERT 0 1"

    crypto = EnvelopeCrypto(DB(), EnvMasterKey(b"\x33" * 32))
    store = AgentHomeStore(crypto, local_dir=tmp_path / "checkpoints")
    workspace = tmp_path / "project-workspace"
    workspace.mkdir()
    (workspace / "index.html").write_text("<h1>the deliverable</h1>", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    uri = await store.push("p1", "primary", workspace)
    # The bytes at rest are ciphertext: neither the HTML nor the source shows.
    raw = Path(uri[len("file://"):]).read_text(encoding="utf-8")
    assert "the deliverable" not in raw and "print" not in raw
    json.loads(raw)  # it is an envelope

    restored = tmp_path / "restored"
    await store.pull(uri, "primary", restored, project_id="p1")
    assert (restored / "index.html").read_text(encoding="utf-8") == "<h1>the deliverable</h1>"
    assert (restored / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
