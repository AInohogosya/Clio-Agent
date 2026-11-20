"""Model Context Protocol: a native client and server for the toolhost.

MCP is how external tool servers join the catalog. This module speaks the
protocol natively over the three transports the specification names:

- **stdio** — the server is a child process and the transport is its own
  standard input/output, one JSON-RPC message per line.
- **Streamable HTTP** — JSON-RPC over POST, with the server free to answer
  directly or to upgrade the response to an SSE stream.
- **SSE** — the classic split: a long-lived `text/event-stream` for server
  messages and a POST endpoint for client messages.

Three properties make a remote server safe to put in a catalog:

- **Pooling and health checks.** A client is one persistent connection per
  server, initialized once, with a bounded health poll; a server that stops
  answering is marked degraded rather than re-dialed on every step.
- **Degraded stand-in descriptors.** When a server is unreachable its tools
  stay *visible* — as descriptors whose description says why they cannot be
  called right now. A catalog that silently loses fifteen tools at breakfast
  is how an agent spends a morning guessing at names it half-remembers; a
  stand-in that says "the linear MCP server is not reachable (connection
  refused)" is a fact the step can act on.
- **No anonymous execution.** Tool results are returned as data; the
  `toolhost`'s gate still reviews every call and the result is trust-tagged
  `untrusted` at the boundary, because text from an external server is
  content, never instructions.

The server side exposes this runtime's own catalog over the same protocol, so
another agent (or a person's editor) can discover and call the tools under
the same guardian rules the agent itself obeys.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

import httpx

from clio.observability.logger import get_logger

logger = get_logger("clio.toolhost.mcp")

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "clio-agent", "version": "3.1"}
HEALTH_POLL_S = 30.0


class MCPError(RuntimeError):
    """A protocol-level failure from an MCP server."""


@dataclass
class MCPToolDescriptor:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"mcp.{self.server}.{self.name}"

    def as_catalog_entry(self) -> dict[str, Any]:
        return {
            "name": self.full_name,
            "description": f"[mcp:{self.server}] {self.description}",
            "input_schema": self.input_schema,
            "source": "mcp",
        }


def degraded_descriptor(server: str, reason: str, *, name: str = "unreachable") -> dict[str, Any]:
    """The stand-in a catalog shows while a server is down.

    It is deliberately not callable: calling it returns the reason, which is
    more useful to a step than a connection error raised three layers down.
    """
    return {
        "name": f"mcp.{server}.{name}",
        "description": (
            f"[mcp:{server}] DEGRADED — this MCP server is not reachable right now "
            f"({reason}). Its tools will return when the server does; do not retry "
            "in a loop, and do not guess at the missing tool names."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "source": "mcp_degraded",
        "degraded": True,
        "degraded_reason": reason,
    }


class MCPTransport:
    """One wire: a callable that sends a JSON-RPC request and reads the reply."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def request(self, payload: dict[str, Any], *, timeout_s: float = 60.0) -> dict[str, Any]:
        raise NotImplementedError


class StdioTransport(MCPTransport):
    """A child process speaking JSON-RPC on its own stdin/stdout, one per line."""

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        resolved = shutil.which(self.command) or self.command
        self._proc = await asyncio.create_subprocess_exec(
            resolved, *self.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **self.env},
        )

    async def stop(self) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            self._proc = None

    async def request(self, payload: dict[str, Any], *, timeout_s: float = 60.0) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise MCPError("stdio transport is not started")
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        async with self._lock:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
            while True:
                raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=timeout_s)
                if not raw:
                    raise MCPError("the MCP server closed its stdout")
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                # Notifications (no id) are acknowledged by being read and
                # skipped; the reply to our request is the one with the id.
                if message.get("id") == payload.get("id"):
                    return message


class StreamableHTTPTransport(MCPTransport):
    """JSON-RPC over POST; the server may answer directly or with an SSE body."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self._client = httpx.AsyncClient(timeout=60.0)

    async def start(self) -> None:
        if self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)

    async def stop(self) -> None:
        await self._client.aclose()

    async def request(self, payload: dict[str, Any], *, timeout_s: float = 60.0) -> dict[str, Any]:
        response = await self._client.post(
            self.url, json=payload,
            headers={
                **self.headers,
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": PROTOCOL_VERSION,
            },
            timeout=timeout_s,
        )
        if response.status_code >= 400:
            raise MCPError(f"HTTP {response.status_code} from {self.url}")
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    message = json.loads(line[len("data:"):].strip())
                    if message.get("id") == payload.get("id"):
                        return message
            raise MCPError("the SSE body carried no reply")
        return response.json()


class SSETransport(MCPTransport):
    """The split transport: an event stream down, a POST endpoint up."""

    def __init__(self, sse_url: str, post_url: str,
                 headers: dict[str, str] | None = None) -> None:
        self.sse_url = sse_url
        self.post_url = post_url
        self.headers = dict(headers or {})
        self._client = httpx.AsyncClient(timeout=None)
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_events())

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        await self._client.aclose()

    async def _read_events(self) -> None:
        try:
            async with self._client.stream("GET", self.sse_url, headers=self.headers) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        with contextlib.suppress(ValueError):
                            await self._queue.put(json.loads(line[len("data:"):].strip()))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("mcp.sse_reader_failed")

    async def request(self, payload: dict[str, Any], *, timeout_s: float = 60.0) -> dict[str, Any]:
        post = await self._client.post(
            self.post_url, json=payload,
            headers={**self.headers, "Content-Type": "application/json"},
            timeout=timeout_s,
        )
        if post.status_code >= 400:
            raise MCPError(f"HTTP {post.status_code} posting to {self.post_url}")
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise MCPError("timed out waiting for the SSE reply")
            message = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            if message.get("id") == payload.get("id"):
                return message


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"                 # stdio | streamable_http | sse
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    sse_url: str = ""
    post_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> MCPServerConfig:
        return cls(
            name=name,
            transport=str(data.get("transport") or "stdio"),
            command=str(data.get("command") or ""),
            args=[str(a) for a in data.get("args", [])],
            url=str(data.get("url") or ""),
            sse_url=str(data.get("sse_url") or ""),
            post_url=str(data.get("post_url") or ""),
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
        )

    def build_transport(self) -> MCPTransport:
        if self.transport == "stdio":
            if not self.command:
                raise MCPError(f"MCP server {self.name!r}: stdio needs a command")
            return StdioTransport(self.command, self.args)
        if self.transport == "streamable_http":
            if not self.url:
                raise MCPError(f"MCP server {self.name!r}: streamable_http needs a url")
            return StreamableHTTPTransport(self.url, self.headers)
        if self.transport == "sse":
            if not (self.sse_url and self.post_url):
                raise MCPError(f"MCP server {self.name!r}: sse needs sse_url and post_url")
            return SSETransport(self.sse_url, self.post_url, self.headers)
        raise MCPError(f"MCP server {self.name!r}: unknown transport {self.transport!r}")


class MCPClient:
    """One server, one persistent connection, initialized once."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.transport = config.build_transport()
        self.initialized = False
        self.degraded_reason: str | None = None
        self.tools: list[MCPToolDescriptor] = []
        self._next_id = 0
        self._lock = asyncio.Lock()

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        return payload

    async def connect(self) -> bool:
        """Start the transport and run the initialize handshake."""
        try:
            await self.transport.start()
            reply = await self.transport.request(self._rpc("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": CLIENT_INFO,
            }))
            if "error" in reply:
                raise MCPError(str(reply["error"]))
            self.initialized = True
            self.degraded_reason = None
            await self._refresh_tools()
            logger.info("mcp.connected", server=self.config.name, tools=len(self.tools))
            return True
        except Exception as exc:
            self.degraded_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("mcp.connect_failed", server=self.config.name,
                           reason=self.degraded_reason)
            return False

    async def _refresh_tools(self) -> None:
        reply = await self.transport.request(self._rpc("tools/list"))
        if "error" in reply:
            raise MCPError(str(reply["error"]))
        self.tools = [
            MCPToolDescriptor(
                server=self.config.name,
                name=str(tool.get("name") or ""),
                description=str(tool.get("description") or ""),
                input_schema=dict(tool.get("inputSchema") or {}),
            )
            for tool in ((reply.get("result") or {}).get("tools") or [])
        ]

    async def call_tool(self, name: str, args: dict[str, Any], *, timeout_s: float = 120.0) -> dict[str, Any]:
        reply = await self.transport.request(
            self._rpc("tools/call", {"name": name, "arguments": args}),
            timeout_s=timeout_s,
        )
        if "error" in reply:
            return {"error": str(reply["error"]), "trust": "untrusted"}
        result = dict(reply.get("result") or {})
        # External text is content, never instructions: the trust tag rides on
        # the envelope so nothing downstream reads it as a directive.
        result["trust"] = "untrusted"
        return result

    async def health_check(self) -> bool:
        if not self.initialized:
            return await self.connect()
        try:
            await self.transport.request(self._rpc("ping"), timeout_s=5.0)
            self.degraded_reason = None
            return True
        except Exception as exc:
            self.degraded_reason = f"{type(exc).__name__}: {exc}"
            return False

    async def stop(self) -> None:
        await self.transport.stop()
        self.initialized = False


class MCPClientPool:
    """Every configured server: pooled, health-checked, degraded honestly."""

    def __init__(self, servers: list[MCPServerConfig] | None = None,
                 *, health_poll_s: float = HEALTH_POLL_S) -> None:
        self.servers = {s.name: s for s in (servers or [])}
        self.clients: dict[str, MCPClient] = {}
        self.health_poll_s = health_poll_s
        self._health_task: asyncio.Task | None = None

    async def start(self) -> None:
        for name, config in self.servers.items():
            client = MCPClient(config)
            self.clients[name] = client
            await client.connect()
        if self.health_poll_s > 0 and self.clients:
            self._health_task = asyncio.create_task(self._health_loop())

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(self.health_poll_s)
            for client in list(self.clients.values()):
                with contextlib.suppress(Exception):
                    await client.health_check()

    async def stop(self) -> None:
        if self._health_task is not None:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None
        for client in list(self.clients.values()):
            with contextlib.suppress(Exception):
                await client.stop()

    def catalog_entries(self) -> list[dict[str, Any]]:
        """The pool as catalog entries, degraded servers included by name."""
        entries: list[dict[str, Any]] = []
        for name, client in self.clients.items():
            if client.initialized and client.degraded_reason is None:
                entries.extend(tool.as_catalog_entry() for tool in client.tools)
            else:
                entries.append(degraded_descriptor(
                    name, client.degraded_reason or "not connected",
                ))
        return entries

    def find(self, full_name: str) -> tuple[MCPClient, MCPToolDescriptor] | None:
        for client in self.clients.values():
            for tool in client.tools:
                if tool.full_name == full_name:
                    return client, tool
        return None

    async def call(self, full_name: str, args: dict[str, Any], *, timeout_s: float = 120.0) -> dict[str, Any]:
        found = self.find(full_name)
        if found is None:
            return {"error": f"no MCP tool named {full_name!r}", "trust": "untrusted"}
        client, tool = found
        if client.degraded_reason is not None:
            return {
                "error": f"the {tool.server} MCP server is degraded: {client.degraded_reason}",
                "trust": "untrusted",
            }
        try:
            return await client.call_tool(tool.name, args, timeout_s=timeout_s)
        except Exception as exc:
            client.degraded_reason = f"{type(exc).__name__}: {exc}"
            return {"error": f"{tool.server} MCP call failed: {exc}", "trust": "untrusted"}


class MCPServer:
    """This runtime's catalog, served over MCP.

    The server exposes `tools/list` and `tools/call` against the toolhost's
    own registry. Calls arriving from outside run through the same gate as
    the agent's own: the server is a door into the runtime, not a credential
    to skip it.
    """

    def __init__(self, catalog_provider: Any, execute: Any) -> None:
        self.catalog_provider = catalog_provider
        self.execute = execute

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """One JSON-RPC message in, one reply out (None for notifications)."""
        method = message.get("method")
        request_id = message.get("id")
        if method is None:
            return None
        if method == "initialize":
            return self._reply(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "clio-toolhost", "version": "3.1"},
            })
        if method in ("notifications/initialized", "ping"):
            return self._reply(request_id, {}) if request_id is not None else None
        if method == "tools/list":
            tools = [
                {
                    "name": str(entry.get("name")),
                    "description": str(entry.get("description") or ""),
                    "inputSchema": dict(entry.get("input_schema") or {}),
                }
                for entry in self.catalog_provider()
            ]
            return self._reply(request_id, {"tools": tools})
        if method == "tools/call":
            params = message.get("params") or {}
            return self._deferred_call(request_id, params)
        return {
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    def _deferred_call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {"tool": params.get("name"), "arguments": params.get("arguments") or {},
                       "note": "call execution is asynchronous; await via the http endpoint"},
        }

    async def handle_async(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method != "tools/call":
            return self.handle(message)
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        arguments = dict(params.get("arguments") or {})
        try:
            result = await self.execute(name, arguments)
            text = result if isinstance(result, str) else json.dumps(result, default=str)
            return self._reply(message.get("id"), {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        except Exception as exc:
            return self._reply(message.get("id"), {
                "content": [{"type": "text", "text": f"tool failed: {exc}"}],
                "isError": True,
            })

    @staticmethod
    def _reply(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


class MCPProxyTool:
    """The shim a pool-registered MCP tool is executed through.

    It exists so an external tool travels the same pipeline as a built-in —
    journalled before execution, reviewed by the gate, audited after — while
    its implementation lives in another process. The result is trust-tagged
    `untrusted` at this boundary and nowhere else.
    """

    def __init__(self, full_name: str, pool: MCPClientPool,
                 descriptor: MCPToolDescriptor | None = None) -> None:
        self.name = full_name
        self.pool = pool
        self.timeout_s = 120.0
        self.description = (
            f"[mcp:{descriptor.server}] {descriptor.description}"
            if descriptor is not None else "[mcp] an external MCP tool"
        )
        self.input_schema = descriptor.input_schema if descriptor is not None else {
            "type": "object", "properties": {},
        }

    async def run(self, args: dict[str, Any], ctx: Any, host: Any) -> Any:
        return await self.pool.call(self.name, dict(args or {}))


def servers_from_config(config: Any) -> list[MCPServerConfig]:
    """The `mcp.servers` block from config, as typed entries."""
    block = getattr(config, "mcp", None)
    servers = getattr(block, "servers", None) or {}
    out: list[MCPServerConfig] = []
    for name, data in servers.items():
        with contextlib.suppress(MCPError):
            out.append(MCPServerConfig.from_dict(str(name), dict(data or {})))
    return out


__all__ = [
    "CLIENT_INFO",
    "MCPClient",
    "MCPClientPool",
    "MCPError",
    "MCPProxyTool",
    "MCPServer",
    "MCPServerConfig",
    "MCPToolDescriptor",
    "MCPTransport",
    "PROTOCOL_VERSION",
    "SSETransport",
    "StdioTransport",
    "StreamableHTTPTransport",
    "degraded_descriptor",
    "servers_from_config",
]
