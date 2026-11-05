from __future__ import annotations

from typing import Any

from ethos.schemas.tools import ToolErrorCode, ToolResult
from ethos.toolhost.browser import BrowserError, NoPageError, SelectorTimeoutError
from ethos.toolhost.server import Tool, ToolContext, Toolhost


class _BrowserTool(Tool):
    def _pool(self, host: Toolhost) -> Any:
        return host.browser_pool

    def _open_pages(self, host: Toolhost) -> str:
        """Where the pages the agent already opened actually live.

        Appended to a `no page open` refusal: the fix is one `session_id` away
        from the browser that already holds the page, and this is the map. An
        error that only says the session is empty leaves the agent to guess at
        ids it has used; one that lists `helmet-search -> https://…` lets the
        next call land in the right browser the first time.
        """
        pages = self._pool(host).open_pages()
        if not pages:
            return "No session in the pool has a page open."
        listed = "; ".join(f"{session_id} -> {url}" for session_id, url in pages.items())
        return f"Sessions with a page open: {listed}."

    def _session_error(self, host: Toolhost, exc: BrowserError) -> ToolResult:
        """A session-layer refusal as a tool verdict, with the code it deserves.

        `no page open` is an environment state the agent can repair by aiming
        the next call at the right session; a selector timeout is a genuine
        timeout on a page that did load. Both carry the full sentence — the
        message is the diagnosis, and the error code only routes it.
        """
        message = str(exc)
        if isinstance(exc, NoPageError):
            return ToolResult(
                ok=False, error_code=ToolErrorCode.ENVIRONMENT,
                error=f"{message} {self._open_pages(host)}",
            )
        if isinstance(exc, SelectorTimeoutError):
            return ToolResult(ok=False, error_code=ToolErrorCode.TIMEOUT, error=message)
        return ToolResult(ok=False, error_code=ToolErrorCode.ENVIRONMENT, error=message)


class BrowserVisitTool(_BrowserTool):
    name = "browser.visit"
    description = (
        "Open a URL in a Playwright browser session; later actions need the "
        "same session_id."
    )
    timeout_s = 90.0
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}, "session_id": {"type": "string"}},
        "required": ["url"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = await self._pool(host).get_or_create(str(args.get("session_id", "default")))
        try:
            return await session.visit(str(args["url"]))
        except BrowserError as exc:
            return self._session_error(host, exc)


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
        try:
            return await session.click(str(args["selector"]))
        except BrowserError as exc:
            return self._session_error(host, exc)


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
        try:
            return await session.type(str(args["selector"]), str(args["text"]),
                                      submit=bool(args.get("submit", False)))
        except BrowserError as exc:
            return self._session_error(host, exc)


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
        try:
            return await session.extract(
                str(args.get("selector", "body")), attribute=args.get("attribute"),
            )
        except BrowserError as exc:
            return self._session_error(host, exc)


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
        try:
            return await session.screenshot(bool(args.get("full_page", False)))
        except BrowserError as exc:
            return self._session_error(host, exc)


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
