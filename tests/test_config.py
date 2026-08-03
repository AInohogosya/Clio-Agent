"""
Tests for the Config class - save_to_env, save_settings, save_to_yaml,
load_custom_providers, add_custom_provider, remove_custom_provider,
validate_api_keys, to_dict, get_api_key.
"""
import tempfile
import json
from pathlib import Path
from unittest import mock

from clio_agent_2.config.settings import Config


class TestConfigInit:
    """Tests for Config initialization"""

    def test_config_loads_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=sk-test-key\nAGENT_NAME=MyAgent\n")

            config = Config(env_path=str(env_path))
            assert config.openai_api_key == "sk-test-key"
            assert config.agent_name == "MyAgent"

    def test_config_defaults_when_no_env(self):
        config = Config(env_path="/nonexistent/.env")
        assert config.default_llm_provider == "openai"
        assert config.current_model == ""
        assert config.llm_settings_locked is True
        assert config.agent_name == "Clio-Agent-2"
        assert config.autonomous_mode is True
        assert config.thinking_interval == 5.0

    def test_config_yaml_path(self):
        config = Config(env_path="/some/path/.env")
        assert config.get_yaml_path() == Path("/some/path/config.yaml")


class TestConfigSaveToEnv:
    """Tests for Config.save_to_env"""

    def test_save_to_env_new_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# existing config\n")

            config = Config(env_path=str(env_path))
            result = config.save_to_env("TEST_KEY", "test_value")

            assert result is True
            content = env_path.read_text()
            assert "TEST_KEY=test_value" in content

    def test_save_to_env_replace_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=old_value\n")

            config = Config(env_path=str(env_path))
            config.save_to_env("OPENAI_API_KEY", "new_value")

            content = env_path.read_text()
            assert "OPENAI_API_KEY=new_value" in content
            assert "old_value" not in content

    def test_save_to_env_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "new" / ".env"

            config = Config(env_path=str(env_path))
            result = config.save_to_env("NEW_KEY", "value")

            assert result is True
            assert env_path.exists()

    def test_save_to_env_updates_os_environ(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"

            config = Config(env_path=str(env_path))
            config.save_to_env("DYNAMIC_KEY", "dynamic_value")

            import os
            assert os.environ.get("DYNAMIC_KEY") == "dynamic_value"
            del os.environ["DYNAMIC_KEY"]


class TestConfigSaveSettings:
    """Tests for Config.save_settings"""

    def test_save_settings_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            result = config.save_settings({
                "OPENAI_API_KEY": "sk-abc123",
                "AGENT_NAME": "NewAgent",
                "THINKING_INTERVAL": "10",
            })

            assert result is True
            content = env_path.read_text()
            assert "OPENAI_API_KEY=sk-abc123" in content
            assert "AGENT_NAME=NewAgent" in content
            assert "THINKING_INTERVAL=10" in content

    def test_save_settings_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            config.save_settings({"AGENT_NAME": "UpdatedAgent"})
            assert config.agent_name == "UpdatedAgent"


class TestConfigCustomProviders:
    """Tests for custom provider management"""

    def test_load_custom_providers_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))
            providers = config.load_custom_providers()
            assert providers == []

    def test_load_custom_providers_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "CUSTOM_PROVIDERS=localai,ollama_local\n"
                "CUSTOM_LOCALAI_BASE_URL=http://localhost:1234/v1\n"
                "CUSTOM_LOCALAI_API_KEY=secret\n"
                "CUSTOM_LOCALAI_LABEL=LocalAI\n"
                "CUSTOM_OLLAMA_LOCAL_BASE_URL=http://localhost:11434/v1\n"
            )

            config = Config(env_path=str(env_path))
            providers = config.load_custom_providers()
            assert len(providers) == 2
            localai = next(p for p in providers if p["id"] == "localai")
            assert localai["base_url"] == "http://localhost:1234/v1"
            assert localai["api_key"] == "secret"
            assert localai["label"] == "LocalAI"

    def test_add_custom_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            config.add_custom_provider(
                "localai", "http://localhost:1234/v1",
                api_key="key123", label="LocalAI"
            )

            providers = config.load_custom_providers()
            assert len(providers) == 1
            assert providers[0]["id"] == "localai"
            assert providers[0]["base_url"] == "http://localhost:1234/v1"

    def test_add_custom_provider_duplicate_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            config.add_custom_provider("localai", "http://localhost:1234/v1")
            config.add_custom_provider("localai", "http://new-url:5678/v1")

            content = env_path.read_text()
            assert "http://new-url:5678/v1" in content
            assert "http://localhost:1234/v1" not in content

    def test_add_custom_provider_invalid_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            try:
                config.add_custom_provider("Bad ID!", "http://localhost:1234/v1")
                assert False, "Expected ValueError"
            except ValueError:
                pass

    def test_add_custom_provider_empty_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            try:
                config.add_custom_provider("", "http://localhost:1234/v1")
                assert False, "Expected ValueError"
            except ValueError:
                pass

    def test_add_custom_provider_missing_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            try:
                config.add_custom_provider("test", "")
                assert False, "Expected ValueError"
            except ValueError:
                pass

    def test_remove_custom_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            config.add_custom_provider("localai", "http://localhost:1234/v1")
            assert len(config.load_custom_providers()) == 1

            result = config.remove_custom_provider("localai")
            assert result is True
            assert len(config.load_custom_providers()) == 0

    def test_remove_custom_provider_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            result = config.remove_custom_provider("nonexistent")
            assert result is False

    def test_custom_provider_suffix_sanitization(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            config.add_custom_provider("my-provider_123", "http://localhost:1234/v1")

            content = env_path.read_text()
            assert "CUSTOM_MY_PROVIDER_123_BASE_URL" in content

    def test_add_custom_provider_with_advanced_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            config.add_custom_provider(
                "myai", "http://localhost:1234/v1",
                api_key="key123", label="My AI",
                auth_header="X-API-Key", auth_prefix="",
                models_path="/v1/models", default_model="my-model"
            )

            providers = config.load_custom_providers()
            assert len(providers) == 1
            p = providers[0]
            assert p["auth_header"] == "X-API-Key"
            assert p["auth_prefix"] == ""
            assert p["models_path"] == "/v1/models"
            assert p["default_model"] == "my-model"


class TestConfigValidateApiKeys:
    """Tests for Config.validate_api_keys and _is_real_secret"""

    def test_validate_api_keys_all_false(self):
        config = Config(env_path="/nonexistent/.env")
        status = config.validate_api_keys()

        assert status["openai"] is False
        assert status["google"] is False
        assert status["telegram"] is False

    def test_validate_api_keys_with_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=sk-realkey123\nGOOGLE_API_KEY=AIza-realkey\n")

            config = Config(env_path=str(env_path))
            status = config.validate_api_keys()

            assert status["openai"] is True
            assert status["google"] is True
            assert status["anthropic"] is False

    def test_is_real_secret_rejects_placeholder(self):
        config = Config(env_path="/nonexistent/.env")

        assert config._is_real_secret(None) is False
        assert config._is_real_secret("") is False
        assert config._is_real_secret("   ") is False
        assert config._is_real_secret("your_api_key_here") is False
        assert config._is_real_secret("<placeholder>") is False
        assert config._is_real_secret("example_key") is False
        assert config._is_real_secret("sk-your-key") is False
        assert config._is_real_secret("xxxx") is False

    def test_is_real_secret_accepts_real_key(self):
        config = Config(env_path="/nonexistent/.env")

        assert config._is_real_secret("sk-abc123def456") is True
        assert config._is_real_secret("AIzaSyRealKeyHere") is True
        assert config._is_real_secret("some-real-token-data") is True


class TestConfigGetApiKey:
    """Tests for Config.get_api_key"""

    def test_get_api_key_openai(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=sk-test123\n")

            config = Config(env_path=str(env_path))
            assert config.get_api_key("openai") == "sk-test123"

    def test_get_api_key_unknown_provider(self):
        config = Config(env_path="/nonexistent/.env")
        assert config.get_api_key("unknown") is None

    def test_get_api_key_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=sk-test123\n")

            config = Config(env_path=str(env_path))
            assert config.get_api_key("OPENAI") == "sk-test123"
            assert config.get_api_key("OpenAI") == "sk-test123"

    def test_get_api_key_custom_provider(self):
        config = Config(env_path="/nonexistent/.env")
        config.custom_providers = [{"id": "localai", "api_key": "customkey", "base_url": "http://x"}]
        assert config.get_api_key("localai") == "customkey"


class TestConfigToDict:
    """Tests for Config.to_dict"""

    def test_to_dict_excludes_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=sk-secret123\n")

            config = Config(env_path=str(env_path))
            d = config.to_dict()

            assert "default_llm_provider" in d
            assert "current_model" in d
            assert "agent_name" in d
            assert "api_keys_configured" in d
            # API keys should NOT be in the dict
            assert "openai_api_key" not in d

    def test_to_dict_includes_custom_providers(self):
        config = Config(env_path="/nonexistent/.env")
        config.custom_providers = [{"id": "localai", "api_key": "secret", "base_url": "http://x", "label": "LocalAI"}]

        d = config.to_dict()
        assert "custom_providers" in d
        assert len(d["custom_providers"]) == 1
        assert "api_key" not in d["custom_providers"][0]
        assert d["custom_providers"][0]["id"] == "localai"


class TestConfigYamlSave:
    """Tests for Config.save_to_yaml"""

    def test_save_to_yaml_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))
            config.agent_name = "TestAgent"

            result = config.save_to_yaml()

            assert result is True
            yaml_path = Path(tmp) / "config.yaml"
            assert yaml_path.exists()
            content = yaml_path.read_text()
            assert "TestAgent" in content

    def test_save_to_yaml_custom_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            config = Config(env_path=str(env_path))

            custom_path = Path(tmp) / "custom.yaml"
            result = config.save_to_yaml(path=str(custom_path))

            assert result is True
            assert custom_path.exists()


class TestConfigCustomSuffix:
    """Tests for Config._custom_suffix"""

    def test_suffix_simple(self):
        config = Config(env_path="/nonexistent/.env")
        assert config._custom_suffix("abc") == "ABC"

    def test_suffix_special_chars(self):
        config = Config(env_path="/nonexistent/.env")
        assert config._custom_suffix("my-provider_123") == "MY_PROVIDER_123"


class TestConfigRemoveEnvValue:
    """Tests for Config._remove_env_value"""

    def test_remove_env_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "KEY1=value1\n"
                "KEY2=value2\n"
                "KEY3=value3\n"
            )

            import os
            config = Config(env_path=str(env_path))
            config._remove_env_value("KEY2")

            content = env_path.read_text()
            assert "KEY1=value1" in content
            assert "KEY2" not in content
            assert "KEY3=value3" in content