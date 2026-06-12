"""
Anthropic Claude LLM Client Adapter

Implements the BaseLLM interface for Anthropic's Claude API.

Installation:
    pip install anthropic

Environment Variables:
    ANTHROPIC_API_KEY: API key for Anthropic API
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse, 
    ModelInfo, ResponseFormat, _estimate_cost
)

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class AnthropicLLMClient(BaseLLM):
    """
    Anthropic Claude LLM client using the official Anthropic SDK.
    
    This adapter implements the BaseLLM interface for Claude models,
    handling parameter mapping and response normalization.
    
    Parameter Mapping:
        - max_tokens -> max_tokens
        - temperature -> temperature
        - top_p -> top_p
        - stop_sequences -> stop_sequences
    
    Usage:
        client = AnthropicLLMClient(api_key="your-api-key")
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - claude-opus-4-7: New most capable model for complex tasks (April 2026)
        - claude-opus-4-6: Claude Opus 4.6 - Advanced reasoning
        - claude-sonnet-4-6: Claude Sonnet 4.6 - Balanced performance
        - claude-sonnet-4-5: Claude Sonnet 4.5 - Efficient model (1M context)
        - claude-haiku-4-5: Claude Haiku 4.5 - Fast, cost-efficient with extended thinking (Oct 2025)
    """

    DEFAULT_MODEL = "claude-opus-4-7"

    MODEL_CONTEXT_WINDOWS = {
        "claude-opus-4-7": 200_000,
        "claude-opus-4-6": 200_000,
        "claude-sonnet-4-6": 200_000,
        "claude-sonnet-4-5": 1_000_000,
        "claude-haiku-4-5": 200_000,
    }

    VISION_MODELS = {
        "claude-opus-4-7", "claude-opus-4-6",
        "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-haiku-4-5",
    }

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._config = kwargs
        self._client = None
        
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package is required. "
                "Install with: pip install anthropic"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.ANTHROPIC
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key:
            raise ValueError(
                "Anthropic API key is required. Provide it as an argument or "
                "set the ANTHROPIC_API_KEY environment variable."
            )
        self._client = Anthropic(api_key=self._api_key)

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        anthropic_config = {}
        
        if config.max_tokens is not None:
            anthropic_config["max_tokens"] = config.max_tokens
        
        if config.temperature is not None:
            anthropic_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            anthropic_config["top_p"] = config.top_p
        
        if config.stop_sequences:
            anthropic_config["stop_sequences"] = config.stop_sequences
        
        if config.system_instruction:
            anthropic_config["system"] = config.system_instruction
        
        if config.response_format == ResponseFormat.JSON:
            anthropic_config["format"] = "json"
        
        return anthropic_config

    def generate(
        self, 
        prompt: str, 
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        start_time = time.time()
        
        try:
            self._ensure_initialized()
            
            model_id = model or self.default_model
            anthropic_config = self._convert_config(config)
            
            response = self._client.messages.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                **anthropic_config
            )
            
            latency = time.time() - start_time
            content = response.content[0].text if response.content else ""
            
            return LLMResponse(
                success=True,
                content=content,
                model=model_id,
                provider="anthropic",
                tokens_used=response.usage.input_tokens + response.usage.output_tokens,
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                latency=latency,
                finish_reason=response.stop_reason,
                raw_response=response
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model or self.default_model,
                provider="anthropic",
                error=str(e),
                latency=time.time() - start_time
            )

    def generate_stream(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Iterator[str]:
        self._ensure_initialized()
        
        model_id = model or self.default_model
        anthropic_config = self._convert_config(config)
        
        with self._client.messages.stream(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            **anthropic_config
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text

    def list_models(self) -> List[ModelInfo]:
        """Return empty list - model validation happens at API call time"""
        return []

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        self._ensure_initialized()
        model_id = model or self.default_model
        response = self._client.messages.count_tokens(
            model=model_id,
            messages=[{"role": "user", "content": text}],
        )
        return response.input_tokens
