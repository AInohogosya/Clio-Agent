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
    """Token counts for one call, in five disjoint buckets.

    Every field counts tokens nobody else in this struct also counts. That is
    not a simplification, it is the only arrangement in which summing the
    buckets against five different rates gives the right answer -- vendors do
    not agree on whether a reported total includes the parts, and they are not
    all the same way wrong.

      input_tokens         not cached, not a cache write: billed at `input`
      cached_input_tokens  read from a cache: billed at `cached_input`
      cache_write_tokens   stored into a cache: billed at `cache_write`
      output_tokens        generated, reasoning excluded: billed at `output`
      reasoning_tokens     spent thinking: billed at `output`

    Anthropic reports input and cache tokens as separate counters, so its
    numbers land here unchanged. OpenAI folds cache reads into `prompt_tokens`
    and reasoning into `completion_tokens`; Google folds cache reads into
    `promptTokenCount` but keeps `thoughtsTokenCount` outside
    `candidatesTokenCount`. A provider adapter that copies a wire total into
    `input_tokens` without subtracting the parts bills those tokens twice, which
    is what the adapters in `gateway/normalizer.py` are for.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )

    @property
    def prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
        )

    @property
    def completion_tokens(self) -> int:
        return self.output_tokens + self.reasoning_tokens


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
    # What the provider says it actually billed, when it says so. OpenRouter
    # reports an exact figure per request; where that exists it is the cost,
    # and a rate card is only the estimate. `None` means "not reported", which
    # is different from zero and must not be stored as zero.
    billed_cost_usd: float | None = None
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
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    error: str | None = None
    # `live` when the provider billed it, `config` when a rate card priced it,
    # `unknown` when a high-water mark stood in. Lets a wrong total be traced
    # to its origin without re-deriving it.
    price_source: str = "unknown"
