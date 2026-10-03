from __future__ import annotations

import httpx

from ethos.gateway.normalizer import (
    build_name_map,
    messages_to_openai_compat,
    response_from_openai_compat,
)
from ethos.gateway.providers.base import Provider, ProviderError


class OpenAICompatProvider(Provider):
    """OpenAI-compatible adapter: OpenRouter, NVIDIA NIM, local vLLM, DeepSeek, Groq."""

    name = "openai_compat"

    def __init__(self, api_key: str | None, base_url: str, quirks, http: httpx.AsyncClient, timeout_s: float = 180.0):
        super().__init__(api_key, base_url, quirks, http, timeout_s)
        # Which models on this endpoint have taken the reasoning parameter. There
        # is no way to know before asking, and asking wrong is cheap here only
        # because the gateway retries: a 400 is not retryable in itself, so the
        # error travels up, the loop comes back round, and by then this knows.
        #
        # Remembered per model, not per endpoint, because the answer is not a
        # property of the endpoint. OpenRouter serves reasoning and non-reasoning
        # models from one host and refuses the parameter for the second kind, so
        # a single per-endpoint flag would throw away the bound for the first
        # kind the moment anybody asked the second one. Endpoints that predate
        # the parameter ignore what they do not know, which costs nothing.
        self._no_reasoning: set[str] = set()

    def available(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _refuses_reasoning(self, exc: ProviderError) -> bool:
        """Whether a 400 is the endpoint saying it has no such parameter."""
        if exc.status != 400:
            return False
        text = str(exc).lower()
        if "reasoning" not in text:
            return False
        return any(word in text for word in (
            "unsupported", "not support", "unknown", "unrecognized", "unrecognised",
            "unexpected", "invalid", "extra", "not allowed", "does not",
        ))

    async def complete(self, request, model: str):
        payload = messages_to_openai_compat(request, model, self.quirks)
        if model in self._no_reasoning:
            payload.pop("reasoning", None)
        try:
            data = await self.post_json(
                f"{self.base_url}/chat/completions", self._headers(), payload
            )
        except ProviderError as exc:
            if "reasoning" in payload and self._refuses_reasoning(exc):
                self._no_reasoning.add(model)
            raise
        return response_from_openai_compat(data, self.name, model, build_name_map(request.tools))
