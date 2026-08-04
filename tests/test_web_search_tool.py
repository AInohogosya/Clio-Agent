"""
Tests for the WebSearchTool class.
Covers search (with and without API key) and fetch_url methods.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.tools.tool_registry import WebSearchTool, ToolResult


def _run(coro):
    return asyncio.run(coro)


def _make_async_ctx(obj):
    obj.__aenter__ = AsyncMock(return_value=obj)
    obj.__aexit__ = AsyncMock(return_value=None)
    return obj


class TestWebSearchTool:
    """Tests for WebSearchTool"""

    def test_search_without_api_key(self):
        """Test search without API key returns configuration message"""
        tool = WebSearchTool(search_api_key=None)
        result = _run(tool.search("test query", num_results=5))

        assert result.success is True
        assert "no api key provided" in result.output.lower()
        assert "SEARCH_API_KEY" in result.output
        assert "test query" in result.output

    def test_search_with_api_key_mocked_success(self):
        """Test search with mocked API response"""
        tool = WebSearchTool(search_api_key="test_key")

        # Mock the aiohttp session
        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "organic": [
                {"title": "Result 1", "link": "http://example.com/1", "snippet": "Snippet 1"},
                {"title": "Result 2", "link": "http://example.com/2", "snippet": "Snippet 2"},
            ]
        })

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.search("test query", num_results=2))

        assert result.success is True
        assert "Result 1" in result.output
        assert "Result 2" in result.output
        assert "http://example.com/1" in result.output
        assert "http://example.com/2" in result.output

    def test_search_with_api_key_mocked_error(self):
        """Test search with API error response"""
        tool = WebSearchTool(search_api_key="test_key")

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.status = 401
        mock_resp.json = AsyncMock(return_value={})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.search("test query"))

        assert result.success is False
        assert "status 401" in result.error.lower()

    def test_search_network_error(self):
        """Test search with network error"""
        tool = WebSearchTool(search_api_key="test_key")

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=Exception("Network error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.search("test query"))

        assert result.success is False
        assert "search error" in result.error.lower()


class TestWebSearchToolFetchUrl:
    """Tests for WebSearchTool.fetch_url"""

    def test_fetch_url_success(self):
        """Test successful URL fetch"""
        tool = WebSearchTool(search_api_key=None)

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_content = MagicMock()
        mock_content.read = AsyncMock(return_value=b"Page content here")
        mock_resp.content = mock_content

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.fetch_url("https://example.com"))

        assert result.success is True
        assert "Content from https://example.com" in result.output
        assert "Page content here" in result.output

    def test_fetch_url_truncates_large_content(self):
        """Test fetch_url truncates content over 5000 chars"""
        tool = WebSearchTool(search_api_key=None)

        large_content = "x" * 10000
        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_content = MagicMock()
        mock_content.read = AsyncMock(return_value=large_content.encode())
        mock_resp.content = mock_content

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.fetch_url("https://example.com"))

        assert result.success is True
        assert "(truncated at 5000 chars)" in result.output
        assert len(result.output) < 10000

    def test_fetch_url_http_error(self):
        """Test fetch_url with HTTP error"""
        tool = WebSearchTool(search_api_key=None)

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.fetch_url("https://example.com/notfound"))

        assert result.success is False
        assert "Error fetching URL" in result.error

    def test_fetch_url_network_error(self):
        """Test fetch_url with network error"""
        tool = WebSearchTool(search_api_key=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=Exception("Connection refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            result = _run(tool.fetch_url("https://example.com"))

        assert result.success is False
        assert "Error fetching URL" in result.error

    def test_fetch_url_with_custom_headers(self):
        """Test fetch_url sends User-Agent header"""
        tool = WebSearchTool(search_api_key=None)

        captured = {}

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_content = MagicMock()
        mock_content.read = AsyncMock(return_value=b"OK")
        mock_resp.content = mock_content

        mock_session = MagicMock()

        def mock_get(url, headers=None):
            captured["headers"] = headers
            return mock_resp

        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.tools.tool_registry.aiohttp.ClientSession", return_value=mock_session):
            _run(tool.fetch_url("https://example.com"))

        assert "User-Agent" in captured.get("headers", {})
        assert "Clio-Agent-2" in captured["headers"]["User-Agent"]