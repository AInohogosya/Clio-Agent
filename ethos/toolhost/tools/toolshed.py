from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost

TOOL_YAML = """\
name: {name}
description: {description}
entrypoint: {entrypoint}
"""


class ToolsRegisterTool(Tool):
    name = "tools.register"
    description = (
        "Register a self-written tool from ~/toolshed/<name>/tool.yaml "
        "(name, description, input schema, entrypoint command)."
    )
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "directory under the toolshed"},
        },
        "required": ["name"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        name = str(args["name"]).strip().strip("/")
        toolshed = host.config.paths.toolshed.expanduser()
        tool_dir = toolshed / name
        manifest = tool_dir / "tool.yaml"
        if not manifest.exists():
            raise FileNotFoundError(f"no tool.yaml in {tool_dir}")
        import yaml

        spec = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        if not spec.get("name") or not spec.get("entrypoint"):
            raise ValueError("tool.yaml requires name and entrypoint")
        entrypoint_path = tool_dir / str(spec["entrypoint"])
        if not entrypoint_path.exists():
            raise FileNotFoundError(f"entrypoint missing: {entrypoint_path}")
        schema = spec.get("parameters") or spec.get("input_schema") or {
            "type": "object", "properties": {},
        }
        await host.service("memory").upsert_env_entity(
            kind="tool", key=f"toolshed:{spec['name']}",
            attrs={
                "name": spec["name"],
                "description": spec.get("description", ""),
                "input_schema": schema,
                "entrypoint": str(entrypoint_path),
                "timeout_s": float(spec.get("timeout_s", 60)),
            },
        )
        return {"registered": spec["name"], "entrypoint": str(entrypoint_path)}


class ToolsRunTool(Tool):
    name = "tools.run"
    description = "Run a toolshed tool: the entrypoint receives JSON args on stdin and must answer JSON on stdout."
    timeout_s = 120.0
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["name"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        name = str(args["name"])
        memory = host.service("memory")
        entity = await memory.get_env_entity(f"toolshed:{name}")
        if entity is None:
            raise KeyError(f"toolshed tool not registered: {name}")
        attrs = entity.get("attrs", {})
        entrypoint = attrs.get("entrypoint")
        timeout_s = float(attrs.get("timeout_s", 60))
        process = await asyncio.create_subprocess_exec(
            str(entrypoint),
            cwd=str(Path(entrypoint).parent),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(args.get("args") or {}).encode("utf-8")),
                timeout=timeout_s,
            )
        except TimeoutError:
            process.kill()
            raise TimeoutError(f"toolshed tool {name} timed out") from None
        if process.returncode != 0:
            raise RuntimeError(
                f"toolshed tool {name} failed rc={process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')[:1000]}"
            )
        try:
            return json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError:
            return {"stdout": stdout.decode("utf-8", errors="replace")[:100_000]}


class ToolsListTool(Tool):
    name = "tools.list"
    description = "List registered toolshed tools."
    timeout_s = 20.0
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        memory = host.service("memory")
        entities = await memory.list_env_entities(kind="tool")
        return [
            {
                "name": e["attrs"].get("name"),
                "description": e["attrs"].get("description", ""),
                "input_schema": e["attrs"].get("input_schema"),
            }
            for e in entities
        ]


__all__ = ["ToolsRegisterTool", "ToolsRunTool", "ToolsListTool", "TOOL_YAML"]
