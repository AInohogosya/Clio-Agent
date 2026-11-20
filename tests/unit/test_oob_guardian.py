"""oob-guardian: the host-level watchdog's enforcement actions."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load(monkeypatch, tmp_path: Path, **env: str):
    """The module reads its configuration at import time; reload it per test."""
    for key in ("CLIO_OOB_DIR", "CLIO_CONTAINER_RUNTIME", "CLIO_GATEWAY_TOKEN_DIR",
                "CLIO_METRICS_URL", "CLIO_DAILY_CAP_USD", "CLIO_MONTHLY_CAP_USD",
                "CLIO_HEARTBEAT_FILE", "CLIO_SUSPEND_CMD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLIO_OOB_DIR", str(tmp_path / "oob"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location(
        "oob_guardian_under_test",
        Path(__file__).resolve().parents[2] / "deploy" / "oob" / "oob_guardian.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["oob_guardian_under_test"] = module
    spec.loader.exec_module(module)
    return module


class FakeRuntime:
    """A container runtime stand-in: records the verbs it was asked for."""

    def __init__(self, containers: list[str], monkeypatch, module) -> None:
        self.containers = containers
        self.calls: list[list[str]] = []
        monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(module, "container_runtime", lambda: "/usr/bin/docker")

        def fake_run(argv, **kwargs):
            self.calls.append(list(argv))
            if argv[1:2] == ["ps"]:
                return type("R", (), {"returncode": 0, "stdout": "\n".join(self.containers), "stderr": ""})()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(module.subprocess, "run", fake_run)


def test_freeze_pauses_every_sandbox_container(tmp_path, monkeypatch):
    module = _load(monkeypatch, tmp_path)
    runtime = FakeRuntime(
        ["clio-sandbox-p1", "clio-sandbox-p2", "some-other-container"], monkeypatch, module,
    )
    frozen = module.freeze_sandboxes("test reason")
    assert frozen == 2
    paused = [call[2] for call in runtime.calls if call[1] == "pause"]
    assert paused == ["clio-sandbox-p1", "clio-sandbox-p2"]
    assert all("some-other-container" not in " ".join(c) for c in runtime.calls), \
        "the watchdog pauses the agent's sandboxes, not other people's containers"


def test_freeze_with_no_runtime_is_a_logged_noop(tmp_path, monkeypatch):
    module = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "container_runtime", lambda: "")
    assert module.freeze_sandboxes("test") == 0


def test_revoke_gateway_tokens_empties_the_directory(tmp_path, monkeypatch):
    tokens = tmp_path / "gateway-secrets"
    tokens.mkdir()
    (tokens / "anthropic.key").write_text("sk-ant-secret", encoding="utf-8")
    (tokens / "openai.key").write_text("sk-secret", encoding="utf-8")
    module = _load(monkeypatch, tmp_path, CLIO_GATEWAY_TOKEN_DIR=str(tokens))
    revoked = module.revoke_gateway_tokens("spend watchdog")
    assert revoked == 2
    assert list(tokens.iterdir()) == []
    assert not (tmp_path / "gateway-secrets" / "anthropic.key").exists()


def test_revoke_without_a_directory_says_so_and_does_nothing(tmp_path, monkeypatch):
    module = _load(monkeypatch, tmp_path, CLIO_GATEWAY_TOKEN_DIR=str(tmp_path / "nope"))
    assert module.revoke_gateway_tokens("x") == 0


def test_monthly_estimate_is_conservative(tmp_path, monkeypatch):
    module = _load(monkeypatch, tmp_path)
    # The estimate assumes the daily total will be spent every remaining day:
    # an over-estimate, which is the safe direction for a cap.
    assert module.monthly_spend_estimate(10.0) >= 10.0


def test_heartbeat_age_reads_the_synced_file(tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("", encoding="utf-8")
    module = _load(monkeypatch, tmp_path, CLIO_HEARTBEAT_FILE=str(heartbeat))
    age = module.heartbeat_age_s()
    assert age is not None and 0 <= age < 5
    module2 = _load(monkeypatch, tmp_path, CLIO_HEARTBEAT_FILE=str(tmp_path / "missing"))
    assert module2.heartbeat_age_s() is None


def test_an_audit_regression_triggers_enforcement(tmp_path, monkeypatch):
    module = _load(monkeypatch, tmp_path)
    (module.OOB_DIR).mkdir(parents=True, exist_ok=True)
    (module.OOB_DIR / "anchors.log").write_text(
        '2026-01-01T00:00:00+00:00 {"seq": 42, "hash": "abc"}\n', encoding="utf-8",
    )
    anchor = tmp_path / "anchor.json"
    anchor.write_text('{"seq": 40, "hash": "abc"}', encoding="utf-8")
    module.ANCHOR_FILE = str(anchor)
    enforced: list[str] = []
    monkeypatch.setattr(module, "enforce", lambda reason: enforced.append(reason))
    module.append_anchor()
    assert any("regression" in reason for reason in enforced)


def test_stop_file_is_the_kill_switch(tmp_path, monkeypatch):
    module = _load(monkeypatch, tmp_path)
    module.OOB_DIR.mkdir(parents=True, exist_ok=True)
    assert not module.stop_file_present()
    (module.OOB_DIR / "stop").write_text("", encoding="utf-8")
    assert module.stop_file_present()
