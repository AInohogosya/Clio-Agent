"""OpenAI-compatible wire shim over the gateway's model routing.

Foundry and APEX — the two agent programs this agent coordinates with — both
speak the OpenAI chat-completions wire format. Rather than teaching each of
them this agent's native `ModelRequest` shape, this module is the translation
layer at the edge: an OpenAI-shaped request comes in, the gateway routes it
across the catalogue in `models.yaml` (with the spend guard, retries, failover
and the base model exactly as for the Self), and an OpenAI-shaped response
goes back out.

This is what resolves the provider-compatibility problem at the program level:
a sub-agent configured with one OpenAI-compatible endpoint name is silently
backed by anthropic, openai, google or openai_compat providers, whichever are
funded and healthy — and fails over between them without the sub-agent ever
knowing the difference.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ethos.schemas.models import ModelMessage, ModelRequest, NormalizedResponse, Role, Tier
from ethos.schemas.tools import ToolCall, ToolSpec

# The largest single completion a shim client may ask for. Foundry's own
# invocation cap is 16k output tokens; this bounds a hostile client instead.
_MAX_TOKENS_CEILING = 32768

_DEFAULT_TIER = Tier.T2

_JSON_FORMAT_NOTE = (
    "Reply with a single JSON object and nothing else. It must validate "
    "against the JSON schema named {name!r} supplied below:\n{schema}"
)


def _data_url_to_attachment(url: str) -> dict[str, Any] | None:
    """A `data:` URL as a message attachment, or `None` when it is not one."""
    scheme, _, rest = url.partition(":")
    if scheme != "data":
        return None
    meta, _, data = rest.partition(",")
    if not data or ";" not in meta:
        return None
    media_type = meta.split(";", 1)[0] or "application/octet-stream"
    return {"type": "image", "media_type": media_type, "data": data}


def _content_to_text(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI content — a string or a list of typed parts — as text plus
    attachments. Text parts are joined; image parts are carried as
    attachments for the normalizer to translate per provider."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    texts: list[str] = []
    attachments: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type", "text")
        if kind in ("text", "input_text"):
            texts.append(str(part.get("text", "")))
        elif kind in ("image_url", "input_image"):
            url = part.get("image_url") or part.get("image") or ""
            if isinstance(url, dict):
                url = url.get("url", "")
            attachment = _data_url_to_attachment(str(url))
            if attachment is not None:
                attachments.append(attachment)
    return "\n".join(t for t in texts if t), attachments


def _role_of(raw: Any) -> Role:
    name = str(raw or "user").strip().lower()
    if name in ("tool", "function"):
        return Role.tool
    if name in ("system", "developer"):
        return Role.system
    if name == "assistant":
        return Role.assistant
    return Role.user


def _messages_from(body: dict[str, Any]) -> list[ModelMessage]:
    out: list[ModelMessage] = []
    for raw in body.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        text, attachments = _content_to_text(raw.get("content"))
        message = ModelMessage(
            role=_role_of(raw.get("role")),
            content=text or None,
            attachments=attachments,
        )
        if raw.get("tool_call_id"):
            message.tool_call_id = str(raw["tool_call_id"])
        for call in raw.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            arguments = fn.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"_raw": arguments}
            message.tool_calls.append(ToolCall(
                id=str(call.get("id") or ""),
                name=str(fn.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            ))
        out.append(message)
    return out


def _tools_from(body: dict[str, Any]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") or {}
        name = fn.get("name") or tool.get("name")
        if not name:
            continue
        specs.append(ToolSpec(
            name=str(name),
            description=str(fn.get("description") or "client-declared tool"),
            input_schema=fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {},
        ))
    return specs


def _response_format_note(body: dict[str, Any]) -> str | None:
    """The instruction a `response_format` constraint becomes on this wire.

    Structured output is a property of the endpoint, and the router may hand
    the request to any provider in the catalogue. Rather than hope the chosen
    model honours a field it never sees, the constraint is restated as an
    instruction — which every model in the catalogue does honour.
    """
    fmt = body.get("response_format")
    if not isinstance(fmt, dict):
        return None
    if fmt.get("type") == "json_object":
        return "Reply with a single valid JSON object and nothing else."
    schema_def = fmt.get("json_schema") if fmt.get("type") == "json_schema" else None
    if not isinstance(schema_def, dict):
        return None
    schema = schema_def.get("schema")
    if not isinstance(schema, dict):
        return None
    return _JSON_FORMAT_NOTE.format(
        name=schema_def.get("name", "response"),
        schema=json.dumps(schema, ensure_ascii=False),
    )


def openai_chat_to_model_request(
    body: dict[str, Any],
    *,
    purpose: str = "compat.chat",
    tier: Tier = _DEFAULT_TIER,
    known_models: set[str] | frozenset[str] = frozenset(),
    thread_id: str | None = None,
) -> ModelRequest:
    """One OpenAI chat-completions body as a gateway `ModelRequest`.

    A model name that is in this deployment's catalogue is honoured as a
    hint; anything else — including the base model's own alias — is left to
    the router, which is the entire point of coming through here.
    """
    max_tokens = body.get("max_completion_tokens") or body.get("max_tokens") or 4096
    try:
        max_tokens = max(1, min(int(max_tokens), _MAX_TOKENS_CEILING))
    except (TypeError, ValueError):
        max_tokens = 4096

    messages = _messages_from(body)
    note = _response_format_note(body)
    if note:
        messages.insert(0, ModelMessage(role=Role.system, content=note))

    model_name = str(body.get("model") or "").strip()
    model_hint = model_name if model_name in known_models else None
    metadata: dict[str, Any] = {"via": "openai-compat"}
    if model_name and model_hint is None:
        metadata["requested_model"] = model_name

    temperature = body.get("temperature")
    return ModelRequest(
        tier=tier,
        purpose=purpose,
        messages=messages,
        tools=_tools_from(body),
        max_tokens=max_tokens,
        temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
        model_hint=model_hint,
        thread_id=thread_id,
        metadata=metadata,
    )


def normalized_to_chat_response(
    resp: NormalizedResponse, requested_model: str = ""
) -> dict[str, Any]:
    """A normalized gateway response as an OpenAI chat-completions body."""
    message: dict[str, Any] = {"role": "assistant", "content": resp.text}
    if resp.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id or f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in resp.tool_calls
        ]
        if not resp.text:
            message["content"] = None
    usage = resp.usage
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model or resp.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if resp.tool_calls else (resp.finish_reason or "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.input_tokens + usage.cached_input_tokens,
            "completion_tokens": usage.output_tokens + usage.reasoning_tokens,
            "total_tokens": (
                usage.input_tokens + usage.cached_input_tokens
                + usage.output_tokens + usage.reasoning_tokens
            ),
        },
    }


def chat_stream_chunks(
    resp: NormalizedResponse, requested_model: str = ""
) -> list[dict[str, Any]]:
    """The same response as an OpenAI-compatible SSE chunk sequence.

    Sub-agents stream first (Foundry does, to keep reasoning models alive
    past a read timeout), so the shim streams too. One content delta carries
    the whole completion: the wire contract is per-chunk liveness, not
    token-by-token delivery.
    """
    created = int(time.time())
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": requested_model or resp.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    chunks = [chunk({"role": "assistant", "content": resp.text or ""})]
    if resp.tool_calls:
        for i, call in enumerate(resp.tool_calls):
            chunks.append(chunk({
                "tool_calls": [{
                    "index": i,
                    "id": call.id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }],
            }))
    chunks.append(chunk({}, finish="tool_calls" if resp.tool_calls else "stop"))
    return chunks


def sse_bytes(chunks: list[dict[str, Any]]) -> list[bytes]:
    lines = [b"data: " + json.dumps(c, ensure_ascii=False).encode() + b"\n\n" for c in chunks]
    lines.append(b"data: [DONE]\n\n")
    return lines


def catalogue_to_models_payload(entries: list[Any]) -> dict[str, Any]:
    """The catalogue as OpenAI's `/v1/models` shape, so a client that lists
    before it calls sees what this deployment can actually serve."""
    return {
        "object": "list",
        "data": [
            {
                "id": entry.model,
                "object": "model",
                "created": 0,
                "owned_by": entry.provider,
                "tiers": list(entry.tiers),
                "pinned": bool(getattr(entry, "pinned", False)),
            }
            for entry in entries
        ],
    }
