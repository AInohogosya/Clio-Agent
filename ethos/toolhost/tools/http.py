from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx

from ethos.toolhost.server import Tool, ToolContext, Toolhost

MAX_BODY_BYTES = 2_000_000
PRIVATE_DENIED = {"127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                  "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"}


class HttpTool(Tool):
    name = "http.request"
    description = (
        "Make an HTTP request. Secrets referenced as {{secret:KEY}} are substituted "
        "only for allow-listed hosts. Private ranges are blocked by default."
    )
    timeout_s = 90.0
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]},
            "headers": {"type": "object"},
            "body": {"type": "string"},
            "json_body": {"type": "object"},
            "timeout_s": {"type": "number"},
            "spend_usd": {"type": "number", "description": "declared money at stake for H2 checks"},
        },
        "required": ["url"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        url = str(args.get("url", ""))
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("only http(s) URLs are allowed")
        hostname = parts.hostname or ""
        if host.config.permissions.network.deny_private_ranges:
            await self._check_private(hostname)
        method = str(args.get("method", "GET")).upper()
        headers = {str(k): str(v) for k, v in (args.get("headers") or {}).items()}
        vault: Any = host.services.get("vault")
        if vault is not None:
            url = vault.substitute(url, hostname)
            headers = {k: vault.substitute(v, hostname) for k, v in headers.items()}
        body = args.get("body")
        json_body = args.get("json_body")
        if vault is not None and isinstance(body, str):
            body = vault.substitute(body, hostname)
        timeout = float(args.get("timeout_s", 60.0))
        max_bytes = host.config.permissions.network.max_response_bytes
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.request(
                method, url, headers=headers,
                content=body.encode("utf-8") if isinstance(body, str) else None,
                json=json_body,
            )
        text = response.text
        truncated = len(text.encode("utf-8", errors="replace")) > max_bytes
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": text[:max_bytes],
            "truncated": truncated,
            "url": str(response.url),
        }

    async def _check_private(self, hostname: str) -> None:
        if hostname in ("localhost", "0.0.0.0"):
            raise PermissionError("private address ranges are blocked by network policy")
        try:
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            try:
                infos = socket.getaddrinfo(hostname, None)
            except OSError as exc:
                raise PermissionError(f"cannot resolve host: {hostname}") from exc
            for info in infos:
                addr = ipaddress.ip_address(info[4][0])
                if any(addr in ipaddress.ip_network(net) for net in PRIVATE_DENIED):
                    raise PermissionError(
                        f"host {hostname} resolves into a private range; blocked by policy"
                    ) from None
            return
        if any(addr in ipaddress.ip_network(net) for net in PRIVATE_DENIED):
            raise PermissionError("private address ranges are blocked by network policy")


__all__ = ["HttpTool", "MAX_BODY_BYTES"]
