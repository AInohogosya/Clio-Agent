"""
Tests for setup_env module - CLI helper functions.
Covers _resolve_provider, _status_line, PROVIDER_* mappings, apply_overrides_from_argv.
"""
import tempfile
from pathlib import Path
from unittest import mock

from clio_agent_2.config.setup_env import (
    get_env_path,
    set_env_value,
    _resolve_provider,
    _status_line,
    PROVIDER_ENV_VARS,
    LLM_PROVIDERS,
    SUGGESTED_MODELS,
    PROVIDER_LABELS,
    apply_overrides_from_argv,
)


class TestResolveProvider:
    """Tests for _resolve_provider"""

    def test_resolve_by_number(self):
        configured = ["openai", "google", "anthropic"]
        assert _resolve_provider("1", configured) == "openai"
        assert _resolve_provider("2", configured) == "google"
        assert _resolve_provider("3", configured) == "anthropic"

    def test_resolve_by_name(self):
        configured = ["openai", "google", "anthropic"]
        assert _resolve_provider("openai", configured) == "openai"
        assert _resolve_provider("google", configured) == "google"

    def test_resolve_by_prefix(self):
        configured = ["openai", "anthropic", "deepseek"]
        # "an" should match "anthropic" (only one match)
        assert _resolve_provider("an", configured) == "anthropic"

    def test_resolve_no_match(self):
        configured = ["openai"]
        assert _resolve_provider("nonexistent", configured) is None

    def test_resolve_ambiguous_prefix(self):
        configured = ["openai", "openrouter"]
        # "open" matches both, so no single match
        assert _resolve_provider("open", configured) is None

    def test_resolve_empty_choice(self):
        configured = ["openai"]
        assert _resolve_provider("", configured) == "openai"

    def test_resolve_out_of_range_number(self):
        configured = ["openai"]
        assert _resolve_provider("5", configured) is None


class TestStatusLine:
    """Tests for _status_line"""

    def test_status_line_ok(self):
        result = _status_line("OpenAI", True)
        assert "✅" in result
        assert "OpenAI" in result

    def test_status_line_not_ok(self):
        result = _status_line("Anthropic", False)
        assert "❌" in result
        assert "Anthropic" in result


class TestProviderMappings:
    """Tests for PROVIDER_* mappings"""

    def test_provider_env_vars_complete(self):
        for pid in LLM_PROVIDERS:
            assert pid in PROVIDER_ENV_VARS
            assert PROVIDER_ENV_VARS[pid].endswith("_API_KEY")

    def test_suggested_models_complete(self):
        for pid in LLM_PROVIDERS:
            assert pid in SUGGESTED_MODELS
            assert len(SUGGESTED_MODELS[pid]) > 0

    def test_provider_labels_complete(self):
        for pid in LLM_PROVIDERS:
            assert pid in PROVIDER_LABELS
            assert len(PROVIDER_LABELS[pid]) > 0

    def test_openai_label(self):
        assert PROVIDER_LABELS["openai"] == "OpenAI"

    def test_anthropic_label(self):
        assert PROVIDER_LABELS["anthropic"] == "Anthropic"


class TestSetEnvValue:
    """Tests for set_env_value"""

    def test_set_env_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("")

            with mock.patch.object(__import__('clio_agent_2.config.settings', fromlist=['Config']), 'Config') as MockConfig:
                mock_config = mock.MagicMock()
                mock_config.save_to_env.return_value = True
                MockConfig.return_value = mock_config

                result = set_env_value("TEST_KEY", "test_value")

                assert result is True
                mock_config.save_to_env.assert_called_with("TEST_KEY", "test_value")

    def test_set_env_value_failure(self):
        with mock.patch.object(__import__('clio_agent_2.config.settings', fromlist=['Config']), 'Config') as MockConfig:
            mock_config = mock.MagicMock()
            mock_config.save_to_env.return_value = False
            MockConfig.return_value = mock_config

            result = set_env_value("TEST_KEY", "test_value")
            assert result is False


class TestApplyOverridesFromArgv:
    """Tests for apply_overrides_from_argv"""

    def test_apply_openai_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"

            import sys
            orig_argv = sys.argv
            sys.argv = ["run.py", "--openai", "sk-test-key"]

            try:
                with mock.patch("clio_agent_2.config.settings.Config") as MockConfig:
                    mock_config = mock.MagicMock()
                    mock_config.save_settings.return_value = True
                    mock_config.get_env_path.return_value = env_path
                    MockConfig.return_value = mock_config

                    result = apply_overrides_from_argv(sys.argv[1:])
                    assert result is True
                    mock_config.save_settings.assert_called()
            finally:
                sys.argv = orig_argv

    def test_apply_search_key(self):
        with mock.patch("clio_agent_2.config.settings.Config") as MockConfig:
            mock_config = mock.MagicMock()
            mock_config.save_settings.return_value = True
            MockConfig.return_value = mock_config

            result = apply_overrides_from_argv(["--search", "search-key"])
            assert result is True
            mock_config.save_settings.assert_called()

    def test_apply_model_settings(self):
        with mock.patch("clio_agent_2.config.settings.Config") as MockConfig:
            mock_config = mock.MagicMock()
            mock_config.save_settings.return_value = True
            MockConfig.return_value = mock_config

            result = apply_overrides_from_argv(["--provider", "anthropic", "--model", "claude-3"])
            assert result is True
            mock_config.save_settings.assert_called()

    def test_apply_custom_provider(self):
        with mock.patch("clio_agent_2.config.settings.Config") as MockConfig:
            mock_config = mock.MagicMock()
            mock_config.save_settings.return_value = True
            MockConfig.return_value = mock_config

            result = apply_overrides_from_argv([
                "--custom",
                '[{"id":"localai","base_url":"http://localhost:1234/v1"}]'
            ])
            assert result is True
            mock_config.add_custom_provider.assert_called()

    def test_apply_invalid_custom_json(self):
        with mock.patch("clio_agent_2.config.settings.Config") as MockConfig:
            mock_config = mock.MagicMock()
            MockConfig.return_value = mock_config

            result = apply_overrides_from_argv(["--custom", "invalid-json"])
            assert result is False

    def test_apply_no_args_returns_false(self):
        with mock.patch("clio_agent_2.config.settings.Config") as MockConfig:
            mock_config = mock.MagicMock()
            MockConfig.return_value = mock_config

            result = apply_overrides_from_argv([])
            assert result is False


class TestGetEnvPath:
    """Tests for get_env_path"""

    def test_get_env_path(self):
        path = get_env_path()
        assert path.exists() or path.name == ".env"
        assert path.suffix == ".env" or path.name == ".env"