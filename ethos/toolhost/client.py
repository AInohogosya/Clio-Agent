from __future__ import annotations

from pathlib import Path
from typing import Any

from ethos.config import EthosConfig
from ethos.gateway.client import open_endpoint_client
from ethos.schemas.tools import ToolResult
from ethos.toolhost.server import ToolContext


class RemoteToolhostClient:
    """HTTP client for ethos-toolhost over its unix socket (or TCP fallback).

    Duck-compatible with Toolhost.execute/catalog so GSL and threads can run
    against an embedded or a remote toolhost without code changes.
    """

    def __init__(self, socket_path: str | None = None, host: str = "127.0.0.1",
                 port: int = 8701, timeout_s: float = 300.0,
                 config: EthosConfig | None = None):
        # One place decides where the toolhost listens, and it is the same place
        # the toolhost process decides. The original hardcoded
        # `Path("/run/ethos/toolhost.sock")` and then, if that was absent, looked in
        # `~/.ethos/run/toolhost.sock` — two guesses about a third party's address,
        # both wrong on any machine that is neither: a Linux deployment running
        # the agent under a service account, where the home is `/var/lib/ethos` and
        # neither path is it, and every Windows machine, where neither path means
        # anything at all. So with no configuration it is *asked*, and the answer
        # comes from the `run_dir` probe the agent's own pid file already uses.
        #
        # `config` is optional and last, so every existing caller that passes a
        # `socket_path` keeps the address it passed, and only the default moves.
        if config is not None:
            host = config.gateway.host
            port = config.gateway.toolhost_port
            socket_path = socket_path or str(config.paths.run_dir / "toolhost.sock")
        if socket_path is None:
            socket_path = str(Path.home() / ".ethos" / "run" / "toolhost.sock")

        # On Windows this is always the TCP branch, and the port is the one the
        # toolhost was told to bind — which is why `toolhost_port` is configured
        # rather than left as a default in two modules that could disagree.
        self._http, self._base = open_endpoint_client(
            socket_path, host, port, timeout_s=timeout_s, virtual_host="toolhost.local",
        )

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        response = await self._http.post(
            "/execute",
            json={"tool": name, "args": args, "context": ctx.model_dump(mode="json")},
        )
        if response.status_code >= 400:
            return ToolResult(ok=False, error_code="execution",
                              error=f"toolhost HTTP {response.status_code}: {response.text[:300]}")
        return ToolResult.model_validate(response.json())

    def catalog(self) -> list[dict[str, Any]]:
        raise RuntimeError("catalog() is synchronous in-process only; use acatalog()")

    async def acatalog(self) -> list[dict[str, Any]]:
        response = await self._http.get("/tools")
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()
