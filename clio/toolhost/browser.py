from __future__ import annotations

import base64
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from clio.observability.logger import get_logger
from clio.schemas.tools import ToolErrorCode, ToolResult

logger = get_logger("clio.toolhost.browser")

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

# How long `visit` waits for a page to load, and how long a selector action
# waits for its element. Named once because the error messages quote the wait
# they actually used, and a message that guesses its own timeout is a message
# nobody can debug against.
VISIT_TIMEOUT_MS = 45_000
SELECTOR_TIMEOUT_MS = 15_000


class BrowserError(RuntimeError):
    pass


class NoPageError(BrowserError):
    """A selector action aimed at a session whose browser never visited anything.

    Every `session_id` is its own browser, and a fresh one sits on
    `about:blank`. A click or an extract there cannot match anything, ever —
    the selector is not slow to appear, it is impossible. Raised before the
    wait starts, so the answer says what is actually wrong instead of spending
    fifteen seconds and then sounding like the site refused to load.
    """


class SelectorTimeoutError(BrowserError):
    """A selector that genuinely did not appear on a page that did load.

    Distinct from `NoPageError` because the two call for opposite readings:
    here the site opened and the failure belongs to the selector or the wait.
    """


class VisitTimeoutError(BrowserError):
    """A page that never finished loading within `visit`'s own ceiling.

    The one timeout that really does mean "the site did not open" — named so
    the error can say that and nothing else can be mistaken for it.
    """


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
        # The URL of the last navigation this session completed. None until a
        # `visit` lands — the marker the selector actions read before waiting
        # on an element that cannot exist, and the field the pool's inventory
        # quotes when it tells the agent where the page it opened actually is.
        self.last_url: str | None = None

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

    def _page_for_selector(self) -> Any:
        """The page a click/type/extract/screenshot acts on, or a refusal that says why.

        A session nobody has visited sits on `about:blank`, and every selector
        waits fifteen seconds there before failing with an error that reads
        like the site refused to load. That is how a successful visit — HTTP
        200, in the session the visit named — ends up reported as "the site
        would not open": the page opened fine, in a different session than the
        one the action named. Refused before the wait, with the recovery in
        the message, because the distinction between "the site is down" and
        "you are in the wrong browser" is the whole diagnosis.
        """
        page = self._require_page()
        if page.url == "about:blank":
            raise NoPageError(
                f"browser session '{self.id}' has no page open — no browser.visit has "
                "succeeded in it, so no selector can ever match (this is not the site "
                "failing). browser.visit the URL in this same session_id first, or "
                "pass the session_id you already opened the page in."
            )
        return page

    async def _selector_call(
        self, action: str, selector: str, run: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Run one selector action, translating Playwright's bare timeout.

        A raw `TimeoutError: waiting for locator(...)` says neither where the
        page was nor whether it ever loaded, and an error that names neither
        gets read as "the site is down" — the reading that sent the agent away
        from pages it had open. The rewrite quotes the selector, the wait and
        the URL the page is on, and says plainly that the page is open, so the
        failure lands where it belongs: the selector, or content that renders
        late. Returns whatever the action returned, so the call happens once.
        """
        page = self._require_page()
        try:
            return await run()
        except Exception as exc:
            # By class name rather than by import: Playwright renames nothing,
            # and this module must keep importing (and being testable) without
            # a browser installed.
            if type(exc).__name__ != "TimeoutError":
                raise
            raise SelectorTimeoutError(
                f"browser.{action} waited {SELECTOR_TIMEOUT_MS // 1000}s for "
                f"'{selector}' and nothing matched — the page IS open at "
                f"{page.url}, so this is a selector or rendering problem, not a "
                "failed visit. Check the selector, or browser.screenshot to see "
                "what the page actually shows."
            ) from exc

    async def visit(self, url: str) -> dict[str, Any]:
        if not re.match(r"^https?://", url):
            raise BrowserError("only http(s) URLs are allowed")
        page = self._require_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=VISIT_TIMEOUT_MS)
        except Exception as exc:
            if type(exc).__name__ != "TimeoutError":
                raise
            raise VisitTimeoutError(
                f"browser.visit timed out after {VISIT_TIMEOUT_MS // 1000}s and "
                f"{url} never finished loading — the site did not open (network "
                "trouble, a slow page, or the site throttling automation)."
            ) from exc
        self.last_url = page.url
        return {"url": url, "status": response.status if response else None, "session": self.id}

    async def click(self, selector: str) -> dict[str, Any]:
        selector = self._validate_selector(selector)
        page = self._page_for_selector()
        await self._selector_call(
            "click", selector, lambda: page.click(selector, timeout=SELECTOR_TIMEOUT_MS)
        )
        return {"clicked": selector, "url": page.url}

    async def type(self, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        selector = self._validate_selector(selector)
        page = self._page_for_selector()

        async def _run() -> None:
            await page.fill(selector, text, timeout=SELECTOR_TIMEOUT_MS)
            if submit:
                await page.press(selector, "Enter")

        await self._selector_call("type", selector, _run)
        return {"typed": selector, "submitted": submit, "url": page.url}

    async def extract(self, selector: str = "body", attribute: str | None = None) -> dict[str, Any]:
        selector = self._validate_selector(selector)
        page = self._page_for_selector()
        if attribute == "html":
            html = await self._selector_call(
                "extract", selector, lambda: page.inner_html(selector, timeout=SELECTOR_TIMEOUT_MS)
            )
            return {"text": html[:200_000], "url": page.url}
        if attribute and attribute not in ("href", "src", "text"):
            raise BrowserError("attribute must be text, href, src or html")
        if attribute:
            value = await self._selector_call(
                "extract", selector,
                lambda: page.get_attribute(selector, attribute, timeout=SELECTOR_TIMEOUT_MS),
            )
            return {"text": str(value or "")[:200_000], "url": page.url}
        text = await self._selector_call(
            "extract", selector, lambda: page.inner_text(selector, timeout=SELECTOR_TIMEOUT_MS)
        )
        return {"text": text[:200_000], "url": page.url}

    async def screenshot(self, full_page: bool = False) -> dict[str, Any]:
        page = self._page_for_selector()
        shot = await page.screenshot(full_page=full_page)
        return {"image_b64": base64.b64encode(shot).decode("ascii"), "bytes": len(shot),
                "url": page.url}

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

    def open_pages(self) -> dict[str, str]:
        """`{session_id: url}` for every session holding a loaded page.

        The recovery half of `NoPageError`: an action that landed in a session
        nobody visited is one `session_id` away from the page the agent already
        opened, and this is the map of where those pages are. Sessions whose
        page never loaded are left out — they have nothing to reuse.
        """
        return {
            session_id: session.last_url
            for session_id, session in self.sessions.items()
            if session.last_url
        }

    async def close(self, session_id: str) -> None:
        await self._discard(session_id)

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self._discard(session_id)
        self._last_used.clear()


def browser_error_result(call_id: str | None, exc: Exception) -> ToolResult:
    code = ToolErrorCode.ENVIRONMENT if isinstance(exc, BrowserError) else ToolErrorCode.EXECUTION
    return ToolResult(call_id=call_id, ok=False, error_code=code, error=str(exc))
