"""
Settings Manager for Clio-Agent-1 AI Agent
Handles API key storage and model configuration
"""

import json
import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

from ..utils.logger import get_logger


@dataclass
class APISettings:
    """API settings data structure - no hardcoded defaults, all empty until user sets them"""
    google_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    xai_api_key: Optional[str] = None
    meta_api_key: Optional[str] = None
    mistral_api_key: Optional[str] = None
    microsoft_api_key: Optional[str] = None
    amazon_access_key: Optional[str] = None
    amazon_secret_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    together_api_key: Optional[str] = None
    minimax_api_key: Optional[str] = None
    zhipuai_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    preferred_provider: str = ""  # Must be explicitly set by user
    google_model: str = ""
    groq_model: str = ""
    openai_model: str = ""
    anthropic_model: str = ""
    xai_model: str = ""
    meta_model: str = ""
    mistral_model: str = ""
    microsoft_model: str = ""
    amazon_model: str = ""
    cohere_model: str = ""
    deepseek_model: str = ""
    together_model: str = ""
    minimax_model: str = ""
    zhipuai_model: str = ""
    openrouter_model: str = ""
    ollama_model: str = ""


class SettingsManager:
    """Manages application settings and API keys with config.yaml persistence"""

    def __init__(self):
        self.logger = get_logger("settings_manager")
        self._config_path = Path(__file__).parent.parent.parent.parent / "config.yaml"
        # Initialize with default settings
        self._settings = APISettings()
        # Load from config.yaml if it exists
        self._load_from_config()

    def _load_from_config(self):
        """Load settings from config.yaml if it exists."""
        try:
            if self._config_path.exists():
                with open(self._config_path, 'r') as f:
                    config = yaml.safe_load(f) or {}
                api_config = config.get('api', {})

                # Load preferred provider
                if api_config.get('preferred_provider'):
                    self._settings.preferred_provider = api_config['preferred_provider']

                # Load API keys
                api_keys = api_config.get('api_keys', {})
                for provider, key in api_keys.items():
                    if key:
                        provider_key_map = {
                            "google": "google_api_key",
                            "groq": "groq_api_key",
                            "openai": "openai_api_key",
                            "anthropic": "anthropic_api_key",
                            "xai": "xai_api_key",
                            "meta": "meta_api_key",
                            "mistral": "mistral_api_key",
                            "microsoft": "microsoft_api_key",
                            "amazon": "amazon_access_key",
                            "cohere": "cohere_api_key",
                            "deepseek": "deepseek_api_key",
                            "together": "together_api_key",
                            "minimax": "minimax_api_key",
                            "zhipuai": "zhipuai_api_key",
                            "openrouter": "openrouter_api_key",
                        }
                        attr = provider_key_map.get(provider)
                        if attr:
                            setattr(self._settings, attr, key)

                # Load models
                models = api_config.get('models', {})
                for provider, model in models.items():
                    if model:
                        provider_model_map = {
                            "google": "google_model",
                            "groq": "groq_model",
                            "openai": "openai_model",
                            "anthropic": "anthropic_model",
                            "xai": "xai_model",
                            "meta": "meta_model",
                            "mistral": "mistral_model",
                            "microsoft": "microsoft_model",
                            "amazon": "amazon_model",
                            "cohere": "cohere_model",
                            "deepseek": "deepseek_model",
                            "together": "together_model",
                            "minimax": "minimax_model",
                            "zhipuai": "zhipuai_model",
                            "ollama": "ollama_model",
                            "openrouter": "openrouter_model",
                        }
                        attr = provider_model_map.get(provider)
                        if attr:
                            setattr(self._settings, attr, model)

                self.logger.info("Settings loaded from config.yaml")
        except Exception as e:
            self.logger.warning(f"Could not load settings from config.yaml: {e}")

    def _save_to_config(self):
        """Save current settings to config.yaml."""
        try:
            config = {}
            if self._config_path.exists():
                with open(self._config_path, 'r') as f:
                    config = yaml.safe_load(f) or {}
            else:
                self._config_path.parent.mkdir(parents=True, exist_ok=True)

            if 'api' not in config:
                config['api'] = {}

            # Save preferred provider
            if self._settings.preferred_provider:
                config['api']['preferred_provider'] = self._settings.preferred_provider

            # Save API keys
            if 'api_keys' not in config['api']:
                config['api']['api_keys'] = {}

            provider_key_map = {
                "google": "google_api_key",
                "groq": "groq_api_key",
                "openai": "openai_api_key",
                "anthropic": "anthropic_api_key",
                "xai": "xai_api_key",
                "meta": "meta_api_key",
                "mistral": "mistral_api_key",
                "microsoft": "microsoft_api_key",
                "amazon": "amazon_access_key",
                "cohere": "cohere_api_key",
                "deepseek": "deepseek_api_key",
                "together": "together_api_key",
                "minimax": "minimax_api_key",
                "zhipuai": "zhipuai_api_key",
                "openrouter": "openrouter_api_key",
            }
            for provider, attr in provider_key_map.items():
                value = getattr(self._settings, attr, None)
                if value:
                    config['api']['api_keys'][provider] = value

            # Save models
            if 'models' not in config['api']:
                config['api']['models'] = {}

            provider_model_map = {
                "google": "google_model",
                "groq": "groq_model",
                "openai": "openai_model",
                "anthropic": "anthropic_model",
                "xai": "xai_model",
                "meta": "meta_model",
                "mistral": "mistral_model",
                "microsoft": "microsoft_model",
                "amazon": "amazon_model",
                "cohere": "cohere_model",
                "deepseek": "deepseek_model",
                "together": "together_model",
                "minimax": "minimax_model",
                "zhipuai": "zhipuai_model",
                "ollama": "ollama_model",
                "openrouter": "openrouter_model",
            }
            for provider, attr in provider_model_map.items():
                value = getattr(self._settings, attr, None)
                if value:
                    config['api']['models'][provider] = value

            with open(self._config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

            self.logger.info("Settings saved to config.yaml")
        except Exception as e:
            self.logger.warning(f"Could not save settings to config.yaml: {e}")

    def _load_settings(self) -> APISettings:
        """Initialize with default settings"""
        return APISettings()

    def _save_settings(self):
        """Save settings to config.yaml"""
        self._save_to_config()
    
    def get_settings(self) -> APISettings:
        """Get current settings"""
        return self._settings
    
    def set_google_api_key(self, api_key: str):
        """Set Google API key"""
        self._settings.google_api_key = api_key
        self.logger.info("Google API key updated")
        self._save_to_config()

    def set_groq_api_key(self, api_key: str):
        """Set Groq API key"""
        self._settings.groq_api_key = api_key
        self.logger.info("Groq API key updated")
        self._save_to_config()

    def set_openai_api_key(self, api_key: str):
        """Set OpenAI API key"""
        self._settings.openai_api_key = api_key
        self.logger.info("OpenAI API key updated")
        self._save_to_config()
    
    def get_google_api_key(self) -> Optional[str]:
        """Get Google API key"""
        return self._settings.google_api_key
    
    def get_groq_api_key(self) -> Optional[str]:
        """Get Groq API key"""
        return self._settings.groq_api_key
    
    def get_preferred_provider(self) -> str:
        """Get preferred provider"""
        return self._settings.preferred_provider
    
    def has_google_api_key(self) -> bool:
        """Check if Google API key is available"""
        return bool(self._settings.google_api_key)
    
    def has_groq_api_key(self) -> bool:
        """Check if Groq API key is available"""
        return bool(self._settings.groq_api_key)
    
    def clear_google_api_key(self):
        """Clear Google API key"""
        self._settings.google_api_key = None
        self.logger.info("Google API key cleared")
        self._save_to_config()
    
    def clear_groq_api_key(self):
        """Clear Groq API key"""
        self._settings.groq_api_key = None
        self.logger.info("Groq API key cleared")
        self._save_to_config()
    
    def get_openai_api_key(self) -> Optional[str]:
        """Get OpenAI API key"""
        return self._settings.openai_api_key
    
    def has_openai_api_key(self) -> bool:
        """Check if OpenAI API key is available"""
        return bool(self._settings.openai_api_key)
    
    def set_anthropic_api_key(self, api_key: str):
        """Set Anthropic API key"""
        self._settings.anthropic_api_key = api_key
        self.logger.info("Anthropic API key updated")
        self._save_to_config()
    
    def get_anthropic_api_key(self) -> Optional[str]:
        """Get Anthropic API key"""
        return self._settings.anthropic_api_key
    
    def has_anthropic_api_key(self) -> bool:
        """Check if Anthropic API key is available"""
        return bool(self._settings.anthropic_api_key)
    
    def clear_anthropic_api_key(self):
        """Clear Anthropic API key"""
        self._settings.anthropic_api_key = None
        self.logger.info("Anthropic API key cleared")
        self._save_to_config()
    
    def set_anthropic_model(self, model: str):
        """Set Anthropic model"""
        # For now, accept any model name - validation will be done during selection
        self._settings.anthropic_model = model
        self.logger.info(f"Anthropic model set to: {model}")
        self._save_to_config()
    
    def get_anthropic_model(self) -> str:
        """Get Anthropic model"""
        return self._settings.anthropic_model
    
    def set_google_model(self, model: str):
        """Set Google model"""
        # Accept any valid Google model name - validation will be done during selection
        self._settings.google_model = model
        self.logger.info(f"Google model set to: {model}")
        self._save_to_config()
    
    def set_groq_model(self, model: str):
        """Set Groq model"""
        # Accept any valid Groq model name - validation will be done during selection
        self._settings.groq_model = model
        self.logger.info(f"Groq model set to: {model}")
        self._save_to_config()
    
    def set_openai_model(self, model: str):
        """Set OpenAI model"""
        # Accept any valid OpenAI model name - validation will be done during selection
        self._settings.openai_model = model
        self.logger.info(f"OpenAI model set to: {model}")
        self._save_to_config()
    
    def get_google_model(self) -> str:
        """Get Google model"""
        return self._settings.google_model
    
    def get_groq_model(self) -> str:
        """Get Groq model"""
        return self._settings.groq_model
    
    def get_openai_model(self) -> str:
        """Get OpenAI model"""
        return self._settings.openai_model
    
    def set_ollama_model(self, model: str):
        """Set Ollama model"""
        self._settings.ollama_model = model
        self.logger.info(f"Ollama model set to: {model}")
        self._save_to_config()
    
    def get_ollama_model(self) -> str:
        """Get Ollama model"""
        return self._settings.ollama_model
    
    # xAI methods
    def set_xai_api_key(self, api_key: str):
        self._settings.xai_api_key = api_key
        self._save_to_config()
    
    def get_xai_api_key(self) -> Optional[str]:
        return self._settings.xai_api_key
    
    def has_xai_api_key(self) -> bool:
        return bool(self._settings.xai_api_key)
    
    def set_xai_model(self, model: str):
        self._settings.xai_model = model
        self._save_to_config()
    
    def get_xai_model(self) -> str:
        return self._settings.xai_model
    
    # Meta methods
    def set_meta_api_key(self, api_key: str):
        self._settings.meta_api_key = api_key
        self._save_to_config()
    
    def get_meta_api_key(self) -> Optional[str]:
        return self._settings.meta_api_key
    
    def has_meta_api_key(self) -> bool:
        return bool(self._settings.meta_api_key)
    
    def set_meta_model(self, model: str):
        self._settings.meta_model = model
        self._save_to_config()
    
    def get_meta_model(self) -> str:
        return self._settings.meta_model
    
    # Mistral methods
    def set_mistral_api_key(self, api_key: str):
        self._settings.mistral_api_key = api_key
        self._save_to_config()
    
    def get_mistral_api_key(self) -> Optional[str]:
        return self._settings.mistral_api_key
    
    def has_mistral_api_key(self) -> bool:
        return bool(self._settings.mistral_api_key)
    
    def set_mistral_model(self, model: str):
        self._settings.mistral_model = model
        self._save_to_config()
    
    def get_mistral_model(self) -> str:
        return self._settings.mistral_model
    
    # Microsoft/Azure methods
    def set_microsoft_api_key(self, api_key: str):
        self._settings.microsoft_api_key = api_key
        self._save_to_config()
    
    def get_microsoft_api_key(self) -> Optional[str]:
        return self._settings.microsoft_api_key
    
    def has_microsoft_api_key(self) -> bool:
        return bool(self._settings.microsoft_api_key)
    
    def set_microsoft_model(self, model: str):
        self._settings.microsoft_model = model
        self._save_to_config()
    
    def get_microsoft_model(self) -> str:
        return self._settings.microsoft_model
    
    # Amazon/Bedrock methods
    def set_amazon_credentials(self, access_key: str, secret_key: str):
        self._settings.amazon_access_key = access_key
        self._settings.amazon_secret_key = secret_key
        self._save_to_config()
    
    def get_amazon_access_key(self) -> Optional[str]:
        return self._settings.amazon_access_key
    
    def get_amazon_secret_key(self) -> Optional[str]:
        return self._settings.amazon_secret_key
    
    def has_amazon_credentials(self) -> bool:
        return bool(self._settings.amazon_access_key and self._settings.amazon_secret_key)
    
    def set_amazon_model(self, model: str):
        self._settings.amazon_model = model
        self._save_to_config()
    
    def get_amazon_model(self) -> str:
        return self._settings.amazon_model
    
    # Cohere methods
    def set_cohere_api_key(self, api_key: str):
        self._settings.cohere_api_key = api_key
        self._save_to_config()
    
    def get_cohere_api_key(self) -> Optional[str]:
        return self._settings.cohere_api_key
    
    def has_cohere_api_key(self) -> bool:
        return bool(self._settings.cohere_api_key)
    
    def set_cohere_model(self, model: str):
        self._settings.cohere_model = model
        self._save_to_config()
    
    def get_cohere_model(self) -> str:
        return self._settings.cohere_model
    
    # DeepSeek methods
    def set_deepseek_api_key(self, api_key: str):
        self._settings.deepseek_api_key = api_key
        self._save_to_config()
    
    def get_deepseek_api_key(self) -> Optional[str]:
        return self._settings.deepseek_api_key
    
    def has_deepseek_api_key(self) -> bool:
        return bool(self._settings.deepseek_api_key)
    
    def set_deepseek_model(self, model: str):
        self._settings.deepseek_model = model
        self._save_to_config()
    
    def get_deepseek_model(self) -> str:
        return self._settings.deepseek_model
    
    # Together AI methods
    def set_together_api_key(self, api_key: str):
        self._settings.together_api_key = api_key
        self._save_to_config()
    
    def get_together_api_key(self) -> Optional[str]:
        return self._settings.together_api_key
    
    def has_together_api_key(self) -> bool:
        return bool(self._settings.together_api_key)
    
    def set_together_model(self, model: str):
        self._settings.together_model = model
        self._save_to_config()
    
    def get_together_model(self) -> str:
        return self._settings.together_model
    
    # MiniMax methods
    def set_minimax_api_key(self, api_key: str):
        self._settings.minimax_api_key = api_key
        self._save_to_config()
    
    def get_minimax_api_key(self) -> Optional[str]:
        return self._settings.minimax_api_key
    
    def has_minimax_api_key(self) -> bool:
        return bool(self._settings.minimax_api_key)
    
    def set_minimax_model(self, model: str):
        self._settings.minimax_model = model
        self._save_to_config()
    
    def get_minimax_model(self) -> str:
        return self._settings.minimax_model
    
    # ZhipuAI methods
    def set_zhipuai_api_key(self, api_key: str):
        self._settings.zhipuai_api_key = api_key
        self._save_to_config()
    
    def get_zhipuai_api_key(self) -> Optional[str]:
        return self._settings.zhipuai_api_key
    
    def has_zhipuai_api_key(self) -> bool:
        return bool(self._settings.zhipuai_api_key)
    
    def set_zhipuai_model(self, model: str):
        self._settings.zhipuai_model = model
        self._save_to_config()
    
    def get_zhipuai_model(self) -> str:
        return self._settings.zhipuai_model
    
    def set_preferred_provider(self, provider: str):
        """Set preferred provider"""
        valid_providers = ["ollama", "google", "groq", "openai", "anthropic",
                          "xai", "meta", "mistral", "microsoft", "azure", "amazon",
                          "cohere", "deepseek", "together", "minimax", "zhipuai", "openrouter"]
        if provider not in valid_providers:
            raise ValueError(f"Provider must be one of: {valid_providers}")
        self._settings.preferred_provider = provider
        self.logger.info(f"Preferred provider set to: {provider}")
        self._save_to_config()
    
    def set_openrouter_api_key(self, api_key: str):
        """Set OpenRouter API key"""
        self._settings.openrouter_api_key = api_key
        self.logger.info("OpenRouter API key updated")
        self._save_to_config()
    
    def get_openrouter_api_key(self) -> Optional[str]:
        """Get OpenRouter API key"""
        return self._settings.openrouter_api_key
    
    def has_openrouter_api_key(self) -> bool:
        """Check if OpenRouter API key is available"""
        return bool(self._settings.openrouter_api_key)
    
    def clear_openrouter_api_key(self):
        """Clear OpenRouter API key"""
        self._settings.openrouter_api_key = None
        self.logger.info("OpenRouter API key cleared")
        self._save_to_config()
    
    def set_openrouter_model(self, model: str):
        """Set OpenRouter model"""
        # Accept any valid OpenRouter model name - validation will be done during selection
        self._settings.openrouter_model = model
        self.logger.info(f"OpenRouter model set to: {model}")
        self._save_to_config()
    
    def get_openrouter_model(self) -> str:
        """Get OpenRouter model"""
        return self._settings.openrouter_model
    
    def set_api_key(self, provider: str, api_key: str):
        """Generic API key setter for any provider"""
        provider_key_map = {
            "google": "google_api_key",
            "groq": "groq_api_key",
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
            "xai": "xai_api_key",
            "meta": "meta_api_key",
            "mistral": "mistral_api_key",
            "microsoft": "microsoft_api_key",
            "azure": "microsoft_api_key",
            "amazon": "amazon_access_key",
            "cohere": "cohere_api_key",
            "deepseek": "deepseek_api_key",
            "together": "together_api_key",
            "minimax": "minimax_api_key",
            "zhipuai": "zhipuai_api_key",
            "openrouter": "openrouter_api_key"
        }

        if provider not in provider_key_map:
            raise ValueError(f"Unknown provider: {provider}")

        setattr(self._settings, provider_key_map[provider], api_key)
        self.logger.info(f"{provider.title()} API key updated")
        self._save_to_config()

    def set_model(self, provider: str, model: str):
        """Generic model setter for any provider"""
        provider_model_map = {
            "google": "google_model",
            "groq": "groq_model",
            "openai": "openai_model",
            "anthropic": "anthropic_model",
            "xai": "xai_model",
            "meta": "meta_model",
            "mistral": "mistral_model",
            "microsoft": "microsoft_model",
            "azure": "microsoft_model",
            "amazon": "amazon_model",
            "cohere": "cohere_model",
            "deepseek": "deepseek_model",
            "together": "together_model",
            "minimax": "minimax_model",
            "zhipuai": "zhipuai_model",
            "ollama": "ollama_model",
            "openrouter": "openrouter_model"
        }

        if provider not in provider_model_map:
            raise ValueError(f"Unknown provider: {provider}")

        setattr(self._settings, provider_model_map[provider], model)
        self.logger.info(f"{provider.title()} model set to: {model}")
        self._save_to_config()
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """Generic API key getter for any provider"""
        provider_key_map = {
            "google": "google_api_key",
            "groq": "groq_api_key",
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
            "xai": "xai_api_key",
            "meta": "meta_api_key",
            "mistral": "mistral_api_key",
            "microsoft": "microsoft_api_key",
            "azure": "microsoft_api_key",
            "amazon": "amazon_access_key",
            "cohere": "cohere_api_key",
            "deepseek": "deepseek_api_key",
            "together": "together_api_key",
            "minimax": "minimax_api_key",
            "zhipuai": "zhipuai_api_key",
            "openrouter": "openrouter_api_key"
        }
        
        if provider not in provider_key_map:
            raise ValueError(f"Unknown provider: {provider}")
        
        return getattr(self._settings, provider_key_map[provider])
    
    def get_model(self, provider: str) -> str:
        """Generic model getter for any provider"""
        provider_model_map = {
            "google": "google_model",
            "groq": "groq_model",
            "openai": "openai_model",
            "anthropic": "anthropic_model",
            "xai": "xai_model",
            "meta": "meta_model",
            "mistral": "mistral_model",
            "microsoft": "microsoft_model",
            "azure": "microsoft_model",
            "amazon": "amazon_model",
            "cohere": "cohere_model",
            "deepseek": "deepseek_model",
            "together": "together_model",
            "minimax": "minimax_model",
            "zhipuai": "zhipuai_model",
            "ollama": "ollama_model",
            "openrouter": "openrouter_model"
        }
        
        if provider not in provider_model_map:
            raise ValueError(f"Unknown provider: {provider}")
        
        return getattr(self._settings, provider_model_map[provider])


# Global settings manager instance
_settings_manager: Optional[SettingsManager] = None


def get_settings_manager() -> SettingsManager:
    """Get global settings manager instance"""
    global _settings_manager
    
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    
    return _settings_manager
