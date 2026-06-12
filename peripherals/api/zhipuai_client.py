"""
ZhipuAI (Z.AI) LLM Client Adapter

Implements the BaseLLM interface for ZhipuAI API.
Uses OpenAI-compatible API structure.

Installation:
    pip install openai

Environment Variables:
    ZHIPUAI_API_KEY: API key for ZhipuAI API (get from https://z.ai)
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse,
    ModelInfo, ResponseFormat, _estimate_cost
)

# Import OpenAI SDK for ZhipuAI compatibility
try:
    from openai import OpenAI, AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class ZhipuAILLMClient(BaseLLM):
    """
    ZhipuAI (Z.AI) LLM client using OpenAI-compatible SDK.
    
    This adapter implements the BaseLLM interface for ZhipuAI's GLM models,
    handling parameter mapping and response normalization.
    
    Parameter Mapping (OpenAI-compatible):
        - max_tokens -> max_tokens
        - temperature -> temperature (0.0 - 1.0)
        - top_p -> top_p (0.0 - 1.0)
        - stop_sequences -> stop (list of strings)
        - system_instruction -> messages[0] with role="system"
    
    Usage:
        client = ZhipuAILLMClient(api_key="your-api-key")
        
        # Simple generation
        response = client.generate("Explain quantum computing")
        print(response.content)
        
        # With configuration
        config = GenerationConfig(
            max_tokens=1000,
            temperature=0.7,
            system_instruction="You are a helpful assistant."
        )
        response = client.generate("Explain AI", config=config)
    
    Latest Models (as of 2026):
        - glm-5.1: New latest flagship model for long-horizon agentic tasks (April 2026)
        - glm-5: 744B total parameters (40B active) - Frontier model with strong reasoning
        - glm-5-turbo: Efficient variant for tool invocation and agent tasks
        - glm-4.7: Advanced coding capabilities model
        - glm-4: 9B parameters multilingual model
    """
    
    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """Initialize ZhipuAI client"""
        super().__init__(api_key, **kwargs)
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI SDK is required for ZhipuAI client. "
                "Install with: pip install openai"
            )
        
        # ZhipuAI API configuration
        self._base_url = kwargs.get('base_url', 'https://api.z.ai/v1')
        self._timeout = kwargs.get('timeout', 60)
        
        self._initialize_client()
    
    @property
    def provider_type(self) -> ProviderType:
        """Return the provider type"""
        return ProviderType.ZHIPUAI
    
    @property
    def default_model(self) -> str:
        """Return the default model for ZhipuAI"""
        return "glm-5.1"
    
    def _initialize_client(self) -> None:
        """Initialize the OpenAI-compatible client for ZhipuAI"""
        api_key = self._api_key or os.getenv('ZHIPUAI_API_KEY')
        if not api_key:
            raise ValueError(
                "ZhipuAI API key is required. Provide it as an argument or "
                "set the ZHIPUAI_API_KEY environment variable. "
                "Get your API key from https://z.ai"
            )
        
        self._client = OpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=self._timeout
        )
    
    def get_model_info(self, model: Optional[str] = None) -> ModelInfo:
        """Get information about a specific model"""
        model = model or self.default_model
        
        # Model information mapping based on official Z.AI documentation
        model_info_map = {
            "glm-5.1": ModelInfo(
                id="glm-5.1",
                name="GLM-5.1",
                provider="zhipuai",
                context_window=200000,
                max_output_tokens=8192,
                supports_vision=False,
                supports_streaming=True,
                description="New flagship model for long-horizon agentic tasks (April 2026)",
                capabilities=["text-generation", "coding", "reasoning", "agentic", "long-horizon"]
            ),
            "glm-5": ModelInfo(
                id="glm-5",
                name="GLM-5",
                provider="zhipuai",
                context_window=128000,
                max_output_tokens=8192,
                supports_vision=False,
                supports_streaming=True,
                description="744B total parameters (40B active) - Frontier model with strong reasoning",
                capabilities=["text-generation", "coding", "reasoning", "agentic"]
            ),
            "glm-5-turbo": ModelInfo(
                id="glm-5-turbo",
                name="GLM-5-Turbo",
                provider="zhipuai",
                context_window=128000,
                max_output_tokens=4096,
                supports_vision=False,
                supports_streaming=True,
                description="Efficient variant optimized for tool invocation and agent tasks",
                capabilities=["text-generation", "tool-use", "agentic"]
            ),
            "glm-4.7": ModelInfo(
                id="glm-4.7",
                name="GLM-4.7",
                provider="zhipuai",
                context_window=128000,
                max_output_tokens=4096,
                supports_vision=False,
                supports_streaming=True,
                description="Advanced coding capabilities model",
                capabilities=["text-generation", "coding", "reasoning"]
            ),
            "glm-4": ModelInfo(
                id="glm-4",
                name="GLM-4",
                provider="zhipuai",
                context_window=128000,
                max_output_tokens=4096,
                supports_vision=False,
                supports_streaming=True,
                description="9B parameters multilingual model with exceptional reasoning",
                capabilities=["text-generation", "multilingual", "reasoning"]
            )
        }
        
        return model_info_map.get(model, ModelInfo(
            id=model,
            name=model,
            provider="zhipuai",
            context_window=128000,
            max_output_tokens=4096,
            supports_vision=False,
            supports_streaming=True,
            description="ZhipuAI GLM model"
        ))
    
    def generate(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate a response from ZhipuAI model.
        
        Args:
            prompt: The input prompt
            config: Generation configuration
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse with generated content
        """
        if not self._client:
            self._initialize_client()
        
        # Use provided config or create default
        config = config or GenerationConfig()
        model = kwargs.get('model', self.default_model)
        
        # Build messages
        messages = []
        
        # Add system instruction if provided
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})
        
        # Add user prompt
        messages.append({"role": "user", "content": prompt})
        
        # Map parameters
        generation_params = {
            "model": model,
            "messages": messages,
            "max_tokens": config.max_tokens or 4096,
            "temperature": config.temperature,
        }
        
        # Add optional parameters
        if config.top_p is not None:
            generation_params["top_p"] = config.top_p
        
        if config.stop_sequences:
            generation_params["stop"] = config.stop_sequences
        
        if config.seed is not None:
            generation_params["seed"] = config.seed
        
        # Add extra parameters (e.g., thinking={"type": "enabled"} for GLM-5)
        generation_params.update(config.extra_params)
        
        start_time = time.time()
        
        try:
            # Make API call
            response = self._client.chat.completions.create(**generation_params)
            
            # Calculate latency
            latency = time.time() - start_time
            
            # Extract response data
            choice = response.choices[0]
            content = choice.message.content or ""
            
            # Get reasoning content if available (GLM-5 feature)
            reasoning_content = getattr(choice.message, 'reasoning_content', None)
            
            # Get token usage
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else None
            completion_tokens = usage.completion_tokens if usage else None
            total_tokens = usage.total_tokens if usage else None
            
            # Estimate cost (ZhipuAI pricing - approximate)
            cost = _estimate_cost("zhipuai", model, prompt_tokens or 0, completion_tokens or 0)

            return LLMResponse(
                success=True,
                content=content,
                model=model,
                provider="zhipuai",
                tokens_used=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                latency=latency,
                finish_reason=choice.finish_reason,
                raw_response=response
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model,
                provider="zhipuai",
                error=str(e),
                latency=time.time() - start_time
            )
    
    def generate_stream(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> Iterator[str]:
        """
        Generate a streaming response from ZhipuAI model.
        
        Args:
            prompt: The input prompt
            config: Generation configuration
            **kwargs: Additional parameters
            
        Yields:
            Chunks of generated content
        """
        if not self._client:
            self._initialize_client()
        
        config = config or GenerationConfig()
        model = kwargs.get('model', self.default_model)
        
        # Build messages
        messages = []
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        # Map parameters
        generation_params = {
            "model": model,
            "messages": messages,
            "max_tokens": config.max_tokens or 4096,
            "temperature": config.temperature,
            "stream": True,
        }
        
        if config.top_p is not None:
            generation_params["top_p"] = config.top_p
        
        if config.stop_sequences:
            generation_params["stop"] = config.stop_sequences
        
        generation_params.update(config.extra_params)
        
        try:
            stream = self._client.chat.completions.create(**generation_params)
            
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
                    # Yield reasoning content if available (GLM-5 feature)
                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        yield f"[Thinking: {delta.reasoning_content}]"
                        
        except Exception as e:
            yield f"[Error: {str(e)}]"
    
    async def generate_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate an asynchronous response from ZhipuAI model.
        
        Args:
            prompt: The input prompt
            config: Generation configuration
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse with generated content
        """
        if not hasattr(self, '_async_client'):
            api_key = self._api_key or os.getenv('ZHIPUAI_API_KEY')
            self._async_client = AsyncOpenAI(
                api_key=api_key,
                base_url=self._base_url,
                timeout=self._timeout
            )
        
        config = config or GenerationConfig()
        model = kwargs.get('model', self.default_model)
        
        # Build messages
        messages = []
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        # Map parameters
        generation_params = {
            "model": model,
            "messages": messages,
            "max_tokens": config.max_tokens or 4096,
            "temperature": config.temperature,
        }
        
        if config.top_p is not None:
            generation_params["top_p"] = config.top_p
        
        if config.stop_sequences:
            generation_params["stop"] = config.stop_sequences
        
        generation_params.update(config.extra_params)
        
        start_time = time.time()
        
        try:
            response = await self._async_client.chat.completions.create(**generation_params)
            
            latency = time.time() - start_time
            
            choice = response.choices[0]
            content = choice.message.content or ""
            
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else None
            completion_tokens = usage.completion_tokens if usage else None
            total_tokens = usage.total_tokens if usage else None
            
            cost = _estimate_cost("zhipuai", model, prompt_tokens or 0, completion_tokens or 0)

            return LLMResponse(
                success=True,
                content=content,
                model=model,
                provider="zhipuai",
                tokens_used=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                latency=latency,
                finish_reason=choice.finish_reason,
                raw_response=response
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model,
                provider="zhipuai",
                error=str(e),
                latency=time.time() - start_time
            )

    async def generate_stream_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Asynchronously generate a streaming response from ZhipuAI model."""
        if not hasattr(self, '_async_client'):
            api_key = self._api_key or os.getenv('ZHIPUAI_API_KEY')
            self._async_client = AsyncOpenAI(
                api_key=api_key,
                base_url=self._base_url,
                timeout=self._timeout
            )
        
        config = config or GenerationConfig()
        model = kwargs.get('model', self.default_model)
        
        messages = []
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        generation_params = {
            "model": model,
            "messages": messages,
            "max_tokens": config.max_tokens or 4096,
            "temperature": config.temperature,
            "stream": True,
        }
        
        if config.top_p is not None:
            generation_params["top_p"] = config.top_p
        if config.stop_sequences:
            generation_params["stop"] = config.stop_sequences
        generation_params.update(config.extra_params)
        
        try:
            stream = await self._async_client.chat.completions.create(**generation_params)
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
        except Exception as e:
            yield f"[Error: {str(e)}]"
    
    def list_models(self) -> List[ModelInfo]:
        """List available ZhipuAI models."""
        return [
            self.get_model_info("glm-5.1"),
            self.get_model_info("glm-5"),
            self.get_model_info("glm-5-turbo"),
            self.get_model_info("glm-4.7"),
            self.get_model_info("glm-4"),
        ]
    
