from __future__ import annotations

import base64
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from ethos.observability.logger import get_logger
from ethos.schemas.tools import ToolErrorCode, ToolResult

logger = get_logger("ethos.toolhost.browser")

SELECTOR_RE = re.compile(r"""^[A-Za-z#.\[\]="'\-\w :>+~*,()]+$""")

# Ceiling on live browsers in one toolhost, and how long one may sit unused.
#
# Four is deliberately small. The agent browsing four things at once is already
# more than the tool surface implies, and every slot costs 150–400 MB of RSS
# that no limit anywhere else in this process would notice.
MAX_SESSIONS = 4

# Five minutes. Long enough that a page the agent is still working through — a
# slow form, a login it has to come back to — survives between tool calls, and
# short enough that an abandoned session is not still resident when the agent
# starts the next piece of work.
IDLE_TTL_S = 300.0


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
    """A capped pool of Chromium sessions with an idle reaper.

    Every session is a whole browser — a driver process, a renderer, a GPU
    process — and Chromium does not give any of it back when the agent stops
    looking at a page. It just sits there, holding 150–400 MB, for as long as
    the toolhost lives. Two things made that unbounded rather than merely
    wasteful, and both had to be closed separately.

    The first is that nothing decided a session was over. The pool had no cap
    and no notion of idleness, so every distinct `session_id` the agent ever
    passed to `browser.visit` was a new browser that stayed. The second is that
    even a capped pool leaks if the cap is only enforced on the way *in*: at the
    limit, one long-lived session and many transient ones is still a pile of
    browsers nobody is using.

    So there are two rules. `max_sessions` is a hard ceiling, and hitting it
    evicts the least-recently-used session rather than refusing the request —
    a pool that says "no" to the fifth browser teaches the agent that browsing
    is broken, and the fifth browser is cheaper than that lesson. And
    `idle_ttl_s` reaps anything unused for long enough, checked on every
    `get_or_create`, which is the only moment the pool is touched. A reaper
    driven by a background timer would close sessions out from under a tool
    call that had just fetched one; this cannot, because the fetch is what
    refreshes the clock.
    """

    def __init__(
        self,
        default_headless: bool = True,
        *,
        max_sessions: int = MAX_SESSIONS,
        idle_ttl_s: float = IDLE_TTL_S,
        clock: Callable[[], float] | None = None,
    ):
        self.sessions: dict[str, BrowserSession] = {}
        self.default_headless = default_headless
        self.max_sessions = max(1, int(max_sessions))
        self.idle_ttl_s = max(0.0, float(idle_ttl_s))
        self._clock = clock or time.monotonic
        # `session_id -> last time it was handed out`. Insertion-ordered, which
        # is also least-recently-used order because `get_or_create` refreshes
        # the entry with `move_to_end`.
        self._last_used: OrderedDict[str, float] = OrderedDict()

    def _touch(self, session_id: str) -> None:
        self._last_used[session_id] = self._clock()
        self._last_used.move_to_end(session_id)

    async def _discard(self, session_id: str) -> None:
        self._last_used.pop(session_id, None)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            try:
                await session.close()
            except Exception:
                # A browser that will not close is a browser that is going away
                # anyway — the toolhost is on its way out, or the driver is
                # already gone and this is the assertion noticing. Either way it
                # must not stop the pool from reaping the next one.
                logger.warning("browser.close_failed", session=session_id, exc_info=True)

    async def reap_idle(self, now: float | None = None) -> list[str]:
        """Close every session unused for longer than `idle_ttl_s`.

        Returns the ids it closed, so a caller — or a test — can see what was
        reaped rather than having to diff the pool afterwards.
        """
        if self.idle_ttl_s <= 0:
            return []
        at = self._clock() if now is None else now
        stale = [sid for sid in self.sessions if at - self._last_used.get(sid, at) >= self.idle_ttl_s]
        for session_id in stale:
            logger.info("browser.session_reaped", session=session_id,
                        idle_s=round(at - self._last_used.get(session_id, at), 1))
            await self._discard(session_id)
        return stale

    async def _evict_lru(self) -> str | None:
        """Close the least-recently-used session. None if there is nothing to."""
        if not self._last_used:
            return None
        victim = next(iter(self._last_used))
        await self._discard(victim)
        return victim

    async def get_or_create(self, session_id: str = "default", headless: bool | None = None) -> BrowserSession:
        if session_id in self.sessions:
            self._touch(session_id)
            return self.sessions[session_id]
        # Idleness first, then the ceiling: a pool holding four dead sessions
        # should not evict a live fourth one when three of the four have been
        # abandoned for twenty minutes.
        await self.reap_idle()
        while len(self.sessions) >= self.max_sessions:
            if await self._evict_lru() is None:
                break
        session = BrowserSession(session_id, headless=self.default_headless if headless is None else headless)
        await session.start()
        self.sessions[session_id] = session
        self._touch(session_id)
        logger.info("browser.session_opened", session=session_id, live=len(self.sessions),
                    max_sessions=self.max_sessions)
        return session

    async def close(self, session_id: str) -> None:
        await self._discard(session_id)

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self._discard(session_id)
        self._last_used.clear()


def browser_error_result(call_id: str | None, exc: Exception) -> ToolResult:
    code = ToolErrorCode.ENVIRONMENT if isinstance(exc, BrowserError) else ToolErrorCode.EXECUTION
    return ToolResult(call_id=call_id, ok=False, error_code=code, error=str(exc))
