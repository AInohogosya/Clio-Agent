"""
Simplified Ollama Provider for VEXIS-CLI
Direct API calls to Ollama - no magic, no auto-fixes
"""

import re
import requests
import json
from typing import Optional, Dict, Any
from dataclasses import dataclass
from ..utils.logger import get_logger


@dataclass
class OllamaResponse:
    """Simple Ollama response"""
    success: bool
    content: str
    model: str
    error: Optional[str] = None


def _model_has_size(model_str: str, size_str: str) -> bool:
    """Check if model name contains size as a standalone token.

    Uses word-boundary matching to avoid false positives:
      "7b"  should NOT match "70b" or "14b"
      "70b" should match "llama3.1:70b" but NOT "llama3.1:700b"
    """
    return bool(re.search(r'(?<![0-9])' + re.escape(size_str) + r'(?![0-9])', model_str))


class SimpleOllamaProvider:
    """
    Simple Ollama provider that just calls the API.
    No auto-signin, no complex error handling.
    """

    # Valid Ollama models (local or cloud)
    DEFAULT_MODEL = "llama4-scout-17b"

    def __init__(self, endpoint: str = "http://localhost:11434", timeout: int = 120):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.logger = get_logger("ollama_provider")

    def chat(self, prompt: str, model: Optional[str] = None, temperature: float = 1.0, max_tokens: int = 5000, system_instructions: Optional[str] = None) -> OllamaResponse:
        """
        Send a chat request to Ollama.

        Args:
            prompt: The prompt to send
            model: Model name (defaults to llama4-scout-17b)
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            system_instructions: Optional system instructions to include

        Returns:
            OllamaResponse with success flag and content or error
        """
        model = model or self.DEFAULT_MODEL

        # Enhanced cloud model validation and fixes
        if model and ":cloud" in model:
            # Check if user is signed in before attempting cloud model
            try:
                import subprocess
                result = subprocess.run(["ollama", "whoami"], capture_output=True, text=True, timeout=5)
                is_signed_in = result.returncode == 0 and result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError, TimeoutError):
                is_signed_in = False
            except Exception:
                is_signed_in = False

            if not is_signed_in:
                # Provide helpful error before making the request
                alternatives = self._get_cloud_alternatives(model)
                return OllamaResponse(
                    success=False,
                    content="",
                    model=model,
                    error=f"🔐 Cloud model '{model}' requires Ollama sign-in.\n\n{alternatives}\n\n📋 **To sign in**:\n1. Run: ollama signin\n2. Complete sign-in in browser\n3. Verify: ollama whoami\n4. Try again"
                )

        # Fix common model name errors
        if model == "qwen3.5:27b-cloud":
            model = "qwen3.5:27b"
        elif model and ":cloud" in model:
            # Validate cloud models exist
            from ..utils.model_definitions import MODEL_FAMILIES
            model_exists = False
            for family_data in MODEL_FAMILIES.values():
                for subfamily_data in family_data.get("subfamilies", {}).values():
                    if model in subfamily_data.get("models", {}):
                        model_exists = True
                        break
                if model_exists:
                    break
            if not model_exists:
                # Try to find similar model without -cloud suffix
                base_model = model.replace("-cloud", "")
                for family_data in MODEL_FAMILIES.values():
                    for subfamily_data in family_data.get("subfamilies", {}).values():
                        if base_model in subfamily_data.get("models", {}):
                            model = base_model
                            model_exists = True
                            break
                    if model_exists:
                        break

        # Build messages array with proper system instruction support
        messages = []
        if system_instructions:
            messages.append({"role": "system", "content": system_instructions})
        messages.append({"role": "user", "content": prompt})

        # Define payload first (needed for task type detection)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

        # Progressive timeout scaling based on model size and task type
        if model:
            # Check if this is task generation (longer prompt) vs command execution (shorter prompt)
            is_task_generation = any(keyword in prompt.lower() for keyword in [
                "convert this user instruction", "cli assistant", "generate numbered steps",
                "task into specific command-line steps", "output format"
            ])

            # Show loading indicator for large models
            # Use regex word-boundary matching to avoid false positives
            # (e.g. "7b" should NOT match "70b", "14b" should NOT match "114b")
            if any(_model_has_size(model, s) for s in ["671b", "397b", "235b"]):
                if is_task_generation:
                    print(f"🔄 Processing complex task with extra-large model '{model}'... This may take 8-12 minutes due to prompt complexity.")
                    timeout = 720  # 12 minutes for task generation with extra-large models
                else:
                    print(f"🔄 Loading extra-large model '{model}'... This may take 5-10 minutes.")
                    timeout = 600  # 10 minutes for extra-large models
            elif any(_model_has_size(model, s) for s in ["122b", "70b", "35b"]) or ":cloud" in model:
                if is_task_generation:
                    print(f"🔄 Processing complex task with large model '{model}'... This may take 5-8 minutes due to prompt complexity.")
                    timeout = 480  # 8 minutes for task generation with large models
                else:
                    print(f"🔄 Loading large model '{model}'... This may take 3-7 minutes.")
                    timeout = 420  # 7 minutes for large/cloud models
            elif any(_model_has_size(model, s) for s in ["32b", "30b"]):
                if is_task_generation:
                    print(f"🔄 Processing complex task with medium-large model '{model}'... This may take 3-5 minutes due to prompt complexity.")
                    timeout = 360  # 6 minutes for task generation with medium-large models
                else:
                    print(f"🔄 Loading medium-large model '{model}'... This may take 2-5 minutes.")
                    timeout = 300  # 5 minutes for medium-large models
            elif any(_model_has_size(model, s) for s in ["27b", "14b", "13b", "12b", "10b", "9b", "8b", "7b"]):
                if is_task_generation:
                    print(f"🔄 Processing complex task with model '{model}'... This may take 2-4 minutes due to prompt complexity.")
                    timeout = 240  # 4 minutes for task generation with small-medium models
                else:
                    print(f"🔄 Loading model '{model}'... This may take 1-3 minutes.")
                    timeout = 180  # 3 minutes for small-medium models
            else:
                timeout = self.timeout
        else:
            timeout = self.timeout

        try:
            # Log request details for debugging
            self.logger.debug(f"Making request to {self.endpoint}/api/chat",
                            model=model,
                            timeout=timeout,
                            payload_size=len(str(payload)))

            response = requests.post(
                f"{self.endpoint}/api/chat",
                json=payload,
                timeout=timeout
            )

            # Log response details for debugging
            self.logger.debug(f"Response received",
                            status_code=response.status_code,
                            headers=dict(response.headers),
                            content_length=len(response.text))

            if response.status_code == 200:
                result = response.json()
                content = result.get("message", {}).get("content", "")
                return OllamaResponse(success=True, content=content, model=model)

            # Handle specific error cases
            error_text = response.text
            self.logger.error(f"Ollama API error",
                           status_code=response.status_code,
                           error_text=error_text[:500] if error_text else "No error text",
                           model=model)

            if response.status_code == 401:
                return OllamaResponse(
                    success=False,
                    content="",
                    model=model,
                    error=f"🔐 Authentication required. Run 'ollama signin' in your terminal, then try again."
                )

            if response.status_code == 404:
                return OllamaResponse(
                    success=False,
                    content="",
                    model=model,
                    error=f"Model '{model}' not found. Run 'ollama pull {model}' to download it."
                )

            # Check for cloud model authentication issues
            if ":cloud" in model and (response.status_code == 403 or response.status_code == 401):
                # Enhanced cloud model error handling
                error_msg = f"🔐 Cloud model '{model}' requires authentication. Please run 'ollama signin' and complete the sign-in process in your browser."

                # Provide specific guidance for common cloud models
                if "qwen3.5" in model:
                    error_msg += f"\n\n💡 **Alternative**: Use 'qwen3.5:27b' (local version) for similar performance without sign-in."
                elif "mistral-large" in model:
                    error_msg += f"\n\n💡 **Alternative**: Use 'mistral:7b' or 'mistral-small:latest' for local processing."
                elif "glm" in model:
                    error_msg += f"\n\n💡 **Alternative**: Use 'gemma3:4b' or 'llama3.2:3b' for efficient local processing."

                error_msg += f"\n\n📋 **Steps to fix**:\n1. Run: ollama signin\n2. Complete sign-in in browser\n3. Verify: ollama whoami\n4. Try again"

                return OllamaResponse(
                    success=False,
                    content="",
                    model=model,
                    error=error_msg
                )

            # Check for rate limiting on cloud models
            if ":cloud" in model and response.status_code == 429:
                return OllamaResponse(
                    success=False,
                    content="",
                    model=model,
                    error=f"⏱️ Cloud model '{model}' rate limited. Please wait a few minutes and try again."
                )

            return OllamaResponse(
                success=False,
                content="",
                model=model,
                error=f"Ollama error (HTTP {response.status_code}): {error_text}"
            )

        except requests.exceptions.Timeout:
            # Handle timeout with size-specific guidance
            if model:
                # Determine model category for better error messaging
                if any(_model_has_size(model, s) for s in ["671b", "397b", "235b"]):
                    model_category = "extra-large"
                    suggestion = "Consider using a smaller model for faster responses (e.g., 9B-27B variants)."
                elif any(_model_has_size(model, s) for s in ["122b", "70b", "35b"]) or ":cloud" in model:
                    model_category = "large"
                    suggestion = "Try again or use a medium model (e.g., 9B-27B variants) for quicker responses."
                elif any(_model_has_size(model, s) for s in ["32b", "30b"]):
                    model_category = "medium-large"
                    suggestion = "Consider using a smaller model for faster responses."
                else:
                    model_category = "medium"
                    suggestion = "Model may still be loading. Try again in a moment."

                if ":cloud" in model:
                    return OllamaResponse(
                        success=False,
                        content="",
                        model=model,
                        error=f"🌩 Cloud {model_category} model '{model}' timed out after {timeout}s. Cloud models require stable internet. {suggestion}"
                    )
                else:
                    return OllamaResponse(
                        success=False,
                        content="",
                        model=model,
                        error=f"⏱️ {model_category} model '{model}' timed out after {timeout}s. Model may be loading. {suggestion}"
                    )
            else:
                return OllamaResponse(
                    success=False,
                    content="",
                    model="unknown",
                    error=f"Request timed out after {timeout}s. Please try again."
                )
        except requests.exceptions.ConnectionError as e:
            return OllamaResponse(
                success=False,
                content="",
                model=model,
                error=f"Connection failed: {str(e)}. Check Ollama is running with 'ollama serve'."
            )
        except Exception as e:
            return OllamaResponse(
                success=False,
                content="",
                model=model,
                error=f"Unexpected error: {str(e)}"
            )

    def _get_cloud_alternatives(self, cloud_model: str) -> str:
        """Get local model alternatives for cloud models"""
        alternatives = "💡 **Local alternatives (no sign-in required)**:\n"

        if "qwen3.5" in cloud_model:
            alternatives += "• qwen3.5:27b (similar performance)\n• qwen3.5:9b (faster)\n• qwen3:8b (efficient)"
        elif "mistral-large" in cloud_model:
            alternatives += "• mistral:7b (balanced)\n• mistral-small:latest (fast)\n• mistral:7b-instruct (optimized)"
        elif "glm" in cloud_model:
            alternatives += "• gemma3:4b (efficient)\n• llama3.2:3b (lightweight)\n• qwen3:4b (multilingual)"
        elif "deepseek" in cloud_model:
            alternatives += "• deepseek-r1:14b (reasoning)\n• deepseek-r1:8b (faster)\n• qwen2.5:14b (strong)"
        elif "nvidia" in cloud_model:
            alternatives += "• nemotron-3-8b (efficient)\n• chatglm3:6b (conversational)\n• llama3.2:3b (lightweight)"
        else:
            alternatives += "• gemma3:4b (efficient all-rounder)\n• llama3.2:3b (lightweight)\n• qwen3:4b (multilingual)\n• mistral:7b (balanced)"

        return alternatives

    def is_available(self) -> bool:
        """Check if Ollama is running"""
        try:
            response = requests.get(f"{self.endpoint}/api/tags", timeout=5)
            return response.status_code == 200
        except (requests.RequestException, TimeoutError):
            return False
