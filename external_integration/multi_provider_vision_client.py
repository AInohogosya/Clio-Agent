"""
Multi-Provider Vision API Client for AI Agent System
Supports 13+ AI providers while maintaining current architecture
"""

import io
import time
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

try:
    from PIL import Image
except ImportError:
    raise ImportError("PIL (Pillow) is required for Vision API client")

from ..utils.exceptions import ValidationError
from ..utils.logger import get_logger
from ..utils.config import load_config
from .ollama_provider import SimpleOllamaProvider
from .openrouter_provider import OpenRouterProvider

# Import API clients with error handling
try:
    import sys
    import os
    sys.path.append(os.path.join(os.path.dirname(__file__), '../../..'))
    from api import LLMFactory, ProviderType, GenerationConfig, LLMResponse
    API_AVAILABLE = True
except ImportError:
    API_AVAILABLE = False

# Import SDK installer
try:
    from ..utils.sdk_installer import create_installer
    SDK_INSTALLER_AVAILABLE = True
except ImportError:
    SDK_INSTALLER_AVAILABLE = False


class APIProvider(Enum):
    """Supported API provider identifiers for type-safe provider specification"""
    OLLAMA = "ollama"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    XAI = "xai"
    META = "meta"
    MISTRAL = "mistral"
    AZURE = "azure"
    AMAZON = "amazon"
    COHERE = "cohere"
    DEEPSEEK = "deepseek"
    GROQ = "groq"
    TOGETHER = "together"
    MINIMAX = "minimax"
    ZHIPUAI = "zhipuai"


@dataclass
class APIResponse:
    """API response structure"""
    success: bool
    content: str
    model: str
    provider: str
    tokens_used: Optional[int] = None
    cost: Optional[float] = None
    latency: Optional[float] = None
    error: Optional[str] = None


@dataclass
class APIRequest:
    """API request structure"""
    prompt: str
    image_data: Optional[bytes] = None
    image_format: str = "PNG"
    max_tokens: int = 5000
    temperature: float = 1.0
    model: Optional[str] = None
    provider: Optional[str] = None
    system_instruction: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None


class MultiProviderVisionAPIClient:
    """Multi-provider Vision API Client with explicit provider routing"""

    # Known provider identifiers for validation
    KNOWN_PROVIDERS = {
        "ollama", "google", "openrouter", "openai", "anthropic", "xai",
        "meta", "mistral", "microsoft", "azure", "amazon", "cohere",
        "deepseek", "groq", "together", "minimax", "zhipuai"
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None, auto_install_sdks: bool = False):
        # Handle config properly
        if config is None:
            try:
                config = load_config().api.__dict__
            except Exception:
                config = {}
        elif hasattr(config, 'api'):
            # It's a Config object, get the api dict
            config = config.api.__dict__

        self.config = config or {}
        self.logger = get_logger(__name__)

        # Initialize Ollama provider (always available)
        self.ollama_provider = SimpleOllamaProvider()

        # Initialize OpenRouter provider (always available, needs API key)
        self.openrouter_provider = OpenRouterProvider(self.config)

        # Cloud provider API clients (initialized lazily)
        self.api_clients = {}
        if API_AVAILABLE:
            self._initialize_api_clients()

        self.logger.info("Multi-provider Vision API client initialized with %d cloud providers" % len(self.api_clients))

    def _initialize_api_clients(self):
        """Initialize all available API clients with API keys from settings"""
        from ..utils.settings_manager import get_settings_manager
        settings = get_settings_manager()

        provider_mappings = {
            'google': (ProviderType.GOOGLE, settings.get_google_api_key),
            'openai': (ProviderType.OPENAI, settings.get_openai_api_key),
            'anthropic': (ProviderType.ANTHROPIC, settings.get_anthropic_api_key),
            'xai': (ProviderType.XAI, settings.get_xai_api_key),
            'meta': (ProviderType.META, settings.get_meta_api_key),
            'mistral': (ProviderType.MISTRAL, settings.get_mistral_api_key),
            'microsoft': (ProviderType.MICROSOFT, settings.get_microsoft_api_key),
            'azure': (ProviderType.MICROSOFT, settings.get_microsoft_api_key),
            'amazon': (ProviderType.AMAZON, lambda: settings.get_amazon_access_key()),
            'cohere': (ProviderType.COHERE, settings.get_cohere_api_key),
            'deepseek': (ProviderType.DEEPSEEK, settings.get_deepseek_api_key),
            'groq': (ProviderType.GROQ, settings.get_groq_api_key),
            'together': (ProviderType.TOGETHER, settings.get_together_api_key),
            'minimax': (ProviderType.MINIMAX, settings.get_minimax_api_key),
            'zhipuai': (ProviderType.ZHIPUAI, settings.get_zhipuai_api_key),
        }

        for provider_name, (provider_type, api_key_getter) in provider_mappings.items():
            try:
                api_key = api_key_getter()
                if not api_key:
                    self.logger.debug("Skipping %s - no API key configured" % provider_name)
                    continue

                client = LLMFactory.create(provider_type, api_key=api_key)
                self.api_clients[provider_name] = client
                self.logger.info("Initialized %s client" % provider_name)
            except ValueError as e:
                self.logger.warning("Provider %s not available: %s" % (provider_name, e))
            except Exception as e:
                self.logger.warning("Failed to initialize %s client: %s" % (provider_name, e))

    def _try_create_client(self, provider: str):
        """Try to create an API client on-the-fly using stored API key."""
        provider_type_map = {
            'google': ProviderType.GOOGLE,
            'openai': ProviderType.OPENAI,
            'anthropic': ProviderType.ANTHROPIC,
            'xai': ProviderType.XAI,
            'meta': ProviderType.META,
            'mistral': ProviderType.MISTRAL,
            'microsoft': ProviderType.MICROSOFT,
            'azure': ProviderType.MICROSOFT,
            'amazon': ProviderType.AMAZON,
            'cohere': ProviderType.COHERE,
            'deepseek': ProviderType.DEEPSEEK,
            'groq': ProviderType.GROQ,
            'together': ProviderType.TOGETHER,
            'minimax': ProviderType.MINIMAX,
            'zhipuai': ProviderType.ZHIPUAI,
        }
        if provider not in provider_type_map:
            return None
        try:
            from ..utils.settings_manager import get_settings_manager
            from api import LLMFactory
            mgr = get_settings_manager()
            api_key = mgr.get_api_key(provider)
            if not api_key:
                return None
            return LLMFactory.create(provider_type_map[provider], api_key=api_key)
        except Exception as e:
            self.logger.warning("Fallback client creation failed for %s: %s" % (provider, e))
            return None

    def _handle_api_request_with_client(self, request: APIRequest, provider: str,
                                         client: Any, start_time: float) -> APIResponse:
        """Handle API request with a provided client (for fallback/custom models)."""
        try:
            from api import GenerationConfig
            config = GenerationConfig(
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system_instruction=request.system_instruction
            )
            prompt = request.prompt
            if request.image_data:
                prompt = "[IMAGE: %s data] %s" % (request.image_format, request.prompt)
            response = client.generate(prompt, config)
            return APIResponse(
                success=response.success,
                content=response.content,
                model=response.model or request.model or 'unknown',
                provider=provider,
                tokens_used=response.tokens_used,
                cost=response.cost,
                latency=time.time() - start_time,
                error=response.error
            )
        except Exception as e:
            return APIResponse(
                success=False,
                content="",
                model=request.model or 'unknown',
                provider=provider,
                error=str(e),
                latency=time.time() - start_time
            )

    def generate_response(self, request: APIRequest) -> APIResponse:
        """Generate response using specified provider with explicit routing"""
        start_time = time.time()

        try:
            provider = request.provider
            if not provider:
                raise ValidationError("No provider specified in request")

            # Validate that the provider is a known identifier
            if provider not in self.KNOWN_PROVIDERS:
                raise ValidationError("Unknown provider '%s'. Valid providers: %s" % (
                    provider, ", ".join(sorted(self.KNOWN_PROVIDERS))))

            # Route to the correct provider handler
            # Each provider is handled individually - no fallthrough logic
            if provider == 'ollama':
                return self._handle_ollama_request(request, start_time)

            if provider == 'openrouter':
                return self._handle_openrouter_request(request, start_time)

            # All other providers use the multi-provider API
            if API_AVAILABLE and provider in self.api_clients:
                return self._handle_api_request(request, provider, start_time)

            # Fallback: try to create a client on-the-fly using stored API key
            # This handles "Custom Model" entries where the SDK might not be
            # pre-initialized but the user has a valid API key
            if API_AVAILABLE:
                fallback_client = self._try_create_client(provider)
                if fallback_client:
                    return self._handle_api_request_with_client(
                        request, provider, fallback_client, start_time)

            # Provider not available
            raise ValidationError(
                "Provider '%s' is not configured or available. "
                "Please check your API key or select a different provider." % provider)

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error("Error generating response: %s" % e)
            return APIResponse(
                success=False,
                content="",
                model=request.model or "unknown",
                provider=request.provider or "unknown",
                error=str(e),
                latency=time.time() - start_time
            )

    def _handle_ollama_request(self, request: APIRequest, start_time: float) -> APIResponse:
        """Handle Ollama requests"""
        try:
            ollama_response = self.ollama_provider.chat(
                prompt=request.prompt,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                system_instructions=request.system_instruction
            )

            return APIResponse(
                success=ollama_response.success,
                content=ollama_response.content,
                model=ollama_response.model,
                provider='ollama',
                error=ollama_response.error,
                latency=time.time() - start_time
            )
        except Exception as e:
            return APIResponse(
                success=False,
                content="",
                model=request.model or 'unknown',
                provider='ollama',
                error=str(e),
                latency=time.time() - start_time
            )

    def _handle_openrouter_request(self, request: APIRequest, start_time: float) -> APIResponse:
        """Handle OpenRouter requests"""
        try:
            openrouter_response = self.openrouter_provider.chat(
                prompt=request.prompt,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                system_instructions=request.system_instruction,
                image_data=request.image_data,
                image_format=request.image_format,
                response_format=request.response_format,
            )

            return APIResponse(
                success=openrouter_response.success,
                content=openrouter_response.content,
                model=openrouter_response.model,
                provider='openrouter',
                error=openrouter_response.error,
                tokens_used=openrouter_response.tokens_used,
                cost=openrouter_response.cost,
                latency=time.time() - start_time
            )
        except Exception as e:
            return APIResponse(
                success=False,
                content="",
                model=request.model or 'unknown',
                provider='openrouter',
                error=str(e),
                latency=time.time() - start_time
            )

    def _handle_api_request(self, request: APIRequest, provider: str, start_time: float) -> APIResponse:
        """Handle multi-provider API requests"""
        try:
            client = self.api_clients[provider]

            config = GenerationConfig(
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system_instruction=request.system_instruction
            )

            prompt_with_image = request.prompt
            if request.image_data:
                prompt_with_image = "[IMAGE: %s data] %s" % (request.image_format, request.prompt)

            response = client.generate(prompt_with_image, config)

            return APIResponse(
                success=response.success,
                content=response.content,
                model=response.model or request.model or 'unknown',
                provider=provider,
                tokens_used=response.tokens_used,
                cost=response.cost,
                latency=time.time() - start_time,
                error=response.error
            )
        except Exception as e:
            return APIResponse(
                success=False,
                content="",
                model=request.model or 'unknown',
                provider=provider,
                error=str(e),
                latency=time.time() - start_time
            )

    def get_available_providers(self) -> List[str]:
        """Get list of actually available providers"""
        available = []
        if self.ollama_provider.is_available():
            available.append('ollama')
        if API_AVAILABLE:
            available.extend(list(self.api_clients.keys()))
        return available

    def install_missing_sdks(self, providers: Optional[List[str]] = None, interactive: bool = True) -> Dict[str, bool]:
        """Install missing SDKs for specified providers"""
        if not SDK_INSTALLER_AVAILABLE:
            return {}
        try:
            installer = create_installer(auto_install=False)
            if providers is None:
                return {}
            return installer.install_missing_sdks(providers, interactive)
        except Exception:
            return {}

    def show_sdk_status(self, providers: Optional[List[str]] = None):
        """Show SDK installation status"""
        if not SDK_INSTALLER_AVAILABLE:
            return
        try:
            installer = create_installer(auto_install=False)
            installer.show_provider_status(providers or [])
        except Exception:
            pass

    def get_provider_models(self, provider: str) -> List[str]:
        """Get available models for a provider"""
        if provider == 'ollama':
            # Return empty list - Ollama models are managed locally
            return []
        if provider == 'openrouter':
            return self.openrouter_provider.get_available_models()
        if API_AVAILABLE and provider in self.api_clients:
            try:
                client = self.api_clients[provider]
                return [model.name for model in client.list_models()]
            except Exception as e:
                self.logger.warning("Failed to get models for %s: %s" % (provider, e))
                return []
        return []


# Factory function for backward compatibility
def create_vision_api_client(config: Optional[Dict[str, Any]] = None, auto_install_sdks: bool = False) -> MultiProviderVisionAPIClient:
    """Create a vision API client instance"""
    return MultiProviderVisionAPIClient(config, auto_install_sdks=auto_install_sdks)


# Legacy compatibility
VisionAPIClient = MultiProviderVisionAPIClient
