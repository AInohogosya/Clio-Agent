from __future__ import annotations

import httpx

from ethos.gateway.normalizer import (
    build_name_map,
    messages_to_openai_responses,
    response_from_openai_responses,
)
from ethos.gateway.providers.base import Provider

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAINativeProvider(Provider):
    """Native OpenAI adapter using the Responses API with reasoning effort."""

    name = "openai"

    def __init__(self, api_key: str | None, base_url: str, quirks, http: httpx.AsyncClient, timeout_s: float = 180.0):
        super().__init__(api_key, base_url or DEFAULT_BASE_URL, quirks, http, timeout_s)

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    async def complete(self, request, model: str):
        payload = messages_to_openai_responses(request, model, self.quirks)
        data = await self.post_json(
            f"{self.base_url}/responses", self._headers(), payload
        )
        return response_from_openai_responses(data, self.name, model, build_name_map(request.tools))
