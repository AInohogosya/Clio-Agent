"""
Google Gemini (DeepMind) LLM Client Adapter

Implements the BaseLLM interface for Google GenAI SDK (google-genai).
Uses the latest Google GenAI SDK (google-genai) which is the GA (General Availability)
version as of May 2025.

Installation:
    pip install google-genai

Environment Variables:
    GOOGLE_API_KEY or GEMINI_API_KEY: API key for Gemini API
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse,
    ModelInfo, ResponseFormat, _estimate_cost
)

# Import Google GenAI SDK
try:
    from google import genai
    from google.genai import types
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False


class GoogleLLMClient(BaseLLM):
    """
    Google Gemini LLM client using the official google-genai SDK.

    This adapter implements the BaseLLM interface for Google's Gemini models,
    handling parameter mapping and response normalization.

    Parameter Mapping:
        - max_tokens -> max_output_tokens
        - temperature -> temperature (0.0 - 2.0)
        - top_p -> top_p (0.0 - 1.0)
        - top_k -> top_k (1 - 40)
        - stop_sequences -> stop_sequences
        - seed -> seed
        - system_instruction -> system_instruction

    Usage:
        client = GoogleLLMClient(api_key="your-api-key")

        # Simple generation
        response = client.generate("Explain quantum computing")
        print(response.content)

        # With configuration
        config = GenerationConfig(
            max_tokens=2000,
            temperature=0.7,
            system_instruction="You are a helpful science tutor."
        )
        response = client.generate("Explain quantum computing", config=config)

    Latest Models (as of 2026):
        - gemini-2.5-pro: Stable 1M context, advanced reasoning and coding
        - gemini-2.5-flash: Stable 1M context, fast and efficient
        - gemini-2.5-flash-lite: Stable 1M context, lightweight
        - gemini-3.1-pro-preview: New Preview 2M context flagship (April 2026)
        - gemini-3-flash-preview: New Preview fast frontier-class (April 2026)
        - gemini-3.1-flash-lite-preview: New Preview lightweight (April 2026)
        - deep-research-preview-04-2026: New Deep Research agent (April 2026)
        - deep-research-max-preview-04-2026: New Deep Research Max agent (April 2026)
    """

    # Default model to use
    DEFAULT_MODEL = "gemini-2.5-pro"

    # Model context windows
    MODEL_CONTEXT_WINDOWS = {
        "gemini-2.5-pro": 1_048_576,
        "gemini-2.5-flash": 1_048_576,
        "gemini-2.5-flash-lite": 1_048_576,
        "gemini-3.1-pro-preview": 2_097_152,
        "gemini-3-flash-preview": 1_048_576,
        "gemini-3.1-flash-lite-preview": 1_048_576,
        "deep-research-preview-04-2026": 1_048_576,
        "deep-research-max-preview-04-2026": 1_048_576,
    }

    # Vision-capable models
    VISION_MODELS = {
        "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
        "gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview",
    }

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """
        Initialize Google Gemini client.

        Args:
            api_key: Gemini API key. If not provided, uses GOOGLE_API_KEY or
                    GEMINI_API_KEY environment variable.
            **kwargs: Additional configuration options
                - project: Vertex AI project ID (for Vertex AI)
                - location: Vertex AI location (for Vertex AI)
                - vertexai: Use Vertex AI instead of Gemini API (bool)
        """
        # Get API key from argument or environment
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

        # Vertex AI configuration
        self._use_vertexai = kwargs.get("vertexai", False)
        self._project = kwargs.get("project")
        self._location = kwargs.get("location", "us-central1")

        self._config = kwargs
        self._client = None

        if not GOOGLE_GENAI_AVAILABLE:
            raise ImportError(
                "google-genai package is required. "
                "Install with: pip install google-genai"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GOOGLE

    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        """Initialize the Google GenAI client"""
        if not self._api_key and not self._use_vertexai:
            raise ValueError(
                "Google API key is required. Provide it as an argument or "
                "set the GOOGLE_API_KEY or GEMINI_API_KEY environment variable."
            )

        if self._use_vertexai:
            # Vertex AI client
            self._client = genai.Client(
                vertexai=True,
                project=self._project or os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=self._location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            )
        else:
            # Gemini Developer API client
            self._client = genai.Client(api_key=self._api_key)

    def _convert_config(self, config: Optional[GenerationConfig]):
        """
        Convert unified GenerationConfig to Google-specific config.

        Maps:
            max_tokens -> max_output_tokens
            temperature -> temperature (Google uses 0.0 - 2.0)
            top_p -> top_p (0.0 - 1.0)
            top_k -> top_k (1 - 40)
            stop_sequences -> stop_sequences
            seed -> seed
            system_instruction -> system_instruction
        """
        if not GOOGLE_GENAI_AVAILABLE:
            raise ImportError("Google GenAI SDK is not installed. Run: pip install google-genai")

        if config is None:
            config = GenerationConfig()

        # Build Google-specific config
        google_config = types.GenerateContentConfig()

        # Map max_tokens to max_output_tokens
        if config.max_tokens is not None:
            google_config.max_output_tokens = config.max_tokens

        # Temperature (Google range: 0.0 - 2.0)
        if config.temperature is not None:
            google_config.temperature = config.temperature

        # Top P (Google range: 0.0 - 1.0)
        if config.top_p is not None:
            google_config.top_p = config.top_p

        # Top K (Google range: 1 - 40)
        if config.top_k is not None:
            google_config.top_k = config.top_k

        # Stop sequences
        if config.stop_sequences:
            google_config.stop_sequences = config.stop_sequences

        # Seed for reproducibility
        if config.seed is not None:
            google_config.seed = config.seed

        # System instruction
        if config.system_instruction:
            google_config.system_instruction = config.system_instruction

        # Response modality for JSON if requested
        if config.response_format == ResponseFormat.JSON:
            google_config.response_mime_type = "application/json"

        # Apply any extra parameters
        for key, value in config.extra_params.items():
            if hasattr(google_config, key):
                setattr(google_config, key, value)

        return google_config

    def generate(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate a response using Google Gemini API.

        Args:
            prompt: Input text prompt
            config: Generation configuration
            model: Model ID to use (defaults to gemini-2.5-flash)
            **kwargs: Additional arguments (image_data for vision)
                - image_data: bytes - Image data for vision models
                - image_format: str - Image format (png, jpeg, etc.)

        Returns:
            LLMResponse with generated content
        """
        start_time = time.time()

        try:
            self._ensure_initialized()

            model_id = model or self.default_model
            google_config = self._convert_config(config)

            # Handle image input if provided
            image_data = kwargs.get("image_data")
            if image_data:
                # Upload file or use inline data
                # For simplicity, we'll pass the prompt and let the caller handle image
                # through the content system if needed
                contents = prompt
            else:
                contents = prompt

            # Make the API call
            response = self._client.models.generate_content(
                model=model_id,
                contents=contents,
                config=google_config
            )

            # Extract content from response
            content = response.text if hasattr(response, 'text') else str(response)

            # Extract token usage if available
            usage_metadata = getattr(response, 'usage_metadata', None)
            prompt_tokens = None
            completion_tokens = None
            total_tokens = None

            if usage_metadata:
                prompt_tokens = getattr(usage_metadata, 'prompt_token_count', None)
                completion_tokens = getattr(usage_metadata, 'candidates_token_count', None)
                total_tokens = getattr(usage_metadata, 'total_token_count', None)

            # Calculate cost
            cost = _estimate_cost(
                "google", model_id,
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
            google_config = self._convert_config(config)

            # Make streaming API call
            for chunk in self._client.models.generate_content_stream(
                model=model_id,
                contents=prompt,
                config=google_config
            ):
                # Extract text from chunk
                text = chunk.text if hasattr(chunk, 'text') else str(chunk)
                if text:
                    yield text

        except Exception as e:
            # In streaming, we yield error as a special message
            yield f"[Error: {str(e)}]"

    async def generate_async(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Asynchronously generate a response.

        Note: This runs the sync method in an executor for compatibility.
        For true async with Google SDK, use generate_stream_async.
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.generate, prompt, config, model, **kwargs
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
        # Get the async generator from the SDK
        self._ensure_initialized()

        model_id = model or self.default_model
        google_config = self._convert_config(config)

        response = self._client.aio.models.generate_content_stream(
            model=model_id,
            contents=prompt,
            config=google_config
        )

        async for chunk in response:
            text = chunk.text if hasattr(chunk, 'text') else str(chunk)
            if text:
                yield text

    def list_models(self) -> List[ModelInfo]:
        """
        List available Gemini models.

        Returns:
            List of ModelInfo objects
        """
        self._ensure_initialized()

        models = []
        try:
            for model in self._client.models.list():
                model_id = model.name if hasattr(model, 'name') else str(model)
                # Strip "models/" prefix
                clean_id = model_id.replace("models/", "")

                # Filter: only include generative text models
                supported_actions = getattr(model, 'supported_actions', None)
                if supported_actions is not None and 'generateContent' not in supported_actions:
                    continue

                # Skip embedding and non-chat models
                if "embedding" in clean_id or "aqa" in clean_id:
                    continue

                context_window = self.MODEL_CONTEXT_WINDOWS.get(clean_id, 1_048_576)
                supports_vision = clean_id in self.VISION_MODELS

                models.append(ModelInfo(
                    id=clean_id,
                    name=clean_id,
                    provider=self.provider_type.value,
                    context_window=context_window,
                    max_output_tokens=8192,
                    supports_vision=supports_vision,
                    supports_streaming=True,
                    description=f"Google Gemini model: {clean_id}"
                ))
            models.sort(key=lambda m: m.id)
        except Exception:
            models = []

        return models

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """Get information about a specific model"""
        models = self.list_models()
        for model in models:
            if model.id == model_id or model.id.endswith(model_id):
                return model

        # Return None if model not found - no fallback to avoid overriding user selection
        return None

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """
        Count tokens in the given text.

        Note: This uses the API to get accurate token counts.
        """
        try:
            self._ensure_initialized()

            model_id = model or self.default_model

            response = self._client.models.count_tokens(
                model=model_id,
                contents=text
            )

            return getattr(response, 'total_tokens', len(text) // 4)

        except Exception:
            # Fallback: rough estimate (4 chars per token for English)
            return len(text) // 4

    def is_available(self) -> bool:
        """Check if Google provider is available"""
        if not GOOGLE_GENAI_AVAILABLE:
            return False

        if self._use_vertexai:
            # For Vertex AI, check project is set
            return (self._project or os.getenv("GOOGLE_CLOUD_PROJECT")) is not None

        return self._api_key is not None


# Register with factory
from .base import LLMFactory
LLMFactory.register(ProviderType.GOOGLE, GoogleLLMClient)
