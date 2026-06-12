"""
Together AI LLM Client Adapter

Implements the BaseLLM interface for Together AI's API.

Installation:
    pip install openai

Environment Variables:
    TOGETHER_API_KEY: API key for Together AI
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
    TOGETHER_AVAILABLE = True
except ImportError:
    TOGETHER_AVAILABLE = False


class TogetherLLMClient(BaseLLM):
    """
    Together AI LLM client using the OpenAI-compatible API.
    
    Usage:
        client = TogetherLLMClient(api_key="your-api-key")
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - meta-llama/Llama-4-Scout-17B-Instruct: New Llama 4 Scout (April 2025)
        - meta-llama/Llama-4-Maverick-17B-Instruct: New Llama 4 Maverick (April 2025)
        - MiniMax/MiniMax-M2.7: New MiniMax M2.7 with agent teams (April 2026)
        - moonshotai/Kimi-K2.6: New Kimi K2.6 with 256K context
        - deepseek-ai/DeepSeek-V4-Pro: New DeepSeek V4 Pro with 1M context (April 2026)
        - Qwen/Qwen3.5-397B-A17B: New Qwen 3.5 397B A17B
        - Meta-Llama-3.3-70B-Instruct: Llama 3.3 70B
    """

    DEFAULT_MODEL = "meta-llama/Llama-4-Scout-17B-Instruct"
    
    MODEL_CONTEXT_WINDOWS = {
        "meta-llama/Llama-4-Scout-17B-Instruct": 128_000,
        "meta-llama/Llama-4-Maverick-17B-Instruct": 128_000,
        "MiniMax/MiniMax-M2.7": 200_000,
        "moonshotai/Kimi-K2.6": 256_000,
        "deepseek-ai/DeepSeek-V4-Pro": 1_000_000,
        "Qwen/Qwen3.5-397B-A17B": 128_000,
        "Meta-Llama-3.3-70B-Instruct": 128_000,
    }
    
    VISION_MODELS = set()

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("TOGETHER_API_KEY")
        self._config = kwargs
        self._client = None
        
        if not TOGETHER_AVAILABLE:
            raise ImportError(
                "openai package is required. "
                "Install with: pip install openai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.TOGETHER
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key:
            raise ValueError(
                "Together AI API key is required. Provide it as an argument or "
                "set the TOGETHER_API_KEY environment variable."
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url="https://api.together.xyz/v1"
        )

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        together_config = {}
        
        if config.max_tokens is not None:
            together_config["max_tokens"] = config.max_tokens
        
        if config.temperature is not None:
            together_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            together_config["top_p"] = config.top_p
        
        if config.stop_sequences:
            together_config["stop"] = config.stop_sequences
        
        if config.response_format == ResponseFormat.JSON:
            together_config["response_format"] = {"type": "json_object"}
        
        return together_config

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
            together_config = self._convert_config(config)
            
            messages = [{"role": "user", "content": prompt}]
            if config and config.system_instruction:
                messages.insert(0, {"role": "system", "content": config.system_instruction})
            
            response = self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                **together_config
            )
            
            latency = time.time() - start_time
            choice = response.choices[0]
            
            return LLMResponse(
                success=True,
                content=choice.message.content or "",
                model=model_id,
                provider="together",
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
                provider="together",
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
        together_config = self._convert_config(config)
        
        messages = [{"role": "user", "content": prompt}]
        if config and config.system_instruction:
            messages.insert(0, {"role": "system", "content": config.system_instruction})
        
        response = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=True,
            **together_config
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def list_models(self) -> List[ModelInfo]:
        models = [
            ModelInfo(
                id="meta-llama/Llama-4-Scout-17B-Instruct",
                name="Llama 4 Scout 17B",
                provider="together",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="New Llama 4 Scout with 10M context (April 2025)"
            ),
            ModelInfo(
                id="meta-llama/Llama-4-Maverick-17B-Instruct",
                name="Llama 4 Maverick 17B",
                provider="together",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="New Llama 4 Maverick with 128 experts (April 2025)"
            ),
            ModelInfo(
                id="MiniMax/MiniMax-M2.7",
                name="MiniMax M2.7",
                provider="together",
                context_window=200_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="New MiniMax M2.7 with agent teams (April 2026)"
            ),
            ModelInfo(
                id="moonshotai/Kimi-K2.6",
                name="Kimi K2.6",
                provider="together",
                context_window=256_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="New Moonshot Kimi K2.6 with 256K context"
            ),
            ModelInfo(
                id="deepseek-ai/DeepSeek-V4-Pro",
                name="DeepSeek V4 Pro",
                provider="together",
                context_window=1_000_000,
                max_output_tokens=8_192,
                supports_vision=False,
                description="New DeepSeek V4 Pro with 1M context (April 2026)"
            ),
            ModelInfo(
                id="Qwen/Qwen3.5-397B-A17B",
                name="Qwen 3.5 397B A17B",
                provider="together",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="New Qwen 3.5 flagship model"
            ),
            ModelInfo(
                id="Meta-Llama-3.3-70B-Instruct",
                name="Llama 3.3 70B",
                provider="together",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="High performance open model"
            ),
        ]

