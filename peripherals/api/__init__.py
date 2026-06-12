"""
VEXIS Unified API Package

A unified interface for multiple LLM providers including Google Gemini and OpenAI.

Usage:
    from api import LLMFactory, ProviderType, GenerationConfig
    
    # Create a client
    client = LLMFactory.create(ProviderType.GOOGLE, api_key="your-key")
    
    # Generate text
    response = client.generate("Hello, how are you?")
    print(response.content)

Providers:
    - Google Gemini (google-genai SDK)
    - OpenAI (openai SDK)
    - Anthropic (anthropic SDK)
    - xAI Grok (OpenAI-compatible)
    - Meta Llama (OpenAI-compatible)
    - Mistral AI (mistralai SDK)
    - Microsoft Azure OpenAI (openai SDK)
    - Amazon Bedrock (boto3)
    - Cohere (cohere SDK)
    - DeepSeek (OpenAI-compatible)
    - Groq (OpenAI-compatible)
    - Together AI (OpenAI-compatible)
    - MiniMax (OpenAI-compatible)
    
Adding New Providers:
    1. Create a new file (e.g., api/anthropic_client.py)
    2. Implement BaseLLM interface
    3. Register with LLMFactory
"""

from .base import (
    BaseLLM,
    LLMFactory,
    ProviderType,
    GenerationConfig,
    LLMResponse,
    ModelInfo,
    ResponseFormat,
    _estimate_cost,
)

# Import specific clients (will raise ImportError if dependencies missing)
try:
    from .google_client import GoogleLLMClient
except ImportError as e:
    GoogleLLMClient = None  # type: ignore

try:
    from .openai_client import OpenAILLMClient
except ImportError as e:
    OpenAILLMClient = None  # type: ignore

try:
    from .anthropic_client import AnthropicLLMClient
except ImportError as e:
    AnthropicLLMClient = None  # type: ignore

try:
    from .xai_client import XAILLMClient
except ImportError as e:
    XAILLMClient = None  # type: ignore

try:
    from .meta_client import MetaLLMClient
except ImportError as e:
    MetaLLMClient = None  # type: ignore

try:
    from .mistral_client import MistralLLMClient
except ImportError as e:
    MistralLLMClient = None  # type: ignore

try:
    from .microsoft_client import MicrosoftLLMClient
except ImportError as e:
    MicrosoftLLMClient = None  # type: ignore

try:
    from .amazon_client import AmazonLLMClient
except ImportError as e:
    AmazonLLMClient = None  # type: ignore

try:
    from .cohere_client import CohereLLMClient
except ImportError as e:
    CohereLLMClient = None  # type: ignore

try:
    from .deepseek_client import DeepSeekLLMClient
except ImportError as e:
    DeepSeekLLMClient = None  # type: ignore

try:
    from .groq_client import GroqLLMClient
except ImportError as e:
    GroqLLMClient = None  # type: ignore

try:
    from .together_client import TogetherLLMClient
except ImportError as e:
    TogetherLLMClient = None  # type: ignore

try:
    from .minimax_client import MiniMaxLLMClient
except ImportError as e:
    MiniMaxLLMClient = None  # type: ignore

try:
    from .zhipuai_client import ZhipuAILLMClient
except ImportError as e:
    ZhipuAILLMClient = None  # type: ignore


# Register providers with factory
if GoogleLLMClient:
    LLMFactory.register(ProviderType.GOOGLE, GoogleLLMClient)

if OpenAILLMClient:
    LLMFactory.register(ProviderType.OPENAI, OpenAILLMClient)

if AnthropicLLMClient:
    LLMFactory.register(ProviderType.ANTHROPIC, AnthropicLLMClient)

if XAILLMClient:
    LLMFactory.register(ProviderType.XAI, XAILLMClient)

if MetaLLMClient:
    LLMFactory.register(ProviderType.META, MetaLLMClient)

if MistralLLMClient:
    LLMFactory.register(ProviderType.MISTRAL, MistralLLMClient)

if MicrosoftLLMClient:
    LLMFactory.register(ProviderType.MICROSOFT, MicrosoftLLMClient)

if AmazonLLMClient:
    LLMFactory.register(ProviderType.AMAZON, AmazonLLMClient)

if CohereLLMClient:
    LLMFactory.register(ProviderType.COHERE, CohereLLMClient)

if DeepSeekLLMClient:
    LLMFactory.register(ProviderType.DEEPSEEK, DeepSeekLLMClient)

if GroqLLMClient:
    LLMFactory.register(ProviderType.GROQ, GroqLLMClient)

if TogetherLLMClient:
    LLMFactory.register(ProviderType.TOGETHER, TogetherLLMClient)

if MiniMaxLLMClient:
    LLMFactory.register(ProviderType.MINIMAX, MiniMaxLLMClient)

if ZhipuAILLMClient:
    LLMFactory.register(ProviderType.ZHIPUAI, ZhipuAILLMClient)


__all__ = [
    # Base classes
    "BaseLLM",
    "LLMFactory",
    "ProviderType",
    "GenerationConfig",
    "LLMResponse",
    "ModelInfo",
    "ResponseFormat",
    "_estimate_cost",
    # Client implementations
    "GoogleLLMClient",
    "OpenAILLMClient",
    "AnthropicLLMClient",
    "XAILLMClient",
    "MetaLLMClient",
    "MistralLLMClient",
    "MicrosoftLLMClient",
    "AmazonLLMClient",
    "CohereLLMClient",
    "DeepSeekLLMClient",
    "GroqLLMClient",
    "TogetherLLMClient",
    "MiniMaxLLMClient",
    "ZhipuAILLMClient",
]


def create_client(
    provider: str,
    api_key: str,
    **kwargs
) -> BaseLLM:
    """
    Convenience function to create a client by provider name.
    """
    provider_map = {
        "google": ProviderType.GOOGLE,
        "gemini": ProviderType.GOOGLE,
        "openai": ProviderType.OPENAI,
        "anthropic": ProviderType.ANTHROPIC,
        "claude": ProviderType.ANTHROPIC,
        "xai": ProviderType.XAI,
        "grok": ProviderType.XAI,
        "meta": ProviderType.META,
        "llama": ProviderType.META,
        "mistral": ProviderType.MISTRAL,
        "microsoft": ProviderType.MICROSOFT,
        "azure": ProviderType.MICROSOFT,
        "amazon": ProviderType.AMAZON,
        "aws": ProviderType.AMAZON,
        "bedrock": ProviderType.AMAZON,
        "cohere": ProviderType.COHERE,
        "deepseek": ProviderType.DEEPSEEK,
        "groq": ProviderType.GROQ,
        "together": ProviderType.TOGETHER,
        "minimax": ProviderType.MINIMAX,
        "zhipuai": ProviderType.ZHIPUAI,
        "zhipu": ProviderType.ZHIPUAI,
        "glm": ProviderType.ZHIPUAI,
    }
    
    return LLMFactory.create(provider_map[provider], api_key=api_key, **kwargs)


def get_available_providers() -> list:
    """Get list of available provider names"""
    return [
        "google", "openai", "anthropic", "xai", "meta", 
        "mistral", "microsoft", "amazon", "cohere", 
        "deepseek", "groq", "together", "minimax", "zhipuai"
    ]
