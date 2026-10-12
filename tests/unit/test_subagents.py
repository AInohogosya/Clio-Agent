from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ethos.config import (
    EthosConfig,
    SubagentConfig,
    SubagentsConfig,
    load_shipped_base_model,
)
from ethos.gateway import compat
from ethos.schemas.models import NormalizedResponse, Tier, Usage
from ethos.subagents import SubagentRegistry
from ethos.subagents.apex import ApexSubAgent
from ethos.subagents.base import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_OUTCOME_UNKNOWN,
    SubAgentResult,
)
from ethos.subagents.foundry import FoundrySubAgent

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---- the base model ---------------------------------------------------------

def test_the_shipped_base_model_parses():
    """`config/base_model.yaml` is the deployment's own base model — it must
    parse, name a supported wire protocol, and be pinned into the catalogue."""
    base = load_shipped_base_model(REPO_ROOT / "config")
    assert base is not None
    assert base.provider in ("anthropic", "openai", "google", "openai_compat")
    entry = base.entry()
    assert entry.pinned is True
    assert entry.model


def test_the_base_model_wins_the_catalogue():
    base = load_shipped_base_model(REPO_ROOT / "config")
    assert base is not None
    cfg = EthosConfig(models={"base": base.model_dump()})
    assert cfg.models.resolved()[0].pinned is True


# ---- the OpenAI-compatible shim --------------------------------------------

def test_a_chat_body_becomes_a_model_request():
    body = {
        "model": "some-unknown-alias",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 512,
        "temperature": 0.2,
    }
    request = compat.openai_chat_to_model_request(body, known_models=frozenset())
    assert request.tier == Tier.T2
    assert request.max_tokens == 512
    assert request.temperature == 0.2
    assert [m.role.value for m in request.messages] == ["system", "user"]
    assert request.model_hint is None
    assert request.metadata["requested_model"] == "some-unknown-alias"


def test_a_known_model_name_is_a_hint_and_an_unknown_one_is_not():
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "x"}]}
    request = compat.openai_chat_to_model_request(
        body, known_models=frozenset({"claude-sonnet-4-5"}),
    )
    assert request.model_hint == "claude-sonnet-4-5"
    assert "requested_model" not in request.metadata


def test_content_parts_arrive_as_text_and_attachments():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is on screen?"},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/jpeg;base64,QUJD",
                    }},
                ],
            },
        ],
    }
    request = compat.openai_chat_to_model_request(body)
    message = request.messages[0]
    assert message.content == "what is on screen?"
    assert message.attachments == [
        {"type": "image", "media_type": "image/jpeg", "data": "QUJD"},
    ]


def test_a_json_schema_response_format_becomes_an_instruction():
    """Structured output is a property of the endpoint; the router may hand the
    request to any provider in the catalogue. The constraint travels as an
    instruction, which every model honours, rather than a field one ignores."""
    schema = {"type": "object", "properties": {"decision": {"type": "string"}}}
    body = {
        "messages": [{"role": "user", "content": "plan"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "tactical_plan", "strict": True, "schema": schema},
        },
    }
    request = compat.openai_chat_to_model_request(body)
    first = request.messages[0]
    assert first.role.value == "system"
    assert "tactical_plan" in (first.content or "")
    assert "decision" in (first.content or "")


def test_tool_definitions_and_history_survive_the_translation():
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "patch__DOT__write", "arguments": '{"path": "x"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "patch__DOT__write",
                "description": "write a patch",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }],
    }
    request = compat.openai_chat_to_model_request(body)
    assistant, tool = request.messages
    assert assistant.tool_calls[0].name == "patch__DOT__write"
    assert assistant.tool_calls[0].arguments == {"path": "x"}
    assert tool.role.value == "tool"
    assert tool.tool_call_id == "call_1"
    assert request.tools[0].name == "patch__DOT__write"
    assert request.tools[0].input_schema == {
        "type": "object", "properties": {"path": {"type": "string"}},
    }


def test_a_normalized_response_round_trips_to_chat_shape():
    resp = NormalizedResponse(
        text="done",
        usage=Usage(input_tokens=100, cached_input_tokens=10, output_tokens=20, reasoning_tokens=5),
        finish_reason="stop",
        model="the-real-model",
    )
    out = compat.normalized_to_chat_response(resp, requested_model="clio-agent-3-beta")
    assert out["model"] == "clio-agent-3-beta"
    assert out["choices"][0]["message"]["content"] == "done"
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"]["prompt_tokens"] == 110
    assert out["usage"]["completion_tokens"] == 25
    assert out["usage"]["total_tokens"] == 135


def test_tool_calls_round_trip_through_the_streaming_shape():
    """Foundry streams first, and reconstructs tool calls from accumulated
    deltas — the SSE shape must carry them or a delegation loses its tools."""
    from ethos.schemas.models import ToolCall

    resp = NormalizedResponse(
        text=None,
        tool_calls=[ToolCall(id="call_9", name="patch__DOT__write", arguments={"path": "x"})],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    chunks = compat.chat_stream_chunks(resp, requested_model="m")
    lines = [line.decode() for line in compat.sse_bytes(chunks)]
    assert lines[-1] == "data: [DONE]\n\n"
    deltas = [json.loads(line[6:]) for line in lines[:-1] if line.startswith("data: ")]
    tool_chunks = [d for d in deltas if d["choices"][0]["delta"].get("tool_calls")]
    assert tool_chunks, "tool_calls must appear as stream deltas"
    assert tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "patch__DOT__write"


# ---- config & registry ------------------------------------------------------

def _config_with(mode: str = "auto", **agent_overrides: Any) -> EthosConfig:
    cfg = EthosConfig()
    cfg.subagents = SubagentsConfig(agents=[
        SubagentConfig(name="foundry", mode=mode, **agent_overrides),
        SubagentConfig(name="apex", mode=mode),
    ])
    return cfg


def test_the_registry_knows_both_programs():
    registry = SubagentRegistry(_config_with())
    assert "foundry" in registry.names()
    assert "apex" in registry.names()
    rows = registry.describe()
    names = {row["name"] for row in rows}
    assert {"foundry", "apex"} <= names


def test_a_disabled_agent_is_not_built():
    cfg = _config_with()
    cfg.subagents.agents[1].enabled = False
    registry = SubagentRegistry(cfg)
    assert "foundry" in registry.names()
    assert "apex" not in registry.names()


def test_an_unknown_agent_name_is_a_clean_unavailable():
    registry = SubagentRegistry(_config_with())
    result = asyncio.run(registry.run("nobody", "do a thing"))
    assert result["status"] == "unavailable"


def test_subagents_derive_their_gateway_url_from_config():
    cfg = _config_with()
    registry = SubagentRegistry(cfg, gateway=None)
    agent = registry.get("foundry")
    assert agent is not None
    assert agent.gateway_url() == f"http://127.0.0.1:{cfg.gateway.port}"
    cfg.gateway.port = 9999
    assert agent.gateway_url() == "http://127.0.0.1:9999"


# ---- foundry adapter --------------------------------------------------------

def test_foundry_summary_maps_to_the_status_vocabulary():
    agent = FoundrySubAgent(_config_with(), SubagentConfig(name="foundry"), gateway=None)
    started = 0.0
    done = agent._result_from_summary(
        {"tasks_by_status": {"MERGED": 3}, "objectives": [{"status": "done"}]}, "inprocess", started,
    )
    assert done.status == STATUS_DONE
    assert "3/3" in done.summary
    partial = agent._result_from_summary(
        {"tasks_by_status": {"MERGED": 1, "BLOCKED": 2}, "objectives": [{"status": "done"}]},
        "inprocess", started,
    )
    assert partial.status == STATUS_BLOCKED
    nothing = agent._result_from_summary(
        {"tasks_by_status": {}, "objectives": [{"status": "planning"}]}, "inprocess", started,
    )
    assert nothing.status == STATUS_FAILED


def test_foundry_subprocess_env_points_at_the_gateway():
    cfg = _config_with()
    cfg.gateway.port = 8700
    agent = FoundrySubAgent(cfg, SubagentConfig(name="foundry"), gateway=None)
    _python, env, _repo = agent._python_and_env(None)
    assert env["FOUNDRY_LLM_ENDPOINT"] == "http://127.0.0.1:8700/v1"
    assert env["FOUNDRY_LLM_PROVIDER"] == "openai"
    assert env["FOUNDRY_LLM_MODEL"] == agent.model_name()


def test_foundry_health_reports_the_import_path():
    agent = FoundrySubAgent(_config_with(), SubagentConfig(name="foundry"), gateway=None)
    health = asyncio.run(agent.health())
    assert health["available"] is True


# ---- apex adapter -----------------------------------------------------------

class _FakeAction:
    def __init__(self, error: str | None = None) -> None:
        self.error = error


class _FakeSnapshot:
    def __init__(self, mode: str, steps: int = 4, error: str | None = None) -> None:
        self.task_id = "task-1"
        self.mode = mode
        self.steps_used = steps
        self.last_error = error
        self.actions = (_FakeAction(error),)


def test_apex_task_modes_map_to_the_status_vocabulary():
    agent = ApexSubAgent(_config_with(), SubagentConfig(name="apex"), gateway=None)
    done = agent._result_from_snapshot(_FakeSnapshot("COMPLETED"), "inprocess")
    assert done.status == STATUS_DONE
    blocked = agent._result_from_snapshot(_FakeSnapshot("BLOCKED"), "inprocess")
    assert blocked.status == STATUS_BLOCKED
    unknown = agent._result_from_snapshot(_FakeSnapshot("BLOCKED_UNKNOWN"), "inprocess")
    assert unknown.status == STATUS_OUTCOME_UNKNOWN
    failed = agent._result_from_snapshot(_FakeSnapshot("FAILED"), "inprocess")
    assert failed.status == STATUS_FAILED
    assert failed.summary  # an outcome is always summarized, whatever it was


def test_apex_subprocess_env_names_the_ethos_provider():
    cfg = _config_with()
    cfg.gateway.port = 8700
    agent = ApexSubAgent(cfg, SubagentConfig(name="apex"), gateway=None)
    env = agent.child_env({
        "LLM_PROVIDER": "ethos",
        "ETHOS_GATEWAY_URL": agent.gateway_url(),
    })
    assert env["LLM_PROVIDER"] == "ethos"
    assert env["ETHOS_GATEWAY_URL"] == "http://127.0.0.1:8700"


# ---- the shim, served -------------------------------------------------------

class _StubService:
    """Just enough gateway for `create_gateway_app`'s compat routes: one
    scripted response, one catalogue, nothing on the network."""

    def __init__(self, config: Any, response: NormalizedResponse) -> None:
        self.config = config
        self._response = response

    async def complete(self, request: Any) -> NormalizedResponse:
        return self._response


def test_the_served_shim_answers_the_openai_wire():
    from fastapi.testclient import TestClient

    from ethos.config import ModelsConfig
    from ethos.gateway.service import create_gateway_app

    cfg = EthosConfig()
    cfg.models = ModelsConfig()  # catalogue empty; every model name routes
    resp = NormalizedResponse(
        text="hello from the catalogue",
        usage=Usage(input_tokens=12, output_tokens=4),
        model="the-model-that-answered",
    )
    client = TestClient(create_gateway_app(_StubService(cfg, resp)))

    listed = client.get("/v1/models").json()
    assert listed["object"] == "list"

    out = client.post("/v1/chat/completions", json={
        "model": "clio-agent-3-beta",
        "messages": [{"role": "user", "content": "ping"}],
    })
    assert out.status_code == 200
    body = out.json()
    assert body["choices"][0]["message"]["content"] == "hello from the catalogue"
    assert body["model"] == "clio-agent-3-beta"

    stream = client.post("/v1/chat/completions", json={
        "model": "clio-agent-3-beta",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": True,
    })
    assert stream.status_code == 200
    assert stream.text.rstrip().endswith("data: [DONE]")
    assert "hello from the catalogue" in stream.text


# ---- the model bridge -------------------------------------------------------

def test_the_foundry_bridge_routes_through_the_gateway():
    """The provider Foundry is handed is synchronous; the gateway is async on
    this loop. The hop must carry the request through intact — system prompt,
    conversation, token budget — and the reply back as an `LLMResponse`."""
    import asyncio
    import sys

    from tests.conftest import ScriptedGateway

    src = REPO_ROOT / "Foundry" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from foundry.llm.base import LLMRequest

    from ethos.subagents.bridge import FoundryGatewayProvider

    gateway = ScriptedGateway(['{"answer": 1}'])
    loop = asyncio.new_event_loop()
    try:
        provider = FoundryGatewayProvider(
            gateway, loop, agent_name="foundry", tier=Tier.T2,
            purpose="subagent.foundry.run", timeout_s=30.0,
        )
        # A real delegation calls the provider from a worker thread; the loop
        # has to be running elsewhere for the hop to be the real thing.
        import threading

        ready = threading.Event()

        def run_loop():
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        ready.wait(timeout=5)
        try:
            response = asyncio.run(asyncio.to_thread(
                provider.complete,
                LLMRequest(system="be brief",
                           messages=[{"role": "user", "content": "report"}],
                           max_output_tokens=1234),
            ))
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
    finally:
        loop.close()

    assert response.content == '{"answer": 1}'
    request = gateway.requests[0]
    assert request.tier == Tier.T2
    assert request.purpose == "subagent.foundry.run"
    assert request.metadata["subagent"] == "foundry"
    assert request.max_tokens == 1234
    assert request.messages[0].content == "be brief"
    assert request.messages[1].content == "report"


# ---- thread-role dispatch ---------------------------------------------------

class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(self, task: str, **opts: Any) -> SubAgentResult:
        self.calls.append((task, opts))
        return SubAgentResult(agent="apex", status=STATUS_DONE, summary="did it", mode="inprocess")


class _FakeRegistry:
    def __init__(self, agent: Any) -> None:
        self._agent = agent

    def get(self, name: str) -> Any:
        return self._agent if name == "apex" else None

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": "apex", "description": "desktop controller"}]


class _FakeMemory:
    def __init__(self) -> None:
        self.episodes: list[dict[str, Any]] = []

    async def record_episode(self, **kwargs: Any) -> None:
        self.episodes.append(kwargs)


def test_a_subagent_thread_role_dispatches_and_records(monkeypatch):
    """A `thread.spawn` with role `apex` runs the program and lands its outcome
    in the same episode memory a native thread uses."""
    import asyncio

    from ethos.threads import worker as thread_worker

    fake_agent = _FakeAgent()
    monkeypatch.setattr("ethos.subagents.SubagentRegistry", lambda *a, **k: _FakeRegistry(fake_agent))
    memory = _FakeMemory()
    spec = {
        "id": "thread-abc",
        "goal": "open TextEdit and write a note",
        "role": "apex",
        "intention_id": "12345678-1234-1234-1234-123456789012",
    }
    result = asyncio.run(thread_worker._run_subagent(
        "apex", spec, EthosConfig(), memory, gateway=None, workdir="/tmp/w",
    ))
    assert result["status"] == STATUS_DONE
    assert result["goal"].startswith("open TextEdit")
    assert fake_agent.calls[0][1]["workdir"] == "/tmp/w"
    assert len(memory.episodes) == 1
    assert memory.episodes[0]["intention_id"] is not None


def test_a_missing_subagent_role_reports_rather_than_crashes(monkeypatch):
    import asyncio

    from ethos.threads import worker as thread_worker

    monkeypatch.setattr("ethos.subagents.SubagentRegistry", lambda *a, **k: _FakeRegistry(_FakeAgent()))
    memory = _FakeMemory()
    spec = {"id": "thread-abc", "goal": "x", "role": "apex"}
    result = asyncio.run(thread_worker._run_subagent(
        "foundry", spec, EthosConfig(), memory, gateway=None, workdir="/tmp/w",
    ))
    assert result["status"] == "unavailable"
    assert "foundry" in result["error"]
