"""
External Integration Layer for AI Agent System
Vision API client and model runner for AI communication
"""

from .ollama_provider import SimpleOllamaProvider
from .model_runner import ModelRunner

# Note: VisionAPIClient is available but not auto-imported to avoid PIL dependency
# from .vision_api_client import VisionAPIClient

__all__ = [
    "ModelRunner",
    "SimpleOllamaProvider",
]
