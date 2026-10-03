from __future__ import annotations

import base64
import re
from typing import Any

from ethos.observability.logger import get_logger
from ethos.schemas.tools import ToolErrorCode, ToolResult

logger = get_logger("ethos.toolhost.browser")

SELECTOR_RE = re.compile(r"""^[A-Za-z#.\[\]="'\-\w :>+~*,()]+$""")


class BrowserError(RuntimeError):
    pass


class BrowserSession:
    """Playwright Chromium automation. Runs headless by default; on the agent's
    desktop VM it can target the Xvfb display for a headed browser."""

    def __init__(self, session_id: str, headless: bool = True):
        self.id = session_id
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserError("playwright is not installed") from exc
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
        except Exception as exc:
            await self._stop_playwright()
            raise BrowserError(
                f"chromium launch failed (run `playwright install chromium`): {exc}"
            ) from exc
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserError("browser session is not open")
        return self._page

    def _validate_selector(self, selector: str) -> str:
        if not selector or not SELECTOR_RE.match(selector.strip()) or len(selector) > 300:
            raise BrowserError("invalid selector")
        return selector.strip()

    async def visit(self, url: str) -> dict[str, Any]:
        if not re.match(r"^https?://", url):
            raise BrowserError("only http(s) URLs are allowed")
        page = self._require_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        return {"url": url, "status": response.status if response else None}

    async def click(self, selector: str) -> dict[str, Any]:
        page = self._require_page()
        await page.click(self._validate_selector(selector), timeout=15_000)
        return {"clicked": selector}

    async def type(self, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        page = self._require_page()
        await page.fill(self._validate_selector(selector), text, timeout=15_000)
        if submit:
            await page.press(self._validate_selector(selector), "Enter")
        return {"typed": selector, "submitted": submit}

    async def extract(self, selector: str = "body", attribute: str | None = None) -> dict[str, Any]:
        page = self._require_page()
        if attribute == "html":
            html = await page.inner_html(self._validate_selector(selector), timeout=15_000)
            return {"text": html[:200_000]}
        if attribute and attribute not in ("href", "src", "text"):
            raise BrowserError("attribute must be text, href, src or html")
        if attribute:
            value = await page.get_attribute(self._validate_selector(selector), attribute, timeout=15_000)
            return {"text": str(value or "")[:200_000]}
        text = await page.inner_text(self._validate_selector(selector), timeout=15_000)
        return {"text": text[:200_000]}

    async def screenshot(self, full_page: bool = False) -> dict[str, Any]:
        page = self._require_page()
        shot = await page.screenshot(full_page=full_page)
        return {"image_b64": base64.b64encode(shot).decode("ascii"), "bytes": len(shot)}

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
        finally:
            await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._browser = None
        self._context = None
        self._page = None


class BrowserPool:
    def __init__(self, default_headless: bool = True):
        self.sessions: dict[str, BrowserSession] = {}
        self.default_headless = default_headless

    async def get_or_create(self, session_id: str = "default", headless: bool | None = None) -> BrowserSession:
        if session_id in self.sessions:
            return self.sessions[session_id]
        session = BrowserSession(session_id, headless=self.default_headless if headless is None else headless)
        await session.start()
        self.sessions[session_id] = session
        return session

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id)


def browser_error_result(call_id: str | None, exc: Exception) -> ToolResult:
    code = ToolErrorCode.ENVIRONMENT if isinstance(exc, BrowserError) else ToolErrorCode.EXECUTION
    return ToolResult(call_id=call_id, ok=False, error_code=code, error=str(exc))
