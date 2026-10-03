from __future__ import annotations

import json

from ethos.config import QuirksConfig
from ethos.gateway.normalizer import (
    build_name_map,
    estimate_tokens,
    messages_to_anthropic,
    messages_to_google,
    messages_to_openai_compat,
    messages_to_openai_responses,
    response_from_anthropic,
    response_from_google,
    response_from_openai_compat,
    response_from_openai_responses,
    sanitize_declarations_for_google,
    sanitize_tool_name,
)
from ethos.schemas.models import (
    ModelMessage,
    Role,
    ThinkingBlock,
)
from ethos.schemas.tools import ToolCall
from tests.conftest import simple_request, tool_specs

QUIRKS = QuirksConfig()


def test_sanitize_tool_name_replaces_dots():
    assert sanitize_tool_name("fs.read") == "fs__read"
    assert sanitize_tool_name("shell.run") == "shell__run"
    assert sanitize_tool_name("plain") == "plain"


def test_name_map_roundtrip():
    tools = tool_specs()
    name_map = build_name_map(tools)
    assert "fs__read" in name_map
    assert name_map["fs__read"] == "fs.read"


def test_anthropic_system_cache_breakpoint():
    request = simple_request()
    payload = messages_to_anthropic(request, "claude-x", QUIRKS.anthropic)
    system = payload["system"]
    assert len(system) == 3
    assert "cache_control" not in system[0]
    assert "cache_control" not in system[1]
    assert system[2]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "identity"


def test_anthropic_no_cache_without_stable_prefix():
    request = simple_request(cache_prefix_blocks=0)
    payload = messages_to_anthropic(request, "claude-x", QUIRKS.anthropic)
    for block in payload["system"]:
        assert "cache_control" not in block


def test_anthropic_thinking_payload_and_temperature():
    request = simple_request(thinking_budget=4096, temperature=0.3)
    payload = messages_to_anthropic(request, "claude-x", QUIRKS.anthropic)
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert "temperature" not in payload


def test_anthropic_preserves_thinking_blocks_with_signature():
    request = simple_request()
    request = request.model_copy(update={
        "messages": request.messages + [
            ModelMessage(
                role=Role.assistant,
                content="I will check the file.",
                thinking=[ThinkingBlock(text="hmm", signature="sig123")],
                tool_calls=[ToolCall(id="tu_1", name="fs.read", arguments={"path": "x"})],
            ),
            ModelMessage(role=Role.tool, tool_call_id="tu_1", content="file contents"),
        ],
    })
    payload = messages_to_anthropic(request, "claude-x", QUIRKS.anthropic)
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    thinking_block = next(b for b in assistant["content"] if b["type"] == "thinking")
    assert thinking_block["thinking"] == "hmm"
    assert thinking_block["signature"] == "sig123"
    tool_use = next(b for b in assistant["content"] if b["type"] == "tool_use")
    assert tool_use["name"] == "fs__read"
    assert tool_use["input"] == {"path": "x"}
    tool_result = [b for m in payload["messages"] for b in m.get("content", []) if b.get("type") == "tool_result"]
    assert tool_result[0]["tool_use_id"] == "tu_1"


def test_anthropic_merges_consecutive_tool_results():
    request = simple_request()
    request = request.model_copy(update={
        "messages": request.messages + [
            ModelMessage(role=Role.tool, tool_call_id="a", content="1"),
            ModelMessage(role=Role.tool, tool_call_id="b", content="2"),
        ],
    })
    payload = messages_to_anthropic(request, "claude-x", QUIRKS.anthropic)
    tool_results = [
        b for m in payload["messages"] for b in m.get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert len(tool_results) == 2
    users = [m for m in payload["messages"] if m["role"] == "user" and
             any(b.get("type") == "tool_result" for b in m["content"])]
    assert len(users) == 1


def test_anthropic_response_normalization():
    data = {
        "content": [
            {"type": "thinking", "thinking": "pondering", "signature": "s"},
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "tu_9", "name": "fs__read", "input": {"path": "y"}},
        ],
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 3,
            "cache_read_input_tokens": 7,
        },
    }
    response = response_from_anthropic(data, "anthropic", "claude-x", {"fs__read": "fs.read"})
    assert response.text == "hello"
    assert response.thinking_blocks[0].signature == "s"
    assert response.tool_calls[0].name == "fs.read"
    assert response.tool_calls[0].id == "tu_9"
    assert response.usage.input_tokens == 10
    assert response.usage.cached_input_tokens == 7
    assert response.cache_creation is True
    assert response.cache_read is True
    assert response.finish_reason == "tool_use"


def test_openai_responses_payload():
    request = simple_request(thinking_budget=20000)
    request = request.model_copy(update={
        "messages": request.messages + [
            ModelMessage(
                role=Role.assistant,
                tool_calls=[ToolCall(id="call_1", name="fs.read", arguments={"path": "z"})],
            ),
            ModelMessage(role=Role.tool, tool_call_id="call_1", content="done"),
        ],
    })
    payload = messages_to_openai_responses(request, "o3", QUIRKS.openai)
    assert payload["instructions"].startswith("identity")
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "high"}
    fn_call = next(i for i in payload["input"] if i.get("type") == "function_call")
    assert fn_call["name"] == "fs__read"
    assert json.loads(fn_call["arguments"]) == {"path": "z"}
    fn_output = next(i for i in payload["input"] if i.get("type") == "function_call_output")
    assert fn_output["call_id"] == "call_1"
    assert fn_output["output"] == "done"
    tools = payload["tools"]
    assert tools[0]["type"] == "function"
    assert tools[0]["name"] == "fs__read"


def test_openai_reasoning_effort_mapping():
    from ethos.gateway.normalizer import _effort_for_budget

    assert _effort_for_budget(100) == "low"
    assert _effort_for_budget(4000) == "medium"
    assert _effort_for_budget(None) is None
    assert _effort_for_budget(90000) == "high"


def test_openai_responses_response_normalization():
    data = {
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "answer"}]},
            {"type": "function_call", "call_id": "call_2", "name": "shell__run",
             "arguments": "{\"command\": \"ls\"}"},
        ],
        "status": "completed",
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 12},
        },
    }
    response = response_from_openai_responses(data, "openai", "o3", {"shell__run": "shell.run"})
    assert response.text == "answer"
    assert response.thinking_blocks[0].raw["type"] == "reasoning"
    assert response.tool_calls[0].arguments == {"command": "ls"}
    assert response.usage.cached_input_tokens == 40
    assert response.usage.reasoning_tokens == 12


def test_google_sanitizes_declarations():
    from ethos.config import load_config

    quirks = load_config("config").quirks.google
    tools = tool_specs()
    declarations = sanitize_declarations_for_google(tools, quirks)
    params = declarations[0]["parameters"]
    assert "additionalProperties" not in params
    assert params["type"] == "object"
    assert "propertyOrdering" in params
    assert params["propertyOrdering"] == ["path"]
    assert declarations[0]["name"] == "fs__read"


def test_google_payload_structure():
    request = simple_request()
    payload = messages_to_google(request, "gemini-2.5-pro", QUIRKS.google)
    assert payload["systemInstruction"]["parts"][0]["text"].startswith("identity")
    assert payload["contents"][0]["role"] == "user"
    assert payload["generationConfig"]["maxOutputTokens"] == 1024


def test_google_thinking_config():
    request = simple_request(thinking_budget=8192)
    payload = messages_to_google(request, "gemini-2.5-pro", QUIRKS.google)
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 8192}


def test_google_function_response_for_tool_messages():
    request = simple_request()
    request = request.model_copy(update={
        "messages": request.messages + [
            ModelMessage(role=Role.tool, tool_call_id="gcall-0", name="fs.read", content="data"),
        ],
    })
    payload = messages_to_google(request, "gemini-2.5-pro", QUIRKS.google)
    parts = [p for c in payload["contents"] for p in c["parts"] if "functionResponse" in p]
    assert parts[0]["functionResponse"]["name"] == "fs__read"
    assert parts[0]["functionResponse"]["response"] == {"result": "data"}


def test_google_response_normalization():
    data = {
        "candidates": [{
            "content": {"parts": [
                {"text": "bonjour"},
                {"functionCall": {"name": "fs__read", "args": {"path": "q"}}},
            ]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10,
                           "cachedContentTokenCount": 5, "thoughtsTokenCount": 3},
    }
    response = response_from_google(data, "google", "gemini-2.5-pro", {"fs__read": "fs.read"})
    assert response.text == "bonjour"
    assert response.tool_calls[0].name == "fs.read"
    assert response.usage.input_tokens == 20
    assert response.usage.reasoning_tokens == 3
    assert response.finish_reason == "STOP"


def test_openai_compat_payload_and_response():
    request = simple_request()
    request = request.model_copy(update={
        "messages": request.messages + [
            ModelMessage(role=Role.assistant, content="partial",
                         thinking=[ThinkingBlock(text="chain")],
                         tool_calls=[ToolCall(id="cc", name="shell.run",
                                              arguments={"command": "pwd"})]),
            ModelMessage(role=Role.tool, tool_call_id="cc", content="/tmp"),
        ],
    })
    payload = messages_to_openai_compat(request, "llama", QUIRKS.openai_compat)
    system = payload["messages"][0]
    assert system["role"] == "system"
    assert system["content"].startswith("identity")
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "chain"
    assert assistant["tool_calls"][0]["function"]["name"] == "shell__run"
    tool_msg = next(m for m in payload["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "cc"
    assert payload["tool_choice"] == "auto"

    data = {
        "choices": [{
            "message": {"content": "hi", "reasoning_content": "thinking...",
                       "tool_calls": [{"id": "t9", "type": "function",
                                       "function": {"name": "shell__run",
                                                    "arguments": "{\"command\": \"ls\"}"}}]},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 33, "completion_tokens": 11,
                  "prompt_tokens_details": {"cached_tokens": 9},
                  "completion_tokens_details": {"reasoning_tokens": 4}},
    }
    response = response_from_openai_compat(data, "openai_compat", "llama", {"shell__run": "shell.run"})
    assert response.text == "hi"
    assert response.thinking_blocks[0].text == "thinking..."
    assert response.tool_calls[0].arguments == {"command": "ls"}
    assert response.usage.cached_input_tokens == 9
    assert response.usage.reasoning_tokens == 4


def test_estimate_tokens_covers_tools_and_messages():
    request = simple_request()
    tokens = estimate_tokens(request)
    assert tokens >= (len("identity") + len("tools") + len("limits") + len("hello")) // 4
    assert tokens >= 64


def test_cache_prefix_blocks_default():
    assert simple_request().cache_prefix_blocks == 3


def test_openai_compat_reads_the_reasoning_openrouter_actually_sends():
    """`reasoning_content` is one spelling of it; `reasoning` is the other.

    Both are in the wild on the same endpoint, and only the first was read. So
    for every reasoning model reached through OpenRouter the gateway saw a reply
    with no reasoning in it — which is the information that says how much of the
    room thinking took, and a reply that is *nothing but* thinking looks exactly
    like a reply that is nothing at all.
    """
    data = {
        "choices": [{
            "message": {
                "content": '{"action": "reply_now"}',
                "reasoning": "let me weigh it",
                "reasoning_details": [{"type": "reasoning.text", "text": "let me weigh it"}],
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    response = response_from_openai_compat(data, "openai_compat", "m", {})
    assert response.thinking_blocks, "the reasoning is not thrown away on the way in"
    assert response.thinking_blocks[0].text == "let me weigh it"
    assert response.thinking_blocks[0].raw, \
        "and it keeps the provider's own record of it, so a continued turn can send it back"


def test_openai_compat_reports_reasoning_with_no_answer_as_unusable():
    """A body of thinking and no content is not a reply, whichever spelling.

    This is the shape a reasoning model returns when it spends the whole of
    `max_tokens` thinking: `finish_reason: length`, thousands of characters of
    reasoning, and nothing written for the caller. It used to pass the gateway's
    readability gate on the strength of having reasoning attached, which is right
    for a tool call and wrong for everything else.
    """
    data = {
        "choices": [{
            "message": {"content": None, "reasoning": "thinking for a very long time"},
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2400},
    }
    response = response_from_openai_compat(data, "openai_compat", "m", {})
    assert response.text is None
    assert response.thinking_blocks, "which is what used to make it look like an answer"


def test_a_reasoning_budget_becomes_the_endpoints_own_parameter():
    """Bounded thinking, on the wire, for the endpoints that understand it."""
    request = simple_request(reasoning_budget=400, max_tokens=8000)
    payload = messages_to_openai_compat(request, "thinking-model", QUIRKS.openai_compat)
    assert payload["reasoning"] == {"max_tokens": 400}
    assert payload["max_tokens"] == 8000, \
        "the ceiling stays whole; only what thinking may take of it is bounded"


def test_no_reasoning_budget_sends_no_reasoning_parameter():
    """A model that does not reason has nothing to apply it to."""
    request = simple_request()
    payload = messages_to_openai_compat(request, "plain-model", QUIRKS.openai_compat)
    assert "reasoning" not in payload


def test_invented_reasoning_is_not_echoed_back_as_the_providers_own():
    """Only reasoning that came from this endpoint is sent back to it.

    Endpoints that reason inside a tool loop generally want their reasoning
    returned with the structured details that made it up, and reject reasoning
    without them. A block this program made up has no details, so it stays where
    it was — which is what happened to every block before this existed.
    """
    request = simple_request()
    request.messages.append(ModelMessage(
        role=Role.assistant, content="hi", thinking=[ThinkingBlock(text="mine")],
    ))
    payload = messages_to_openai_compat(request, "m", QUIRKS.openai_compat)
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert "reasoning_details" not in assistant
    assert "reasoning" not in assistant

    request.messages[-1] = ModelMessage(
        role=Role.assistant, content="hi",
        thinking=[ThinkingBlock(text="theirs", raw={"type": "reasoning.text", "text": "theirs"})],
    )
    payload = messages_to_openai_compat(request, "m", QUIRKS.openai_compat)
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_details"] == [{"type": "reasoning.text", "text": "theirs"}]
    assert assistant["reasoning"] == "theirs"
