"""Zero-code OpenAPI and GraphQL importers: a schema in, typed tools out.

A deployment that already describes its API — an OpenAPI 3.x document, a
GraphQL schema or introspection result — should not need a hand-written tool
per endpoint. These importers turn the schema into typed, schema-validated
tool descriptors:

- **OpenAPI 3.x** — every operation becomes one tool named
  `openapi.<slug>.<operationId>`, with its parameters, request body and
  response summarized into the input schema. `$ref`s are resolved
  recursively (including refs to refs and refs inside arrays/allOf), so a
  tool's schema is the complete type and not a pointer the step cannot
  follow.
- **GraphQL** — the SDL parser reads type and field definitions (or an
  introspection JSON result), and every query and mutation field becomes
  `graphql.<slug>.<field>` with arguments mapped to the input schema.

Imported tools execute through the toolhost like every other tool — the
guardian's CEL policies govern them and each carries its own
`cel_policy_json` for endpoint-specific rules.
"""

from __future__ import annotations

import json
import re
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.toolhost.importers")

MAX_REF_DEPTH = 12


class ImportError_(ValueError):
    """A schema that cannot be imported. Named to avoid the builtin shadow."""


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()
    return slug or "api"


# ---------------------------------------------------------------------------
# OpenAPI


def resolve_ref(document: dict[str, Any], node: Any, *, depth: int = 0) -> Any:
    """Resolve `$ref` nodes recursively against `components`.

    Refs pointing at refs are followed (bounded), refs inside `items`,
    `allOf`/`oneOf`/`anyOf` and `properties` are resolved too. A cycle is a
    schema bug, and the bounded depth turns it into an import error instead
    of an infinite walk.
    """
    if depth > MAX_REF_DEPTH:
        raise ImportError_("$ref chain is too deep (a cycle?)")
    if isinstance(node, list):
        # Ordinary nesting does not count toward the depth: the bound is on
        # the length of a `$ref` CHAIN (A → B → C → A), which is the cycle
        # worth catching. Counting every container level would reject any
        # document deeper than a dozen objects, which is most of them.
        return [resolve_ref(document, item, depth=depth) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = str(node["$ref"])
        if not ref.startswith("#/"):
            raise ImportError_(f"external $ref not resolvable: {ref}")
        target: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise ImportError_(f"dangling $ref: {ref}")
            target = target[part]
        merged = dict(resolve_ref(document, target, depth=depth + 1))
        # Sibling keys of `$ref` (OpenAPI 3.1 allows them) win over the target.
        for key, value in node.items():
            if key != "$ref":
                merged[key] = resolve_ref(document, value, depth=depth + 1)
        return merged
    return {key: resolve_ref(document, value, depth=depth) for key, value in node.items()}


def _schema_from_parameters(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters:
        name = str(parameter.get("name") or "")
        if not name:
            continue
        schema = dict(parameter.get("schema") or {"type": "string"})
        if parameter.get("description"):
            schema.setdefault("description", parameter["description"])
        properties[name] = schema
        if parameter.get("required"):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def openapi_tools(document: dict[str, Any], *, base_url: str = "",
                  slug: str | None = None) -> list[dict[str, Any]]:
    """Every operation in an OpenAPI 3.x document, as tool descriptors."""
    if not isinstance(document, dict) or "paths" not in document:
        raise ImportError_("not an OpenAPI document (no `paths`)")
    slug = slug or _slug(str((document.get("info") or {}).get("title") or "api"))
    document = resolve_ref(document, document)
    servers = document.get("servers") or []
    server_url = base_url or (str(servers[0].get("url") or "") if servers else "")
    tools: list[dict[str, Any]] = []
    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or f"{method}_{path}")
            tool_name = f"openapi.{slug}.{_slug(operation_id)}"
            input_schema = _schema_from_parameters(
                [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]
            )
            body = operation.get("requestBody")
            if isinstance(body, dict):
                content = (body.get("content") or {})
                media = content.get("application/json") or next(iter(content.values()), {})
                body_schema = dict(media.get("schema") or {"type": "object"})
                input_schema["properties"]["body"] = body_schema
                if body.get("required"):
                    input_schema["required"].append("body")
            tools.append({
                "tool_key": tool_name,
                "source": "openapi",
                "description": (
                    f"[{slug}] {method.upper()} {path} — "
                    f"{' '.join(str(operation.get('summary') or operation.get('description') or operation_id).split())[:200]}"
                ),
                "input_schema": input_schema,
                "endpoint": {
                    "method": method.upper(),
                    "url": f"{server_url.rstrip('/')}{path}",
                    "parameters": [str(p.get("name")) for p in
                                   (path_item.get("parameters") or []) + (operation.get("parameters") or [])
                                   if p.get("name")],
                },
            })
    return tools


# ---------------------------------------------------------------------------
# GraphQL


_SDL_TYPE_RE = re.compile(
    r"type\s+(\w+)\s*(?:implements\s+[^{]+)?\{(?P<body>[^}]*)\}", re.DOTALL,
)
_SDL_FIELD_RE = re.compile(
    r"(?P<name>\w+)\s*(?:\((?P<args>[^)]*)\))?\s*:\s*(?P<type>[\[\]!\w]+)",
)
_SDL_ARG_RE = re.compile(r"(?P<name>\w+)\s*:\s*(?P<type>[\[\]!\w]+)(?:\s*=\s*(?P<default>[^,)]+))?")


def _gql_type_to_json(schema_type: str) -> dict[str, Any]:
    gql = schema_type.strip()
    if gql.endswith("!"):
        return _gql_type_to_json(gql[:-1])
    if gql.startswith("[") and gql.endswith("]"):
        return {"type": "array", "items": _gql_type_to_json(gql[1:-1])}
    mapping = {
        "Int": {"type": "integer"}, "Float": {"type": "number"},
        "Boolean": {"type": "boolean"}, "String": {"type": "string"},
        "ID": {"type": "string"},
    }
    return mapping.get(gql, {"type": "string", "description": f"GraphQL type {gql}"})


def _fields_from_sdl(body: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for match in _SDL_FIELD_RE.finditer(body or ""):
        args = []
        for arg in _SDL_ARG_RE.finditer(match.group("args") or ""):
            args.append({
                "name": arg.group("name"),
                "schema": _gql_type_to_json(arg.group("type")),
                "required": arg.group("type").endswith("!"),
            })
        fields.append({"name": match.group("name"), "args": args,
                       "type": match.group("type")})
    return fields


def graphql_tools(source: str, *, slug: str | None = None,
                  endpoint: str = "", introspection: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Query and mutation fields as tool descriptors.

    Two accepted sources: introspection JSON (preferred — it is what a live
    server gives you) or SDL text. The SDL parser is deliberately narrow —
    it reads `type`/`Query`/`Mutation` blocks with field arguments — because
    a full GraphQL grammar here would be a second parser to keep correct;
    anything richer comes in as introspection.
    """
    slug = slug or "graphql"
    fields_by_container: dict[str, list[dict[str, Any]]] = {}
    if introspection is not None:
        schema = (((introspection.get("data") or introspection).get("__schema")) or {})
        for type_info in schema.get("types") or []:
            name = str(type_info.get("name") or "")
            if name not in ("Query", "Mutation"):
                continue
            fields = []
            for field in type_info.get("fields") or []:
                args = []
                for arg in field.get("args") or []:
                    args.append({
                        "name": str(arg.get("name")),
                        "schema": _gql_type_to_json(_introspection_type(arg.get("type") or {})),
                        "required": bool((arg.get("type") or {}).get("kind") == "NON_NULL"),
                    })
                fields.append({"name": str(field.get("name")), "args": args,
                               "description": str(field.get("description") or "")})
            fields_by_container[name] = fields
    else:
        for match in _SDL_TYPE_RE.finditer(source or ""):
            container = match.group(1)
            if container in ("Query", "Mutation"):
                fields_by_container[container] = _fields_from_sdl(match.group("body"))
    tools: list[dict[str, Any]] = []
    for container, fields in fields_by_container.items():
        kind = "query" if container == "Query" else "mutation"
        for field in fields:
            input_schema = {
                "type": "object",
                "properties": {arg["name"]: arg["schema"] for arg in field["args"]},
                "required": [arg["name"] for arg in field["args"] if arg["required"]],
            }
            tools.append({
                "tool_key": f"graphql.{slug}.{_slug(field['name'])}",
                "source": "graphql",
                "description": (
                    f"[{slug}] GraphQL {kind} `{field['name']}` — "
                    f"{(field.get('description') or 'defined by the schema')[:200]}"
                ),
                "input_schema": input_schema,
                "endpoint": {"url": endpoint, "operation": kind, "field": field["name"]},
            })
    return tools


def _introspection_type(type_info: dict[str, Any]) -> str:
    """`{kind: LIST, ofType: {kind: NON_NULL, ofType: {...name: 'Issue'}}}` → `[Issue!]`."""
    kind = type_info.get("kind")
    if kind == "NON_NULL":
        return f"{_introspection_type(type_info.get('ofType') or {})}!"
    if kind == "LIST":
        return f"[{_introspection_type(type_info.get('ofType') or {})}]"
    return str(type_info.get("name") or "String")


# ---------------------------------------------------------------------------
# Persistence: imported tools register into `imported_tools`.


async def persist_imported_tools(db: Any, tools: list[dict[str, Any]], *,
                                 space_id: str = "primary", tenant_id: str = "local",
                                 connector_id: str | None = None,
                                 cel_policy: dict[str, Any] | None = None) -> int:
    """Write imported descriptors into the catalog table. Returns the count."""
    written = 0
    for tool in tools:
        await db.execute(
            "INSERT INTO imported_tools (tool_key, tenant_id, space_id, source, connector_id, "
            "spec_json, cel_policy_json, enabled) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, true) "
            "ON CONFLICT (tool_key, tenant_id, space_id) DO UPDATE SET "
            "spec_json = EXCLUDED.spec_json, connector_id = EXCLUDED.connector_id, "
            "updated_at = now()",
            tool["tool_key"], tenant_id, space_id, tool["source"], connector_id,
            json.dumps({k: v for k, v in tool.items() if k != "tool_key"}),
            json.dumps(cel_policy or {}),
        )
        written += 1
    logger.info("importers.persisted", count=written, source=tools[0]["source"] if tools else None)
    return written


def catalog_entries_from_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Imported tool rows as catalog entries the narrowing can route."""
    entries: list[dict[str, Any]] = []
    for row in rows:
        spec = row["spec_json"] if not isinstance(row["spec_json"], str) else json.loads(row["spec_json"])
        entries.append({
            "name": str(row["tool_key"]),
            "description": str(spec.get("description") or ""),
            "input_schema": dict(spec.get("input_schema") or {}),
            "source": str(row["source"]),
        })
    return entries


__all__ = [
    "ImportError_",
    "catalog_entries_from_rows",
    "graphql_tools",
    "openapi_tools",
    "persist_imported_tools",
    "resolve_ref",
]
