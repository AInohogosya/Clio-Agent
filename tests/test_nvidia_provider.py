"""
Tests for NVIDIAProvider edge cases and NVIDIA-specific functionality.
"""
import asyncio
import json
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.llm_router import NVIDIAProvider, LLM_REQUEST_TIMEOUT


def _run(coro):
    return asyncio.run(coro)


class _AsyncIter:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class TestNVIDIAProviderStreaming:
    """Tests for NVIDIAProvider stream_chat"""

    def test_stream_yields_reasoning_and_content(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = _AsyncIter([
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking step 1"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"final answer part 1"}}]}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking step 2"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"final answer part 2"}}]}\n',
            b'data: [DONE]\n',
        ])

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("test-key")
            chunks = []

            async def _collect():
                async for c in provider.stream_chat([{"role": "user", "content": "hi"}], model="test-model"):
                    chunks.append(c)

            _run(_collect())

        assert "thinking step 1" in chunks
        assert "final answer part 1" in chunks
        assert "thinking step 2" in chunks
        assert "final answer part 2" in chunks

    def test_stream_handles_malformed_json(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = _AsyncIter([
            b'data: not valid json\n',
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
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
                async for c in provider.stream_chat([{"role": "user", "content": "hi"}], model="test"):
                    chunks.append(c)

            _run(_collect())

        assert "ok" in chunks

    def test_stream_empty_delta(self):
        """Stream should handle empty deltas gracefully"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = _AsyncIter([
            b'data: {"choices":[{"delta":{}}]}\n',
            b'data: {"choices":[{"delta":{"content":"real content"}}]}\n',
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
                async for c in provider.stream_chat([{"role": "user", "content": "hi"}], model="test"):
                    chunks.append(c)

            _run(_collect())

        assert "real content" in chunks
        assert len([c for c in chunks if c == ""]) == 0


class TestNVIDIAProviderChatCompletion:
    """Tests for NVIDIAProvider chat_completion"""

    def test_chat_with_reasoning_budget(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Response with reasoning"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            result = _run(provider.chat_completion(
                [{"role": "user", "content": "think deeply"}],
                model="nvidia/nemotron-3-ultra",
                enable_thinking=True,
                reasoning_budget=2000
            ))

        assert result == "Response with reasoning"
        payload = captured["json"]
        assert "extra_body" in payload
        assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
        assert payload["extra_body"]["reasoning_budget"] == 2000

    def test_chat_without_reasoning(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Simple response"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            result = _run(provider.chat_completion(
                [{"role": "user", "content": "simple"}],
                model="nvidia/nemotron-3-ultra"
            ))

        assert result == "Simple response"
        payload = captured["json"]
        assert "extra_body" not in payload or payload.get("extra_body") == {}

    def test_default_model_used(self):
        captured = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": "Default model response"}}]
        })

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(side_effect=lambda url, headers=None, json=None: (captured.update(json=json), mock_resp)[1])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            result = _run(provider.chat_completion(
                [{"role": "user", "content": "test"}]
            ))

        assert result == "Default model response"
        payload = captured["json"]
        assert payload["model"] == "nvidia/nemotron-3-ultra-550b-a55b"


class TestNVIDIAProviderListModels:
    """Tests for NVIDIAProvider list_models"""

    def test_list_models(self):
        mock_resp = MagicMock()
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

        assert len(models) == 3
        assert "nvidia/nemotron-3-ultra" in models
        assert "meta/llama-3.1-70b" in models


class TestNVIDIAProviderErrorHandling:
    """Tests for NVIDIAProvider error handling"""

    def test_chat_401_raises_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.json = AsyncMock(return_value={"error": {"message": "Invalid API key"}})

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("bad-key")
            try:
                _run(provider.chat_completion([{"role": "user", "content": "hi"}], model="test"))
                assert False, "Expected exception"
            except Exception as e:
                assert "authentication" in str(e).lower() or "401" in str(e)

    def test_stream_error_handling(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("Network error"))

        mock_session = MagicMock()
        mock_session.post = mock.MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with mock.patch("clio_agent_2.core.llm_router.aiohttp.ClientSession", return_value=mock_session):
            provider = NVIDIAProvider("key")
            chunks = []

            async def _collect():
                async for c in provider.stream_chat([{"role": "user", "content": "hi"}], model="test"):
                    chunks.append(c)

            try:
                _run(_collect())
            except Exception:
                pass  # Expected to fail