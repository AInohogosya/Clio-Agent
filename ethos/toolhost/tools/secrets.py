from __future__ import annotations

from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


class SecretsSetTool(Tool):
    name = "secrets.set"
    description = "Store a secret encrypted at rest. Only its {{secret:KEY}} handle is ever used."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"},
        },
        "required": ["key", "value"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        vault = host.service("vault")
        vault.set_secret(str(args["key"]), str(args["value"]))
        return {"key": str(args["key"]), "stored": True,
                "handle": "{{secret:" + str(args["key"]) + "}}"},


class SecretsGetTool(Tool):
    name = "secrets.get"
    description = "Check a secret exists. NEVER returns plaintext to the mind."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        vault = host.service("vault")
        from ethos.gateway.secrets import SecretNotFoundError

        try:
            vault.get_secret(str(args["key"]))
            return {"key": str(args["key"]), "exists": True}
        except SecretNotFoundError:
            return {"key": str(args["key"]), "exists": False}


class SecretsListTool(Tool):
    name = "secrets.list"
    description = "List secret keys (no values)."
    timeout_s = 20.0
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        vault = host.service("vault")
        return {"keys": vault.list_keys()}


__all__ = ["SecretsSetTool", "SecretsGetTool", "SecretsListTool"]
