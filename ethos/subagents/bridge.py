"""The model bridge: sub-agent model calls, routed through this agent's gateway.

Two sub-agents, two call conventions, one routing table:

* Foundry's `Orchestrator` takes any object with a *synchronous*
  `complete(LLMRequest) -> LLMResponse` — an in-process provider is injected,
  which hops to this agent's running event loop and calls the gateway there.
* APEX speaks the OpenAI chat-completions wire format through its own SDK
  clients; it is pointed at this agent's gateway shim over loopback HTTP.

Both paths land in the same place: `GatewayService.complete`, where
`models.yaml`, the spend guard, the retry/failover policy and the base model
decide what actually serves the request. A sub-agent that was configured for
one provider is therefore backed by whichever of anthropic, openai, google or
openai_compat this deployment has funded and healthy — without the sub-agent
carrying a key, a base URL or an opinion about any of them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ethos.observability.logger import get_logger
from ethos.schemas.models import ModelMessage, ModelRequest, Role

logger = get_logger("ethos.subagents.bridge")

# The largest completion a sub-agent may ask the gateway for. Foundry's own
# invocation cap is 16k output tokens; the ceiling bounds a misbehaving client.
_MAX_TOKENS_CEILING = 32768

_KNOWN_ROLES = {r.value for r in Role}


def _message_from_raw(raw: dict[str, Any]) -> ModelMessage:
    """One of Foundry's internal conversation messages as a `ModelMessage`.

    Foundry keeps tool interactions as `{"role": "assistant", "tool_call":
    {...}}` notes plus `{"role": "tool", ...}` results; the normalizers want
    assistant `tool_calls` arrays with ids, so that one shape is restated
    here rather than in each provider's wire format.
    """
    role_name = str(raw.get("role", "user"))
    role = Role(role_name) if role_name in _KNOWN_ROLES else Role.user
    message = ModelMessage(role=role, content=raw.get("content") or None)
    tool_call = raw.get("tool_call")
    if isinstance(tool_call, dict):
        args = tool_call.get("args")
        message.tool_call_id = str(tool_call.get("id") or "call_0")
        message.tool_calls = [{
            "id": str(tool_call.get("id") or "call_0"),
            "name": str(tool_call.get("name") or ""),
            "arguments": args if isinstance(args, dict) else {},
        }]
    if raw.get("tool_call_id"):
        message.tool_call_id = str(raw["tool_call_id"])
    return message


class FoundryGatewayProvider:
    """Foundry's `LLMProvider` protocol, answered by this agent's gateway.

    Synchronous, because `Orchestrator` is synchronous — the coroutine hops
    to the event loop this agent is already running, so the call shares that
    loop's gateway, spend guard and metrics instead of running a private
    asyncio world of its own.
    """

    def __init__(
        self,
        gateway: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        agent_name: str,
        tier: Any,
        purpose: str,
        timeout_s: float = 3600.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._loop = loop
        self._tier = tier
        self._purpose = purpose
        self._timeout_s = timeout_s
        self._metadata = {"subagent": agent_name, **(metadata or {})}

    def complete(self, request: Any) -> Any:
        from foundry.llm.base import LLMResponse, LLMToolCall

        model_request = ModelRequest(
            tier=self._tier,
            purpose=self._purpose,
            messages=[
                ModelMessage(role=Role.system, content=request.system),
                *[_message_from_raw(m) for m in request.messages],
            ],
            tools=[
                {"name": name, "description": f"FOUNDRY tool {name}"}
                for name in (request.tools or [])
            ],
            max_tokens=max(1, min(int(request.max_output_tokens), _MAX_TOKENS_CEILING)),
            temperature=float(request.temperature) if request.temperature is not None else None,
            metadata=self._metadata,
        )
        response = asyncio.run_coroutine_threadsafe(
            self._gateway.complete(model_request), self._loop,
        ).result(timeout=self._timeout_s)
        return LLMResponse(
            content=response.text,
            tool_calls=[
                LLMToolCall(
                    id=call.id,
                    name=call.name,
                    args=call.arguments,
                )
                for call in response.tool_calls
            ],
        )


def apex_planner_config(config: Any, gateway_url: str, model_name: str) -> Any:
    """APEX's `PlannerConfig`, pointed at this agent's gateway.

    APEX gains an `ethos` provider (see `APEX/ai_planner.py`): the
    chat-completions call path, keyless, against the gateway's
    OpenAI-compatible shim. APEX's own prompt building, schema validation and
    repair loop stay exactly as they are.
    """
    from ai_planner import PlannerConfig

    return PlannerConfig(
        provider="ethos",
        model=model_name,
        base_url=f"{gateway_url}/v1",
        max_output_tokens=min(16384, max(2048, 4096)),
    )


__all__ = ["FoundryGatewayProvider", "apex_planner_config"]
