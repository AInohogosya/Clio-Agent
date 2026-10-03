from __future__ import annotations

from typing import Any

from ethos.toolhost.desktop import DesktopError
from ethos.toolhost.server import Tool, ToolContext, Toolhost


class _DesktopTool(Tool):
    def _controller(self, host: Toolhost) -> Any:
        return host.desktop


class DesktopKeyTool(_DesktopTool):
    name = "desktop.key"
    description = "Send a key sequence to the desktop on Xvfb :1 (xdotool)."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {"keys": {"type": "string"}},
        "required": ["keys"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        import asyncio

        return await asyncio.to_thread(self._controller(host).key, str(args["keys"]))


class DesktopTypeTool(_DesktopTool):
    name = "desktop.type"
    description = "Type text on the desktop (xdotool)."
    timeout_s = 60.0
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        import asyncio

        return await asyncio.to_thread(self._controller(host).type, str(args["text"]))


class DesktopClickTool(_DesktopTool):
    name = "desktop.click"
    description = "Move the mouse and click at (x, y)."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "integer"},
        },
        "required": ["x", "y"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        import asyncio

        return await asyncio.to_thread(
            self._controller(host).click, int(args["x"]), int(args["y"]),
            int(args.get("button", 1)),
        )


class DesktopMoveTool(_DesktopTool):
    name = "desktop.move"
    description = "Move the mouse to (x, y)."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
        "required": ["x", "y"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        import asyncio

        return await asyncio.to_thread(
            self._controller(host).move, int(args["x"]), int(args["y"]),
        )


class DesktopScreenshotTool(_DesktopTool):
    name = "desktop.screenshot"
    description = "Screenshot the desktop (base64 PNG)."
    timeout_s = 45.0
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        import asyncio

        return await asyncio.to_thread(self._controller(host).screenshot)


class DesktopWindowsTool(_DesktopTool):
    name = "desktop.windows"
    description = "List visible windows on the desktop."
    timeout_s = 30.0
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        import asyncio

        return await asyncio.to_thread(self._controller(host).windows)


__all__ = [
    "DesktopKeyTool", "DesktopTypeTool", "DesktopClickTool", "DesktopMoveTool",
    "DesktopScreenshotTool", "DesktopWindowsTool", "DesktopError",
]
