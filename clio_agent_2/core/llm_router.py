"""
LLM Router for Clio-Agent-2.
Supports a wide range of providers.

Built-in providers (see ``BUILTIN_PROVIDER_INFO`` for the full, authoritative
list): OpenAI, Google (Gemini), Anthropic, OpenRouter, Grok (xAI), DeepSeek,
Mistral, Groq, Perplexity, Together, Fireworks, NVIDIA, NVIDIA KIM, Qwen
(Alibaba), HuggingFace, DeepInfra and Ollama (local).

On top of those, users can add arbitrary *custom* "Other" providers by
supplying a provider ID, base URL and (optional) API key — any service that
exposes an OpenAI-compatible ``/chat/completions`` endpoint works out of the
box through :class:`OpenAICompatibleProvider`.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import aiohttp

from .retry import retry_async


class AuthenticationError(Exception):
    """Custom exception for authentication errors."""


def _sanitize_aiohttp_error(exc: Exception) -> Exception:
    """
    Sanitize aiohttp exceptions to remove sensitive data (API keys, tokens).

    aiohttp.ClientResponseError may contain headers/body with Authorization
    headers in its string representation. We wrap it in a generic exception
    with a sanitized message.
    """
    if isinstance(exc, aiohttp.ClientResponseError):
        # Sanitize the message - remove any Authorization header values
        msg = str(exc)
        # Remove Bearer tokens from the message
        import re
        msg = re.sub(r'Bearer\s+[A-Za-z0-9_\-]{20,}', 'Bearer ***REDACTED***', msg)
        msg = re.sub(r'Authorization:\s*[A-Za-z0-9_\-]{20,}', 'Authorization: ***REDACTED***', msg)
        msg = re.sub(r'x-goog-api-key:\s*[A-Za-z0-9_\-]{20,}', 'x-goog-api-key: ***REDACTED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'x-api-key:\s*[A-Za-z0-9_\-]{20,}', 'x-api-key: ***REDACTED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'api-key:\s*[A-Za-z0-9_\-]{20,}', 'api-key: ***REDACTED***', msg, flags=re.IGNORECASE)
        return aiohttp.ClientResponseError(
            exc.request_info,
            exc.history,
            status=exc.status,
            message=msg,
            headers=exc.headers,
        )
    return exc


# Transient failures that are worth retrying. A completion request that fails
# for one of these reasons (network blip, provider overload, the per-attempt
# ``ClientTimeout`` -- now ``LLM_REQUEST_TIMEOUT``, tenfold the original 120s --
# surfacing as a timeout, etc.) is re-attempted by ``LLMRouter.chat``.
# Permanent problems -- a missing model/provider (``ValueError``) or bad API
# credentials (``AuthenticationError``) -- are NOT retryable: retrying them
# would only waste time in the autonomous loop.
_LLM_RETRYABLE: Tuple[type, ...] = (
    asyncio.TimeoutError,
    aiohttp.ClientError,
    ConnectionError,
    OSError,
    RuntimeError,
)

# Per-attempt HTTP timeout for a *single* completion request. Bumped tenfold
# from the original 120s so a slow-but-alive provider (large models, heavy
# tool-output summaries, cold starts) is not cut off mid-response. The
# overall per-message cap is ``MESSAGE_PROCESS_TIMEOUT`` in core/agent.py,
# which was raised to match so a long-but-valid response is never killed
# by the interface watchdog. ``chat_completion`` reads this via ``kwargs``
# (``request_timeout``) but defaults to this constant when not supplied.
LLM_REQUEST_TIMEOUT = 1200.0  # 10x the original 120s


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs
    ) -> str:
        pass

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        pass

    @abstractmethod
    async def list_models(self) -> List[str]:
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.openai.com/v1"

    @property
    def name(self) -> str:
        return "openai"

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o",
        **kwargs
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["choices"][0]["message"]["content"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            content = chunk["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                f"{self.base_url}/models",
                headers=headers
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return [model["id"] for model in data.get("data", [])]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


class GoogleProvider(LLMProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def name(self) -> str:
        return "google"

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "gemini-pro",
        **kwargs
    ) -> str:
        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "system"] else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        payload = {
            "contents": contents,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/models/{model}:generateContent",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "gemini-pro",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        # Similar to chat_completion but with streaming
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "system"] else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        payload = {
            "contents": contents,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line:
                        try:
                            import json
                            chunk = json.loads(line)
                            content = chunk["candidates"][0]["content"]["parts"][0]["text"]
                            yield content
                        except Exception:
                            continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                f"{self.base_url}/models",
                headers={"x-goog-api-key": self.api_key},
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return [
                    model["name"].split("/")[-1]
                    for model in data.get("models", [])
                    if "generateContent" in model.get("supportedGenerationMethods", [])
                ]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.anthropic.com/v1"
        self.headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    @property
    def name(self) -> str:
        return "anthropic"

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "claude-3-opus-20240229",
        max_tokens: int = 4096,
        **kwargs
    ) -> str:
        # Convert messages to Anthropic format
        system_messages = [m for m in messages if m["role"] == "system"]
        other_messages = [m for m in messages if m["role"] != "system"]

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": other_messages,
            **kwargs
        }

        if system_messages:
            payload["system"] = "\n".join(m["content"] for m in system_messages)

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["content"][0]["text"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "claude-3-opus-20240229",
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        system_messages = [m for m in messages if m["role"] == "system"]
        other_messages = [m for m in messages if m["role"] != "system"]

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": other_messages,
            "stream": True,
            **kwargs
        }

        if system_messages:
            payload["system"] = "\n".join(m["content"] for m in system_messages)

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        try:
                            import json
                            event = json.loads(data)
                            if event.get("type") == "content_block_delta":
                                yield event["delta"]["text"]
                        except Exception:
                            continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        # Anthropic doesn't have a public models endpoint, return known models
        return [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-2.1",
            "claude-2.0",
        ]


class OpenRouterProvider(LLMProvider):
    """OpenRouter API provider for accessing multiple models."""

    def __init__(self, api_key: str, http_referer: str = None, app_name: str = None):
        self.api_key = api_key
        self.base_url = "https://openrouter.ai/api/v1"
        self.http_referer = http_referer or "https://clio-agent-2.local"
        self.app_name = app_name or "Clio-Agent-2"

    @property
    def name(self) -> str:
        return "openrouter"

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for OpenRouter API requests."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.http_referer,
            "X-Title": self.app_name,
        }
        return headers

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "openai/gpt-4o",
        **kwargs
    ) -> str:
        headers = self._get_headers()

        payload = {
            "model": model,
            "messages": messages,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status == 401:
                        error_data = await response.json()
                        raise AuthenticationError(
                            f"OpenRouter authentication failed: {error_data.get('error', {}).get('message', 'Invalid API key')}"
                        )
                    elif response.status == 403:
                        error_data = await response.json()
                        raise AuthenticationError(
                            f"OpenRouter access forbidden: {error_data.get('error', {}).get('message', 'Check your API key and referer')}"
                        )

                    response.raise_for_status()
                    data = await response.json()
                    return data["choices"][0]["message"]["content"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "openai/gpt-4o",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        headers = self._get_headers()

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status == 401:
                        error_data = await response.json()
                        raise AuthenticationError(
                            f"OpenRouter authentication failed: {error_data.get('error', {}).get('message', 'Invalid API key')}"
                        )
                    elif response.status == 403:
                        error_data = await response.json()
                        raise AuthenticationError(
                            f"OpenRouter access forbidden: {error_data.get('error', {}).get('message', 'Check your API key and referer')}"
                        )

                    response.raise_for_status()
                    async for line in response.content:
                        line = line.decode('utf-8').strip()
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                import json
                                chunk = json.loads(data)
                                content = chunk["choices"][0].get("delta", {}).get("content", "")
                                if content:
                                    yield content
                            except Exception:
                                continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        headers = self._get_headers()
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.base_url}/models",
                    headers=headers
                ) as response:
                    if response.status == 401:
                        error_data = await response.json()
                        raise AuthenticationError(
                            f"OpenRouter authentication failed: {error_data.get('error', {}).get('message', 'Invalid API key')}"
                        )
                    response.raise_for_status()
                    data = await response.json()
                    return [model["id"] for model in data.get("data", [])]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


class GrokProvider(LLMProvider):
    """Grok (xAI) API provider.

    xAI's API is OpenAI-compatible, so this reuses the same request/response
    shape as the OpenAI provider.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.x.ai/v1"

    @property
    def name(self) -> str:
        return "grok"

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "grok-2-latest",
        **kwargs
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["choices"][0]["message"]["content"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "grok-2-latest",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            content = chunk["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                f"{self.base_url}/models",
                headers=headers
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return [model["id"] for model in data.get("data", [])]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


class DeepSeekProvider(LLMProvider):
    """DeepSeek API provider.

    DeepSeek's API is OpenAI-compatible. The base URL is set to include the
    ``/v1`` segment so that both ``/chat/completions`` and ``/models`` resolve
    correctly against the same endpoint.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/v1"

    @property
    def name(self) -> str:
        return "deepseek"

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "deepseek-chat",
        **kwargs
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["choices"][0]["message"]["content"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "deepseek-chat",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            content = chunk["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                f"{self.base_url}/models",
                headers=headers
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return [model["id"] for model in data.get("data", [])]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


class NVIDIAProvider(LLMProvider):
    """NVIDIA NIM API provider with reasoning/thinking support.

    NVIDIA's Nemotron models support extended reasoning (thinking) via
    ``extra_body`` parameters (``chat_template_kwargs`` / ``reasoning_budget``)
    and return ``reasoning_content`` in streaming deltas alongside the usual
    ``content``.  This provider uses aiohttp directly (like every other provider
    in this module) and coalesces both reasoning and content into a single
    output string.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://integrate.api.nvidia.com/v1"

    @property
    def name(self) -> str:
        return "nim"

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        model = model or "nvidia/nemotron-3-ultra-550b-a55b"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        enable_thinking = kwargs.pop("enable_thinking", None)
        reasoning_budget = kwargs.pop("reasoning_budget", None)
        if enable_thinking:
            payload["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": True},
            }
            if reasoning_budget:
                payload["extra_body"]["reasoning_budget"] = reasoning_budget
        payload.update({k: v for k, v in kwargs.items() if k not in ("request_timeout",)})

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["choices"][0]["message"]["content"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        model = model or "nvidia/nemotron-3-ultra-550b-a55b"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        enable_thinking = kwargs.pop("enable_thinking", None)
        reasoning_budget = kwargs.pop("reasoning_budget", None)
        if enable_thinking:
            payload["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": True},
            }
            if reasoning_budget:
                payload["extra_body"]["reasoning_budget"] = reasoning_budget
        payload.update({k: v for k, v in kwargs.items() if k not in ("request_timeout",)})

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        import json
                        chunk = json.loads(data)
                    except Exception:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    reasoning = delta.get("reasoning_content")
                    if reasoning is not None:
                        yield reasoning
                    content = delta.get("content")
                    if content is not None:
                        yield content
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                f"{self.base_url}/models",
                headers=headers
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return [model["id"] for model in data.get("data", [])]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


class OpenAICompatibleProvider(LLMProvider):
    """Generic OpenAI-compatible Chat Completions provider.

    A large number of LLM vendors expose an OpenAI-compatible
    ``/chat/completions`` (and often ``/models``) endpoint. Rather than writing
    a whole new provider class for each one, this single class talks to any of
    them given a base URL and (optional) API key. It is used for the built-in
    presets that happen to be OpenAI-compatible (Mistral, Groq, Perplexity,
    Together, Fireworks, NVIDIA, Qwen, HuggingFace, DeepInfra, Ollama) and for
    user-supplied "Other" providers configured at runtime.

    Args:
        api_key: Bearer token (or other secret). May be empty for local / keyless
            servers (e.g. a default Ollama install).
        base_url: Base URL of the API, e.g. ``https://api.mistral.ai/v1``. The
            ``/chat/completions`` and ``/models`` paths are appended to it.
        provider_name: The provider id (used as ``name`` and for logging).
        auth_header: Header that carries the credential. Defaults to
            ``Authorization``. Some vendors use a custom header (e.g.
            ``api-key``) — pass it here.
        auth_prefix: Prefix prepended to the key, e.g. ``Bearer``. Pass ``""``
            (empty string) when the header value should be the raw key with no
            prefix.
        extra_headers: Optional mapping of additional static headers.
        models_path: Path (appended to ``base_url``) hit by ``list_models``.
            Defaults to ``/models``. Set to ``""`` to disable model listing.
        default_model: Model used when the caller does not pass one.
        label: Human-friendly display name (defaults to ``provider_name``).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        provider_name: str,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer",
        extra_headers: Optional[Dict[str, str]] = None,
        models_path: str = "/models",
        default_model: Optional[str] = None,
        label: Optional[str] = None,
    ):
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.provider_name = (provider_name or "").strip().lower()
        self.auth_header = auth_header or "Authorization"
        self.auth_prefix = auth_prefix if auth_prefix is not None else "Bearer"
        self.extra_headers = extra_headers or {}
        self.models_path = models_path or ""
        self.default_model = default_model
        self.label = label or self.provider_name

    @property
    def name(self) -> str:
        return self.provider_name

    def _headers(self) -> Dict[str, str]:
        """Build the request headers, including auth if a key is present."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            if self.auth_prefix:
                headers[self.auth_header] = f"{self.auth_prefix} {self.api_key}"
            else:
                headers[self.auth_header] = self.api_key
        headers.update(self.extra_headers)
        return headers

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        model = model or self.default_model
        if not model:
            raise ValueError(
                f"No model specified for provider '{self.provider_name}'. "
                f"Pass a model explicitly (e.g. /llm_default {self.provider_name} <model>)."
            )
        headers = self._headers()
        payload = {
            "model": model,
            "messages": messages,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data["choices"][0]["message"]["content"]
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e


    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        model = model or self.default_model
        if not model:
            raise ValueError(
                f"No model specified for provider '{self.provider_name}'. "
                f"Pass a model explicitly (e.g. /llm_default {self.provider_name} <model>)."
            )
        headers = self._headers()
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs
        }

        timeout = aiohttp.ClientTimeout(total=kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            content = chunk["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e

    async def list_models(self) -> List[str]:
        # Not every OpenAI-compatible server implements a /models endpoint
        # (Ollama, many local vLLM/LM Studio setups, etc.). When it is missing
        # or misbehaves we must NOT crash the model listing — just report no
        # models so the rest of the UI keeps working.
        if not self.models_path:
            return []
        headers = self._headers()
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                f"{self.base_url}{self.models_path}",
                headers=headers
            ) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientResponseError as e:
            raise _sanitize_aiohttp_error(e) from e
        except Exception:
            return []

        # OpenAI-style: {"data": [{"id": "..."}, ...]}
        items = data.get("data", []) if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            if isinstance(items[0], dict) and "id" in items[0]:
                return [m["id"] for m in items]
            if isinstance(items[0], str):
                return list(items)
        return []


class LLMSettingsLockedError(RuntimeError):
    """
    Raised when code attempts to change the LLM provider/model while the
    settings are locked (``LLMRouter.llm_settings_locked`` is True).

    This is the core of the guardrail that keeps the agent's underlying
    LLM settings from changing unexpectedly or "on their own".
    """


# ---------------------------------------------------------------------------
# Built-in provider catalogue (single source of truth)
# ---------------------------------------------------------------------------
# Every provider Clio-Agent-2 knows how to construct out of the box. This is
# the canonical list consumed by the CLI, the configuration screen and the
# docs, so adding a provider here makes it appear everywhere at once.
#
#   label        human-friendly name shown in menus
#   env_var      .env variable holding the API key (also the config attr,
#                lower-cased, e.g. MISTRAL_API_KEY -> config.mistral_api_key)
#   base_url     API base URL (without a trailing slash)
#   default_model suggested model used as a placeholder / first suggestion
#   requires_key True if a key is mandatory to register the provider
#   kind         "dedicated" (its own class) or "openai-compatible"
#               (handled by OpenAICompatibleProvider)
BUILTIN_PROVIDER_INFO = {
    "openai":      {"label": "OpenAI", "env_var": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o", "requires_key": True, "kind": "dedicated"},
    "google":      {"label": "Google (Gemini)", "env_var": "GOOGLE_API_KEY", "base_url": "https://generativelanguage.googleapis.com/v1beta", "default_model": "gemini-1.5-pro", "requires_key": True, "kind": "dedicated"},
    "anthropic":   {"label": "Anthropic", "env_var": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com/v1", "default_model": "claude-3-5-sonnet-latest", "requires_key": True, "kind": "dedicated"},
    "openrouter":  {"label": "OpenRouter", "env_var": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1", "default_model": "openai/gpt-4o", "requires_key": True, "kind": "dedicated"},
    "grok":        {"label": "Grok (xAI)", "env_var": "GROK_API_KEY", "base_url": "https://api.x.ai/v1", "default_model": "grok-2-latest", "requires_key": True, "kind": "dedicated"},
    "deepseek":    {"label": "DeepSeek", "env_var": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat", "requires_key": True, "kind": "dedicated"},
    "mistral":     {"label": "Mistral AI", "env_var": "MISTRAL_API_KEY", "base_url": "https://api.mistral.ai/v1", "default_model": "mistral-large-latest", "requires_key": True, "kind": "openai-compatible"},
    "groq":        {"label": "Groq", "env_var": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1", "default_model": "llama-3.3-70b-versatile", "requires_key": True, "kind": "openai-compatible"},
    "perplexity":  {"label": "Perplexity", "env_var": "PERPLEXITY_API_KEY", "base_url": "https://api.perplexity.ai", "default_model": "sonar", "requires_key": True, "kind": "openai-compatible"},
    "together":    {"label": "Together AI", "env_var": "TOGETHER_API_KEY", "base_url": "https://api.together.xyz/v1", "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "requires_key": True, "kind": "openai-compatible"},
    "fireworks":   {"label": "Fireworks AI", "env_var": "FIREWORKS_API_KEY", "base_url": "https://api.fireworks.ai/inference/v1", "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "requires_key": True, "kind": "openai-compatible"},
    "qwen":        {"label": "Qwen (Alibaba)", "env_var": "QWEN_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen-max", "requires_key": True, "kind": "openai-compatible"},
    "huggingface": {"label": "HuggingFace", "env_var": "HUGGINGFACE_API_KEY", "base_url": "https://api-inference.huggingface.co/v1", "default_model": "meta-llama/Llama-3.3-70B-Instruct", "requires_key": True, "kind": "openai-compatible"},
    "deepinfra":   {"label": "DeepInfra", "env_var": "DEEPINFRA_API_KEY", "base_url": "https://api.deepinfra.com/v1/openai", "default_model": "meta-llama/Llama-3.3-70B-Instruct", "requires_key": True, "kind": "openai-compatible"},
    "ollama":      {"label": "Ollama (local)", "env_var": "OLLAMA_API_KEY", "base_url": "http://localhost:11434/v1", "default_model": "llama3", "requires_key": False, "kind": "openai-compatible"},
    "nim":         {"label": "NVIDIA NIM", "env_var": "NIM_API_KEY", "base_url": "https://integrate.api.nvidia.com/v1", "default_model": "nvidia/nemotron-3-ultra-550b-a55b", "requires_key": True, "kind": "openai-compatible"},
}

# Provider ids this router knows how to construct, regardless of whether an
# API key is currently configured. Custom ("Other") providers are dynamic and
# registered separately, so they are not listed here.
SUPPORTED_PROVIDERS = tuple(BUILTIN_PROVIDER_INFO.keys())


class LLMRouter:
    """
    Routes LLM requests to appropriate providers.
    Supports dynamic provider selection and model listing.
    """

    def __init__(self, config):
        """
        Initialize the LLM router with configured providers.

        Args:
            config: Configuration object with API keys
        """
        # Initialize core attributes FIRST so they always exist on the instance,
        # even if a later step (e.g. provider registration) were to fail or the
        # supplied config object is missing some expected attributes. Code in
        # the CLI/agent reads `current_model` and `default_provider` directly,
        # so they must never raise AttributeError.
        self.providers: Dict[str, LLMProvider] = {}

        # --- LLM settings guardrail -----------------------------------------
        # ``current_model`` / ``default_provider`` are the agent's *underlying
        # LLM settings*. They are stored in private backing fields and exposed
        # as properties (see below) whose setters refuse to change the value
        # while ``llm_settings_locked`` is True. This stops the settings from
        # changing unexpectedly or "on their own" (e.g. via prompt injection
        # or an autonomous self-edit) -- which is precisely what operators
        # want to avoid. The guardrail defaults to LOCKED; only an explicit
        # operator action (``/llm_unlock``) flips ``llm_settings_locked``.
        self._default_provider = getattr(config, "default_llm_provider", "openai")
        # The currently selected model. There is no built-in default — it must be
        # set explicitly and is persisted across restarts via config/.env.
        self._current_model = getattr(config, "current_model", "")

        # How many times ``chat`` retries a transient LLM failure before giving
        # up. The agent is configured to "never give up easily": the same
        # request is retried up to this many total attempts. Set to 1 to disable
        # retries. Defaults to 5 per the project's retry policy.
        self.max_chat_attempts = 5

        # Keep a reference to the config so providers can be (re)registered
        # later, e.g. after an API key is added at runtime via /reconfigure.
        self.config = config

        # Lock state. Defaults to True (locked) for any config that does not
        # explicitly say otherwise, so the safe posture is the default.
        self.llm_settings_locked = bool(
            getattr(config, "llm_settings_locked", True)
        )

        # Register available providers from the supplied config.
        self.register_providers(config)

    # Provider names this router knows how to construct, regardless of whether
    # an API key is currently configured for them. Used by the CLI/agent to
    # distinguish an unknown provider from a known-but-unconfigured one.
    # -- LLM settings properties (guardrail) -----------------------------
    # Both are plain string values, but exposed as properties so that EVERY
    # write -- whether from a slash command, the reconfigure wizard, or
    # arbitrary in-process code -- is forced through the lock check below.
    @property
    def default_provider(self) -> str:
        """Currently selected LLM provider (read access)."""
        return self._default_provider

    @default_provider.setter
    def default_provider(self, value: str) -> None:
        self.set_llm_provider(value)

    @property
    def current_model(self) -> str:
        """Currently selected LLM model (read access)."""
        return self._current_model

    @current_model.setter
    def current_model(self, value: str) -> None:
        self.set_llm_model(value)

    def set_llm_provider(self, provider: str, *, force: bool = False) -> None:
        """
        Set the default LLM provider, honouring the settings lock.

        Args:
            provider: Provider name, e.g. ``"openai"``.
            force: Bypass the lock. Used ONLY by the explicit operator
                unlock flow (``/llm_unlock``); never by normal commands.

        Raises:
            LLMSettingsLockedError: If locked and ``force`` is False.
            ValueError: If ``provider`` is not a known provider.
        """
        if self.llm_settings_locked and not force:
            raise LLMSettingsLockedError(
                "LLM settings are locked. Run /llm_unlock to allow changes, "
                "then retry."
            )
        provider = (provider or "").strip().lower()
        # Accept any built-in provider, or any provider that is currently
        # registered (this includes user-supplied "Other" providers, which are
        # added to self.providers during register_providers()).
        known = SUPPORTED_PROVIDERS + tuple(self.providers.keys())
        if provider not in known:
            raise ValueError(
                f"Unknown provider '{provider}'. Supported: "
                f"{', '.join(SUPPORTED_PROVIDERS)}"
            )
        self._default_provider = provider

    def set_llm_model(self, model: str, *, force: bool = False) -> None:
        """
        Set the selected LLM model, honouring the settings lock.

        Args:
            model: Model name, e.g. ``"gpt-4o"``.
            force: Bypass the lock. Used ONLY by the explicit operator
                unlock flow (``/llm_unlock``); never by normal commands.

        Raises:
            LLMSettingsLockedError: If locked and ``force`` is False.
            ValueError: If ``model`` is empty.
        """
        if self.llm_settings_locked and not force:
            raise LLMSettingsLockedError(
                "LLM settings are locked. Run /llm_unlock to allow changes, "
                "then retry."
            )
        model = (model or "").strip()
        if not model:
            raise ValueError("Model name cannot be empty.")
        self._current_model = model

    def lock_llm_settings(self) -> None:
        """Lock the LLM provider/model so they can no longer be changed."""
        self.llm_settings_locked = True

    def unlock_llm_settings(self) -> None:
        """Unlock the LLM provider/model to permit an explicit change."""
        self.llm_settings_locked = False


    def register_providers(self, config=None) -> List[str]:
        """
        (Re)build the set of usable providers from a config object.

        Only providers whose API key is present are registered. Safe to call
        multiple times, e.g. after a new API key is saved at runtime. Uses
        getattr() so a partial/incomplete config object cannot raise.

        Args:
            config: Configuration object with API keys. Defaults to the config
                    passed at construction time.

        Returns:
            The list of provider names that are now available.
        """
        if config is None:
            config = getattr(self, "config", None)

        openai_key = getattr(config, "openai_api_key", None)
        google_key = getattr(config, "google_api_key", None)
        anthropic_key = getattr(config, "anthropic_api_key", None)
        openrouter_key = getattr(config, "openrouter_api_key", None)
        openrouter_referer = getattr(config, "openrouter_http_referer", None)
        openrouter_app = getattr(config, "openrouter_app_name", None)
        grok_key = getattr(config, "grok_api_key", None)
        deepseek_key = getattr(config, "deepseek_api_key", None)
        nim_key = getattr(config, "nim_api_key", None)

        # --- Dedicated providers (their own request/response shapes) ---------
        if openai_key:
            self.providers["openai"] = OpenAIProvider(openai_key)
        if google_key:
            self.providers["google"] = GoogleProvider(google_key)
        if anthropic_key:
            self.providers["anthropic"] = AnthropicProvider(anthropic_key)
        if openrouter_key:
            self.providers["openrouter"] = OpenRouterProvider(
                openrouter_key,
                http_referer=openrouter_referer,
                app_name=openrouter_app
            )
        if grok_key:
            self.providers["grok"] = GrokProvider(grok_key)
        if deepseek_key:
            self.providers["deepseek"] = DeepSeekProvider(deepseek_key)
        if nim_key:
            self.providers["nim"] = NVIDIAProvider(nim_key)

        # --- OpenAI-compatible built-in presets ------------------------------
        # Every provider in BUILTIN_PROVIDER_INFO flagged "openai-compatible"
        # is served by the single generic provider; we just vary base URL + key.
        # Providers with a dedicated class above (e.g. nim) are skipped here.
        for pid, info in BUILTIN_PROVIDER_INFO.items():
            if info["kind"] != "openai-compatible":
                continue
            if pid in self.providers:
                continue
            key = getattr(config, info["env_var"].lower(), None)
            base_url = getattr(config, f"{pid}_base_url", None) or info["base_url"]
            if pid == "ollama":
                # Ollama is local and keyless by default; only register it when
                # the user has explicitly pointed at an Ollama instance.
                if key or getattr(config, "ollama_base_url", None):
                    self.providers[pid] = OpenAICompatibleProvider(
                        key or "", base_url, pid,
                        default_model=info["default_model"], label=info["label"]
                    )
                continue
            if key:
                self.providers[pid] = OpenAICompatibleProvider(
                    key, base_url, pid,
                    default_model=info["default_model"], label=info["label"]
                )

        # --- Custom "Other" providers (user-supplied) -----------------------
        load_custom = getattr(config, "load_custom_providers", None)
        if callable(load_custom):
            for cp in load_custom():
                pid = (cp.get("id") or "").strip().lower()
                base_url = (cp.get("base_url") or "").strip()
                if not pid or not base_url:
                    continue
                self.providers[pid] = OpenAICompatibleProvider(
                    cp.get("api_key") or "",
                    base_url,
                    pid,
                    auth_header=cp.get("auth_header", "Authorization"),
                    auth_prefix=cp.get("auth_prefix", "Bearer"),
                    models_path=cp.get("models_path", "/models"),
                    default_model=cp.get("default_model", ""),
                    label=cp.get("label", pid),
                )

        return self.get_available_providers()

    def get_provider(self, provider_name: str) -> Optional[LLMProvider]:
        """Get a specific provider by name."""
        return self.providers.get(provider_name.lower())

    def get_available_providers(self) -> List[str]:
        """Get list of available provider names."""
        return list(self.providers.keys())

    async def chat(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        deadline: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Send a chat completion request.
        
        Args:
            messages: List of message dictionaries
            provider: Provider name (optional, uses the configured provider if not specified)
            model: Model name (optional, uses the configured model if not specified)
            **kwargs: Additional arguments for the API
        
        Returns:
            The response text from the LLM
        
        Raises:
            ValueError: If no model has been configured.
        """
        # Use getattr() so a partial/old router instance that happens to lack
        # these attributes can never raise AttributeError here.
        provider_name = provider or getattr(self, "default_provider", "openai")
        provider_instance = self.get_provider(provider_name)

        if not provider_instance:
            raise ValueError(f"Provider '{provider_name}' is not configured")

        model_name = model or getattr(self, "current_model", "")
        if not model_name:
            raise ValueError(
                "No LLM model configured. Set one explicitly, for example with "
                "/llm_default <provider> <model> or /config model <model>."
            )

        # Retry the exact same request on transient failures (timeouts, network
        # blips, provider overload). Permanent errors such as a missing model or
        # bad credentials are not in ``_LLM_RETRYABLE`` and therefore raise
        # immediately instead of looping forever.
        async def _attempt_chat() -> str:
            # Honour the raised per-attempt timeout (LLM_REQUEST_TIMEOUT,
            # tenfold of the original 120s). A caller may still override it
            # via ``request_timeout=...`` in kwargs.
            return await provider_instance.chat_completion(
                messages,
                model_name,
                **{"request_timeout": LLM_REQUEST_TIMEOUT, **kwargs},
            )

        return await retry_async(
            _attempt_chat,
            max_attempts=self.max_chat_attempts,
            retryable_exceptions=_LLM_RETRYABLE,
            label=f"llm chat ({provider_name}/{model_name})",
            deadline=deadline,
        )

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Send a streaming chat completion request.
        
        Args:
            messages: List of message dictionaries
            provider: Provider name (optional)
            model: Model name (optional)
            **kwargs: Additional arguments for the API
        
        Yields:
            Chunks of response text
        
        Raises:
            ValueError: If no model has been configured.
        """
        # Use getattr() so a partial/old router instance that happens to lack
        # these attributes can never raise AttributeError here.
        provider_name = provider or getattr(self, "default_provider", "openai")
        provider_instance = self.get_provider(provider_name)

        if not provider_instance:
            raise ValueError(f"Provider '{provider_name}' is not configured")

        model_name = model or getattr(self, "current_model", "")
        if not model_name:
            raise ValueError(
                "No LLM model configured. Set one explicitly, for example with "
                "/llm_default <provider> <model> or /config model <model>."
            )
        async for chunk in provider_instance.stream_chat(messages, model_name, **kwargs):
            yield chunk

    async def list_all_models(self) -> Dict[str, List[str]]:
        """
        List all available models from all configured providers.
        
        Returns:
            Dictionary mapping provider names to their model lists
        """
        models = {}
        for name, provider in self.providers.items():
            try:
                models[name] = await provider.list_models()
            except Exception as e:
                models[name] = [f"Error: {str(e)}"]
        return models

    async def search_models(self, query: str) -> List[Dict[str, str]]:
        """
        Search for models matching a query across all providers.
        
        Args:
            query: Search query string
        
        Returns:
            List of matching models with provider information
        """
        results = []
        all_models = await self.list_all_models()

        query_lower = query.lower()
        for provider, models in all_models.items():
            for model in models:
                if query_lower in model.lower():
                    results.append({
                        "provider": provider,
                        "model": model,
                    })

        return results
