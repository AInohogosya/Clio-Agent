"""
xAI Grok LLM Client Adapter

Implements the BaseLLM interface for xAI's Grok API.

Installation:
    pip install openai

Environment Variables:
    XAI_API_KEY: API key for xAI API
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
    XAI_AVAILABLE = True
except ImportError:
    XAI_AVAILABLE = False


class XAILLMClient(BaseLLM):
    """
    xAI Grok LLM client using the OpenAI-compatible API.
    
    This adapter implements the BaseLLM interface for xAI's Grok models,
    handling parameter mapping and response normalization.
    
    Parameter Mapping:
        - max_tokens -> max_tokens
        - temperature -> temperature
        - top_p -> top_p
        - stop_sequences -> stop
    
    Usage:
        client = XAILLMClient(api_key="your-api-key")
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - grok-4.3: New most intelligent model with 1M context, native video (April 2026)
        - grok-4: Grok 4 - Advanced reasoning with 131K context
        - grok-4-fast: Grok 4 Fast - Low latency with 131K context
        - grok-4-thinking: Grok 4 Thinking - Extended reasoning with 131K context
    """

    DEFAULT_MODEL = "grok-4.3"

    MODEL_CONTEXT_WINDOWS = {
        "grok-4.3": 1_000_000,
        "grok-4": 131_072,
        "grok-4-fast": 131_072,
        "grok-4-thinking": 131_072,
    }

    VISION_MODELS = {"grok-4.3", "grok-4", "grok-4-fast"}

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("XAI_API_KEY")
        self._config = kwargs
        self._client = None
        
        if not XAI_AVAILABLE:
            raise ImportError(
                "openai package is required. "
                "Install with: pip install openai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.XAI
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key:
            raise ValueError(
                "xAI API key is required. Provide it as an argument or "
                "set the XAI_API_KEY environment variable."
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url="https://api.x.ai/v1"
        )

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        xai_config = {}
        
        if config.max_tokens is not None:
            xai_config["max_tokens"] = config.max_tokens
        
        if config.temperature is not None:
            xai_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            xai_config["top_p"] = config.top_p
        
        if config.stop_sequences:
            xai_config["stop"] = config.stop_sequences
        
        if config.system_instruction:
            xai_config["messages"] = [{"role": "system", "content": config.system_instruction}]
        
        if config.response_format == ResponseFormat.JSON:
            xai_config["response_format"] = {"type": "json_object"}
        
        return xai_config

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
            xai_config = self._convert_config(config)
            
            messages = [{"role": "user", "content": prompt}]
            if config and config.system_instruction:
                messages.insert(0, {"role": "system", "content": config.system_instruction})
            
            response = self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                **xai_config
            )
            
            latency = time.time() - start_time
            choice = response.choices[0]
            
            return LLMResponse(
                success=True,
                content=choice.message.content or "",
                model=model_id,
                provider="xai",
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
                provider="xai",
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
        xai_config = self._convert_config(config)
        
        messages = [{"role": "user", "content": prompt}]
        if config and config.system_instruction:
            messages.insert(0, {"role": "system", "content": config.system_instruction})
        
        response = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=True,
            **xai_config
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def list_models(self) -> List[ModelInfo]:
        """Return empty list - model validation happens at API call time"""
        return []

