from __future__ import annotations

import json
import re
from typing import Any

from ethos.config import ProviderQuirks
from ethos.schemas.models import (
    ModelMessage,
    ModelRequest,
    NormalizedResponse,
    Role,
    ThinkingBlock,
    Tier,
    Usage,
)
from ethos.schemas.tools import ToolCall, ToolSpec

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_name(name: str) -> str:
    return _NAME_RE.sub("__", name)[:64]


def build_name_map(tools: list[ToolSpec]) -> dict[str, str]:
    return {sanitize_tool_name(t.name): t.name for t in tools}


def _stable_system_text(request: ModelRequest) -> tuple[list[str], list[ModelMessage]]:
    system_msgs: list[ModelMessage] = []
    rest: list[ModelMessage] = []
    for msg in request.messages:
        if msg.role == Role.system:
            system_msgs.append(msg)
        else:
            rest.append(msg)
    return [m.content or "" for m in system_msgs], rest


def _assistant_has_content(msg: ModelMessage) -> bool:
    return bool(msg.content or msg.tool_calls or msg.thinking)


def _collapse_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Merge consecutive tool-role messages into one turn with attachment entries."""
    out: list[ModelMessage] = []
    for msg in messages:
        if msg.role == Role.tool and out and out[-1].role == Role.tool:
            prev = out[-1]
            prev.attachments.append({
                "tool_call_id": msg.tool_call_id,
                "name": msg.name,
                "content": msg.content,
            })
        else:
            m = msg.model_copy()
            if m.role == Role.tool:
                m.attachments = [{
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                    "content": m.content,
                }]
            out.append(m)
    return out


def _json_loads_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {"_raw": raw}
    return {}


def reasoning_from_openai_compat(message: dict[str, Any]) -> str | None:
    """The chain of thought an OpenAI-compatible endpoint reported, if it did.

    Two spellings, and both of them are in the wild because they are two
    different conventions riding the same endpoint. DeepSeek-R1 and the
    aggregators that copied it send `reasoning_content`; OpenRouter sends
    `reasoning` (plus a structured `reasoning_details`). Only the first was
    read, so for every reasoning model reached through OpenRouter the gateway
    saw a reply with no reasoning in it at all.

    That is not a cosmetic gap. Reasoning tokens are billed inside
    `completion_tokens` and are spent out of the *same* `max_tokens` budget as
    the answer, so "how much of the room did thinking take" is the difference
    between an answer that arrived and an empty body. A response that is
    nothing but thinking looked exactly like a response that was nothing at
    all, `GatewayService._readable` called it an empty body, and a caller
    asking for JSON got `{}` back and a recorded silence in place of the
    decision it had paid for.
    """
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _truncate_tools(tools: list[ToolSpec], limit: int) -> list[ToolSpec]:
    return tools[:limit] if limit else tools


# Every provider spells "I ran out of output room" differently, and the gateway
# passes each provider's own word straight through into `finish_reason`:
# Anthropic reports `stop_reason: max_tokens`, the OpenAI Responses API reports
# `status: incomplete`, Chat Completions reports `finish_reason: length`, and
# Google reports `finishReason: MAX_TOKENS`. Compared case-insensitively, all
# four are the same event, and it is the one event that turns a valid-looking
# prefix into unparseable JSON.
#
# Normalized here rather than at each call site because that is what this module
# is for, and because a caller that recognises only its own provider's word
# reads every other provider's truncation as a complete answer.
TRUNCATION_REASONS = frozenset({"length", "max_tokens", "incomplete"})


def was_truncated(finish_reason: str | None) -> bool:
    return bool(finish_reason) and finish_reason.strip().lower() in TRUNCATION_REASONS


# ---------------------------------------------------------------- Anthropic

def messages_to_anthropic(request: ModelRequest, entry_model: str, quirks: ProviderQuirks) -> dict[str, Any]:
    system_texts, rest = _stable_system_text(request)
    rest = _collapse_messages(rest)
    tools = _truncate_tools(request.tools, int(quirks.model_dump().get("max_tools", 64)))

    system_blocks: list[dict[str, Any]] = []
    for i, text in enumerate(system_texts):
        block: dict[str, Any] = {"type": "text", "text": text}
        if quirks.model_dump().get("cache_control", True) and request.cache_prefix_blocks > 0 and i == request.cache_prefix_blocks - 1:
            block["cache_control"] = {"type": "ephemeral"}
        system_blocks.append(block)

    content_messages: list[dict[str, Any]] = []
    for msg in rest:
        if msg.role == Role.user:
            fenced = msg.content or ""
            content_messages.append({
                "role": "user",
                "content": [{"type": "text", "text": fenced}] if fenced else [],
            })
        elif msg.role == Role.assistant:
            if not _assistant_has_content(msg):
                continue
            blocks: list[dict[str, Any]] = []
            for tb in msg.thinking:
                block: dict[str, Any] = {"type": "thinking", "thinking": tb.text}
                if tb.signature:
                    block["signature"] = tb.signature
                blocks.append(block)
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": call.id,
                    "name": sanitize_tool_name(call.name),
                    "input": call.arguments,
                })
            content_messages.append({"role": "assistant", "content": blocks})
        elif msg.role == Role.tool:
            results: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": att.get("tool_call_id"),
                    "content": [{"type": "text", "text": str(att.get("content", ""))}],
                }
                for att in msg.attachments
            ]
            if results:
                if content_messages and content_messages[-1]["role"] == "user":
                    content_messages[-1]["content"].extend(results)
                else:
                    content_messages.append({"role": "user", "content": results})

    payload: dict[str, Any] = {
        "model": entry_model,
        "max_tokens": request.max_tokens,
        "messages": content_messages,
    }
    if system_blocks:
        payload["system"] = system_blocks
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    tools_wire = [
        {
            "name": sanitize_tool_name(t.name),
            "description": t.description,
            "input_schema": t.input_schema or {"type": "object", "properties": {}},
        }
        for t in tools
    ]
    if tools_wire:
        payload["tools"] = tools_wire
    if request.thinking_budget and int(quirks.model_dump().get("thinking", True)):
        payload["thinking"] = {"type": "enabled", "budget_tokens": int(request.thinking_budget)}
        payload.pop("temperature", None)
    return payload


def response_from_anthropic(
    data: dict[str, Any], provider: str, model: str, name_map: dict[str, str],
) -> NormalizedResponse:
    text_parts: list[str] = []
    thinking: list[ThinkingBlock] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "thinking":
            thinking.append(ThinkingBlock(text=block.get("thinking", ""), signature=block.get("signature")))
        elif block.get("type") == "tool_use":
            wire_name = block.get("name", "")
            tool_calls.append(ToolCall(
                id=block.get("id", ""),
                name=name_map.get(wire_name, wire_name),
                arguments=dict(block.get("input") or {}),
            ))
    usage_data = data.get("usage", {}) or {}
    usage = Usage(
        input_tokens=int(usage_data.get("input_tokens", 0)),
        cached_input_tokens=int(usage_data.get("cache_read_input_tokens", 0)),
        output_tokens=int(usage_data.get("output_tokens", 0)),
    )
    return NormalizedResponse(
        text="".join(text_parts) or None,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=data.get("stop_reason"),
        provider=provider,
        model=model,
        cache_creation=bool(usage_data.get("cache_creation_input_tokens")),
        cache_read=bool(usage_data.get("cache_read_input_tokens")),
        raw=data,
    )


# ------------------------------------------------------- OpenAI (Responses)

def _effort_for_budget(budget: int | None) -> str | None:
    if budget is None:
        return None
    if budget < 4000:
        return "low"
    if budget < 16000:
        return "medium"
    return "high"


def messages_to_openai_responses(request: ModelRequest, entry_model: str, quirks: ProviderQuirks) -> dict[str, Any]:
    system_texts, rest = _stable_system_text(request)
    input_items: list[dict[str, Any]] = []
    for msg in rest:
        if msg.role == Role.user:
            input_items.append({
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": msg.content or ""}],
            })
        elif msg.role == Role.assistant:
            for tb in msg.thinking:
                item: dict[str, Any] = {"type": "reasoning"}
                if tb.raw:
                    item.update(tb.raw)
                if tb.text:
                    item.setdefault("summary", [{"type": "summary_text", "text": tb.text}])
                input_items.append(item)
            if msg.content or msg.tool_calls:
                input_items.append({
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": msg.content or ""}],
                })
            for call in msg.tool_calls:
                input_items.append({
                    "type": "function_call",
                    "call_id": call.id,
                    "name": sanitize_tool_name(call.name),
                    "arguments": json.dumps(call.arguments or {}),
                })
        elif msg.role == Role.tool:
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.tool_call_id or "",
                "output": msg.content or "",
            })

    payload: dict[str, Any] = {
        "model": entry_model,
        "input": input_items,
        "store": False,
        "max_output_tokens": request.max_tokens,
    }
    if system_texts:
        payload["instructions"] = "\n\n".join(t for t in system_texts if t)
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    tools = _truncate_tools(request.tools, int(quirks.model_dump().get("max_tools", 128)))
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": sanitize_tool_name(t.name),
                "description": t.description,
                "parameters": t.input_schema or {"type": "object", "properties": {}},
                "strict": False,
            }
            for t in tools
        ]
    effort = _effort_for_budget(request.thinking_budget)
    if effort:
        payload["reasoning"] = {"effort": effort}
    return payload


def response_from_openai_responses(
    data: dict[str, Any], provider: str, model: str, name_map: dict[str, str],
) -> NormalizedResponse:
    text_parts: list[str] = []
    thinking: list[ThinkingBlock] = []
    tool_calls: list[ToolCall] = []
    for item in data.get("output", []) or []:
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
        elif item_type == "reasoning":
            thinking.append(ThinkingBlock(text="", raw=dict(item)))
        elif item_type == "function_call":
            wire_name = item.get("name", "")
            tool_calls.append(ToolCall(
                id=item.get("call_id", item.get("id", "")),
                name=name_map.get(wire_name, wire_name),
                arguments=_json_loads_args(item.get("arguments")),
            ))
    usage_data = data.get("usage", {}) or {}
    usage = Usage(
        input_tokens=int(usage_data.get("input_tokens", 0)),
        cached_input_tokens=int((usage_data.get("input_tokens_details") or {}).get("cached_tokens", 0)),
        output_tokens=int(usage_data.get("output_tokens", 0)),
        reasoning_tokens=int((usage_data.get("output_tokens_details") or {}).get("reasoning_tokens", 0)),
    )
    return NormalizedResponse(
        text="".join(text_parts) or None,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=data.get("status"),
        provider=provider,
        model=model,
        raw=data,
    )


# ------------------------------------------------------------------ Google

def _sanitize_schema_for_google(schema: dict[str, Any], unsupported: set[str]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in unsupported:
            continue
        if key == "type":
            out["type"] = str(value).lower()
        elif key in ("properties", "items"):
            if isinstance(value, dict):
                if key == "properties":
                    out["properties"] = {
                        pk: _sanitize_schema_for_google(pv, unsupported) for pk, pv in value.items()
                    }
                else:
                    out["items"] = _sanitize_schema_for_google(value, unsupported)
            else:
                out[key] = value
        else:
            out[key] = value
    if out.get("type") == "object" and "properties" in out:
        out["propertyOrdering"] = sorted(out["properties"].keys())
    return out


def sanitize_declarations_for_google(
    tools: list[ToolSpec], quirks: ProviderQuirks,
) -> list[dict[str, Any]]:
    quirks_data = quirks.model_dump()
    unsupported = set(quirks_data.get("unsupported_schema_keys", []))
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append({
            "name": sanitize_tool_name(t.name),
            "description": t.description,
            "parameters": _sanitize_schema_for_google(
                t.input_schema or {"type": "object", "properties": {}}, unsupported
            ),
        })
    return out


def messages_to_google(request: ModelRequest, entry_model: str, quirks: ProviderQuirks) -> dict[str, Any]:
    system_texts, rest = _stable_system_text(request)
    rest = _collapse_messages(rest)
    contents: list[dict[str, Any]] = []
    call_counter = 0

    def _add(role: str, parts: list[dict[str, Any]]) -> None:
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for msg in rest:
        if msg.role == Role.user:
            _add("user", [{"text": msg.content or ""}])
        elif msg.role == Role.assistant:
            parts: list[dict[str, Any]] = []
            for tb in msg.thinking:
                if tb.text:
                    parts.append({"thought": True, "text": tb.text})
            if msg.content:
                parts.append({"text": msg.content})
            for call in msg.tool_calls:
                call_counter += 1
                parts.append({"functionCall": {"name": sanitize_tool_name(call.name), "args": call.arguments or {}}})
            _add("model", parts)
        elif msg.role == Role.tool:
            for att in msg.attachments:
                _add("user", [{
                    "functionResponse": {
                        "name": sanitize_tool_name(att.get("name", "")),
                        "response": {"result": att.get("content", "")},
                    }
                }])
            if msg.content and not msg.attachments:
                _add("user", [{"text": f"tool_result[{msg.tool_call_id or ''}]: {msg.content or ''}"}])

    payload: dict[str, Any] = {
        "model": entry_model,
        "contents": contents,
    }
    if system_texts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
    tools = _truncate_tools(request.tools, int(quirks.model_dump().get("max_declarations", 32)))
    if tools:
        payload["tools"] = [{"functionDeclarations": sanitize_declarations_for_google(tools, quirks)}]
    generation: dict[str, Any] = {"maxOutputTokens": request.max_tokens}
    if request.temperature is not None:
        generation["temperature"] = request.temperature
    if request.thinking_budget:
        generation["thinkingConfig"] = {"thinkingBudget": int(request.thinking_budget)}
    payload["generationConfig"] = generation
    return payload


def response_from_google(
    data: dict[str, Any], provider: str, model: str, name_map: dict[str, str],
) -> NormalizedResponse:
    text_parts: list[str] = []
    thinking: list[ThinkingBlock] = []
    tool_calls: list[ToolCall] = []
    candidates = data.get("candidates") or []
    if candidates:
        parts = (candidates[0].get("content") or {}).get("parts", []) or []
        for i, part in enumerate(parts):
            if part.get("thought"):
                thinking.append(ThinkingBlock(text=part.get("text", "")))
            elif "text" in part:
                text_parts.append(part.get("text", ""))
            elif "functionCall" in part:
                fc = part["functionCall"]
                wire_name = fc.get("name", "")
                tool_calls.append(ToolCall(
                    id=f"gcall-{i}",
                    name=name_map.get(wire_name, wire_name),
                    arguments=dict(fc.get("args") or {}),
                ))
    usage_data = data.get("usageMetadata", {}) or {}
    usage = Usage(
        input_tokens=int(usage_data.get("promptTokenCount", 0)),
        cached_input_tokens=int(usage_data.get("cachedContentTokenCount", 0)),
        output_tokens=int(usage_data.get("candidatesTokenCount", 0)),
        reasoning_tokens=int(usage_data.get("thoughtsTokenCount", 0)),
    )
    finish = (candidates[0].get("finishReason") if candidates else None)
    return NormalizedResponse(
        text="".join(text_parts) or None,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=finish,
        provider=provider,
        model=model,
        raw=data,
    )


# -------------------------------------------------- OpenAI-compatible chat

def _echo_reasoning(wire: dict[str, Any], blocks: list[ThinkingBlock]) -> None:
    """Sends an assistant turn's own reasoning back, in the spelling it arrived in.

    Only when the block carries the provider's structured record of it. An
    OpenAI-compatible endpoint that reasons inside a tool loop generally wants
    its reasoning echoed together with the details that made it up -- reasoning
    with no details behind it is rejected by some of them -- and a block this
    program invented has neither. So a block from the provider round-trips and a
    block the agent made up stays where it was, which is what happened to both
    before this existed.
    """
    details = [dict(tb.raw) for tb in blocks if tb.raw]
    if not details:
        return
    wire["reasoning_details"] = details
    text = "\n".join(tb.text for tb in blocks if tb.text)
    if text:
        wire["reasoning"] = text


def messages_to_openai_compat(request: ModelRequest, entry_model: str, quirks: ProviderQuirks) -> dict[str, Any]:
    system_texts, rest = _stable_system_text(request)
    wire_messages: list[dict[str, Any]] = []
    if system_texts:
        wire_messages.append({"role": "system", "content": "\n\n".join(system_texts)})
    for msg in rest:
        if msg.role == Role.user:
            wire_messages.append({"role": "user", "content": msg.content or ""})
        elif msg.role == Role.assistant:
            m: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.thinking:
                m["reasoning_content"] = "\n".join(tb.text for tb in msg.thinking if tb.text)
                _echo_reasoning(m, msg.thinking)
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": sanitize_tool_name(call.name),
                            "arguments": json.dumps(call.arguments or {}),
                        },
                    }
                    for call in msg.tool_calls
                ]
            wire_messages.append(m)
        elif msg.role == Role.tool:
            wire_messages.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "",
                "content": msg.content or "",
            })

    payload: dict[str, Any] = {
        "model": entry_model,
        "messages": wire_messages,
        "max_tokens": request.max_tokens,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.reasoning_budget:
        # What is left after thinking is the answer, and on a reasoning model
        # reached through one of these endpoints `max_tokens` is the *whole*
        # budget -- reasoning is spent out of it and is billed inside
        # `completion_tokens`. So a request that only sets `max_tokens` is
        # bidding on how much thinking the model happens to do first, and a
        # model that thinks for the whole of it returns no answer at all:
        # `finish_reason: length` and an empty body. Measured on the deliberation
        # against the agent's own base model, an empty answer came back 4 times
        # in 6 at a ceiling of 900, about half the time at 2400, once in 12 at
        # 8000, and not once in 12 at 8000 with thinking bounded here.
        #
        # This is OpenRouter's parameter, and it is the one field an
        # OpenAI-compatible endpoint may not know: `OpenAICompatProvider` drops
        # it after a rejection rather than letting an endpoint's ignorance break
        # the request.
        payload["reasoning"] = {"max_tokens": int(request.reasoning_budget)}
    tools = _truncate_tools(request.tools, int(quirks.model_dump().get("max_tools", 128)))
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": sanitize_tool_name(t.name),
                    "description": t.description,
                    "parameters": t.input_schema or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        payload["tool_choice"] = "auto"
    return payload


def response_from_openai_compat(
    data: dict[str, Any], provider: str, model: str, name_map: dict[str, str],
) -> NormalizedResponse:
    choices = data.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    tool_calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function", {})
        wire_name = fn.get("name", "")
        tool_calls.append(ToolCall(
            id=call.get("id", ""),
            name=name_map.get(wire_name, wire_name),
            arguments=_json_loads_args(fn.get("arguments")),
        ))
    thinking: list[ThinkingBlock] = []
    reasoning = reasoning_from_openai_compat(message)
    if reasoning:
        details = message.get("reasoning_details")
        thinking.append(ThinkingBlock(
            text=reasoning,
            raw={"details": list(details)} if isinstance(details, list) and details else None,
        ))
    usage_data = data.get("usage", {}) or {}
    usage = Usage(
        input_tokens=int(usage_data.get("prompt_tokens", 0)),
        cached_input_tokens=int((usage_data.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
        output_tokens=int(usage_data.get("completion_tokens", 0)),
        reasoning_tokens=int((usage_data.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)),
    )
    return NormalizedResponse(
        text=message.get("content") or None,
        thinking_blocks=thinking,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=choices[0].get("finish_reason") if choices else None,
        provider=provider,
        model=model,
        raw=data,
    )


def estimate_tokens(request: ModelRequest) -> int:
    chars = sum(len(m.content or "") for m in request.messages)
    chars += sum(len(json.dumps(t.input_schema)) for t in request.tools)
    return max(64, chars // 4)


def tier_floor_ok(request: ModelRequest, floor: float, quality: float) -> bool:
    if request.tier == Tier.T0:
        return True
    return quality >= floor
