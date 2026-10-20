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


def backoff_delay(
    attempt: int, *, base_s: float, jitter: float, cap_s: float,
    retry_after_s: float | None = None,
) -> float:
    """One wait on the shared retry curve: exponential, the cap applied last.

    The same curve `GatewayService._delay_before_retry` uses, with the same
    ordering — jitter widens the wait and the ceiling still holds it, and a
    provider's `Retry-After` lengthens the wait without ever exceeding the cap.
    """
    delay = base_s * (2**attempt)
    if jitter > 0:
        delay *= 1.0 + random.random() * jitter
    delay = min(delay, cap_s)
    if retry_after_s is not None:
        delay = max(delay, min(retry_after_s, cap_s))
    return delay


def open_endpoint_client(
    socket_path: str | None, host: str, port: int, *, timeout_s: float,
    sync: bool = False, virtual_host: str = "gateway.local",
) -> tuple[Any, str]:
    """An httpx client for a local service, over its unix socket or loopback TCP.

    The one place that asks the question `gateway/client` used to ask three
    times: `SUPPORTS_UNIX_SOCKETS` first — httpx's `uds=` transport is an
    `AF_UNIX` socket that does not exist on Windows, so a stray file at the
    path must not send a Windows client down the branch it cannot take — and
    only then whether the file is there. The virtual base URL never resolves;
    it is the name the transport dials by, because the socket has no port.
    """
    import httpx

    use_socket = bool(socket_path) and SUPPORTS_UNIX_SOCKETS and os.path.exists(str(socket_path))
    if use_socket:
        transport = (
            httpx.HTTPTransport(uds=str(socket_path))
            if sync else httpx.AsyncHTTPTransport(uds=str(socket_path))
        )
        client = (
            httpx.Client(transport=transport, base_url=f"http://{virtual_host}", timeout=timeout_s)
            if sync else httpx.AsyncClient(transport=transport, base_url=f"http://{virtual_host}",
                                           timeout=timeout_s)
        )
        return client, f"http://{virtual_host}"
    base = f"http://{host}:{port}"
    client = httpx.Client(base_url=base, timeout=timeout_s) if sync else httpx.AsyncClient(
        base_url=base, timeout=timeout_s,
    )
    return client, base


class GatewayClient:
    """HTTP client for ethos-gateway over its unix socket (or TCP fallback)."""

    def __init__(self, config: EthosConfig, timeout_s: float = 180.0):
        self.config = config
        self._http, self._base = open_endpoint_client(
            config.gateway.socket_path, config.gateway.host, config.gateway.port,
            timeout_s=timeout_s,
        )

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
                delay = backoff_delay(
                    attempt, base_s=retry.backoff_s, jitter=retry.jitter,
                    cap_s=retry.max_backoff_s,
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
