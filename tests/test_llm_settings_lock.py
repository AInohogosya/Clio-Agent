"""Guardrail tests: the agent's underlying LLM provider/model must not
change unexpectedly or "on their own" (e.g. via prompt injection
picked up from the web, or an autonomous self-edit).

The LLMRouter keeps these settings in private backing fields and exposes
them through properties whose setters refuse writes while the settings
are locked. The lock defaults to ON, so the safe posture is the
default. Only an explicit operator unlock (``/llm_unlock``) permits a
change.
"""
import pytest

from clio_agent_2.core.llm_router import LLMRouter, LLMSettingsLockedError


class _Cfg:
    default_llm_provider = "openai"
    current_model = "gpt-4o"
    openai_api_key = None
    google_api_key = None
    anthropic_api_key = None
    openrouter_api_key = None
    openrouter_http_referer = None
    openrouter_app_name = None
    grok_api_key = None
    deepseek_api_key = None


def _router(**cfg_overrides):
    cfg = _Cfg()
    for key, value in cfg_overrides.items():
        setattr(cfg, key, value)
    return LLMRouter(cfg)


def test_defaults_to_locked():
    # No llm_settings_locked attribute -> safe default is locked.
    router = _router()
    assert router.llm_settings_locked is True


def test_locked_blocks_model_change():
    router = _router()
    with pytest.raises(LLMSettingsLockedError):
        router.current_model = "gpt-4o-mini"
    # The value must be untouched.
    assert router.current_model == "gpt-4o"


def test_locked_blocks_provider_change():
    router = _router()
    with pytest.raises(LLMSettingsLockedError):
        router.default_provider = "anthropic"
    assert router.default_provider == "openai"


def test_unlock_allows_change():
    router = _router()
    router.unlock_llm_settings()
    assert router.llm_settings_locked is False
    router.current_model = "gpt-4o-mini"
    router.default_provider = "anthropic"
    assert router.current_model == "gpt-4o-mini"
    assert router.default_provider == "anthropic"


def test_explicit_lock_false_allows_change():
    router = _router(llm_settings_locked=False)
    router.current_model = "gpt-4o-mini"
    assert router.current_model == "gpt-4o-mini"


def test_validation_rejects_unknown_provider():
    router = _router(llm_settings_locked=False)
    with pytest.raises(ValueError):
        router.set_llm_provider("not-a-real-provider")


def test_validation_rejects_empty_model():
    router = _router(llm_settings_locked=False)
    with pytest.raises(ValueError):
        router.set_llm_model("   ")


def test_can_be_relocked():
    router = _router()
    router.unlock_llm_settings()
    router.lock_llm_settings()
    assert router.llm_settings_locked is True
    with pytest.raises(LLMSettingsLockedError):
        router.current_model = "gpt-4o-mini"
