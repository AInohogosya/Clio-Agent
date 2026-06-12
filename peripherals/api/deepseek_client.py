"""
DeepSeek LLM Client Adapter

Implements the BaseLLM interface for DeepSeek's API.

Installation:
    pip install openai

Environment Variables:
    DEEPSEEK_API_KEY: API key for DeepSeek
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
    DEEPSEEK_AVAILABLE = True
except ImportError:
    DEEPSEEK_AVAILABLE = False


class DeepSeekLLMClient(BaseLLM):
    """
    DeepSeek LLM client using the OpenAI-compatible API.
    
    Usage:
        client = DeepSeekLLMClient(api_key="your-api-key")
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - deepseek-v4-pro: New DeepSeek V4 Pro - 1.6T params (49B active), 1M context (April 2026)
        - deepseek-v4-flash: New DeepSeek V4 Flash - 284B params (13B active), 1M context (April 2026)
        - deepseek-chat: Deprecated alias for v4-flash (non-thinking mode)
        - deepseek-reasoner: Deprecated alias for v4-flash (thinking mode)
    """

    DEFAULT_MODEL = "deepseek-v4-pro"
    
    MODEL_CONTEXT_WINDOWS = {
        "deepseek-v4-pro": 1_000_000,
        "deepseek-v4-flash": 1_000_000,
        "deepseek-chat": 1_000_000,
        "deepseek-reasoner": 1_000_000,
    }
    
    VISION_MODELS = set()

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self._config = kwargs
        self._client = None
        
        if not DEEPSEEK_AVAILABLE:
            raise ImportError(
                "openai package is required. "
                "Install with: pip install openai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.DEEPSEEK
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key:
            raise ValueError(
                "DeepSeek API key is required. Provide it as an argument or "
                "set the DEEPSEEK_API_KEY environment variable."
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url="https://api.deepseek.com"
        )

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        deepseek_config = {}
        
        if config.max_tokens is not None:
            deepseek_config["max_tokens"] = config.max_tokens
        
        if config.temperature is not None:
            deepseek_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            deepseek_config["top_p"] = config.top_p
        
        if config.stop_sequences:
            deepseek_config["stop"] = config.stop_sequences
        
        if config.response_format == ResponseFormat.JSON:
            deepseek_config["response_format"] = {"type": "json_object"}
        
        return deepseek_config

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
            deepseek_config = self._convert_config(config)
            
            messages = [{"role": "user", "content": prompt}]
            if config and config.system_instruction:
                messages.insert(0, {"role": "system", "content": config.system_instruction})
            
            response = self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                **deepseek_config
            )
            
            latency = time.time() - start_time
            choice = response.choices[0]
            
            return LLMResponse(
                success=True,
                content=choice.message.content or "",
                model=model_id,
                provider="deepseek",
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
                provider="deepseek",
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
        deepseek_config = self._convert_config(config)
        
        messages = [{"role": "user", "content": prompt}]
        if config and config.system_instruction:
            messages.insert(0, {"role": "system", "content": config.system_instruction})
        
        response = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=True,
            **deepseek_config
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(
                id="deepseek-v4-pro",
                name="DeepSeek V4 Pro",
                provider="deepseek",
                context_window=1_000_000,
                max_output_tokens=8_192,
                supports_vision=False,
                description="New V4 Pro: 1.6T params (49B active), 1M context, hybrid thinking (April 2026)"
            ),
            ModelInfo(
                id="deepseek-v4-flash",
                name="DeepSeek V4 Flash",
                provider="deepseek",
                context_window=1_000_000,
                max_output_tokens=8_192,
                supports_vision=False,
                description="New V4 Flash: 284B params (13B active), 1M context, fast & efficient (April 2026)"
            ),
            ModelInfo(
                id="deepseek-chat",
                name="DeepSeek Chat (Deprecated)",
                provider="deepseek",
                context_window=131_072,
                max_output_tokens=8_192,
                supports_vision=False,
                description="Alias for v4-flash non-thinking mode (deprecated 2026/07/24)"
            ),
            ModelInfo(
                id="deepseek-reasoner",
                name="DeepSeek Reasoner (Deprecated)",
                provider="deepseek",
                context_window=131_072,
                max_output_tokens=8_192,
                supports_vision=False,
                description="Alias for v4-flash thinking mode (deprecated 2026/07/24)"
            ),
        ]
