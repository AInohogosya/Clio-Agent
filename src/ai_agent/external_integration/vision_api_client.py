"""
Vision API Client for AI Agent System
Simplified: Ollama Cloud Models only
"""

import io
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

try:
    from PIL import Image
except ImportError:
    raise ImportError("PIL (Pillow) is required for Vision API client")

from ..utils.exceptions import ValidationError
from ..utils.logger import get_logger
from ..utils.config import load_config
from .ollama_provider import SimpleOllamaProvider


class APIProvider(Enum):
    """Supported API providers"""
    OLLAMA = "ollama"
    GOOGLE = "google"


@dataclass
class APIResponse:
    """API response structure"""
    success: bool
    content: str
    model: str
    provider: str
    tokens_used: Optional[int] = None
    cost: Optional[float] = None
    latency: Optional[float] = None
    error: Optional[str] = None


@dataclass
class APIRequest:
    """API request structure"""
    prompt: str
    image_data: Optional[bytes] = None
    image_format: str = "PNG"
    max_tokens: int = 5000
    temperature: float = 1.0
    model: Optional[str] = None
    provider: Optional[APIProvider] = None
    system_instruction: Optional[str] = None


class VisionAPIClient:
    """Vision API client with Ollama and Google support"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or load_config().api.__dict__
        self.logger = get_logger("vision_api_client")

        # Initialize simple Ollama provider
        endpoint = self.config.get("local_endpoint", "http://localhost:11434")
        timeout = self.config.get("timeout", 120)
        self.ollama = SimpleOllamaProvider(endpoint=endpoint, timeout=timeout)

        # Initialize Google provider only if API key is available
        self.google_provider = None
        if self.config.get("google_api_key"):
            from .google_provider import GoogleProvider
            self.google_provider = GoogleProvider(self.config)

        self.logger.info(
            "Vision API client initialized",
            ollama_available=self.ollama.is_available(),
            google_available=self.google_provider is not None,
        )

    def analyze_image(self, request: APIRequest) -> APIResponse:
        """Analyze image using the specified or current provider"""
        start_time = time.time()

        # Validate request
        self._validate_request(request)

        # Route to appropriate provider
        if request.provider == APIProvider.GOOGLE.value and self.google_provider:
            response = self._call_google(request)
        else:
            response = self._call_ollama(request)

        # Add latency to response
        response.latency = time.time() - start_time
        return response

    def _call_ollama(self, request: APIRequest) -> APIResponse:
        """Call Ollama provider"""
        model = request.model or self.config.get("local_model", "llama3.2:latest")

        result = self.ollama.chat(
            prompt=request.prompt,
            model=model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            system_instructions=request.system_instruction
        )

        if result.success:
            self.logger.info("Ollama analysis successful", model=result.model)
            return APIResponse(
                success=True,
                content=result.content,
                model=result.model,
                provider="ollama",
                cost=0.0,
            )
        else:
            self.logger.error("Ollama analysis failed", error=result.error)
            
            # Enhanced error handling for authentication issues
            if "Authentication required" in result.error:
                try:
                    from ..utils.ollama_error_handler import handle_ollama_error
                    context = {
                        'model_name': result.model,
                        'operation': 'vision_analysis'
                    }
                    handle_ollama_error(result.error, context, display_to_user=True)
                    
                    # Prompt user to sign in (only in terminal - NEVER in Telegram mode)
                    import sys
                    import os
                    # Check if running in Telegram mode via environment variable
                    is_telegram_mode = os.getenv('VEXIS_TELEGRAM_MODE', '').lower() in ('true', '1', 'yes')
                    if sys.stdin.isatty() and not is_telegram_mode:  # Only prompt if in terminal AND not in Telegram mode
                        try:
                            choice = input("\nWould you like to sign in to Ollama now? (y/n): ").lower().strip()
                            if choice in ['y', 'yes']:
                                import subprocess
                                print("\n🔐 Opening Ollama sign-in...")
                                signin_result = subprocess.run(["ollama", "signin"], capture_output=False, text=True)
                                if signin_result.returncode == 0:
                                    print("✓ Sign-in initiated. Please complete it in your browser.")
                                    print("Then try running your command again.")
                                else:
                                    print("✗ Failed to initiate sign-in.")
                        except (KeyboardInterrupt, EOFError):
                            print("\nOperation cancelled.")
                    elif is_telegram_mode:
                        # In Telegram mode, log the issue but don't block execution
                        self.logger.info("Ollama authentication required but running in Telegram mode - skipping interactive sign-in prompt")
                except ImportError:
                    pass  # Fallback to just logging the error
            return APIResponse(
                success=False,
                content="",
                model=model,
                provider="ollama",
                error=result.error,
            )

    def _call_google(self, request: APIRequest) -> APIResponse:
        """Call Google provider"""
        if not self.google_provider:
            return APIResponse(
                success=False,
                content="",
                model=request.model or "unknown",
                provider="google",
                error="Google provider not configured. Set google_api_key in config.",
            )

        try:
            return self.google_provider.analyze_image(request)
        except Exception as e:
            return APIResponse(
                success=False,
                content="",
                model=request.model or "unknown",
                provider="google",
                error=f"Google API error: {str(e)}",
            )

    def _validate_request(self, request: APIRequest):
        """Validate API request"""
        if not request.prompt:
            raise ValidationError("Prompt cannot be empty", "prompt", request.prompt)

        if len(request.prompt) > 10000:
            raise ValidationError("Prompt too long", "prompt", len(request.prompt))

        if request.image_data:
            if len(request.image_data) > 20 * 1024 * 1024:  # 20MB limit
                raise ValidationError("Image too large", "image_data", len(request.image_data))

            # Validate image format
            try:
                Image.open(io.BytesIO(request.image_data))
            except Exception as e:
                raise ValidationError(f"Invalid image format: {e}", "image_data", "format_error")

        if request.max_tokens < 1 or request.max_tokens > 7000:
            raise ValidationError("Invalid max_tokens", "max_tokens", request.max_tokens)

        if not (0.0 <= request.temperature <= 2.0):
            raise ValidationError("Invalid temperature", "temperature", request.temperature)

    def get_available_providers(self) -> List[str]:
        """Get list of available providers"""
        providers = []
        if self.ollama.is_available():
            providers.append("ollama")
        if self.google_provider:
            providers.append("google")
        return providers

    def is_ollama_available(self) -> bool:
        """Check if Ollama is available"""
        return self.ollama.is_available()
