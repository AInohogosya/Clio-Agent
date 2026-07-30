"""Tests for the expanded LLM provider support.

Covers the generic OpenAI-compatible provider, the built-in provider
catalogue, and user-supplied "Other" (custom) providers end-to-end
(config persistence + router registration).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from clio_agent_2.core.llm_router import (
    BUILTIN_PROVIDER_INFO,
    LLMRouter,
    OpenAICompatibleProvider,
    SUPPORTED_PROVIDERS,
)
from clio_agent_2.config.settings import Config


class _Ctx:
    """Async context manager yielding a pre-built response."""

    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *exc):
        return False


class _AsyncIter:
    """Tiny async iterator over a list of byte chunks (for streaming)."""

    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


def _patch_session(monkeypatch, session):
    # aiohttp.ClientSession(...) is awaited as `async with session: ...`,
    # so the returned object must itself be an async context manager.
    monkeypatch.setattr(
        "clio_agent_2.core.llm_router.aiohttp.ClientSession",
        lambda *a, **k: _Ctx(session),
    )


# ---------------------------------------------------------------------------
# Built-in catalogue
# ---------------------------------------------------------------------------

def test_builtin_provider_info_includes_new_providers():
    for pid in (
        "mistral", "groq", "perplexity", "together", "fireworks", "nim",
        "qwen", "huggingface", "deepinfra", "ollama",
    ):
        assert pid in BUILTIN_PROVIDER_INFO, f"missing built-in provider {pid}"
        info = BUILTIN_PROVIDER_INFO[pid]
        assert info["base_url"].startswith("http")
        assert info["default_model"]
    for pid in ("openai", "google", "anthropic", "openrouter", "grok", "deepseek"):
        assert pid in BUILTIN_PROVIDER_INFO


def test_supported_providers_derived_from_info():
    assert set(SUPPORTED_PROVIDERS) == set(BUILTIN_PROVIDER_INFO.keys())
    assert "openai" in SUPPORTED_PROVIDERS
    assert "ollama" in SUPPORTED_PROVIDERS


# ---------------------------------------------------------------------------
# Generic OpenAI-compatible provider
# ---------------------------------------------------------------------------

def test_chat_completion_builds_url_and_auth(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={"choices": [{"message": {"content": "hello"}}]})
    captured = {}

    session = MagicMock()

    def _post(url, headers=None, json=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Ctx(resp)

    session.post = _post
    _patch_session(monkeypatch, session)

    provider = OpenAICompatibleProvider("mykey", "https://api.example.com/v1", "myprovider")
    result = asyncio.run(provider.chat_completion(
        [{"role": "user", "content": "hi"}], model="m1"
    ))
    assert result == "hello"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer mykey"


def test_chat_completion_custom_auth_header_and_prefix(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={"choices": [{"message": {"content": "x"}}]})
    captured = {}

    session = MagicMock()

    def _post(url, headers=None, json=None):
        captured["headers"] = headers
        return _Ctx(resp)

    session.post = _post
    _patch_session(monkeypatch, session)

    provider = OpenAICompatibleProvider(
        "secret", "https://api.example.com/v1", "p",
        auth_header="api-key", auth_prefix="",
    )
    asyncio.run(provider.chat_completion([{"role": "user", "content": "hi"}], model="m"))
    assert captured["headers"]["api-key"] == "secret"
    assert "Authorization" not in captured["headers"]


def test_list_models_parses_openai_shape(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={"data": [{"id": "a"}, {"id": "b"}]})
    session = MagicMock()
    session.get = MagicMock(return_value=_Ctx(resp))
    _patch_session(monkeypatch, session)

    provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "p")
    assert asyncio.run(provider.list_models()) == ["a", "b"]


def test_list_models_tolerates_errors(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=aiohttp.ClientError("boom"))
    resp.json = AsyncMock(return_value={})
    session = MagicMock()
    session.get = MagicMock(return_value=_Ctx(resp))
    _patch_session(monkeypatch, session)

    provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "p")
    assert asyncio.run(provider.list_models()) == []


def test_stream_chat_yields_chunks(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.content = _AsyncIter([
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n',
        b'data: [DONE]\n',
    ])
    session = MagicMock()
    session.post = MagicMock(return_value=_Ctx(resp))
    _patch_session(monkeypatch, session)

    provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "p")
    chunks = []

    async def _collect():
        async for c in provider.stream_chat([{"role": "user", "content": "hi"}], model="m"):
            chunks.append(c)

    asyncio.run(_collect())
    assert chunks == ["he"]


# ---------------------------------------------------------------------------
# Custom "Other" providers
# ---------------------------------------------------------------------------

class _Cfg:
    """Minimal config stand-in that owns a list of custom providers."""

    def __init__(self, custom):
        self._custom = custom
        self.ollama_base_url = None
        for pid in BUILTIN_PROVIDER_INFO:
            setattr(self, f"{pid}_api_key", None)

    def load_custom_providers(self):
        return self._custom


def test_register_and_select_custom_provider():
    cfg = _Cfg([{
        "id": "localai",
        "base_url": "http://localhost:1234/v1",
        "api_key": "",
        "label": "LocalAI",
    }])
    router = LLMRouter(cfg)
    assert "localai" in router.get_available_providers()

    router.unlock_llm_settings()
    router.set_llm_provider("localai")
    assert router.default_provider == "localai"


def test_unknown_provider_still_rejected():
    import pytest
    cfg = _Cfg([])
    router = LLMRouter(cfg)
    router.unlock_llm_settings()
    with pytest.raises(ValueError):
        router.set_llm_provider("definitely-not-real")


def test_config_custom_provider_roundtrip(tmp_path):
    import pytest
    env = tmp_path / ".env"
    cfg = Config(env_path=str(env))

    cfg.add_custom_provider("localai", "http://localhost:1234/v1", label="LocalAI")
    providers = cfg.load_custom_providers()
    assert any(p["id"] == "localai" for p in providers)
    content = env.read_text()
    assert "CUSTOM_PROVIDERS=localai" in content
    assert "CUSTOM_LOCALAI_BASE_URL=http://localhost:1234/v1" in content

    assert cfg.validate_api_keys().get("localai") is True

    assert cfg.remove_custom_provider("localai") is True
    assert not any(p["id"] == "localai" for p in cfg.load_custom_providers())
    assert "localai" not in cfg.validate_api_keys()


def test_config_custom_provider_rejects_bad_id(tmp_path):
    import pytest
    cfg = Config(env_path=str(tmp_path / ".env"))
    with pytest.raises(ValueError):
        cfg.add_custom_provider("Bad ID!", "http://x/v1")
    with pytest.raises(ValueError):
        cfg.add_custom_provider("okid", "")
