from __future__ import annotations

import httpx

from ethos.gateway.normalizer import (
    build_name_map,
    messages_to_google,
    response_from_google,
)
from ethos.gateway.providers.base import Provider

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


class GoogleNativeProvider(Provider):
    """Native Google GenAI adapter with function_declarations sanitization."""

    name = "google"

    def __init__(self, api_key: str | None, base_url: str, quirks, http: httpx.AsyncClient, timeout_s: float = 180.0):
        super().__init__(api_key, base_url or DEFAULT_BASE_URL, quirks, http, timeout_s)

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(self, request, model: str):
        payload = messages_to_google(request, model, self.quirks)
        headers = {
            "x-goog-api-key": self.api_key or "",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1beta/models/{model}:generateContent"
        data = await self.post_json(url, headers, payload)
        return response_from_google(data, self.name, model, build_name_map(request.tools))
