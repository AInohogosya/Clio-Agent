"""
Tests for setup_env interactive flows.
"""
import sys
from pathlib import Path
from unittest import mock
from io import StringIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clio_agent_2.config.setup_env import (
    configure_llm_keys,
    configure_custom_provider,
    _add_custom_provider_flow,
    _remove_custom_provider_flow,
    configure_search_api,
    configure_telegram,
    configure_discord,
    configure_whatsapp,
    configure_model,
    print_status,
    PROVIDER_LABELS,
    LLM_PROVIDERS,
    SUGGESTED_MODELS,
)


class TestSetupEnvConfigureLLMKeys:
    """Tests for configure_llm_keys"""

    def test_configure_llm_keys_exits_on_back(self):
        with mock.patch("builtins.input", side_effect=["99"]):  # Back option
            with mock.patch("clio_agent_2.config.setup_env.Config") as MockConfig:
                mock_config = mock.MagicMock()
                mock_config.validate_api_keys.return_value = {p: False for p in LLM_PROVIDERS}
                MockConfig.return_value = mock_config

                configure_llm_keys(mock_config)

    def test_configure_llm_keys_invalid_choice(self):
        with mock.patch("builtins.input", side_effect=["invalid", "99"]):
            with mock.patch("clio_agent_2.config.setup_env.Config") as MockConfig:
                mock_config = mock.MagicMock()
                mock_config.validate_api_keys.return_value = {p: False for p in LLM_PROVIDERS}
                MockConfig.return_value = mock_config

                configure_llm_keys(mock_config)


class TestSetupEnvCustomProvider:
    """Tests for configure_custom_provider and flows"""

    def test_add_custom_provider_flow(self):
        with mock.patch("builtins.input", side_effect=[
            "localai",
            "http://localhost:1234/v1",
            "key123",
            "LocalAI",
            "n",  # No advanced settings
        ]):
            with mock.patch("clio_agent_2.config.setup_env.Config") as MockConfig:
                mock_config = mock.MagicMock()
                mock_config.add_custom_provider.return_value = True
                MockConfig.return_value = mock_config

                _add_custom_provider_flow(mock_config)

                mock_config.add_custom_provider.assert_called_with(
                    "localai", "http://localhost:1234/v1",
                    api_key="key123", label="LocalAI",
                    auth_header="Authorization", auth_prefix="Bearer",
                    models_path="/models", default_model=""
                )

    def test_add_custom_provider_flow_advanced(self):
        with mock.patch("builtins.input", side_effect=[
            "myprovider",
            "http://localhost:5000/v1",
            "secret",
            "My Provider",
            "y",  # Yes advanced
            "X-API-Key",
            "",
            "/custom/models",
            "my-model",
        ]):
            with mock.patch("clio_agent_2.config.setup_env.Config") as MockConfig:
                mock_config = mock.MagicMock()
                mock_config.add_custom_provider.return_value = True
                MockConfig.return_value = mock_config

                _add_custom_provider_flow(mock_config)

                mock_config.add_custom_provider.assert_called_with(
                    "myprovider", "http://localhost:5000/v1",
                    api_key="secret", label="My Provider",
                    auth_header="X-API-Key", auth_prefix="Bearer",
                    models_path="/custom/models", default_model="my-model"
                )

    def test_remove_custom_provider_flow(self):
        with mock.patch("builtins.input", side_effect=["1"]):
            with mock.patch("clio_agent_2.config.setup_env.Config") as MockConfig:
                mock_config = mock.MagicMock()
                mock_config.remove_custom_provider.return_value = True
                mock_config.load_custom_providers.return_value = [
                    {"id": "localai", "label": "LocalAI", "base_url": "http://localhost:1234/v1"}
                ]
                MockConfig.return_value = mock_config

                _remove_custom_provider_flow(mock_config, mock_config.load_custom_providers())

                mock_config.remove_custom_provider.assert_called_with("localai")


class TestSetupEnvOtherConfig:
    """Tests for other configure_* functions"""

    def test_configure_search_api(self):
        with mock.patch("builtins.input", return_value="search-key"):
            with mock.patch("clio_agent_2.config.setup_env.set_env_value") as mock_set:
                mock_set.return_value = True
                configure_search_api(mock.MagicMock())
                mock_set.assert_called_with("SEARCH_API_KEY", "search-key")

    def test_configure_search_api_skip(self):
        with mock.patch("builtins.input", return_value=""):
            with mock.patch("clio_agent_2.config.setup_env.set_env_value") as mock_set:
                configure_search_api(mock.MagicMock())
                mock_set.assert_not_called()

    def test_configure_telegram(self):
        with mock.patch("builtins.input", return_value="123456:ABC-DEF"):
            with mock.patch("clio_agent_2.config.setup_env.set_env_value") as mock_set:
                mock_set.return_value = True
                configure_telegram(mock.MagicMock())
                mock_set.assert_called_with("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")

    def test_configure_discord(self):
        with mock.patch("builtins.input", return_value="discord-token"):
            with mock.patch("clio_agent_2.config.setup_env.set_env_value") as mock_set:
                mock_set.return_value = True
                configure_discord(mock.MagicMock())
                mock_set.assert_called_with("DISCORD_BOT_TOKEN", "discord-token")

    def test_configure_whatsapp_full(self):
        inputs = iter([
            "phone123",      # phone number ID
            "access_token",  # access token
            "app_secret",    # app secret
            "verify_token",  # verify token
            "https://webhook.url",  # webhook URL
            "9000",          # webhook port
        ])
        with mock.patch("builtins.input", lambda _: next(inputs)):
            with mock.patch("clio_agent_2.config.setup_env.set_env_value") as mock_set:
                mock_set.return_value = True
                configure_whatsapp(mock.MagicMock())
                assert mock_set.call_count == 6

    def test_configure_model(self):
        with mock.patch("clio_agent_2.config.setup_env.Config") as MockConfig:
            mock_config = mock.MagicMock()
            mock_config.validate_api_keys.return_value = {"openai": True, "anthropic": True}
            MockConfig.return_value = mock_config

            with mock.patch("builtins.input", side_effect=["1", "gpt-4o-custom"]):
                configure_model(mock_config)

                mock_config.save_settings.assert_called_with({
                    "DEFAULT_LLM_PROVIDER": "openai",
                    "DEFAULT_MODEL": "gpt-4o-custom",
                })


class TestSetupEnvPrintStatus:
    """Tests for print_status"""

    def test_print_status(self):
        with mock.patch("builtins.print") as mock_print:
            mock_config = mock.MagicMock()
            mock_config.validate_api_keys.return_value = {"openai": True, "telegram": False}
            mock_config.default_llm_provider = "openai"
            mock_config.current_model = "gpt-4o"
            mock_config.custom_providers = []

            print_status(mock_config)

            # Verify print was called multiple times
            assert mock_print.call_count > 5


class TestSetupEnvMappings:
    """Tests for provider mappings"""

    def test_provider_labels_complete(self):
        for pid in LLM_PROVIDERS:
            assert pid in PROVIDER_LABELS
            assert len(PROVIDER_LABELS[pid]) > 0

    def test_suggested_models_complete(self):
        for pid in LLM_PROVIDERS:
            assert pid in SUGGESTED_MODELS
            assert len(SUGGESTED_MODELS[pid]) > 0

    def test_provider_env_vars_complete(self):
        from clio_agent_2.config.setup_env import PROVIDER_ENV_VARS
        for pid in LLM_PROVIDERS:
            assert pid in PROVIDER_ENV_VARS
            assert PROVIDER_ENV_VARS[pid].endswith("_API_KEY")