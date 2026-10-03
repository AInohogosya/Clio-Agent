from __future__ import annotations

import httpx

from ethos.gateway.normalizer import (
    build_name_map,
    messages_to_anthropic,
    response_from_anthropic,
)
from ethos.gateway.providers.base import Provider

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicNativeProvider(Provider):
    """Native Anthropic adapter: thinking blocks preserved, prompt caching."""

    name = "anthropic"

    def __init__(self, api_key: str | None, base_url: str, quirks, http: httpx.AsyncClient, timeout_s: float = 120.0):
        super().__init__(api_key, base_url or DEFAULT_BASE_URL, quirks, http, timeout_s)

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(self, request, model: str):
        payload = messages_to_anthropic(request, model, self.quirks)
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        data = await self.post_json(f"{self.base_url}/v1/messages", headers, payload)
        return response_from_anthropic(data, self.name, model, build_name_map(request.tools))
