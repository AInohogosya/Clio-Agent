"""
Base Abstract Class for LLM Providers

This module defines the common interface that all LLM API clients must implement,
enabling unified access to different providers (Google Gemini, OpenAI, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union, Iterator, AsyncIterator
from enum import Enum


class ProviderType(Enum):
    """Supported LLM provider types"""
    GOOGLE = "google"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    GROQ = "groq"
    XAI = "xai"
    META = "meta"
    MISTRAL = "mistral"
    MICROSOFT = "microsoft"
    AMAZON = "amazon"
    COHERE = "cohere"
    DEEPSEEK = "deepseek"
    TOGETHER = "together"
    MINIMAX = "minimax"
    ZHIPUAI = "zhipuai"


class ResponseFormat(Enum):
    """Supported response formats"""
    TEXT = "text"
    JSON = "json"
    STREAM = "stream"


@dataclass
class GenerationConfig:
    """Unified generation configuration across all providers
    
    This class normalizes parameter names across different APIs:
    - OpenAI: max_tokens, temperature, top_p
    - Google: max_output_tokens, temperature, top_p
    """
    max_tokens: Optional[int] = None  # Unified: max_tokens (OpenAI) / max_output_tokens (Google)
    temperature: float = 1.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    seed: Optional[int] = None
    response_format: ResponseFormat = ResponseFormat.TEXT
    system_instruction: Optional[str] = None
    timeout: int = 60
    
    # Provider-specific extensions (passed through as-is)
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Unified response structure across all providers"""
    success: bool
    content: str
    model: str
    provider: str
    tokens_used: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost: Optional[float] = None
    latency: Optional[float] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Any] = None  # Provider-specific raw response for advanced access


@dataclass
class ModelInfo:
    """Model information structure"""
    id: str
    name: str
    provider: str
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: bool = False
    supports_streaming: bool = True
    description: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)


class BaseLLM(ABC):
    """
    Abstract Base Class for all LLM API clients.
    
    This class defines the common interface that all provider adapters must implement,
    enabling unified access to different LLM APIs through the adapter pattern.
    
    Usage:
        # Create a provider-specific client
        client = GoogleLLMClient(api_key="...")
        
        # Use the unified interface
        response = client.generate("Tell me a joke")
        print(response.content)
    
    Implementation Notes:
        - All methods must handle their own error handling and return LLMResponse
        - Parameter mapping should be done in the concrete implementation
        - Raw provider responses should be stored in raw_response field
    """

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """
        Initialize the LLM client.
        
        Args:
            api_key: API key for authentication
            **kwargs: Provider-specific configuration options
        """
        self._api_key = api_key
        self._config = kwargs
        self._client = None  # Provider-specific client instance
        
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type enum"""
        pass
    
    @property
    @abstractmethod
    def default_model(self) -> str:
        """Return the default model identifier for this provider"""
        pass
    
    @abstractmethod
    def _initialize_client(self) -> None:
        """Initialize the provider-specific client"""
        pass
    
    @abstractmethod
    def generate(
        self, 
        prompt: str, 
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate a response from the LLM.
        
        This is the primary method for non-streaming text generation.
        
        Args:
            prompt: The input prompt/text
            config: Generation configuration parameters
            **kwargs: Additional provider-specific parameters
            
        Returns:
            LLMResponse containing the generated content and metadata
        """
        pass
    
    @abstractmethod
    def generate_stream(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> Iterator[str]:
        """
        Generate a streaming response from the LLM.
        
        Yields text chunks as they become available.
        
        Args:
            prompt: The input prompt/text
            config: Generation configuration parameters
            **kwargs: Additional provider-specific parameters
            
        Yields:
            Text chunks (strings) as they are generated
        """
        pass
    
    async def generate_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Asynchronously generate a response from the LLM.
        
        Default implementation wraps sync method for providers without true async support.
        Override this method if the provider has native async support.
        """
        return self.generate(prompt, config, **kwargs)
    
    async def generate_stream_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Asynchronously generate a streaming response.
        
        Default implementation wraps sync method for providers without true async support.
        Override this method if the provider has native async streaming support.
        """
        for chunk in self.generate_stream(prompt, config, **kwargs):
            yield chunk
    
    @abstractmethod
    def list_models(self) -> List[ModelInfo]:
        """
        List available models for this provider.
        
        Returns:
            List of ModelInfo objects describing available models
        """
        pass
    
    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """
        Get information about a specific model.
        
        Default implementation searches through list_models().
        Override this method for more efficient lookup or custom logic.
        
        Args:
            model_id: The model identifier
            
        Returns:
            ModelInfo if found, None otherwise
        """
        for model in self.list_models():
            if model.id == model_id:
                return model
        return None
    
    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """
        Count tokens in the given text for a specific model.
        
        Default implementation provides a rough estimate (len(text) // 4).
        Override this method for more accurate token counting using provider SDKs.
        
        Args:
            text: The text to count tokens for
            model: Optional model identifier (uses default if not specified)
            
        Returns:
            Estimated token count
        """
        return len(text) // 4
    
    def is_available(self) -> bool:
        """
        Check if the provider is properly configured and available.
        
        Returns:
            True if the provider is ready to use
        """
        return self._api_key is not None and len(self._api_key) > 0
    
    def _ensure_initialized(self) -> None:
        """Ensure the client is initialized"""
        if self._client is None:
            self._initialize_client()


class LLMFactory:
    """
    Factory class for creating LLM clients based on provider type.
    
    Usage:
        client = LLMFactory.create(ProviderType.GOOGLE, api_key="...")
        response = client.generate("Hello!")
    """
    
    _providers: Dict[ProviderType, type] = {}
    
    @classmethod
    def register(cls, provider_type: ProviderType, provider_class: type):
        """Register a provider class with the factory"""
        cls._providers[provider_type] = provider_class
    
    @classmethod
    def create(
        cls, 
        provider_type: ProviderType, 
        api_key: Optional[str] = None,
        **kwargs
    ) -> BaseLLM:
        """
        Create an LLM client for the specified provider.
        
        Args:
            provider_type: The provider type enum
            api_key: API key for authentication
            **kwargs: Provider-specific configuration
            
        Returns:
            Configured LLM client instance
            
        Raises:
            ValueError: If provider type is not registered
        """
        if provider_type not in cls._providers:
            raise ValueError(f"Provider {provider_type} not registered. "
                           f"Available: {list(cls._providers.keys())}")
        
        provider_class = cls._providers[provider_type]
        return provider_class(api_key=api_key, **kwargs)
    
    @classmethod
    def available_providers(cls) -> List[ProviderType]:
        """Get list of registered provider types"""
        return list(cls._providers.keys())


def _estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """
    Estimate cost for API usage based on provider and model.
    
    This is a utility function that provides approximate pricing.
    Actual costs may vary based on pricing tier and region.
    
    Args:
        provider: Provider name
        model: Model identifier
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        
    Returns:
        Estimated cost in USD, or None if pricing unknown
    """
    # Pricing per 1M tokens (input, output)
    pricing = {
        "google": {
            "gemini-3.1-pro": (2.50, 15.0),  # Latest pricing
            "gemini-3-flash": (0.125, 0.375),
            "gemini-2.5-flash": (0.075, 0.30),
            "gemini-2.5-pro": (1.25, 10.0),
        },
        "openai": {
            "gpt-5.4": (5.00, 20.0),  # Latest GPT-5 pricing
            "gpt-5.4-pro": (7.50, 30.0),  # Professional tier
            "gpt-5.4-mini": (0.50, 2.00),  # Cost-optimized
            "gpt-5.4-nano": (0.25, 1.00),  # Ultra-lightweight
            "gpt-5-mini": (0.50, 2.00),
        },
        "anthropic": {
            "claude-opus-4.6": (15.0, 75.0),  # Latest Claude
            "claude-sonnet-4.6": (3.00, 15.0),  # Latest Sonnet
            "claude-sonnet-4.5": (3.0, 15.0),
        },
        "minimax": {
            "minimax-m2.7": (0.10, 0.10),
            "minimax-m2.5": (0.10, 0.10),
            "minimax-m2": (0.10, 0.10),
        },
        "zhipuai": {
            "glm-5.1": (0.50, 0.50),
            "glm-5": (0.50, 0.50),
            "glm-5-turbo": (0.25, 0.25),
            "glm-4.7": (0.25, 0.25),
            "glm-4": (0.25, 0.25),
        }
    }
    
    provider_pricing = pricing.get(provider.lower(), {})
    
    # Try exact match first, then prefix match (longest prefix first)
    if model in provider_pricing:
        input_price, output_price = provider_pricing[model]
    else:
        # Sort prefixes by length descending so longer/more-specific prefixes match first
        sorted_prefixes = sorted(provider_pricing.keys(), key=len, reverse=True)
        for model_prefix in sorted_prefixes:
            if model.startswith(model_prefix):
                input_price, output_price = provider_pricing[model_prefix]
                break
        else:
            return None
    
    prompt_cost = (prompt_tokens * input_price) / 1_000_000
    completion_cost = (completion_tokens * output_price) / 1_000_000
    
    return round(prompt_cost + completion_cost, 6)
