"""AXIOM as a sub-agent: the seam, the routing, and the honest verdicts.

AXIOM is the third program wired into the registry, and the tests here pin
what its delegation means: the engine config that points the program's own
client at the gateway shim (never at a vendor, never at the mock engine), the
result mapping from AXIOM's single-turn response to the status vocabulary, the
registry and tool wiring, and the one routing rule that keeps Foundry's
repo-scale class intact while bounded coding tasks learn a worker of their own.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from clio.config import ClioConfig, SubagentConfig
from clio.prompts.gsl import delegation_note
from clio.subagents import SubagentRegistry
from clio.subagents.axiom import AxiomSubAgent
from clio.subagents.base import (
    STATUS_DONE,
    STATUS_FAILED,
)
from clio.subagents.bridge import axiom_engine_config


def _config_with(**overrides: Any) -> ClioConfig:
    agents = [
        SubagentConfig(name="foundry"),
        SubagentConfig(name="apex"),
        SubagentConfig(name="axiom", **overrides),
    ]
    return ClioConfig(subagents={"agents": agents})


# ---- registry and wiring ------------------------------------------------------

def test_the_registry_knows_three_programs():
    """AXIOM ships enabled like Foundry and APEX: the default deployment has
    three workers, and the catalog describes all three."""
    registry = SubagentRegistry(_config_with())
    assert {"foundry", "apex", "axiom"} <= set(registry.names())
    rows = registry.describe()
    axiom = next(row for row in rows if row["name"] == "axiom")
    assert axiom["mode"] == "auto"
    assert "coding" in axiom["description"]


def test_the_default_deployment_names_axiom():
    """A config with no `subagents:` section still has the third worker."""
    assert ClioConfig().subagents.agent("axiom") is not None


def test_the_axiom_tool_is_in_the_catalog():
    from clio.subagents.tools import AxiomRunTool

    tool = AxiomRunTool()
    assert tool.name == "subagent.axiom"
    assert tool.input_schema["required"] == ["task"]


# ---- the bridge ---------------------------------------------------------------

def test_the_engine_config_points_at_the_gateway_not_a_vendor():
    """AXIOM's own config is built to ride this agent's gateway: the provider
    is pinned off litellm/auto, the base URL is the shim, and the key is the
    placeholder the shim does not check -- because without a usable base model
    the program would silently route to its mock engine."""

    class _Recording:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    built = axiom_engine_config(
        _Recording,
        gateway_url="http://127.0.0.1:8700/",
        model_name="gemma4:31b-cloud",
        workspace="/tmp/ws",
    )
    assert built.kwargs["provider"] == "openai"
    assert built.kwargs["base_provider"] == "openai"
    assert built.kwargs["base_url"] == "http://127.0.0.1:8700/v1"
    assert built.kwargs["model"] == "gemma4:31b-cloud"
    assert built.kwargs["api_key"] == "clio-gateway"
    assert built.kwargs["workspace"] == "/tmp/ws"


# ---- the verdict --------------------------------------------------------------

def test_a_successful_run_maps_to_done():
    agent = AxiomSubAgent(_config_with(), _config_with().subagents.agent("axiom"))
    response = SimpleNamespace(
        success=True,
        summary="Added the parser and its tests.",
        run_id="r_abc",
        provider="openai-compat",
        deliverables=["src/parser.py"],
        changes=[
            SimpleNamespace(path="src/parser.py", action="created"),
            SimpleNamespace(path="tests/test_parser.py", action="created"),
        ],
        verification=SimpleNamespace(verdict="pass", score=1.0),
    )
    result = agent._result_from_response(response, "inprocess", 0.0)
    assert result.status == STATUS_DONE
    assert "parser" in result.summary
    assert result.detail["verification"] == {"verdict": "pass", "score": 1.0}
    assert len(result.detail["changes"]) == 2


def test_a_failed_run_maps_to_failed_and_says_why():
    """`success=False` is the program's own verdict -- the gauntlet did not
    pass, nothing was committed. The delegation reports failed, never done."""
    agent = AxiomSubAgent(_config_with(), _config_with().subagents.agent("axiom"))
    response = SimpleNamespace(
        success=False,
        summary="",
        run_id="r_def",
        provider="openai-compat",
        deliverables=[],
        changes=[],
        verification=SimpleNamespace(verdict="fail", score=0.2),
    )
    result = agent._result_from_response(response, "inprocess", 0.0)
    assert result.status == STATUS_FAILED
    assert "verification fail" in result.summary


def test_the_mock_engine_is_never_a_verdict(monkeypatch):
    """With no bridge engaged, AXIOM runs its deterministic mock engine and
    would report `done` with confident-sounding fiction. The adapter refuses:
    an in-process run whose reasoning engine is the mock fails the delegation
    instead of completing it."""

    class _FakeLLM:
        provider = "mock"

    class _FakeOrchestrator:
        def __init__(self, config: Any) -> None:
            self.llm = _FakeLLM()

    def _fake_import(self):
        return SimpleNamespace, _FakeOrchestrator

    monkeypatch.setattr(AxiomSubAgent, "_import_engine", _fake_import)
    cfg = _config_with()
    cfg.subagents.agents[2].mode = "inprocess"
    agent = AxiomSubAgent(cfg, cfg.subagents.agent("axiom"))
    result = asyncio.run(agent.run("write a function", workdir="/tmp/ws"))
    assert result.status == STATUS_FAILED
    assert "mock" in (result.error or "")


# ---- health -------------------------------------------------------------------

def test_health_reports_the_subprocess_path_when_import_fails(tmp_path):
    """AXIOM's dependencies live in its own virtualenv; a checkout without one
    is still a real program -- health says so, and names the bootstrap."""
    cfg = _config_with()
    cfg.subagents.agents[2].root = str(tmp_path)
    agent = AxiomSubAgent(cfg, cfg.subagents.agent("axiom"))
    info = asyncio.run(agent.health())
    assert info["available"] is False
    assert "subprocess" in info["import"]
    assert "AXIOM/.venv" in info["import"]


# ---- routing ------------------------------------------------------------------

def test_a_bounded_coding_task_routes_to_axiom_without_a_repo_named():
    """'Fix the bug in the parser' names no repository: Foundry's cues are
    quiet, and the task still has a worker. The cue sentence routes by what
    the work is, not by who is named."""
    intention = SimpleNamespace(
        title="Fix the bug where the parser drops empty lines",
        desired_end_state="the parser keeps empty lines and the tests pass",
        constraints=None,
    )
    note = delegation_note(intention)
    assert "subagent.axiom`" in note
    assert "AXIOM's job by what the work is" in note


def test_naming_axiom_is_binding_like_the_other_workers():
    """A person who writes "use AXIOM" has made the routing decision. The
    named-worker sentence must fire for AXIOM exactly as it does for APEX and
    Foundry; otherwise the request can be silently re-routed or done by hand."""
    intention = SimpleNamespace(
        title="Have AXIOM clean up the README",
        desired_end_state="the README matches the current flags",
        constraints=None,
    )
    note = delegation_note(intention)
    assert "That choice is binding" in note
    assert "`subagent.axiom`" in note


def test_repo_scale_wording_stays_with_foundry():
    """The AXIOM cue is checked only when Foundry's are quiet. Work that names
    the repository is Foundry's by its own cues; the note must not send it to
    two workers."""
    intention = SimpleNamespace(
        title="Implement pagination in the repository's API layer",
        desired_end_state="merged and deployed",
        constraints=None,
    )
    note = delegation_note(intention)
    assert "subagent.foundry`" in note
    # The prompt text names AXIOM in its own bullet; what must be absent is
    # the routed cue sentence that would send this work to a second worker.
    assert "AXIOM's job by what the work is" not in note
