from __future__ import annotations

import asyncio
import os
import random
from typing import Any

import httpx

from ethos.config import EthosConfig
from ethos.observability.logger import get_logger
from ethos.platform import SUPPORTS_UNIX_SOCKETS
from ethos.schemas.models import ModelRequest, NormalizedResponse

logger = get_logger("ethos.gateway.client")


class GatewayClient:
    """HTTP client for ethos-gateway over its unix socket (or TCP fallback)."""

    def __init__(self, config: EthosConfig, timeout_s: float = 180.0):
        self.config = config
        socket_path = config.gateway.socket_path
        # `SUPPORTS_UNIX_SOCKETS`, not just `os.path.exists`. httpx's `uds=`
        # transport is built on an `AF_UNIX` socket, which does not exist on
        # Windows, and a socket file that happens to exist at that path on a
        # Windows machine — a checkout of the repo, a stray file, a directory —
        # would otherwise send the client down the branch it cannot take. So the
        # question asked is whether the transport is *possible*, and only then
        # whether the file is there.
        use_socket = SUPPORTS_UNIX_SOCKETS and os.path.exists(socket_path)
        if use_socket:
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            self._base = "http://gateway.local"
            self._http = httpx.AsyncClient(transport=transport, base_url=self._base, timeout=timeout_s)
        else:
            self._base = f"http://{config.gateway.host}:{config.gateway.port}"
            self._http = httpx.AsyncClient(base_url=self._base, timeout=timeout_s)

    async def complete(self, request: ModelRequest) -> NormalizedResponse:
        """
        Ask the gateway, and keep asking while the asking is what is failing.

        Deliberately narrow. A response that arrived is an answer about something
        -- the gateway has already spent its own retry budget on the model, and
        its 502 carries no way to tell that apart from the provider being down,
        so repeating it here would pay for the same work twice. What is worth
        repeating is the case where nothing came back at all: the socket was not
        there yet, or the gateway process was restarting underneath us. Those
        are the failures that resolve themselves in a second and used to end a
        goal outright.
        """
        retry = self.config.gateway.retry
        attempts = 1 + max(0, retry.max_retries)
        for attempt in range(attempts):
            try:
                response = await self._http.post(
                    "/v1/complete", json=request.model_dump(mode="json")
                )
            except (httpx.TransportError, httpx.HTTPError) as exc:
                if attempt == attempts - 1:
                    raise RuntimeError(f"gateway unreachable: {exc}") from exc
                delay = min(
                    retry.backoff_s * (2**attempt)
                    * (1.0 + random.random() * retry.jitter if retry.jitter > 0 else 1.0),
                    retry.max_backoff_s,
                )
                logger.warning("gateway.unreachable_retry", attempt=attempt + 1,
                               of=attempts, retry_in_s=round(delay, 2), error=str(exc)[:200])
                await asyncio.sleep(delay)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"gateway error {response.status_code}: {response.text[:500]}")
            return NormalizedResponse.model_validate(response.json())
        raise RuntimeError("gateway complete failed without a response")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._http.post("/v1/embed", json={"texts": texts})
        response.raise_for_status()
        return response.json()["vectors"]

    async def budget(self) -> dict[str, Any]:
        response = await self._http.get("/budget")
        response.raise_for_status()
        return response.json()

    async def healthz(self) -> dict[str, Any]:
        response = await self._http.get("/healthz")
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()


class DirectGateway:
    """In-process GatewayClient shim used in single-process dev/test mode."""

    def __init__(self, service: Any):
        self.service = service

    async def complete(self, request: ModelRequest) -> NormalizedResponse:
        return await self.service.complete(request)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return self.service.embed(texts)

    async def budget(self) -> dict[str, Any]:
        return await self.service.budget_state()

    async def aclose(self) -> None:
        await self.service.aclose()
