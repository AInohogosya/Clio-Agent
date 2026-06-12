"""
Groq LLM Client Adapter

Implements the BaseLLM interface for Groq's API.

Installation:
    pip install openai

Environment Variables:
    GROQ_API_KEY: API key for Groq
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
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class GroqLLMClient(BaseLLM):
    """
    Groq LLM client using the OpenAI-compatible API.
    
    Usage:
        client = GroqLLMClient(api_key="your-api-key")
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - openai/gpt-oss-120b: OpenAI GPT-OSS 120B - Open source powerhouse (New, April 2026)
        - qwen/qwen3-32b: New Qwen3 32B - Preview model
        - meta-llama/llama-4-scout-17b-16e-instruct: Llama 4 Scout - Preview model
        - llama-3.3-70b-versatile: Llama 3.3 70B - Production high performance
        - llama-3.1-8b-instant: Llama 3.1 8B - Fast, efficient
        - openai/gpt-oss-20b: OpenAI GPT-OSS 20B - Efficient open source
        
        Deprecated models (still available but will be shut down):
        - meta-llama/llama-4-maverick-17b-128e-instruct: Deprecated March 2026
        - moonshotai/kimi-k2-instruct-0905: Deprecated April 2026
        - gemma2-9b-it: Deprecated October 2025
    """

    DEFAULT_MODEL = "openai/gpt-oss-120b"
    
    MODEL_CONTEXT_WINDOWS = {
        "openai/gpt-oss-120b": 128_000,
        "openai/gpt-oss-20b": 128_000,
        "qwen/qwen3-32b": 128_000,
        "meta-llama/llama-4-scout-17b-16e-instruct": 128_000,
        "llama-3.3-70b-versatile": 128_000,
        "llama-3.1-8b-instant": 128_000,
        "meta-llama/llama-4-maverick-17b-128e-instruct": 128_000,
        "moonshotai/kimi-k2-instruct-0905": 256_000,
        "gemma2-9b-it": 8_192,
    }
    
    VISION_MODELS = set()

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("GROQ_API_KEY")
        self._config = kwargs
        self._client = None
        
        if not GROQ_AVAILABLE:
            raise ImportError(
                "openai package is required. "
                "Install with: pip install openai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GROQ
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key:
            raise ValueError(
                "Groq API key is required. Provide it as an argument or "
                "set the GROQ_API_KEY environment variable."
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url="https://api.groq.com/openai/v1"
        )

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        groq_config = {}
        
        if config.max_tokens is not None:
            groq_config["max_tokens"] = config.max_tokens
        
        if config.temperature is not None:
            groq_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            groq_config["top_p"] = config.top_p
        
        if config.stop_sequences:
            groq_config["stop"] = config.stop_sequences
        
        if config.response_format == ResponseFormat.JSON:
            groq_config["response_format"] = {"type": "json_object"}
        
        return groq_config

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
            groq_config = self._convert_config(config)
            
            messages = [{"role": "user", "content": prompt}]
            if config and config.system_instruction:
                messages.insert(0, {"role": "system", "content": config.system_instruction})
            
            response = self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                **groq_config
            )
            
            latency = time.time() - start_time
            choice = response.choices[0]
            
            return LLMResponse(
                success=True,
                content=choice.message.content or "",
                model=model_id,
                provider="groq",
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
                provider="groq",
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
        groq_config = self._convert_config(config)
        
        messages = [{"role": "user", "content": prompt}]
        if config and config.system_instruction:
            messages.insert(0, {"role": "system", "content": config.system_instruction})
        
        response = self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=True,
            **groq_config
        )
        
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(
                id="openai/gpt-oss-120b",
                name="GPT-OSS 120B",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="New OpenAI open source 120B parameter model (April 2026)",
                capabilities=["streaming", "function_calling"]
            ),
            ModelInfo(
                id="qwen/qwen3-32b",
                name="Qwen3 32B",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="New Qwen3 32B model (Preview)",
                capabilities=["streaming"]
            ),
            ModelInfo(
                id="meta-llama/llama-4-scout-17b-16e-instruct",
                name="Llama 4 Scout 17B",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="Llama 4 Scout Preview with advanced reasoning",
                capabilities=["streaming"]
            ),
            ModelInfo(
                id="llama-3.3-70b-versatile",
                name="Llama 3.3 70B Versatile",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="Production high performance with 128K context"
            ),
            ModelInfo(
                id="llama-3.1-8b-instant",
                name="Llama 3.1 8B Instant",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="Fast, efficient for simple tasks"
            ),
            ModelInfo(
                id="openai/gpt-oss-20b",
                name="GPT-OSS 20B",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="OpenAI efficient open source 20B parameter model",
                capabilities=["streaming", "function_calling"]
            ),
            # Deprecated models (kept for backward compatibility)
            ModelInfo(
                id="meta-llama/llama-4-maverick-17b-128e-instruct",
                name="Llama 4 Maverick 17B (Deprecated)",
                provider="groq",
                context_window=128_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="DEPRECATED: Use openai/gpt-oss-120b instead (shut down March 2026)"
            ),
            ModelInfo(
                id="moonshotai/kimi-k2-instruct-0905",
                name="Kimi K2 0905 (Deprecated)",
                provider="groq",
                context_window=256_000,
                max_output_tokens=32_768,
                supports_vision=False,
                supports_streaming=True,
                description="DEPRECATED: Use openai/gpt-oss-120b instead (shut down April 2026)"
            ),
            ModelInfo(
                id="gemma2-9b-it",
                name="Gemma 2 9B (Deprecated)",
                provider="groq",
                context_window=8_192,
                max_output_tokens=8_192,
                supports_vision=False,
                supports_streaming=True,
                description="DEPRECATED: Use llama-3.1-8b-instant instead (shut down October 2025)"
            ),
        ]
