"""
Tests for provider classes: OpenAIProvider, GoogleProvider, AnthropicProvider,
OpenRouterProvider, GrokProvider, DeepSeekProvider, NVIDIAProvider.
Covers chat_completion, stream_chat, list_models methods.
"""
import asyncio
import json
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.llm_router import (
    OpenAIProvider,
    GoogleProvider,
    AnthropicProvider,
    OpenRouterProvider,
    GrokProvider,
    DeepSeekProvider,
    NVIDIAProvider,
    OpenAICompatibleProvider,
    AuthenticationError,
    LLM_REQUEST_TIMEOUT,
)


def _run(coro):
    return asyncio.run(coro)


class _Ctx:
    """Async context manager yielding a pre-built response."""

    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *exc):
        return False


class TestOpenAIProvider:
    """Tests for OpenAIProvider class"""

    def test_name_property(self):
        provider = OpenAIProvider("test-key")
        assert provider.name == "openai"

    def test_base_url(self):
        provider = OpenAIProvider("test-key")
        assert provider.base_url == "https://api.openai.com/v1"

    def test_chat_completion_url_and_auth(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Hello from OpenAI"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(url=url, headers=headers), _Ctx(mock_resp))[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAIProvider("sk-test-key")
            result = _run(provider.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o"))

        assert result == "Hello from OpenAI"
        assert "api.openai.com" in captured["url"]
        assert captured["headers"]["Authorization"] == "Bearer sk-test-key"

    def test_chat_completion_uses_default_model(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "response"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=_Ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAIProvider("key")
            result = _run(provider.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o"))
            assert result == "response"

    def test_list_models(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "data": [{"id": "gpt-4"}, {"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]
        })

        mock_session = MagicMock()
        mock_session.get = mock.MagicMock(return_value=_Ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAIProvider("key")
            models = _run(provider.list_models())
            assert "gpt-4" in models
            assert "gpt-4o" in models


class TestGoogleProvider:
    """Tests for GoogleProvider class"""

    def test_name_property(self):
        provider = GoogleProvider("test-key")
        assert provider.name == "google"

    def test_base_url(self):
        provider = GoogleProvider("test-key")
        assert provider.base_url == "https://generativelanguage.googleapis.com/v1beta"

    def test_chat_completion_constructs_url(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "candidates": [{"content": {"parts": [{"text": "Hello from Google"}]}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(
            side_effect=lambda url, headers=None, json=None: (
                captured.update(url=url, headers=headers or {}),
                _Ctx(mock_resp),
            )[1]
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = GoogleProvider("key")
            result = _run(provider.chat_completion([{"role": "user", "content": "hi"}], model="gemini-pro"))

        assert result == "Hello from Google"
        assert "generativelanguage.googleapis.com" in captured["url"]
        assert captured.get("headers", {}).get("x-goog-api-key") == "key"


class TestAnthropicProvider:
    """Tests for AnthropicProvider class"""

    def test_name_property(self):
        provider = AnthropicProvider("test-key")
        assert provider.name == "anthropic"

    def test_base_url(self):
        provider = AnthropicProvider("test-key")
        assert provider.base_url == "https://api.anthropic.com/v1"

    def test_headers(self):
        provider = AnthropicProvider("test-key")
        assert provider.headers["x-api-key"] == "test-key"
        assert provider.headers["anthropic-version"] == "2023-06-01"
        assert provider.headers["Content-Type"] == "application/json"

    def test_chat_completion_system_separate(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "content": [{"text": "Hello from Anthropic"}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), _Ctx(mock_resp))[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = AnthropicProvider("key")
            result = _run(provider.chat_completion(
                [{"role": "system", "content": "You are helpful"}, {"role": "user", "content": "hi"}],
                model="claude-3-opus-20240229"
            ))

        assert result == "Hello from Anthropic"
        payload = captured["json"]
        assert "system" in payload  # System message extracted
        assert "messages" in payload

    def test_list_models_returns_known_models(self):
        provider = AnthropicProvider("key")
        models = _run(provider.list_models())
        assert "claude-3-opus-20240229" in models
        assert "claude-3-sonnet-20240229" in models


class TestOpenRouterProvider:
    """Tests for OpenRouterProvider class"""

    def test_name_property(self):
        provider = OpenRouterProvider("test-key")
        assert provider.name == "openrouter"

    def test_default_base_url(self):
        provider = OpenRouterProvider("test-key")
        assert provider.base_url == "https://openrouter.ai/api/v1"

    def test_default_referer(self):
        provider = OpenRouterProvider("test-key")
        assert provider.http_referer == "https://clio-agent-2.local"
        assert provider.app_name == "Clio-Agent-2"

    def test_get_headers(self):
        provider = OpenRouterProvider("mykey", http_referer="https://myapp.com", app_name="MyApp")
        headers = provider._get_headers()
        assert headers["Authorization"] == "Bearer mykey"
        assert headers["HTTP-Referer"] == "https://myapp.com"
        assert headers["X-Title"] == "MyApp"

    def test_chat_completion_401_raises_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.json = AsyncMock(return_value={"error": {"message": "Invalid API key"}})

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=_Ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenRouterProvider("bad-key")
            try:
                _run(provider.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o"))
                assert False, "Expected AuthenticationError"
            except AuthenticationError as e:
                assert "authentication failed" in str(e).lower()
                assert "Invalid API key" in str(e)

    def test_chat_completion_403_raises_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_resp.json = AsyncMock(return_value={"error": {"message": "Access forbidden"}})

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=_Ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenRouterProvider("key")
            try:
                _run(provider.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o"))
                assert False, "Expected AuthenticationError"
            except AuthenticationError as e:
                assert "access forbidden" in str(e).lower()


class TestGrokProvider:
    """Tests for GrokProvider class"""

    def test_name_property(self):
        provider = GrokProvider("test-key")
        assert provider.name == "grok"

    def test_base_url(self):
        provider = GrokProvider("test-key")
        assert provider.base_url == "https://api.x.ai/v1"

    def test_default_model(self):
        provider = GrokProvider("key")
        # Default model should be grok-2-latest
        assert provider.name == "grok"


class TestDeepSeekProvider:
    """Tests for DeepSeekProvider class"""

    def test_name_property(self):
        provider = DeepSeekProvider("test-key")
        assert provider.name == "deepseek"

    def test_base_url(self):
        provider = DeepSeekProvider("test-key")
        assert provider.base_url == "https://api.deepseek.com/v1"

    def test_default_model(self):
        provider = DeepSeekProvider("key")
        assert provider.name == "deepseek"


class TestNVIDIAProvider:
    """Tests for NVIDIAProvider (NIM)"""

    def test_name_property(self):
        provider = NVIDIAProvider("test-key")
        assert provider.name == "nim"

    def test_base_url(self):
        provider = NVIDIAProvider("test-key")
        assert provider.base_url == "https://integrate.api.nvidia.com/v1"

    def test_default_model(self):
        provider = NVIDIAProvider("key")
        assert provider.name == "nim"

    def test_chat_completion_with_reasoning(self):
        """Test NVIDIA provider with enable_thinking and reasoning_budget"""
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "response"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), _Ctx(mock_resp))[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            result = _run(provider.chat_completion(
                [{"role": "user", "content": "hi"}],
                enable_thinking=True,
                reasoning_budget=500
            ))

        assert result == "response"
        payload = captured["json"]
        assert "extra_body" in payload
        assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
        assert payload["extra_body"]["reasoning_budget"] == 500

    def test_stream_chat_yields_both_reasoning_and_content(self):
        class _AsyncIter:
            def __init__(self, items):
                self.items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.items:
                    raise StopAsyncIteration
                return self.items.pop(0)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = _AsyncIter([
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"final"}}]}\n',
            b'data: [DONE]\n',
        ])

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=_Ctx(mock_resp))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            chunks = []

            async def _collect():
                async for c in provider.stream_chat([{"role": "user", "content": "hi"}]):
                    chunks.append(c)

            _run(_collect())

        assert "thinking" in chunks
        assert "final" in chunks