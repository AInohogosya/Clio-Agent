from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ethos.toolhost.server import Tool, ToolContext, Toolhost


def _content_hash(entrypoint: Any) -> str:
    import hashlib
    from pathlib import Path

    try:
        data = Path(str(entrypoint)).read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def verify_tool_signature(
    content_hash: str, signature: str | None, public_key: str | None = None,
) -> bool:
    """Ed25519-verify a toolshed manifest when a signature is present.

    Returns True when there is nothing to verify or the signature checks
    out; False when a signature is present but does not verify.
    """
    if not signature:
        return True
    if not public_key:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key))
        key.verify(bytes.fromhex(signature), content_hash.encode("utf-8"))
        return True
    except Exception:
        return False


TOOL_YAML = """\
name: {name}
description: {description}
entrypoint: {entrypoint}
# permissions (enforced by GuardianGate before execution):
# allowed_paths: []
# allowed_hosts: []
# secret_scopes: []
# timeout_s: 60
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
        content_hash = _content_hash(entrypoint_path)
        signature = spec.get("signature") or spec.get("ed25519_signature")
        public_key = spec.get("public_key") or spec.get("signing_public_key")
        if signature and not verify_tool_signature(content_hash, str(signature), public_key and str(public_key)):
            raise ValueError(f"toolshed tool {spec['name']!r} signature does not verify")
        manifest = {
            "allowed_paths": list(spec.get("allowed_paths") or []),
            "allowed_hosts": list(spec.get("allowed_hosts") or []),
            "secret_scopes": list(spec.get("secret_scopes") or []),
            "timeout_s": float(spec.get("timeout_s", 60)),
        }
        await host.service("memory").upsert_env_entity(
            kind="tool", key=f"toolshed:{spec['name']}",
            attrs={
                "name": spec["name"],
                "description": spec.get("description", ""),
                "input_schema": schema,
                "entrypoint": str(entrypoint_path),
                "timeout_s": manifest["timeout_s"],
                "manifest": manifest,
                "content_hash": content_hash,
                "signature": signature or None,
            },
        )
        return {
            "registered": spec["name"], "entrypoint": str(entrypoint_path),
            "manifest": manifest, "content_hash": content_hash,
        }


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
        # Manifest-gated: re-verify the content hash and enforce the
        # manifest before execution (the gate does too, on the tool call).
        manifest = attrs.get("manifest") or {}
        expected_hash = attrs.get("content_hash")
        if expected_hash:
            actual = _content_hash(entrypoint)
            if actual != expected_hash:
                raise ValueError(
                    f"toolshed tool {name!r} content changed since registration"
                )
        if manifest.get("signature") and not verify_tool_signature(
            expected_hash or "", str(manifest["signature"]),
            str(manifest.get("public_key") or ""),
        ):
            raise ValueError(f"toolshed tool {name!r} signature does not verify")
        # Path/host fences for the tool call itself, checked here as well as
        # in GuardianGate so the run path is safe even without the gate.
        inner_args = dict(args.get("args") or {})
        for key in ("path", "to_path", "url", "host"):
            value = inner_args.get(key)
            if isinstance(value, str) and value:
                allowed_paths = manifest.get("allowed_paths") or []
                allowed_hosts = manifest.get("allowed_hosts") or []
                if key in ("path", "to_path") and allowed_paths and not any(
                    value.startswith(p) for p in allowed_paths
                ):
                    raise PermissionError(
                        f"tool {name!r} manifest does not allow path {value!r}"
                    )
                if key in ("url", "host") and allowed_hosts and not any(
                    value.startswith(p) for p in allowed_hosts
                ):
                    raise PermissionError(
                        f"tool {name!r} manifest does not allow host {value!r}"
                    )
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
                "manifest": e["attrs"].get("manifest") or {},
                "content_hash": e["attrs"].get("content_hash"),
            }
            for e in entities
        ]


__all__ = ["ToolsRegisterTool", "ToolsRunTool", "ToolsListTool", "TOOL_YAML"]
