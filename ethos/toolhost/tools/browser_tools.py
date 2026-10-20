from __future__ import annotations

from typing import Any

from ethos.toolhost.browser import BrowserError
from ethos.toolhost.server import Tool, ToolContext, Toolhost


class _BrowserTool(Tool):
    def _pool(self, host: Toolhost) -> Any:
        return host.browser_pool


class BrowserVisitTool(_BrowserTool):
    name = "browser.visit"
    description = "Open a URL in the Playwright browser session."
    timeout_s = 90.0
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}, "session_id": {"type": "string"}},
        "required": ["url"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = await self._pool(host).get_or_create(str(args.get("session_id", "default")))
        return await session.visit(str(args["url"]))


class BrowserClickTool(_BrowserTool):
    name = "browser.click"
    description = "Click a CSS selector in the browser session."
    timeout_s = 40.0
    input_schema = {
        "type": "object",
        "properties": {"selector": {"type": "string"}, "session_id": {"type": "string"}},
        "required": ["selector"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = await self._pool(host).get_or_create(str(args.get("session_id", "default")))
        return await session.click(str(args["selector"]))


class BrowserTypeTool(_BrowserTool):
    name = "browser.type"
    description = "Type text into a form field (optionally pressing Enter)."
    timeout_s = 40.0
    input_schema = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "submit": {"type": "boolean"},
            "session_id": {"type": "string"},
        },
        "required": ["selector", "text"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = await self._pool(host).get_or_create(str(args.get("session_id", "default")))
        return await session.type(str(args["selector"]), str(args["text"]),
                                  submit=bool(args.get("submit", False)))


class BrowserExtractTool(_BrowserTool):
    name = "browser.extract"
    description = "Extract text (or html/href/src) from a selector."
    timeout_s = 40.0
    input_schema = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "attribute": {"type": "string", "enum": ["text", "html", "href", "src"]},
            "session_id": {"type": "string"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = await self._pool(host).get_or_create(str(args.get("session_id", "default")))
        return await session.extract(
            str(args.get("selector", "body")), attribute=args.get("attribute"),
        )


class BrowserScreenshotTool(_BrowserTool):
    name = "browser.screenshot"
    description = "Take a screenshot (base64 PNG)."
    timeout_s = 60.0
    input_schema = {
        "type": "object",
        "properties": {"full_page": {"type": "boolean"}, "session_id": {"type": "string"}},
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = await self._pool(host).get_or_create(str(args.get("session_id", "default")))
        return await session.screenshot(bool(args.get("full_page", False)))


class BrowserCloseTool(_BrowserTool):
    name = "browser.close"
    description = "Close a browser session."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        await self._pool(host).close(str(args.get("session_id", "default")))
        return {"closed": str(args.get("session_id", "default"))}


__all__ = [
    "BrowserVisitTool", "BrowserClickTool", "BrowserTypeTool", "BrowserExtractTool",
    "BrowserScreenshotTool", "BrowserCloseTool", "BrowserError",
]
