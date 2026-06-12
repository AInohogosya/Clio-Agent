"""
Meta Llama LLM Client Adapter

Implements the BaseLLM interface for Meta's Llama API.

Installation:
    pip install openai

Environment Variables:
    META_API_KEY: API key for Meta Llama API
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse, 
    ModelInfo, ResponseFormat, _estimate_cost
)

try:
    from openai import OpenAI
    META_AVAILABLE = True
except ImportError:
    META_AVAILABLE = False


class MetaLLMClient(BaseLLM):
    """
    Meta Llama LLM client using the OpenAI-compatible Llama API.
    
    This adapter implements the BaseLLM interface for Meta's Llama models,
    handling parameter mapping and response normalization.
    
    Usage:
        client = MetaLLMClient(api_key="your-api-key")
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - llama-4-scout-17b-16e-instruct: New Llama 4 Scout - 10M context, multimodal (April 2025)
        - llama-4-maverick-17b-128e-instruct: New Llama 4 Maverick - 1M context, 128 experts (April 2025)
        - llama-3.3-70b-instruct: Llama 3.3 70B - Text only, strong performance
    """

    DEFAULT_MODEL = "llama-4-scout-17b-16e-instruct"

    MODEL_CONTEXT_WINDOWS = {
        "llama-4-scout-17b-16e-instruct": 10_000_000,
        "llama-4-maverick-17b-128e-instruct": 1_000_000,
        "llama-3.3-70b-instruct": 128_000,
        "llama-3.1-405b-instruct": 128_000,
        "llama-3.1-70b-instruct": 128_000,
        "llama-3.1-8b-instruct": 128_000,
    }

    VISION_MODELS = {"llama-4-scout-17b-16e-instruct", "llama-4-maverick-17b-128e-instruct"}

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("META_API_KEY")
        self._config = kwargs
        self._client = None
        
        if not META_AVAILABLE:
            raise ImportError(
                "openai package is required. "
                "Install with: pip install openai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.META
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key:
            raise ValueError(
                "Meta API key is required. Provide it as an argument or "
                "set the META_API_KEY environment variable."
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url="https://api.meta.ai/v1"
        )

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        meta_config = {}
        
        if config.max_tokens is not None:
            meta_config["max_tokens"] = config.max_tokens
        
        if config.temperature is not None:
            meta_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            meta_config["top_p"] = config.top_p
        
        if config.stop_sequences:
            meta_config["stop"] = config.stop_sequences
        
        if config.response_format == ResponseFormat.JSON:
            meta_config["response_format"] = {"type": "json_object"}
        
        return meta_config

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
            meta_config = self._convert_config(config)
            
            messages = [{"role": "user", "content": prompt}]
            if config and config.system_instruction:
                messages.insert(0, {"role": "system", "content": config.system_instruction})
            
            response = self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                **meta_config
            )
            
            latency = time.time() - start_time
            choice = response.choices[0]
            
            return LLMResponse(
                success=True,
                content=choice.message.content or "",
                model=model_id,
                provider="meta",
                tokens_used=response.usage.total_tokens,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                latency=latency,
                finish_reason=choice.finish_reason,
                raw_response=response
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model or self.default_model,
                provider="meta",
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
        meta_config = self._convert_config(config)
        
        messages = [{"role": "user", "content": prompt}]
        if config and config.system_instruction:
            messages.insert(0, {"role": "system", "content": config.system_instruction})
        
        response = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=True,
            **meta_config
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def list_models(self) -> List[ModelInfo]:
        """Return empty list - model validation happens at API call time"""
        return []
