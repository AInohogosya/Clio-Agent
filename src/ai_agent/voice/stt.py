"""
Speech-to-Text (STT) Module

Supports both local models (faster-whisper) and cloud APIs (OpenAI Whisper, etc.)
with automatic model downloading and management.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
import tempfile

logger = logging.getLogger(__name__)


@dataclass
class STTConfig:
    """Configuration for STT."""
    provider: str = "local"  # "local", "openai", "groq", "deepgram"
    model: str = "base"  # For local: tiny, base, small, medium, large-v3
    language: Optional[str] = None  # None for auto-detect
    device: str = "auto"  # "cpu", "cuda", "auto"
    compute_type: str = "auto"  # "float16", "int8", "auto"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    # Local model settings
    models_dir: str = "~/.cache/clio_agent/stt_models"
    # VAD settings for local models
    vad_filter: bool = True
    vad_parameters: Optional[Dict[str, Any]] = None


class STTProvider(ABC):
    """Abstract base class for STT providers."""
    
    @abstractmethod
    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribe audio data to text."""
        pass
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the provider. Returns True on success."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass
    
    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Check if provider is ready."""
        pass


class LocalWhisperSTT(STTProvider):
    """Local Whisper STT using faster-whisper."""
    
    def __init__(self, config: STTConfig):
        self.config = config
        self._model = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "faster-whisper"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._model is not None
    
    async def initialize(self) -> bool:
        """Initialize faster-whisper model."""
        try:
            # Try importing faster-whisper
            from faster_whisper import WhisperModel
        except ImportError:
            logger.info("faster-whisper not installed, installing...")
            if not await self._install_faster_whisper():
                return False
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.error("Failed to import faster-whisper after installation")
                return False
        
        # Determine device
        device = self.config.device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        
        # Determine compute type
        compute_type = self.config.compute_type
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        
        # Model path
        model_size = self.config.model
        models_dir = Path(self.config.models_dir).expanduser()
        models_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Loading Whisper model: {model_size} on {device} ({compute_type})")
        
        try:
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                download_root=str(models_dir),
                local_files_only=False
            )
            self._initialized = True
            logger.info("Local Whisper model loaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            return False
    
    async def _install_faster_whisper(self) -> bool:
        """Install faster-whisper package."""
        try:
            subprocess.run([
                sys.executable, "-m", "pip", "install", 
                "faster-whisper", "--quiet"
            ], check=True, capture_output=True)
            logger.info("faster-whisper installed successfully")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install faster-whisper: {e}")
            return False
    
    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribe audio using local Whisper model."""
        if not self.is_ready:
            raise RuntimeError("STT provider not initialized")
        
        # Write audio to temp file (faster-whisper works with files)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            # Write WAV header
            import wave
            with wave.open(tmp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)
        
        try:
            # Run transcription in executor to avoid blocking
            loop = asyncio.get_event_loop()
            segments, info = await loop.run_in_executor(
                None,
                lambda: self._model.transcribe(
                    tmp_path,
                    language=self.config.language,
                    vad_filter=self.config.vad_filter,
                    vad_parameters=self.config.vad_parameters or {},
                    beam_size=5,
                    best_of=5,
                    temperature=0.0,
                )
            )
            
            # Combine segments
            text = " ".join([seg.text for seg in segments]).strip()
            
            logger.debug(f"Transcribed: {text[:100]}... (language: {info.language})")
            return text
            
        finally:
            # Cleanup temp file
            try:
                os.unlink(tmp_path)
            except:
                pass


class OpenAIWhisperSTT(STTProvider):
    """OpenAI Whisper API STT."""
    
    def __init__(self, config: STTConfig):
        self.config = config
        self._client = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "openai-whisper"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._client is not None
    
    async def initialize(self) -> bool:
        """Initialize OpenAI client."""
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
            base_url=base_url
        )
        self._initialized = True
        logger.info("OpenAI Whisper API initialized")
        return True
    
    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribe using OpenAI Whisper API."""
        if not self.is_ready:
            raise RuntimeError("STT provider not initialized")
        
        # Write audio to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            import wave
            with wave.open(tmp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)
        
        try:
            with open(tmp_path, "rb") as audio_file:
                response = await self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=self.config.language,
                    response_format="text"
                )
            return response.strip()
        finally:
            try:
                os.unlink(tmp_path)
            except:
                pass


class GroqWhisperSTT(STTProvider):
    """Groq Whisper API STT (fast)."""
    
    def __init__(self, config: STTConfig):
        self.config = config
        self._client = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "groq-whisper"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._client is not None
    
    async def initialize(self) -> bool:
        """Initialize Groq client."""
        try:
            from groq import AsyncGroq
        except ImportError:
            logger.error("groq package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            logger.error("Groq API key not provided")
            return False
        
        self._client = AsyncGroq(api_key=api_key)
        self._initialized = True
        logger.info("Groq Whisper API initialized")
        return True
    
    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribe using Groq Whisper API."""
        if not self.is_ready:
            raise RuntimeError("STT provider not initialized")
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            import wave
            with wave.open(tmp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)
        
        try:
            with open(tmp_path, "rb") as audio_file:
                response = await self._client.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=audio_file,
                    language=self.config.language,
                    response_format="text"
                )
            return response.strip()
        finally:
            try:
                os.unlink(tmp_path)
            except:
                pass


class STTManager:
    """Manages STT providers and handles initialization."""
    
    PROVIDERS = {
        "local": LocalWhisperSTT,
        "faster-whisper": LocalWhisperSTT,
        "openai": OpenAIWhisperSTT,
        "groq": GroqWhisperSTT,
    }
    
    def __init__(self, config: STTConfig = None):
        self.config = config or STTConfig()
        self._provider: Optional[STTProvider] = None
        self._initialized = False
    
    @property
    def provider(self) -> Optional[STTProvider]:
        return self._provider
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._provider is not None and self._provider.is_ready
    
    async def initialize(self) -> bool:
        """Initialize the configured STT provider."""
        provider_class = self.PROVIDERS.get(self.config.provider)
        if not provider_class:
            logger.error(f"Unknown STT provider: {self.config.provider}")
            return False
        
        self._provider = provider_class(self.config)
        self._initialized = await self._provider.initialize()
        return self._initialized
    
    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribe audio using the active provider."""
        if not self.is_ready:
            raise RuntimeError("STT not initialized. Call initialize() first.")
        return await self._provider.transcribe(audio_data, sample_rate)
    
    def switch_provider(self, provider_name: str, **kwargs) -> bool:
        """Switch to a different provider."""
        if provider_name not in self.PROVIDERS:
            logger.error(f"Unknown provider: {provider_name}")
            return False
        
        # Update config
        self.config.provider = provider_name
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        
        # Reinitialize
        self._initialized = False
        return asyncio.create_task(self.initialize())


async def create_stt(config: STTConfig = None) -> STTManager:
    """Factory function to create and initialize STT manager."""
    manager = STTManager(config)
    await manager.initialize()
    return manager