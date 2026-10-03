from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ethos.schemas.tools import ToolCall, ToolSpec


class Tier(str, Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class Role(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ThinkingBlock(BaseModel):
    text: str = ""
    signature: str | None = None
    raw: dict[str, Any] | None = None


class ModelMessage(BaseModel):
    role: Role
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    thinking: list[ThinkingBlock] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ModelRequest(BaseModel):
    tier: Tier
    purpose: str
    messages: list[ModelMessage]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = 4096
    temperature: float | None = None
    thinking_budget: int | None = None
    # How much of the output budget reasoning may spend, for a model that reasons
    # *inside* `max_tokens` rather than in a budget of its own.
    #
    # Deliberately not `thinking_budget`. That one asks the router for a model
    # that can think at all, so setting it makes every entry that does not
    # declare `supports_thinking` ineligible and a request nobody can answer is
    # a worse outcome than an answer that took too long. This only bounds how
    # much of the room thinking may take, which is the question that actually
    # decides whether the answer arrives: on those endpoints reasoning is billed
    # inside `completion_tokens` and spent out of the same ceiling, so a request
    # that sets only `max_tokens` is betting on how much the model thinks first.
    # A model that does not reason has nothing to apply it to.
    reasoning_budget: int | None = None
    cache_prefix_blocks: int = 3
    provider_hint: str | None = None
    model_hint: str | None = None
    thread_id: str | None = None
    intention_id: str | None = None
    budget_bucket: str = "discretionary"
    consequence_class: str = "routine"
    task_class: str = "general"
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


class NormalizedResponse(BaseModel):
    text: str | None = None
    thinking_blocks: list[ThinkingBlock] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Usage()
    finish_reason: str | None = None
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    cache_creation: bool = False
    cache_read: bool = False
    raw: dict[str, Any] | None = None


class ModelCallRecord(BaseModel):
    provider: str
    model: str
    tier: str
    purpose: str
    thread_id: str | None
    intention_id: str | None
    budget_bucket: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    error: str | None = None
