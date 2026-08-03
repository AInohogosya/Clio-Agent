"""
Tests for the LLMRouter class - chat, stream_chat, list_all_models, search_models,
register_providers, get_provider, get_available_providers.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.llm_router import (
    LLMRouter,
    LLMSettingsLockedError,
    OpenAIProvider,
    OpenAICompatibleProvider,
    AuthenticationError,
    SUPPORTED_PROVIDERS,
    BUILTIN_PROVIDER_INFO,
    LLM_REQUEST_TIMEOUT,
)


def _run(coro):
    return asyncio.run(coro)


class _StubProvider:
    """A minimal provider for testing"""
    name = "stub"

    def __init__(self):
        self.chat_completion = AsyncMock(return_value="response")
        self.stream_chat = mock.AsyncMock()
        async def _stream():
            yield "chunk"
        self.stream_chat.side_effect = _stream
        self.list_models = AsyncMock(return_value=["model-a", "model-b"])


class _TestConfig:
    """Minimal config for LLMRouter tests"""
    def __init__(self, **kwargs):
        self.default_llm_provider = kwargs.get("default_llm_provider", "openai")
        self.current_model = kwargs.get("current_model", "gpt-4o")
        self.openai_api_key = kwargs.get("openai_api_key", "sk-test")
        self.google_api_key = kwargs.get("google_api_key", "test-google")
        self.anthropic_api_key = kwargs.get("anthropic_api_key", None)
        self.openrouter_api_key = kwargs.get("openrouter_api_key", None)
        self.openrouter_http_referer = kwargs.get("openrouter_http_referer", None)
        self.openrouter_app_name = kwargs.get("openrouter_app_name", None)
        self.grok_api_key = kwargs.get("grok_api_key", None)
        self.deepseek_api_key = kwargs.get("deepseek_api_key", None)
        self.mistral_api_key = kwargs.get("mistral_api_key", None)
        self.groq_api_key = kwargs.get("groq_api_key", None)
        self.perplexity_api_key = kwargs.get("perplexity_api_key", None)
        self.together_api_key = kwargs.get("together_api_key", None)
        self.fireworks_api_key = kwargs.get("fireworks_api_key", None)
        self.nvidia_api_key = kwargs.get("nvidia_api_key", None)
        self.nim_api_key = kwargs.get("nim_api_key", None)
        self.qwen_api_key = kwargs.get("qwen_api_key", None)
        self.huggingface_api_key = kwargs.get("huggingface_api_key", None)
        self.deepinfra_api_key = kwargs.get("deepinfra_api_key", None)
        self.ollama_api_key = kwargs.get("ollama_api_key", None)
        self.ollama_base_url = kwargs.get("ollama_base_url", None)
        self.llm_settings_locked = kwargs.get("llm_settings_locked", False)
        self.custom_providers = kwargs.get("custom_providers", [])

    def load_custom_providers(self):
        return self.custom_providers


class TestLLMRouterInit:
    """Tests for LLMRouter initialization"""

    def test_router_defaults_to_openai(self):
        router = LLMRouter(_TestConfig())
        assert router.default_provider == "openai"
        assert "openai" in router.providers

    def test_router_defaults_to_locked(self):
        """LLM settings are locked by default"""
        router = LLMRouter(_TestConfig(llm_settings_locked=None))
        assert router.llm_settings_locked is True

    def test_router_max_chat_attempts_default(self):
        router = LLMRouter(_TestConfig())
        assert router.max_chat_attempts == 5

    def test_default_model_empty(self):
        config = _TestConfig(current_model="")
        router = LLMRouter(config)
        assert router.current_model == ""


class TestLLMRouterChat:
    """Tests for LLMRouter.chat"""

    def test_chat_delegates_to_provider(self):
        router = LLMRouter(_TestConfig())
        router.providers["openai"] = _StubProvider()

        result = _run(router.chat(
            [{"role": "user", "content": "Hello"}],
        ))
        assert result == "response"

    def test_chat_no_model_error(self):
        router = LLMRouter(_TestConfig(current_model=""))
        router.providers["openai"] = _StubProvider()

        try:
            _run(router.chat([{"role": "user", "content": "Hi"}]))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "No LLM model configured" in str(e)

    def test_chat_no_provider_error(self):
        router = LLMRouter(_TestConfig(current_model="gpt-4o", default_llm_provider="unknown"))
        try:
            _run(router.chat([{"role": "user", "content": "Hi"}]))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "not configured" in str(e)

    def test_chat_passes_request_timeout(self):
        router = LLMRouter(_TestConfig())
        stub = _StubProvider()
        router.providers["openai"] = stub

        _run(router.chat([{"role": "user", "content": "Hi"}]))

        assert stub.chat_completion.called
        kwargs = stub.chat_completion.call_args.kwargs
        assert kwargs["request_timeout"] == LLM_REQUEST_TIMEOUT


class TestLLMRouterStreamChat:
    """Tests for LLMRouter.stream_chat"""

    def test_stream_chat_yields_chunks(self):
        router = LLMRouter(_TestConfig())

        stub = _StubProvider()
        async def _stream():
            yield "chunk1"
            yield "chunk2"
        stub.stream_chat = _stream

        router.providers["openai"] = stub

        chunks = []
        async def _collect():
            async for chunk in router.stream_chat([{"role": "user", "content": "Hi"}]):
                chunks.append(chunk)

        _run(_collect())
        assert chunks == ["chunk1", "chunk2"]

    def test_stream_chat_no_model_error(self):
        router = LLMRouter(_TestConfig(current_model=""))

        try:
            async def _collect():
                async for _ in router.stream_chat([{"role": "user", "content": "Hi"}]):
                    pass

            _run(_collect())
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "No LLM model configured" in str(e)


class TestLLMRouterListModels:
    """Tests for LLMRouter.list_all_models and search_models"""

    def test_list_all_models(self):
        router = LLMRouter(_TestConfig())
        stub = _StubProvider()
        router.providers["stub"] = stub

        result = _run(router.list_all_models())

        assert "stub" in result
        assert result["stub"] == ["model-a", "model-b"]

    def test_list_all_models_handles_errors(self):
        router = LLMRouter(_TestConfig())
        stub = _StubProvider()
        stub.list_models = AsyncMock(side_effect=Exception("API down"))
        router.providers["stub"] = stub

        result = _run(router.list_all_models())

        assert "stub" in result
        assert "Error" in result["stub"][0]

    def test_search_models_finds_matches(self):
        router = LLMRouter(_TestConfig())
        stub = _StubProvider()
        router.providers["stub"] = stub

        result = _run(router.search_models("model-a"))
        assert len(result) == 1
        assert result[0]["provider"] == "stub"
        assert result[0]["model"] == "model-a"

    def test_search_models_no_matches(self):
        router = LLMRouter(_TestConfig())
        stub = _StubProvider()
        router.providers["stub"] = stub

        result = _run(router.search_models("nonexistent"))
        assert result == []

    def test_search_models_case_insensitive(self):
        router = LLMRouter(_TestConfig())
        stub = _StubProvider()
        router.providers["stub"] = stub

        result = _run(router.search_models("MODEL-A"))
        assert len(result) == 1


class TestLLMRouterProviderManagement:
    """Tests for register_providers, get_provider, get_available_providers"""

    def test_register_providers_with_keys(self):
        config = _TestConfig()
        router = LLMRouter(config)
        assert "openai" in router.providers
        assert "google" in router.providers

    def test_register_providers_no_keys(self):
        config = _TestConfig(openai_api_key=None, google_api_key=None)
        router = LLMRouter(config)
        assert len(router.providers) == 0

    def test_register_providers_ollama_requires_base_url(self):
        config = _TestConfig(ollama_api_key=None, ollama_base_url=None)
        router = LLMRouter(config)
        assert "ollama" not in router.providers

        config2 = _TestConfig(ollama_api_key=None, ollama_base_url="http://localhost:11434")
        router2 = LLMRouter(config2)
        assert "ollama" in router2.providers

    def test_get_provider_case_insensitive(self):
        router = LLMRouter(_TestConfig())
        provider = router.get_provider("OPENAI")
        assert provider is not None
        assert provider.name == "openai"

    def test_get_provider_unknown_returns_none(self):
        router = LLMRouter(_TestConfig())
        assert router.get_provider("nonexistent") is None

    def test_get_available_providers(self):
        router = LLMRouter(_TestConfig())
        providers = router.get_available_providers()
        assert "openai" in providers
        assert "google" in providers

    def test_register_custom_provider(self):
        config = _TestConfig(custom_providers=[{
            "id": "localai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "key123",
        }])
        router = LLMRouter(config)
        assert "localai" in router.providers
        provider = router.providers["localai"]
        assert provider.name == "localai"
        assert provider.base_url == "http://localhost:1234/v1"

    def test_register_providers_re_registers(self):
        config = _TestConfig()
        router = LLMRouter(config)
        initial_count = len(router.providers)
        router.register_providers()
        assert len(router.providers) == initial_count


class TestLLMRouterSettingsGuardrail:
    """Tests for LLM settings lock/unlock"""

    def test_set_llm_provider_locked(self):
        router = LLMRouter(_TestConfig())
        router.lock_llm_settings()
        try:
            router.set_llm_provider("anthropic")
            assert False, "Expected LLMSettingsLockedError"
        except LLMSettingsLockedError:
            pass

    def test_set_llm_model_locked(self):
        router = LLMRouter(_TestConfig())
        router.lock_llm_settings()
        try:
            router.set_llm_model("gpt-4o-mini")
            assert False, "Expected LLMSettingsLockedError"
        except LLMSettingsLockedError:
            pass

    def test_set_llm_provider_unlocked(self):
        router = LLMRouter(_TestConfig())
        router.unlock_llm_settings()
        router.set_llm_provider("anthropic")
        assert router.default_provider == "anthropic"

    def test_set_llm_model_unlocked(self):
        router = LLMRouter(_TestConfig())
        router.unlock_llm_settings()
        router.set_llm_model("gpt-4o-mini")
        assert router.current_model == "gpt-4o-mini"

    def test_property_setter_triggers_lock(self):
        router = LLMRouter(_TestConfig())
        router.lock_llm_settings()
        try:
            router.default_provider = "anthropic"
            assert False, "Expected LLMSettingsLockedError"
        except LLMSettingsLockedError:
            pass

    def test_set_llm_provider_unknown(self):
        router = LLMRouter(_TestConfig())
        router.unlock_llm_settings()
        try:
            router.set_llm_provider("not-a-provider")
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_set_llm_model_empty(self):
        router = LLMRouter(_TestConfig())
        router.unlock_llm_settings()
        try:
            router.set_llm_model("  ")
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_lock_unlock_cycle(self):
        router = LLMRouter(_TestConfig())
        assert router.llm_settings_locked is False  # _TestConfig defaults to unlocked
        router.lock_llm_settings()
        assert router.llm_settings_locked is True
        router.unlock_llm_settings()
        assert router.llm_settings_locked is False


class TestLLMRouterSupportedProviders:
    """Tests for SUPPORTED_PROVIDERS and BUILTIN_PROVIDER_INFO"""

    def test_supported_providers_is_tuple(self):
        assert isinstance(SUPPORTED_PROVIDERS, tuple)

    def test_supported_providers_matches_builtin(self):
        assert set(SUPPORTED_PROVIDERS) == set(BUILTIN_PROVIDER_INFO.keys())

    def test_builtin_provider_info_has_required_fields(self):
        for pid, info in BUILTIN_PROVIDER_INFO.items():
            assert "label" in info
            assert "env_var" in info
            assert "base_url" in info
            assert "default_model" in info
            assert "requires_key" in info
            assert "kind" in info

    def test_builtin_provider_labels(self):
        assert BUILTIN_PROVIDER_INFO["openai"]["label"] == "OpenAI"
        assert BUILTIN_PROVIDER_INFO["anthropic"]["label"] == "Anthropic"
        assert BUILTIN_PROVIDER_INFO["openrouter"]["label"] == "OpenRouter"


class TestOpenAICompatibleProvider:
    """Tests for OpenAICompatibleProvider"""

    def test_headers_with_bearer(self):
        provider = OpenAICompatibleProvider("mykey", "https://api.example.com/v1", "test")
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer mykey"

    def test_headers_without_key(self):
        provider = OpenAICompatibleProvider("", "https://api.example.com/v1", "test")
        headers = provider._headers()
        assert "Authorization" not in headers

    def test_headers_custom_auth(self):
        provider = OpenAICompatibleProvider(
            "secret", "https://api.example.com/v1", "test",
            auth_header="api-key", auth_prefix=""
        )
        headers = provider._headers()
        assert headers["api-key"] == "secret"
        assert "Authorization" not in headers

    def test_headers_extra_headers(self):
        provider = OpenAICompatibleProvider(
            "mykey", "https://api.example.com/v1", "test",
            extra_headers={"X-Custom": "value"}
        )
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer mykey"
        assert headers["X-Custom"] == "value"

    def test_name_property(self):
        provider = OpenAICompatibleProvider("key", "https://api.example.com/v1", "my_provider")
        assert provider.name == "my_provider"

    def test_base_url_strips_trailing_slash(self):
        provider = OpenAICompatibleProvider("key", "https://api.example.com/v1/", "test")
        assert provider.base_url == "https://api.example.com/v1"

    def test_no_model_raises_error(self):
        provider = OpenAICompatibleProvider("", "https://api.example.com/v1", "test")

        try:
            _run(provider.chat_completion([{"role": "user", "content": "hi"}], model=None))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "No model specified" in str(e)

    def test_list_models_no_path_returns_empty(self):
        provider = OpenAICompatibleProvider(
            "key", "https://api.example.com/v1", "test", models_path=""
        )
        assert _run(provider.list_models()) == []


class TestAuthenticationError:
    """Tests for AuthenticationError"""

    def test_authentication_error_is_exception(self):
        assert issubclass(AuthenticationError, Exception)

    def test_authentication_error_message(self):
        err = AuthenticationError("Invalid API key")
        assert "Invalid API key" in str(err)