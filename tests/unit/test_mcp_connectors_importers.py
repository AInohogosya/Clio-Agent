"""MCP client/server, the OAuth connector broker, and schema importers."""
from __future__ import annotations

import json
from typing import Any

import pytest

from clio.toolhost.importers import (
    graphql_tools,
    openapi_tools,
    resolve_ref,
)
from clio.toolhost.mcp import (
    MCPClient,
    MCPClientPool,
    MCPServer,
    MCPServerConfig,
    degraded_descriptor,
)

# ---- MCP: the server face ---------------------------------------------------------


def test_the_mcp_server_lists_the_catalog():
    server = MCPServer(
        catalog_provider=lambda: [
            {"name": "fs.read", "description": "read a file",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        ],
        execute=None,
    )
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "clio-toolhost"
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed["result"]["tools"][0]["name"] == "fs.read"
    assert listed["result"]["tools"][0]["inputSchema"]["properties"]["path"]


async def test_the_mcp_server_executes_through_its_executor():
    async def execute(name: str, args: dict[str, Any]) -> Any:
        return {"tool": name, "got": args}

    server = MCPServer(catalog_provider=lambda: [], execute=execute)
    reply = await server.handle_async({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "fs.read", "arguments": {"path": "/tmp/x"}},
    })
    text = reply["result"]["content"][0]["text"]
    assert json.loads(text)["got"] == {"path": "/tmp/x"}


def test_the_mcp_server_refuses_unknown_methods():
    server = MCPServer(catalog_provider=lambda: [], execute=None)
    reply = server.handle({"jsonrpc": "2.0", "id": 4, "method": "no/such"})
    assert reply["error"]["code"] == -32601


# ---- MCP: the client, over stdio --------------------------------------------------


class FakeStdioServer:
    """A tiny JSON-RPC server over a python subprocess, MCP-shaped."""

    SCRIPT = """
import json, sys
tools = [{"name": "echo", "description": "echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-03-26", "capabilities": {}}}
    elif method == "tools/list":
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": tools}}
    elif method == "tools/call":
        text = (message.get("params") or {}).get("arguments", {}).get("text", "")
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": text}]}}
    elif method == "ping":
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": {}}
    else:
        reply = {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "no"}}
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
"""


async def test_stdio_client_connects_lists_and_calls(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(FakeStdioServer.SCRIPT, encoding="utf-8")
    import sys

    client = MCPClient(MCPServerConfig(
        name="fake", transport="stdio", command=sys.executable, args=[str(script)],
    ))
    try:
        assert await client.connect()
        assert [t.full_name for t in client.tools] == ["mcp.fake.echo"]
        result = await client.call_tool("echo", {"text": "hello mcp"})
        assert result["content"][0]["text"] == "hello mcp"
        assert result["trust"] == "untrusted", "external text arrives trust-tagged"
        assert await client.health_check()
    finally:
        await client.stop()


async def test_an_unreachable_server_degrades_instead_of_vanishing(tmp_path):
    pool = MCPClientPool([
        MCPServerConfig(name="gone", transport="stdio",
                        command="/nonexistent/binary-xyz", args=[]),
    ], health_poll_s=0)
    await pool.start()
    entries = pool.catalog_entries()
    assert len(entries) == 1
    assert entries[0]["degraded"] is True
    assert "gone" in entries[0]["name"]
    assert "not reachable" in entries[0]["description"]
    result = await pool.call("mcp.gone.whatever", {})
    assert "error" in result
    await pool.stop()


def test_degraded_descriptor_says_what_and_why():
    entry = degraded_descriptor("linear", "ConnectionRefusedError: refused")
    assert entry["degraded_reason"].startswith("ConnectionRefusedError")
    assert "DEGRADED" in entry["description"]


# ---- OAuth connector broker ---------------------------------------------------------


class FakeEnvelope:
    """An envelope stand-in whose "ciphertext" is opaque (base64), so a test
    asserting "the token is not in the row" is testing what it says."""

    async def seal(self, payload: Any, space_id: str, *, aad: str = "") -> str:
        import base64

        return json.dumps({
            "space": space_id, "aad": aad,
            "body": base64.b64encode(str(payload).encode("utf-8")).decode("ascii"),
        })

    async def open(self, sealed: Any, space_id: str, **_: Any) -> bytes:
        import base64

        return base64.b64decode(json.loads(sealed)["body"])


class ConnectorDB:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.startswith("INSERT INTO connector_accounts"):
            self.rows[args[0]] = {
                "connector_id": args[0], "tenant_id": args[1], "space_id": args[2],
                "provider": args[3], "sealed_tokens": json.loads(args[5]),
                "scopes": json.loads(args[6]), "project_scope_json": json.loads(args[7]),
                "status": "connected", "expires_at": None,
            }
            return "INSERT 0 1"
        if sql.startswith("UPDATE connector_accounts SET sealed_tokens"):
            row = self.rows[args[0]]
            row["sealed_tokens"] = json.loads(args[1])
            return "UPDATE 1"
        if sql.startswith("UPDATE connector_accounts SET status"):
            if args[0] in self.rows:
                self.rows[args[0]]["status"] = "revoked" if "revoked" in sql else "expired"
                return "UPDATE 1"
            return "UPDATE 0"
        return "UPDATE 0"

    async def fetchrow(self, sql: str, *args: Any):
        row = self.rows.get(args[0])
        return row

    async def fetch(self, sql: str, *args: Any):
        return list(self.rows.values())


async def test_authorization_begin_and_callback_seal_the_tokens(monkeypatch):
    from clio.toolhost import connectors as module

    db = ConnectorDB()
    broker = module.ConnectorBroker(
        db, FakeEnvelope(),
        providers={"acme": module.ProviderDefinition(
            name="acme", authorize_url="https://acme.example/authorize",
            token_url="https://acme.example/token", default_scopes=["read"],
        )},
        client_credentials={"acme": ("client-1", "secret-1")},
    )
    begun = broker.begin_authorization("acme", project_id="p1")
    assert begun["url"].startswith("https://acme.example/authorize?")
    assert "code_challenge=" in begun["url"], "PKCE is on by default"

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"access_token": "at-123", "refresh_token": "rt-456", "expires_in": 3600}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_: Any) -> None: return None

        async def post(self, url: str, data: Any = None, headers: Any = None):
            assert url == "https://acme.example/token"
            assert data["code_verifier"], "the verifier travels on exchange"
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    report = await broker.complete_authorization("acme", "code-1", begun["state"])
    connector_id = report["connector_id"]
    row = db.rows[connector_id]
    assert "at-123" not in json.dumps(row["sealed_tokens"]), "tokens are sealed at rest"

    token = await broker.access_token(connector_id, project_id="p1")
    assert token == "at-123"


async def test_a_replayed_state_is_refused():
    from clio.toolhost.connectors import ConnectorBroker, ConnectorError

    broker = ConnectorBroker(ConnectorDB(), FakeEnvelope(),
                             client_credentials={"acme": ("c", "s")},
                             providers={"acme": __import__("clio.toolhost.connectors",
                                                           fromlist=["ProviderDefinition"]).ProviderDefinition(
                                 name="acme", authorize_url="u", token_url="t")})
    broker.begin_authorization("acme")
    with pytest.raises(ConnectorError):
        await broker.complete_authorization("acme", "code", "not-the-state")


async def test_a_connector_not_scoped_to_a_project_refuses():
    from clio.toolhost.connectors import ConnectorBroker, ConnectorError

    db = ConnectorDB()
    broker = ConnectorBroker(db, FakeEnvelope(), client_credentials={"acme": ("c", "s")})
    # Hand-write an account scoped to p1 only, sealed the way the broker seals.
    import base64

    envelope = json.dumps({
        "space": "primary", "aad": "connector:conn-1",
        "body": base64.b64encode(json.dumps({"access_token": "at"}).encode("utf-8")).decode("ascii"),
    })
    db.rows["conn-1"] = {
        "connector_id": "conn-1", "tenant_id": "local", "space_id": "primary",
        "provider": "github", "status": "connected", "expires_at": None,
        "project_scope_json": {"p1": ["repo"]},
        "sealed_tokens": {"space_id": "primary", "envelope": envelope},
    }
    with pytest.raises(ConnectorError):
        await broker.access_token("conn-1", project_id="p2")
    assert await broker.access_token("conn-1", project_id="p1") == "at"


# ---- OpenAPI / GraphQL importers ------------------------------------------------------


OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Widget API"},
    "servers": [{"url": "https://api.widgets.example/v1"}],
    "paths": {
        "/widgets/{id}": {
            "get": {
                "operationId": "getWidget",
                "summary": "Fetch one widget",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"$ref": "#/components/parameters/Verbose"},
                ],
            },
            "delete": {"operationId": "deleteWidget"},
        },
        "/widgets": {
            "post": {
                "operationId": "createWidget",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/Widget"},
                    }},
                },
            },
        },
    },
    "components": {
        "parameters": {"Verbose": {"name": "verbose", "in": "query",
                                   "schema": {"type": "boolean"}}},
        "schemas": {
            "Widget": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "tags": {"type": "array", "items": {"$ref": "#/components/schemas/Tag"}},
                },
                "required": ["name"],
            },
            "Tag": {"type": "object", "properties": {"key": {"type": "string"}}},
        },
    },
}


def test_openapi_import_resolves_refs_recursively():
    tools = openapi_tools(OPENAPI)
    by_key = {t["tool_key"]: t for t in tools}
    assert set(by_key) == {
        "openapi.widget_api.getwidget",
        "openapi.widget_api.deletewidget",
        "openapi.widget_api.createwidget",
    }
    get = by_key["openapi.widget_api.getwidget"]
    props = get["input_schema"]["properties"]
    assert "id" in props and props["id"]["type"] == "string"
    assert "verbose" in props, "a $ref parameter resolves to its definition"
    assert get["endpoint"]["url"] == "https://api.widgets.example/v1/widgets/{id}"

    post = by_key["openapi.widget_api.createwidget"]
    body = post["input_schema"]["properties"]["body"]
    assert body["properties"]["name"]["type"] == "string"
    assert body["properties"]["tags"]["items"]["properties"]["key"]["type"] == "string", \
        "$refs inside arrays resolve"


def test_openapi_rejects_a_dangling_ref():
    from clio.toolhost.importers import ImportError_

    with pytest.raises(ImportError_):
        openapi_tools({
            "info": {"title": "x"}, "paths": {"/a": {"get": {
                "parameters": [{"$ref": "#/components/parameters/Missing"}],
            }}},
        })


def test_resolve_ref_catches_cycles():
    from clio.toolhost.importers import ImportError_

    doc = {"components": {"schemas": {"A": {"$ref": "#/components/schemas/B"},
                                      "B": {"$ref": "#/components/schemas/A"}}}}
    with pytest.raises(ImportError_):
        resolve_ref(doc, {"$ref": "#/components/schemas/A"})


SDL = """
type Query {
  issue(id: ID!): String
  search(query: String, limit: Int = 10): [String!]!
}

type Mutation {
  createIssue(title: String!, body: String): String
}
"""


def test_graphql_sdl_import_maps_fields_and_args():
    tools = graphql_tools(SDL, slug="linear", endpoint="https://api.linear.app/graphql")
    by_key = {t["tool_key"]: t for t in tools}
    assert "graphql.linear.issue" in by_key
    issue = by_key["graphql.linear.issue"]
    assert issue["input_schema"]["required"] == ["id"]
    assert issue["endpoint"]["operation"] == "query"
    create = by_key["graphql.linear.createissue"]
    assert create["endpoint"]["operation"] == "mutation"
    assert create["input_schema"]["properties"]["title"]["type"] == "string"


def test_graphql_introspection_import():
    introspection = {"data": {"__schema": {"types": [
        {"name": "Query", "fields": [
            {"name": "viewer", "description": "the viewer", "args": [],
             "type": {"kind": "OBJECT", "name": "User"}},
        ]},
        {"name": "Mutation", "fields": [
            {"name": "post", "description": "", "args": [
                {"name": "text", "type": {"kind": "NON_NULL",
                                          "ofType": {"kind": "SCALAR", "name": "String"}}},
            ], "type": {"kind": "SCALAR", "name": "String"}},
        ]},
    ]}}}
    tools = graphql_tools("", slug="g", introspection=introspection)
    keys = {t["tool_key"] for t in tools}
    assert {"graphql.g.viewer", "graphql.g.post"} <= keys
    post = next(t for t in tools if t["tool_key"] == "graphql.g.post")
    assert post["input_schema"]["required"] == ["text"]


async def test_persist_imported_tools_writes_rows():
    from clio.toolhost.importers import catalog_entries_from_rows, persist_imported_tools

    class DB:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        async def execute(self, sql, *args):
            self.rows.append({"tool_key": args[0], "source": args[3],
                              "spec_json": json.loads(args[5])})
            return "INSERT 0 1"

    db = DB()
    tools = openapi_tools(OPENAPI)
    count = await persist_imported_tools(db, tools)
    assert count == len(tools)
    entries = catalog_entries_from_rows(db.rows)
    assert entries[0]["name"].startswith("openapi.")
    assert entries[0]["input_schema"]
