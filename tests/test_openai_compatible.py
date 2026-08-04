"""
Tests for the OpenAICompatibleProvider and NVIDIAProvider specific behaviors.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.llm_router import (
    OpenAICompatibleProvider,
    NVIDIAProvider,
    LLM_REQUEST_TIMEOUT,
)


def _run(coro):
    return asyncio.run(coro)


def _make_async_ctx(obj):
    """Make an object usable as an async context manager (for mocking aiohttp responses)."""
    obj.__aenter__ = AsyncMock(return_value=obj)
    obj.__aexit__ = AsyncMock(return_value=None)
    return obj


class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *exc):
        return False


class TestOpenAICompatibleProviderAuth:
    """Tests for OpenAICompatibleProvider authentication variants"""

    def test_default_bearer_auth(self):
        provider = OpenAICompatibleProvider("mykey", "https://api.example.com/v1", "test")
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer mykey"

    def test_no_auth_when_empty_key(self):
        provider = OpenAICompatibleProvider("", "https://api.example.com/v1", "test")
        headers = provider._headers()
        assert "Authorization" not in headers

    def test_custom_auth_header(self):
        provider = OpenAICompatibleProvider(
            "secret", "https://api.example.com/v1", "test",
            auth_header="X-API-Key", auth_prefix=""
        )
        headers = provider._headers()
        assert headers["X-API-Key"] == "secret"
        assert "Authorization" not in headers

    def test_custom_auth_prefix(self):
        provider = OpenAICompatibleProvider(
            "token", "https://api.example.com/v1", "test",
            auth_header="Authorization", auth_prefix="Token"
        )
        headers = provider._headers()
        assert headers["Authorization"] == "Token token"

    def test_extra_headers(self):
        provider = OpenAICompatibleProvider(
            "key", "https://api.example.com/v1", "test",
            extra_headers={"X-Custom": "value", "X-Other": "test"}
        )
        headers = provider._headers()
        assert headers["X-Custom"] == "value"
        assert headers["X-Other"] == "test"
        assert headers["Authorization"] == "Bearer key"


class TestOpenAICompatibleProviderChat:
    """Tests for OpenAICompatibleProvider chat_completion"""

    def test_chat_completion_success(self):
        captured = {}

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Response from compatible provider"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(url=url, headers=headers, json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider(
                "test-key", "https://api.example.com/v1", "test",
                default_model="test-model"
            )
            result = _run(provider.chat_completion(
                [{"role": "user", "content": "Hello"}]
            ))

        assert result == "Response from compatible provider"
        assert "api.example.com" in captured["url"]
        assert captured["headers"]["Authorization"] == "Bearer test-key"
        assert captured["json"]["model"] == "test-model"

    def test_chat_completion_no_model_raises(self):
        provider = OpenAICompatibleProvider("", "https://api.example.com/v1", "test")

        try:
            _run(provider.chat_completion([{"role": "user", "content": "hi"}], model=None))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "No model specified" in str(e)

    def test_chat_completion_uses_request_timeout(self):
        captured = {}

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "ok"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test")
            _run(provider.chat_completion(
                [{"role": "user", "content": "hi"}],
                model="test-model",
                request_timeout=LLM_REQUEST_TIMEOUT,
            ))

        assert captured["json"]["request_timeout"] == LLM_REQUEST_TIMEOUT


class TestOpenAICompatibleProviderStream:
    """Tests for OpenAICompatibleProvider stream_chat"""

    def test_stream_chat_yields_chunks(self):
        class _AsyncIter:
            def __init__(self, items):
                self.items = list(items)
            def __aiter__(self):
                return self
            async def __anext__(self):
                if not self.items:
                    raise StopAsyncIteration
                return self.items.pop(0)

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = _AsyncIter([
            b'data: {"choices":[{"delta":{"content":"chunk1"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"chunk2"}}]}\n',
            b'data: [DONE]\n',
        ])

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test")
            chunks = []

            async def _collect():
                async for c in provider.stream_chat(
                    [{"role": "user", "content": "hi"}], model="test"
                ):
                    chunks.append(c)

            _run(_collect())

        assert chunks == ["chunk1", "chunk2"]


class TestOpenAICompatibleProviderListModels:
    """Tests for OpenAICompatibleProvider list_models"""

    def test_list_models_openai_format(self):
        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "data": [{"id": "model-a"}, {"id": "model-b"}]
        })

        mock_session = MagicMock()
        mock_session.get = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test")
            models = _run(provider.list_models())

        assert models == ["model-a", "model-b"]

    def test_list_models_string_array_format(self):
        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=["model-x", "model-y"])

        mock_session = MagicMock()
        mock_session.get = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test")
            models = _run(provider.list_models())

        assert models == ["model-x", "model-y"]

    def test_list_models_empty_response(self):
        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={})

        mock_session = MagicMock()
        mock_session.get = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test")
            models = _run(provider.list_models())

        assert models == []

    def test_list_models_error_returns_empty(self):
        mock_session = MagicMock()
        mock_session.get = mock.MagicMock(side_effect=Exception("Network error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test")
            models = _run(provider.list_models())

        assert models == []

    def test_list_models_disabled(self):
        provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "test", models_path="")
        models = _run(provider.list_models())
        assert models == []


class TestNVIDIAProviderAdvanced:
    """Advanced tests for NVIDIAProvider"""

    def test_name_property(self):
        provider = NVIDIAProvider("key")
        assert provider.name == "nim"

    def test_base_url(self):
        provider = NVIDIAProvider("key")
        assert provider.base_url == "https://integrate.api.nvidia.com/v1"

    def test_default_model(self):
        provider = NVIDIAProvider("key")
        # Default model used by chat_completion when model is None
        from unittest.mock import AsyncMock as AsyncMock_

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock_(return_value={
            "choices": [{"message": {"content": "ok"}}]
        })

        mock_session = MagicMock()
        captured = {}
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            _run(provider.chat_completion([{"role": "user", "content": "hi"}]))

        assert captured["json"]["model"] == "nvidia/nemotron-3-ultra-550b-a55b"

    def test_chat_with_reasoning_budget_in_payload(self):
        captured = {}

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Reasoning response"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            result = _run(provider.chat_completion(
                [{"role": "user", "content": "think"}],
                enable_thinking=True,
                reasoning_budget=1000
            ))

        assert result == "Reasoning response"
        payload = captured["json"]
        assert "extra_body" in payload
        assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
        assert payload["extra_body"]["reasoning_budget"] == 1000

    def test_stream_yields_reasoning_then_content(self):
        class _AsyncIter:
            def __init__(self, items):
                self.items = list(items)
            def __aiter__(self):
                return self
            async def __anext__(self):
                if not self.items:
                    raise StopAsyncIteration
                return self.items.pop(0)

        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = _AsyncIter([
            b'data: {"choices":[{"delta":{"reasoning_content":"reasoning1"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"content1"}}]}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"reasoning2"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"content2"}}]}\n',
            b'data: [DONE]\n',
        ])

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            chunks = []

            async def _collect():
                async for c in provider.stream_chat(
                    [{"role": "user", "content": "hi"}], model="test"
                ):
                    chunks.append(c)

            _run(_collect())

        assert "reasoning1" in chunks
        assert "content1" in chunks
        assert "reasoning2" in chunks
        assert "content2" in chunks

    def test_list_models(self):
        mock_resp = _make_async_ctx(MagicMock())
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "data": [
                {"id": "nvidia/nemotron-3-ultra"},
                {"id": "nvidia/nemotron-4-340b"},
                {"id": "meta/llama-3.1-70b"},
            ]
        })

        mock_session = MagicMock()
        mock_session.get = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            models = _run(provider.list_models())

        assert "nvidia/nemotron-3-ultra" in models
        assert "nvidia/nemotron-4-340b" in models
        assert "meta/llama-3.1-70b" in models