from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ethos.schemas.tools import ToolErrorCode
from ethos.toolhost.browser import (
    BrowserPool,
    BrowserSession,
    NoPageError,
    SelectorTimeoutError,
    VisitTimeoutError,
)
from ethos.toolhost.server import ToolContext
from ethos.toolhost.tools.browser_tools import (
    BrowserClickTool,
    BrowserExtractTool,
    BrowserScreenshotTool,
    BrowserVisitTool,
)

AMAZON_SEARCH_URL = "https://www.amazon.co.jp/s?k=%E3%83%98%E3%83%AB%E3%83%A8%E3%83%BC"


"""
The helmet search of 2026-11-02, in miniature.

Three `browser.visit` calls to Amazon Japan returned HTTP 200 — the site opened
every time. The follow-up `browser.click` and `browser.extract` each named a
`session_id` one word away from the one the visit used ("helmet-search" →
"amazon-search", default → "amazon_helmet"), so both ran in a fresh browser on
`about:blank`, waited out Playwright's fifteen seconds, and came back with an
error that read like the site refused to load. The agent reported "every
attempt to open Amazon Japan failed". These tests pin the three layers of the
repair: an empty session is refused before the wait, with the recovery spelled
out; a real selector timeout names the page it is on and says the page is
open; and the tool verdicts carry the inventory of sessions that do hold the
page.
"""


class _FakePage:
    """Duck-typed Playwright page: records calls, scripted to time out or not."""

    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.calls: list[tuple[str, ...]] = []
        self.fail_action: str | None = None

    def _fail(self, action: str, timeout: Any) -> None:
        if self.fail_action == action:
            raise TimeoutError(f"Page.{action}: Timeout {timeout}ms exceeded.")

    async def goto(self, url: str, wait_until: str | None = None, timeout: Any = None):
        self.calls.append(("goto", url))
        self._fail("goto", timeout)
        self.url = url
        return SimpleNamespace(status=200)

    async def click(self, selector: str, timeout: Any = None) -> None:
        self.calls.append(("click", selector))
        self._fail("click", timeout)

    async def fill(self, selector: str, text: str, timeout: Any = None) -> None:
        self.calls.append(("fill", selector))
        self._fail("fill", timeout)

    async def press(self, selector: str, key: str) -> None:
        self.calls.append(("press", selector, key))

    async def inner_text(self, selector: str, timeout: Any = None) -> str:
        self.calls.append(("inner_text", selector))
        self._fail("inner_text", timeout)
        return "extracted text"

    async def inner_html(self, selector: str, timeout: Any = None) -> str:
        self.calls.append(("inner_html", selector))
        self._fail("inner_html", timeout)
        return "<html></html>"

    async def get_attribute(self, selector: str, attribute: str, timeout: Any = None) -> str:
        self.calls.append(("get_attribute", selector, attribute))
        self._fail("get_attribute", timeout)
        return "#000000"

    async def screenshot(self, full_page: bool = False) -> bytes:
        self.calls.append(("screenshot",))
        return b"png-bytes"


def _session(page: _FakePage, session_id: str = "helmet-search") -> BrowserSession:
    """A real BrowserSession over a fake page — no browser installed needed."""
    session = BrowserSession(session_id)
    session._page = page
    return session


def _loaded_pool() -> tuple[BrowserPool, BrowserSession, BrowserSession]:
    """The pool the logs show: one session holding the page, one empty."""
    pool = BrowserPool()
    loaded = _session(_FakePage(url=AMAZON_SEARCH_URL), "helmet-search")
    loaded.last_url = AMAZON_SEARCH_URL
    empty = _session(_FakePage(), "amazon_helmet")
    pool.sessions = {"helmet-search": loaded, "amazon_helmet": empty}
    pool._last_used.update({"helmet-search": 1.0, "amazon_helmet": 2.0})
    return pool, loaded, empty


# ---------------------------------------------------------------------------
# The session layer: the empty-session guard


async def test_click_in_a_session_that_never_visited_refuses_before_the_wait():
    page = _FakePage()
    session = _session(page)
    with pytest.raises(NoPageError) as exc:
        await session.click("a.a-link-normal.s-no-outline")
    assert "helmet-search" in str(exc.value)
    assert "browser.visit" in str(exc.value)
    assert page.calls == [], "no selector wait was spent on a page that cannot match"


async def test_extract_and_screenshot_hit_the_same_guard():
    page = _FakePage()
    session = _session(page)
    with pytest.raises(NoPageError):
        await session.extract("div.s-result-item")
    with pytest.raises(NoPageError):
        await session.screenshot()
    assert page.calls == []


async def test_type_hits_the_guard_too():
    page = _FakePage()
    session = _session(page)
    with pytest.raises(NoPageError):
        await session.type("#twotabsearchtextbox", "helmet", submit=True)
    assert page.calls == []


async def test_visit_then_click_acts_on_the_page_this_session_opened():
    page = _FakePage()
    session = _session(page)
    result = await session.visit(AMAZON_SEARCH_URL)
    assert result["status"] == 200
    assert result["session"] == "helmet-search"
    assert session.last_url == AMAZON_SEARCH_URL

    clicked = await session.click("a.a-link-normal.s-no-outline")
    assert clicked == {"clicked": "a.a-link-normal.s-no-outline", "url": AMAZON_SEARCH_URL}
    assert ("click", "a.a-link-normal.s-no-outline") in page.calls


# ---------------------------------------------------------------------------
# The session layer: timeouts that say what actually happened


async def test_a_selector_timeout_names_the_page_and_says_the_page_is_open():
    page = _FakePage(url=AMAZON_SEARCH_URL)
    page.fail_action = "click"
    session = _session(page)
    with pytest.raises(SelectorTimeoutError) as exc:
        await session.click("a.a-link-normal.s-no-outline")
    message = str(exc.value)
    assert AMAZON_SEARCH_URL in message, "the error must say where the page is"
    assert "IS open" in message, "the error must not read as a failed visit"
    assert "a.a-link-normal.s-no-outline" in message
    assert "15s" in message


async def test_an_extract_timeout_is_read_as_a_selector_problem_not_a_dead_site():
    page = _FakePage(url=AMAZON_SEARCH_URL)
    page.fail_action = "inner_text"
    session = _session(page)
    with pytest.raises(SelectorTimeoutError) as exc:
        await session.extract("div.s-result-item")
    assert "page IS open" in str(exc.value)


async def test_an_extract_timeout_with_an_attribute_is_wrapped_the_same_way():
    page = _FakePage(url=AMAZON_SEARCH_URL)
    page.fail_action = "get_attribute"
    session = _session(page)
    with pytest.raises(SelectorTimeoutError):
        await session.extract("a.a-link-normal", attribute="href")


async def test_a_type_timeout_is_wrapped_with_the_page_url():
    page = _FakePage(url=AMAZON_SEARCH_URL)
    page.fail_action = "fill"
    session = _session(page)
    with pytest.raises(SelectorTimeoutError) as exc:
        await session.type("#twotabsearchtextbox", "helmet")
    assert AMAZON_SEARCH_URL in str(exc.value)


async def test_a_visit_timeout_is_the_one_error_that_means_the_site_did_not_open():
    page = _FakePage()
    page.fail_action = "goto"
    session = _session(page)
    with pytest.raises(VisitTimeoutError) as exc:
        await session.visit(AMAZON_SEARCH_URL)
    message = str(exc.value)
    assert "never finished loading" in message
    assert AMAZON_SEARCH_URL in message
    assert session.last_url is None, "a page that never loaded is not a page to reuse"


async def test_a_non_timeout_failure_passes_through_unwrapped():
    page = _FakePage(url=AMAZON_SEARCH_URL)

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("browser closed")

    page.click = broken  # type: ignore[method-assign]
    session = _session(page)
    with pytest.raises(RuntimeError, match="browser closed"):
        await session.click("a.result")


# ---------------------------------------------------------------------------
# The pool layer: where the pages actually are


def test_open_pages_lists_only_sessions_holding_a_loaded_page():
    pool, loaded, empty = _loaded_pool()
    assert pool.open_pages() == {"helmet-search": AMAZON_SEARCH_URL}


# ---------------------------------------------------------------------------
# The tool layer: verdicts the model can act on


def _host(pool: BrowserPool) -> SimpleNamespace:
    return SimpleNamespace(browser_pool=pool)


async def test_click_in_the_wrong_session_says_where_the_page_actually_is():
    pool, _, _ = _loaded_pool()
    tool = BrowserClickTool()
    result = await tool.run(
        {"selector": "a.a-link-normal.s-no-outline", "session_id": "amazon_helmet"},
        ToolContext(), _host(pool),
    )
    assert isinstance(result.ok, bool) and result.ok is False
    assert result.error_code == ToolErrorCode.ENVIRONMENT
    assert "no page open" in result.error
    assert "not the site failing" in result.error
    assert f"helmet-search -> {AMAZON_SEARCH_URL}" in result.error, (
        "the inventory is the recovery: the next call lands in the right browser"
    )


async def test_extract_in_an_empty_session_carries_the_same_inventory():
    pool, _, _ = _loaded_pool()
    tool = BrowserExtractTool()
    result = await tool.run(
        {"selector": "div.s-result-item", "attribute": "text", "session_id": "amazon_helmet"},
        ToolContext(), _host(pool),
    )
    assert result.ok is False
    assert "helmet-search -> " in result.error


async def test_a_selector_timeout_verdict_carries_the_timeout_code():
    pool, loaded, _ = _loaded_pool()
    loaded._page.fail_action = "click"
    tool = BrowserClickTool()
    result = await tool.run(
        {"selector": "a.a-link-normal", "session_id": "helmet-search"},
        ToolContext(), _host(pool),
    )
    assert result.ok is False
    assert result.error_code == ToolErrorCode.TIMEOUT
    assert "page IS open" in result.error


async def test_a_visit_timeout_verdict_names_the_site_that_did_not_open():
    pool = BrowserPool()
    page = _FakePage()
    page.fail_action = "goto"
    session = _session(page, "default")
    pool.sessions = {"default": session}
    pool._last_used.update({"default": 1.0})
    tool = BrowserVisitTool()
    result = await tool.run({"url": AMAZON_SEARCH_URL}, ToolContext(), _host(pool))
    assert result.ok is False
    assert result.error_code == ToolErrorCode.ENVIRONMENT
    assert "did not open" in result.error


async def test_an_empty_pool_says_no_session_has_a_page():
    pool = BrowserPool()
    empty = _session(_FakePage(), "amazon_helmet")
    pool.sessions = {"amazon_helmet": empty}
    pool._last_used.update({"amazon_helmet": 1.0})
    tool = BrowserScreenshotTool()
    result = await tool.run({"session_id": "amazon_helmet"}, ToolContext(), _host(pool))
    assert result.ok is False
    assert "No session in the pool has a page open." in result.error


# ---------------------------------------------------------------------------
# The catalog: the contract stated where the model reads it


def test_the_catalog_states_the_session_rule_where_a_sequence_starts():
    """One line of prefix, because the catalog budget is nearly spent.

    The full lesson lives in the errors — the inventory of open pages is free
    at the moment of failure and expensive in the cached prefix. The visit line
    carries the one sentence that prevents the first mistake.
    """
    assert "same session_id" in BrowserVisitTool.description
