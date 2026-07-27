"""
Multi-Provider LLM Client

Supports OpenAI, Anthropic, NVIDIA NIM, Google, Groq, Ollama, and more.
"""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, AsyncGenerator, Union
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    """LLM message with optional multimodal content."""
    role: str  # system, user, assistant, tool
    content: Union[str, List[Dict[str, Any]]]  # Text or multimodal content
    name: Optional[str] = None  # For tool calls
    tool_call_id: Optional[str] = None  # For tool responses
    tool_calls: Optional[List[Dict[str, Any]]] = None  # Assistant tool calls


@dataclass
class LLMConfig:
    """LLM configuration."""
    provider: str = "openai"  # openai, anthropic, google, groq, ollama, nvidia, openrouter, etc.
    model: str = "gpt-4o"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = 4096
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    timeout: int = 120
    # Provider-specific
    ollama_host: str = "http://localhost:11434"
    # Streaming
    stream: bool = True
    # Tools
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[str] = None


@dataclass
class LLMResponse:
    """LLM response."""
    content: str
    model: str
    provider: str
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize provider. Returns True on success."""
        pass
    
    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        """Non-streaming chat completion."""
        pass
    
    @abstractmethod
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass
    
    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether provider supports vision."""
        pass
    
    @property
    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether provider supports tool calling."""
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI and compatible APIs (OpenAI, Azure, OpenRouter, etc.)."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
    
    @property
    def name(self) -> str:
        return "openai"
    
    @property
    def supports_vision(self) -> bool:
        return True
    
    @property
    def supports_tools(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            logger.error("openai package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.error("OpenAI API key not provided")
            return False
        
        base_url = self.config.api_base or os.environ.get("OPENAI_BASE_URL")
        
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.config.timeout
        )
        self._initialized = True
        logger.info(f"OpenAI provider initialized (model: {self.config.model})")
        return True
    
    def _format_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        """Format messages for OpenAI API."""
        formatted = []
        for msg in messages:
            if isinstance(msg.content, str):
                formatted.append({"role": msg.role, "content": msg.content})
            else:
                # Multimodal content
                formatted.append({"role": msg.role, "content": msg.content})
            
            if msg.tool_calls:
                formatted[-1]["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                formatted[-1]["tool_call_id"] = msg.tool_call_id
            if msg.name:
                formatted[-1]["name"] = msg.name
        
        return formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        response = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
            tools=config.tools,
            tool_choice=config.tool_choice,
            stream=False
        )
        
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            provider=self.name,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            } if response.usage else None,
            finish_reason=choice.finish_reason,
            tool_calls=choice.message.tool_calls if choice.message.tool_calls else None
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        stream = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
            tools=config.tools,
            tool_choice=config.tool_choice,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
    
    @property
    def name(self) -> str:
        return "anthropic"
    
    @property
    def supports_vision(self) -> bool:
        return True
    
    @property
    def supports_tools(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            logger.error("anthropic package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("Anthropic API key not provided")
            return False
        
        base_url = self.config.api_base or os.environ.get("ANTHROPIC_BASE_URL")
        
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=self.config.timeout
        )
        self._initialized = True
        logger.info(f"Anthropic provider initialized (model: {self.config.model})")
        return True
    
    def _format_messages(self, messages: List[LLMMessage]) -> tuple:
        """Format messages for Anthropic API. Returns (system, messages)."""
        system = ""
        formatted = []
        
        for msg in messages:
            if msg.role == "system":
                system = msg.content if isinstance(msg.content, str) else ""
            else:
                if isinstance(msg.content, str):
                    formatted.append({"role": msg.role, "content": msg.content})
                else:
                    # Multimodal
                    formatted.append({"role": msg.role, "content": msg.content})
        
        return system, formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        system, formatted = self._format_messages(messages)
        
        response = await self._client.messages.create(
            model=config.model,
            system=system,
            messages=formatted,
            max_tokens=config.max_tokens or 4096,
            temperature=config.temperature,
            top_p=config.top_p,
            tools=config.tools,
            tool_choice=config.tool_choice
        )
        
        content = ""
        tool_calls = []
        
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input)
                    }
                })
        
        return LLMResponse(
            content=content,
            model=response.model,
            provider=self.name,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens
            },
            finish_reason=response.stop_reason,
            tool_calls=tool_calls if tool_calls else None
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        system, formatted = self._format_messages(messages)
        
        stream = await self._client.messages.create(
            model=config.model,
            system=system,
            messages=formatted,
            max_tokens=config.max_tokens or 4096,
            temperature=config.temperature,
            top_p=config.top_p,
            tools=config.tools,
            tool_choice=config.tool_choice,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.type == "content_block_delta" and chunk.delta.type == "text_delta":
                yield chunk.delta.text


class GoogleProvider(LLMProvider):
    """Google Gemini API."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
    
    @property
    def name(self) -> str:
        return "google"
    
    @property
    def supports_vision(self) -> bool:
        return True
    
    @property
    def supports_tools(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        try:
            import google.generativeai as genai
        except ImportError:
            logger.error("google-generativeai package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            logger.error("Google API key not provided")
            return False
        
        genai.configure(api_key=api_key)
        self._client = genai.GenerativeModel(self.config.model)
        self._initialized = True
        logger.info(f"Google provider initialized (model: {self.config.model})")
        return True
    
    def _format_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        """Format for Gemini API."""
        formatted = []
        for msg in messages:
            role = "user" if msg.role == "user" else "model" if msg.role == "assistant" else "user"
            if isinstance(msg.content, str):
                formatted.append({"role": role, "parts": [msg.content]})
            else:
                parts = []
                for item in msg.content:
                    if item["type"] == "text":
                        parts.append(item["text"])
                    elif item["type"] == "image_url":
                        # Would need to handle image
                        pass
                formatted.append({"role": role, "parts": parts})
        return formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        generation_config = {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_output_tokens": config.max_tokens,
        }
        
        response = await self._client.generate_content_async(
            formatted,
            generation_config=generation_config,
            tools=config.tools,
            tool_config={"function_calling_config": config.tool_choice} if config.tool_choice else None
        )
        
        content = response.text or ""
        
        return LLMResponse(
            content=content,
            model=config.model,
            provider=self.name,
            finish_reason=response.candidates[0].finish_reason.name if response.candidates else None
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        generation_config = {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_output_tokens": config.max_tokens,
        }
        
        response = await self._client.generate_content_async(
            formatted,
            generation_config=generation_config,
            stream=True
        )
        
        async for chunk in response:
            if chunk.text:
                yield chunk.text


class GroqProvider(LLMProvider):
    """Groq API (fast inference)."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
    
    @property
    def name(self) -> str:
        return "groq"
    
    @property
    def supports_vision(self) -> bool:
        return False  # Groq doesn't support vision yet
    
    @property
    def supports_tools(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        try:
            from groq import AsyncGroq
        except ImportError:
            logger.error("groq package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            logger.error("Groq API key not provided")
            return False
        
        self._client = AsyncGroq(
            api_key=api_key,
            timeout=self.config.timeout
        )
        self._initialized = True
        logger.info(f"Groq provider initialized (model: {self.config.model})")
        return True
    
    def _format_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in messages:
            if isinstance(msg.content, str):
                formatted.append({"role": msg.role, "content": msg.content})
            else:
                formatted.append({"role": msg.role, "content": msg.content})
        return formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        response = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            tools=config.tools,
            tool_choice=config.tool_choice,
            stream=False
        )
        
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            provider=self.name,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            } if response.usage else None,
            finish_reason=choice.finish_reason,
            tool_calls=choice.message.tool_calls if choice.message.tool_calls else None
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        stream = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            tools=config.tools,
            tool_choice=config.tool_choice,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class OllamaProvider(LLMProvider):
    """Ollama local LLM."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
        self._session = None
    
    @property
    def name(self) -> str:
        return "ollama"
    
    @property
    def supports_vision(self) -> bool:
        # Some Ollama models support vision (llava, etc.)
        return True
    
    @property
    def supports_tools(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not installed")
            return False
        
        self._session = aiohttp.ClientSession()
        
        # Test connection
        try:
            async with self._session.get(f"{self.config.ollama_host}/api/tags") as resp:
                if resp.status == 200:
                    self._initialized = True
                    logger.info(f"Ollama provider initialized (host: {self.config.ollama_host})")
                    return True
        except Exception as e:
            logger.error(f"Failed to connect to Ollama: {e}")
        
        return False
    
    def _format_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in messages:
            if isinstance(msg.content, str):
                formatted.append({"role": msg.role, "content": msg.content})
            else:
                # Handle multimodal for vision models
                content_parts = []
                for item in msg.content:
                    if item["type"] == "text":
                        content_parts.append(item["text"])
                    elif item["type"] == "image_url":
                        # Ollama expects base64 images
                        url = item["image_url"]["url"]
                        if url.startswith("data:"):
                            # Extract base64
                            content_parts.append(url.split(",")[1])
                formatted.append({"role": msg.role, "content": "\n".join(content_parts)})
        return formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        payload = {
            "model": config.model,
            "messages": formatted,
            "stream": False,
            "options": {
                "temperature": config.temperature,
                "top_p": config.top_p,
                "num_predict": config.max_tokens or -1,
            }
        }
        
        if config.tools:
            payload["tools"] = config.tools
        
        async with self._session.post(
            f"{self.config.ollama_host}/api/chat",
            json=payload
        ) as resp:
            data = await resp.json()
        
        content = data.get("message", {}).get("content", "")
        
        return LLMResponse(
            content=content,
            model=config.model,
            provider=self.name,
            finish_reason=data.get("done_reason")
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        payload = {
            "model": config.model,
            "messages": formatted,
            "stream": True,
            "options": {
                "temperature": config.temperature,
                "top_p": config.top_p,
                "num_predict": config.max_tokens or -1,
            }
        }
        
        async with self._session.post(
            f"{self.config.ollama_host}/api/chat",
            json=payload
        ) as resp:
            async for line in resp.content:
                line = line.decode('utf-8').strip()
                if line:
                    try:
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                    except json.JSONDecodeError:
                        pass
    
    async def close(self):
        if self._session:
            await self._session.close()


class NVIDIAProvider(LLMProvider):
    """NVIDIA NIM API."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
    
    @property
    def name(self) -> str:
        return "nvidia"
    
    @property
    def supports_vision(self) -> bool:
        return True
    
    @property
    def supports_tools(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            logger.error("openai package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            logger.error("NVIDIA API key not provided")
            return False
        
        base_url = self.config.api_base or "https://integrate.api.nvidia.com/v1"
        
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.config.timeout
        )
        self._initialized = True
        logger.info(f"NVIDIA provider initialized (model: {self.config.model})")
        return True
    
    # Use OpenAI-compatible formatting
    def _format_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in messages:
            if isinstance(msg.content, str):
                formatted.append({"role": msg.role, "content": msg.content})
            else:
                formatted.append({"role": msg.role, "content": msg.content})
        return formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        response = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            stream=False
        )
        
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            provider=self.name,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            } if response.usage else None,
            finish_reason=choice.finish_reason
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        stream = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class OpenRouterProvider(LLMProvider):
    """OpenRouter API (access to 300+ models)."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._initialized = False
    
    @property
    def name(self) -> str:
        return "openrouter"
    
    @property
    def supports_vision(self) -> bool:
        return True  # Depends on model
    
    @property
    def supports_tools(self) -> bool:
        return True  # Depends on model
    
    async def initialize(self) -> bool:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            logger.error("openai package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            logger.error("OpenRouter API key not provided")
            return False
        
        base_url = self.config.api_base or "https://openrouter.ai/api/v1"
        
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.config.timeout
        )
        self._initialized = True
        logger.info(f"OpenRouter provider initialized (model: {self.config.model})")
        return True
    
    def _format_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in messages:
            if isinstance(msg.content, str):
                formatted.append({"role": msg.role, "content": msg.content})
            else:
                formatted.append({"role": msg.role, "content": msg.content})
        return formatted
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> LLMResponse:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        response = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            stream=False
        )
        
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            provider=self.name,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            } if response.usage else None,
            finish_reason=choice.finish_reason
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: LLMConfig
    ) -> AsyncGenerator[str, None]:
        if not self._initialized:
            raise RuntimeError("Provider not initialized")
        
        formatted = self._format_messages(messages)
        
        stream = await self._client.chat.completions.create(
            model=config.model,
            messages=formatted,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class LLMManager:
    """Manages LLM providers and routing."""
    
    PROVIDERS = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "google": GoogleProvider,
        "groq": GroqProvider,
        "ollama": OllamaProvider,
        "nvidia": NVIDIAProvider,
        "openrouter": OpenRouterProvider,
    }
    
    def __init__(self, config: LLMConfig = None):
        self.config = config or LLMConfig()
        self._provider: Optional[LLMProvider] = None
        self._initialized = False
    
    @property
    def provider(self) -> Optional[LLMProvider]:
        return self._provider
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._provider is not None
    
    async def initialize(self) -> bool:
        """Initialize the configured provider."""
        provider_class = self.PROVIDERS.get(self.config.provider)
        if not provider_class:
            logger.error(f"Unknown provider: {self.config.provider}")
            return False
        
        self._provider = provider_class(self.config)
        self._initialized = await self._provider.initialize()
        return self._initialized
    
    async def chat(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None
    ) -> LLMResponse:
        """Chat completion."""
        if not self.is_ready:
            raise RuntimeError("LLM not initialized. Call initialize() first.")
        
        cfg = config or self.config
        return await self._provider.chat(messages, cfg)
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        config: Optional[LLMConfig] = None
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion."""
        if not self.is_ready:
            raise RuntimeError("LLM not initialized. Call initialize() first.")
        
        cfg = config or self.config
        async for chunk in self._provider.chat_stream(messages, cfg):
            yield chunk
    
    def switch_provider(self, provider_name: str, **kwargs) -> bool:
        """Switch to a different provider."""
        if provider_name not in self.PROVIDERS:
            logger.error(f"Unknown provider: {provider_name}")
            return False
        
        self.config.provider = provider_name
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        
        self._initialized = False
        return asyncio.create_task(self.initialize())


async def create_llm(config: LLMConfig = None) -> LLMManager:
    """Factory function to create and initialize LLM manager."""
    manager = LLMManager(config)
    await manager.initialize()
    return manager