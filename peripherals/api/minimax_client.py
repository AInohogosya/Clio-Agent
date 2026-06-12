"""
MiniMax LLM Client Adapter

Implements the BaseLLM interface for MiniMax API.
Uses OpenAI-compatible API structure.

Installation:
    pip install openai

Environment Variables:
    MINIMAX_API_KEY: API key for MiniMax API
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse,
    ModelInfo, ResponseFormat, _estimate_cost
)

# Import OpenAI SDK for MiniMax compatibility
try:
    from openai import OpenAI, AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class MiniMaxLLMClient(BaseLLM):
    """
    MiniMax LLM client using OpenAI-compatible SDK.
    
    This adapter implements the BaseLLM interface for MiniMax models,
    handling parameter mapping and response normalization.
    
    Parameter Mapping (OpenAI-compatible):
        - max_tokens -> max_tokens
        - temperature -> temperature (0.0 - 1.0)
        - top_p -> top_p (0.0 - 1.0)
        - stop_sequences -> stop (list of strings)
        - system_instruction -> messages[0] with role="system"
    
    Usage:
        client = MiniMaxLLMClient(api_key="your-api-key")
        
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
        - minimax-m2.7: Latest M2.7 model with agent teams and complex skills
        - minimax-m2.5: State-of-the-art productivity and coding model
        - minimax-m2: High efficiency coding and agentic model
    """
    
    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """Initialize MiniMax client"""
        super().__init__(api_key, **kwargs)
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI SDK is required for MiniMax client. "
                "Install with: pip install openai"
            )
        
        # MiniMax API configuration
        self._base_url = kwargs.get('base_url', 'https://api.minimax.chat/v1')
        self._timeout = kwargs.get('timeout', 60)
        
        self._initialize_client()
    
    @property
    def provider_type(self) -> ProviderType:
        """Return the provider type"""
        return ProviderType.MINIMAX
    
    @property
    def default_model(self) -> str:
        """Return the default model for MiniMax"""
        return "minimax-m2.7"
    
    def _initialize_client(self) -> None:
        """Initialize the OpenAI-compatible client for MiniMax"""
        api_key = self._api_key or os.getenv('MINIMAX_API_KEY')
        if not api_key:
            raise ValueError(
                "MiniMax API key is required. Provide it as an argument or "
                "set the MINIMAX_API_KEY environment variable."
            )
        
        self._client = OpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=self._timeout
        )
    
    def get_model_info(self, model: Optional[str] = None) -> ModelInfo:
        """Get information about a specific model"""
        model = model or self.default_model
        
        # Model information mapping
        model_info_map = {
            "minimax-m2.7": ModelInfo(
                id="minimax-m2.7",
                name="MiniMax M2.7",
                provider="minimax",
                context_window=200000,
                max_output_tokens=8192,
                supports_vision=False,
                supports_streaming=True,
                description="Latest M2.7 model with agent teams and complex skills",
                capabilities=["text-generation", "coding", "reasoning", "agent"]
            ),
            "minimax-m2.5": ModelInfo(
                id="minimax-m2.5",
                name="MiniMax M2.5",
                provider="minimax",
                context_window=200000,
                max_output_tokens=8192,
                supports_vision=False,
                supports_streaming=True,
                description="State-of-the-art productivity and coding model",
                capabilities=["text-generation", "coding", "reasoning"]
            ),
            "minimax-m2": ModelInfo(
                id="minimax-m2",
                name="MiniMax M2",
                provider="minimax",
                context_window=200000,
                max_output_tokens=8192,
                supports_vision=False,
                supports_streaming=True,
                description="High efficiency coding and agentic model",
                capabilities=["text-generation", "coding", "agentic"]
            )
        }
        
        return model_info_map.get(model, ModelInfo(
            id=model,
            name=model,
            provider="minimax",
            context_window=200000,
            max_output_tokens=8192,
            supports_vision=False,
            supports_streaming=True,
            description="MiniMax model"
        ))
    
    def generate(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate a response from MiniMax model.
        
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
        
        # Add extra parameters
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
            
            # Get token usage
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else None
            completion_tokens = usage.completion_tokens if usage else None
            total_tokens = usage.total_tokens if usage else None
            
            # Estimate cost (MiniMax pricing - approximate)
            cost = _estimate_cost("minimax", model, prompt_tokens or 0, completion_tokens or 0)

            return LLMResponse(
                success=True,
                content=content,
                model=model,
                provider="minimax",
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
                provider="minimax",
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
        Generate a streaming response from MiniMax model.
        
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
                        
        except Exception as e:
            yield f"[Error: {str(e)}]"
    
    async def generate_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate an asynchronous response from MiniMax model.
        
        Args:
            prompt: The input prompt
            config: Generation configuration
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse with generated content
        """
        if not hasattr(self, '_async_client'):
            api_key = self._api_key or os.getenv('MINIMAX_API_KEY')
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
            
            cost = _estimate_cost("minimax", model, prompt_tokens or 0, completion_tokens or 0)

            return LLMResponse(
                success=True,
                content=content,
                model=model,
                provider="minimax",
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
                provider="minimax",
                error=str(e),
                latency=time.time() - start_time
            )

    async def generate_stream_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Asynchronously generate a streaming response from MiniMax model."""
        if not hasattr(self, '_async_client'):
            api_key = self._api_key or os.getenv('MINIMAX_API_KEY')
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
        """List available MiniMax models."""
        return [
            self.get_model_info("minimax-m2.7"),
            self.get_model_info("minimax-m2.5"),
            self.get_model_info("minimax-m2"),
        ]
