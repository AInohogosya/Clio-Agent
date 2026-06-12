"""
OpenAI LLM Client Adapter

Implements the BaseLLM interface for OpenAI API.
Uses the official OpenAI Python SDK.

Installation:
    pip install openai

Environment Variables:
    OPENAI_API_KEY: API key for OpenAI API
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse,
    ModelInfo, ResponseFormat, _estimate_cost
)

# Import OpenAI SDK
try:
    from openai import OpenAI, AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class OpenAILLMClient(BaseLLM):
    """
    OpenAI LLM client using the official OpenAI Python SDK.
    
    This adapter implements the BaseLLM interface for OpenAI models,
    handling parameter mapping and response normalization.
    
    Parameter Mapping (direct - OpenAI SDK uses standard naming):
        - max_tokens -> max_tokens (or max_completion_tokens for o1 models)
        - temperature -> temperature (0.0 - 2.0)
        - top_p -> top_p (0.0 - 1.0)
        - stop_sequences -> stop (list of strings)
        - seed -> seed
    
    Usage:
        client = OpenAIClient(api_key="your-key")
        response = client.generate("Explain quantum computing", config=config)
    
    Latest Models (as of 2026):
        - gpt-5.5: New flagship model with advanced reasoning (April 2026)
        - gpt-5.5-pro: New version with smarter, more precise responses (April 2026)
        - gpt-5.4: Multimodal flagship model
        - gpt-5.4-mini: Cost-optimized mini model
        - gpt-5.4-nano: Ultra-lightweight nano model
        - gpt-5: Previous intelligent reasoning model
        - gpt-5-mini: Near-frontier intelligence for cost-sensitive workloads
        - gpt-5-nano: Fastest, most cost-efficient version
        
        Reasoning models (released April 2025):
        - o3: Most powerful reasoning model
        - o4-mini: Cost-efficient reasoning model
        - o3-mini: Lightweight reasoning model
    """

    # Default model to use
    DEFAULT_MODEL = "gpt-5.5"

    # Model context windows (approximate)
    MODEL_CONTEXT_WINDOWS = {
        "gpt-5.5": 1_048_576,
        "gpt-5.5-pro": 1_048_576,
        "gpt-5.4": 1_048_576,
        "gpt-5.4-mini": 1_048_576,
        "gpt-5.4-nano": 1_048_576,
        "gpt-5": 1_048_576,
        "gpt-5-mini": 1_048_576,
        "gpt-5-nano": 1_048_576,
        "o3": 200_000,
        "o4-mini": 200_000,
        "o3-mini": 200_000,
    }

    # Max output tokens per model
    MODEL_MAX_TOKENS = {
        "gpt-5.5": 32_768,
        "gpt-5.5-pro": 32_768,
        "gpt-5.4": 32_768,
        "gpt-5.4-mini": 16_384,
        "gpt-5.4-nano": 8_192,
        "gpt-5": 16_384,
        "gpt-5-mini": 16_384,
        "gpt-5-nano": 8_192,
        "o3": 32_768,
        "o4-mini": 16_384,
        "o3-mini": 16_384,
    }

    # Vision-capable models
    VISION_MODELS = {
        "gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-5", "gpt-5-mini", "gpt-5-nano",
    }

    # Reasoning models (use different parameter handling)
    # Note: These were released April 2025, not April 2026
    REASONING_MODELS = {"o3", "o3-mini", "o4-mini"}

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """
        Initialize OpenAI client.
        
        Args:
            api_key: OpenAI API key. If not provided, uses OPENAI_API_KEY
                    environment variable.
            **kwargs: Additional configuration options
                - base_url: Custom API base URL
                - timeout: Request timeout in seconds
                - max_retries: Number of retries for failed requests
                - organization: OpenAI organization ID
                - project: OpenAI project ID
        """
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._config = kwargs
        self._client = None
        self._async_client = None
        
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "openai package is required. "
                "Install with: pip install openai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OPENAI
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        """Initialize the OpenAI client"""
        if not self._api_key:
            raise ValueError(
                "OpenAI API key is required. Provide it as an argument or "
                "set the OPENAI_API_KEY environment variable."
            )
        
        client_kwargs = {
            "api_key": self._api_key,
            "timeout": self._config.get("timeout", 60),
            "max_retries": self._config.get("max_retries", 2),
        }
        
        # Optional parameters
        if "base_url" in self._config:
            client_kwargs["base_url"] = self._config["base_url"]
        if "organization" in self._config:
            client_kwargs["organization"] = self._config["organization"]
        if "project" in self._config:
            client_kwargs["project"] = self._config["project"]
        
        self._client = OpenAI(**client_kwargs)
        self._async_client = AsyncOpenAI(**client_kwargs)

    def _is_reasoning_model(self, model: str) -> bool:
        """Check if model is a reasoning model (o3, o3-mini, etc.)"""
        return any(r in model for r in self.REASONING_MODELS)

    def _build_messages(
        self, 
        prompt: str, 
        system_instruction: Optional[str] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Build the messages array for the chat completion API.
        
        Handles vision input if image_data is provided.
        """
        messages = []
        
        # Add system message if provided (not for reasoning models)
        if system_instruction and not self._is_reasoning_model(kwargs.get("model", "")):
            messages.append({
                "role": "system",
                "content": system_instruction
            })
        
        # Build user message
        image_data = kwargs.get("image_data")
        if image_data:
            # Vision input
            import base64
            image_format = kwargs.get("image_format", "png")
            base64_image = base64.b64encode(image_data).decode('utf-8')
            
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{image_format};base64,{base64_image}"
                        }
                    }
                ]
            })
        else:
            # Text only
            messages.append({
                "role": "user",
                "content": prompt
            })
        
        return messages

    def _build_params(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        config: Optional[GenerationConfig],
        stream: bool = False
    ) -> Dict[str, Any]:
        """Build API parameters from unified config"""
        params = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        
        if config is None:
            return params
        
        is_reasoning = self._is_reasoning_model(model)
        
        # Handle max_tokens vs max_completion_tokens
        if config.max_tokens is not None:
            if is_reasoning:
                # Reasoning models use max_completion_tokens
                params["max_completion_tokens"] = config.max_tokens
            else:
                params["max_tokens"] = config.max_tokens
        
        # Temperature (not supported by reasoning models)
        if not is_reasoning and config.temperature is not None:
            params["temperature"] = config.temperature
        
        # Top P
        if config.top_p is not None:
            params["top_p"] = config.top_p
        
        # Stop sequences
        if config.stop_sequences:
            params["stop"] = config.stop_sequences
        
        # Seed for reproducibility
        if config.seed is not None:
            params["seed"] = config.seed
        
        # Response format for JSON mode
        if config.response_format == ResponseFormat.JSON:
            params["response_format"] = {"type": "json_object"}
        
        # Apply extra parameters
        params.update(config.extra_params)
        
        return params

    def generate(
        self, 
        prompt: str, 
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate a response using OpenAI API.
        
        Args:
            prompt: Input text prompt
            config: Generation configuration
            model: Model ID to use (defaults to gpt-5.4)
            **kwargs: Additional arguments
                - image_data: bytes - Image data for vision models
                - image_format: str - Image format (png, jpeg, etc.)
        
        Returns:
            LLMResponse with generated content
        """
        start_time = time.time()
        
        try:
            self._ensure_initialized()
            
            model_id = model or self.default_model
            
            # Build messages
            system_instruction = config.system_instruction if config else None
            messages = self._build_messages(
                prompt, 
                system_instruction=system_instruction,
                model=model_id,
                **kwargs
            )
            
            # Build parameters
            params = self._build_params(model_id, messages, config, stream=False)
            
            # Make API call
            response = self._client.chat.completions.create(**params)
            
            # Extract content
            content = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason
            
            # Extract token usage
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else None
            completion_tokens = usage.completion_tokens if usage else None
            total_tokens = usage.total_tokens if usage else None
            
            # Calculate cost
            cost = _estimate_cost(
                "openai", model_id,
                prompt_tokens or 0, completion_tokens or 0
            ) if prompt_tokens and completion_tokens else None
            
            latency = time.time() - start_time
            
            return LLMResponse(
                success=True,
                content=content,
                model=model_id,
                provider=self.provider_type.value,
                tokens_used=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                latency=latency,
                finish_reason=finish_reason,
                raw_response=response
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model or self.default_model,
                provider=self.provider_type.value,
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
        """
        Generate a streaming response.
        
        Yields text chunks as they are generated.
        """
        try:
            self._ensure_initialized()
            
            model_id = model or self.default_model
            
            # Build messages
            system_instruction = config.system_instruction if config else None
            messages = self._build_messages(
                prompt,
                system_instruction=system_instruction,
                model=model_id,
                **kwargs
            )
            
            # Build parameters with streaming enabled
            params = self._build_params(model_id, messages, config, stream=True)
            
            # Make streaming API call
            for chunk in self._client.chat.completions.create(**params):
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
                    
        except Exception as e:
            yield f"[Error: {str(e)}]"

    async def generate_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Asynchronously generate a response using OpenAI API.
        """
        start_time = time.time()
        
        try:
            self._ensure_initialized()
            
            model_id = model or self.default_model
            
            # Build messages
            system_instruction = config.system_instruction if config else None
            messages = self._build_messages(
                prompt,
                system_instruction=system_instruction,
                model=model_id,
                **kwargs
            )
            
            # Build parameters
            params = self._build_params(model_id, messages, config, stream=False)
            
            # Make async API call
            response = await self._async_client.chat.completions.create(**params)
            
            # Extract content
            content = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason
            
            # Extract token usage
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else None
            completion_tokens = usage.completion_tokens if usage else None
            total_tokens = usage.total_tokens if usage else None
            
            # Calculate cost
            cost = _estimate_cost(
                "openai", model_id,
                prompt_tokens or 0, completion_tokens or 0
            ) if prompt_tokens and completion_tokens else None
            
            latency = time.time() - start_time
            
            return LLMResponse(
                success=True,
                content=content,
                model=model_id,
                provider=self.provider_type.value,
                tokens_used=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                latency=latency,
                finish_reason=finish_reason,
                raw_response=response
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model or self.default_model,
                provider=self.provider_type.value,
                error=str(e),
                latency=time.time() - start_time
            )

    async def generate_stream_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Asynchronously generate a streaming response.
        """
        self._ensure_initialized()
        
        model_id = model or self.default_model
        
        # Build messages
        system_instruction = config.system_instruction if config else None
        messages = self._build_messages(
            prompt,
            system_instruction=system_instruction,
            model=model_id,
            **kwargs
        )
        
        # Build parameters with streaming enabled
        params = self._build_params(model_id, messages, config, stream=True)
        
        # Make streaming API call
        stream = await self._async_client.chat.completions.create(**params)
        
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def list_models(self) -> List[ModelInfo]:
        """
        List available OpenAI models from the API.

        Returns:
            List of ModelInfo objects from the OpenAI API
        """
        try:
            self._ensure_initialized()
            response = self._client.models.list()
            models = []
            for m in response.data:
                model_id = m.id
                # Filter to only include chat/completion models
                if not any(prefix in model_id for prefix in ["gpt", "o1", "o3", "o4"]):
                    continue
                # Skip fine-tuned and embedding models
                if "embed" in model_id or "tts" in model_id or "dall" in model_id:
                    continue
                context_window = self.MODEL_CONTEXT_WINDOWS.get(model_id, 128000)
                max_tokens = self.MODEL_MAX_TOKENS.get(model_id, 4096)
                vision = model_id in self.VISION_MODELS
                models.append(ModelInfo(
                    id=model_id,
                    name=model_id,
                    provider=self.provider_type.value,
                    context_window=context_window,
                    max_output_tokens=max_tokens,
                    supports_vision=vision,
                    supports_streaming=True,
                    description="OpenAI model: %s" % model_id
                ))
            models.sort(key=lambda m: m.id)
            return models
        except Exception:
            return []

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """Get information about a specific model"""
        models = self.list_models()
        for model in models:
            if model.id == model_id:
                return model
        
        # Handle dated versions (e.g., gpt-4o-2024-08-06)
        for model in models:
            if model_id.startswith(model.id):
                return model
        
        return None

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """
        Count tokens using tiktoken if available.
        
        Falls back to character-based estimate if tiktoken not installed.
        """
        try:
            import tiktoken
            
            model_id = model or self.default_model
            
            # Map model to encoding
            encoding_map = {
                "gpt-5.5": "o200k_base",
                "gpt-5.4": "o200k_base",
                "gpt-5.4-mini": "o200k_base",
                "gpt-5.4-nano": "o200k_base",
                "gpt-5": "o200k_base",
                "gpt-4.1": "o200k_base",
                "gpt-5-codex": "o200k_base",
                "gpt-5.3-codex": "o200k_base",
                "gpt-5.2-codex": "o200k_base",
                "gpt-5.1-codex": "o200k_base",
                "gpt-5.1-codex-max": "o200k_base",
                "gpt-5.1-codex-mini": "o200k_base",
                "gpt-oss-20b": "o200k_base",
                "gpt-oss-120b": "o200k_base",
                "gpt-5.2": "o200k_base",
                "gpt-5.2-pro": "o200k_base",
                "o3-pro": "o200k_base",
                "o4-mini": "o200k_base",
                "gpt-4.1-nano": "o200k_base",
                "computer-use-preview": "o200k_base",
                "gpt-4o-search-preview": "o200k_base",
                "o3-mini": "o200k_base",
                "omni-moderation-v1": "o200k_base",
                "gpt-5.1": "o200k_base",
                "gpt-5-pro": "o200k_base",
                "o3": "o200k_base",
                "gpt-4.1-mini": "o200k_base",
                "o1-pro": "o200k_base",
                "gpt-4o-mini-search": "o200k_base",
                "gpt-4.5-preview": "o200k_base",
                "o1": "o200k_base",
                "o1-mini": "o200k_base",
                "gpt-4o": "o200k_base",
                "gpt-4o-mini": "o200k_base",
                "gpt-4": "cl100k_base",
            }
            
            encoding_name = encoding_map.get(model_id, "cl100k_base")
            
            try:
                encoding = tiktoken.get_encoding(encoding_name)
            except (KeyError, ValueError):
                encoding = tiktoken.encoding_for_model(model_id)
            
            return len(encoding.encode(text))
            
        except ImportError:
            # Fallback: rough estimate (4 chars per token for English)
            return len(text) // 4
        except Exception:
            return len(text) // 4

    def is_available(self) -> bool:
        """Check if OpenAI provider is available"""
        if not OPENAI_AVAILABLE:
            return False
        return self._api_key is not None and len(self._api_key) > 0


# Register with factory
from .base import LLMFactory
LLMFactory.register(ProviderType.OPENAI, OpenAILLMClient)
