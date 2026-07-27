"""
Text-to-Speech (TTS) Module

Supports both local models (piper, kokoro, coqui) and cloud APIs (OpenAI, ElevenLabs, etc.)
with automatic model downloading and management.
"""

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator
import wave

logger = logging.getLogger(__name__)


@dataclass
class TTSConfig:
    """Configuration for TTS."""
    provider: str = "local"  # "local", "openai", "elevenlabs", "piper", "kokoro", "coqui"
    model: str = "en_US-lessac-medium"  # For local models
    voice: str = "alloy"  # For cloud APIs
    language: str = "en"
    speed: float = 1.0
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    # Local model settings
    models_dir: str = "~/.cache/clio_agent/tts_models"
    # Output settings
    sample_rate: int = 22050
    # Piper settings
    piper_binary: Optional[str] = None
    # Kokoro settings
    kokoro_model: str = "kokoro-v0.19"


class TTSProvider(ABC):
    """Abstract base class for TTS providers."""
    
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes (WAV format)."""
        pass
    
    @abstractmethod
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis for real-time playback."""
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
    
    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Output sample rate."""
        pass


class PiperTTS(TTSProvider):
    """Local TTS using Piper (fast, lightweight)."""
    
    def __init__(self, config: TTSConfig):
        self.config = config
        self._model_path = None
        self._initialized = False
        self._piper_binary = None
        
    @property
    def name(self) -> str:
        return "piper"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._model_path is not None and self._piper_binary is not None
    
    @property
    def sample_rate(self) -> int:
        return 22050  # Piper default
    
    async def _find_or_install_piper(self) -> Optional[str]:
        """Find or install Piper binary."""
        # Check config-specified binary
        if self.config.piper_binary and Path(self.config.piper_binary).exists():
            return self.config.piper_binary
        
        # Check common locations
        for path in [
            "piper",
            "/usr/local/bin/piper",
            "/opt/homebrew/bin/piper",
            str(Path.home() / ".local" / "bin" / "piper"),
        ]:
            if shutil.which(path):
                return path
        
        # Try installing via pip
        logger.info("Piper not found, installing via pip...")
        try:
            subprocess.run([
                sys.executable, "-m", "pip", "install", "piper-tts", "--quiet"
            ], check=True, capture_output=True)
            
            # Find installed binary
            for path in ["piper", str(Path.home() / ".local" / "bin" / "piper")]:
                if shutil.which(path):
                    return path
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install Piper: {e}")
        
        return None
    
    async def _download_model(self, model_name: str) -> Optional[Path]:
        """Download Piper voice model."""
        models_dir = Path(self.config.models_dir).expanduser() / "piper"
        models_dir.mkdir(parents=True, exist_ok=True)
        
        model_path = models_dir / f"{model_name}.onnx"
        config_path = models_dir / f"{model_name}.onnx.json"
        
        if model_path.exists() and config_path.exists():
            return model_path
        
        # Download from Piper voices repository
        base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
        
        try:
            import urllib.request
            
            # Download model
            model_url = f"{base_url}/en/{model_name}/{model_name}.onnx"
            config_url = f"{base_url}/en/{model_name}/{model_name}.onnx.json"
            
            logger.info(f"Downloading Piper model: {model_name}")
            urllib.request.urlretrieve(model_url, model_path)
            urllib.request.urlretrieve(config_url, config_path)
            
            return model_path
        except Exception as e:
            logger.error(f"Failed to download Piper model: {e}")
            return None
    
    async def initialize(self) -> bool:
        """Initialize Piper TTS."""
        self._piper_binary = await self._find_or_install_piper()
        if not self._piper_binary:
            logger.error("Piper binary not found and could not be installed")
            return False
        
        self._model_path = await self._download_model(self.config.model)
        if not self._model_path:
            logger.error(f"Failed to get Piper model: {self.config.model}")
            return False
        
        self._initialized = True
        logger.info(f"Piper TTS initialized with model: {self.config.model}")
        return True
    
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text using Piper."""
        if not self.is_ready:
            raise RuntimeError("Piper TTS not initialized")
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output_path = tmp.name
        
        try:
            # Run piper
            cmd = [
                self._piper_binary,
                "--model", str(self._model_path),
                "--output_file", output_path,
                "--length_scale", str(1.0 / self.config.speed),
            ]
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await proc.communicate(input=text.encode())
            
            if proc.returncode != 0:
                raise RuntimeError(f"Piper failed: {stderr.decode()}")
            
            # Read output
            with open(output_path, "rb") as f:
                audio_data = f.read()
            
            return audio_data
            
        finally:
            try:
                os.unlink(output_path)
            except:
                pass
    
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis using Piper (chunked)."""
        # For Piper, we synthesize full then chunk
        audio_data = await self.synthesize(text)
        
        # Yield in chunks
        chunk_size = 4096
        for i in range(0, len(audio_data), chunk_size):
            yield audio_data[i:i+chunk_size]


class CoquiTTS(TTSProvider):
    """Local TTS using Coqui TTS (XTTS v2, etc.)."""
    
    def __init__(self, config: TTSConfig):
        self.config = config
        self._tts = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "coqui"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._tts is not None
    
    @property
    def sample_rate(self) -> int:
        return self.config.sample_rate
    
    async def initialize(self) -> bool:
        """Initialize Coqui TTS."""
        try:
            from TTS.api import TTS
        except ImportError:
            logger.info("Coqui TTS not installed, installing...")
            try:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "coqui-tts", "--quiet"
                ], check=True, capture_output=True)
                from TTS.api import TTS
            except Exception as e:
                logger.error(f"Failed to install Coqui TTS: {e}")
                return False
        
        try:
            # Determine model
            model_name = self.config.model
            if model_name == "xtts_v2":
                model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
            elif model_name == "vits":
                model_name = "tts_models/en/ljspeech/vits"
            
            logger.info(f"Loading Coqui TTS model: {model_name}")
            
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            self._tts = await loop.run_in_executor(
                None,
                lambda: TTS(model_name).to("cuda" if self._has_cuda() else "cpu")
            )
            
            self._initialized = True
            logger.info("Coqui TTS initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to load Coqui TTS: {e}")
            return False
    
    def _has_cuda(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False
    
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text using Coqui TTS."""
        if not self.is_ready:
            raise RuntimeError("Coqui TTS not initialized")
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output_path = tmp.name
        
        try:
            loop = asyncio.get_event_loop()
            
            if "xtts" in str(self._tts.model_name).lower():
                # XTTS v2 requires speaker reference
                await loop.run_in_executor(
                    None,
                    lambda: self._tts.tts_to_file(
                        text=text,
                        file_path=output_path,
                        language=self.config.language,
                        speaker_wav=None,  # Use default
                        speed=self.config.speed,
                    )
                )
            else:
                await loop.run_in_executor(
                    None,
                    lambda: self._tts.tts_to_file(
                        text=text,
                        file_path=output_path,
                        speed=self.config.speed,
                    )
                )
            
            with open(output_path, "rb") as f:
                audio_data = f.read()
            
            return audio_data
            
        finally:
            try:
                os.unlink(output_path)
            except:
                pass
    
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis (Coqui doesn't natively stream, so chunk)."""
        audio_data = await self.synthesize(text)
        chunk_size = 4096
        for i in range(0, len(audio_data), chunk_size):
            yield audio_data[i:i+chunk_size]


class KokoroTTS(TTSProvider):
    """Local TTS using Kokoro (fast, high quality)."""
    
    def __init__(self, config: TTSConfig):
        self.config = config
        self._model = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "kokoro"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._model is not None
    
    @property
    def sample_rate(self) -> int:
        return 24000  # Kokoro default
    
    async def initialize(self) -> bool:
        """Initialize Kokoro TTS."""
        try:
            from kokoro import KPipeline
        except ImportError:
            logger.info("Kokoro not installed, installing...")
            try:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "kokoro-onnx", "--quiet"
                ], check=True, capture_output=True)
                from kokoro import KPipeline
            except Exception as e:
                logger.error(f"Failed to install Kokoro: {e}")
                return False
        
        try:
            logger.info(f"Loading Kokoro model: {self.config.kokoro_model}")
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: KPipeline(lang_code=self.config.language[0])  # 'en' -> 'e'
            )
            self._initialized = True
            logger.info("Kokoro TTS initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to load Kokoro: {e}")
            return False
    
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text using Kokoro."""
        if not self.is_ready:
            raise RuntimeError("Kokoro TTS not initialized")
        
        # Kokoro generates audio in chunks
        audio_chunks = []
        
        loop = asyncio.get_event_loop()
        generator = await loop.run_in_executor(
            None,
            lambda: self._model(text, voice=self.config.voice, speed=self.config.speed)
        )
        
        for _, _, audio in generator:
            audio_chunks.append(audio)
        
        # Combine chunks
        import numpy as np
        combined = np.concatenate(audio_chunks)
        
        # Convert to WAV bytes
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output_path = tmp.name
        
        try:
            import soundfile as sf
            await loop.run_in_executor(
                None,
                lambda: sf.write(output_path, combined, self.sample_rate)
            )
            
            with open(output_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(output_path)
            except:
                pass
    
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis using Kokoro."""
        if not self.is_ready:
            raise RuntimeError("Kokoro TTS not initialized")
        
        loop = asyncio.get_event_loop()
        generator = await loop.run_in_executor(
            None,
            lambda: self._model(text, voice=self.config.voice, speed=self.config.speed)
        )
        
        import soundfile as sf
        import io
        
        for _, _, audio in generator:
            # Write chunk to bytes
            buffer = io.BytesIO()
            await loop.run_in_executor(
                None,
                lambda: sf.write(buffer, audio, self.sample_rate, format='WAV')
            )
            buffer.seek(0)
            yield buffer.read()


class OpenAITTS(TTSProvider):
    """OpenAI TTS API."""
    
    def __init__(self, config: TTSConfig):
        self.config = config
        self._client = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "openai-tts"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._client is not None
    
    @property
    def sample_rate(self) -> int:
        return 24000  # OpenAI TTS default
    
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
        logger.info("OpenAI TTS initialized")
        return True
    
    async def synthesize(self, text: str) -> bytes:
        """Synthesize using OpenAI TTS."""
        if not self.is_ready:
            raise RuntimeError("OpenAI TTS not initialized")
        
        response = await self._client.audio.speech.create(
            model="tts-1-hd",
            voice=self.config.voice,
            input=text,
            speed=self.config.speed,
            response_format="wav"
        )
        
        return response.content
    
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis using OpenAI TTS streaming."""
        if not self.is_ready:
            raise RuntimeError("OpenAI TTS not initialized")
        
        async with self._client.audio.speech.with_streaming_response.create(
            model="tts-1-hd",
            voice=self.config.voice,
            input=text,
            speed=self.config.speed,
            response_format="wav"
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                yield chunk


class ElevenLabsTTS(TTSProvider):
    """ElevenLabs TTS API (high quality)."""
    
    def __init__(self, config: TTSConfig):
        self.config = config
        self._client = None
        self._initialized = False
        
    @property
    def name(self) -> str:
        return "elevenlabs"
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._client is not None
    
    @property
    def sample_rate(self) -> int:
        return 22050
    
    async def initialize(self) -> bool:
        """Initialize ElevenLabs client."""
        try:
            from elevenlabs.client import AsyncElevenLabs
        except ImportError:
            logger.error("elevenlabs package not installed")
            return False
        
        api_key = self.config.api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            logger.error("ElevenLabs API key not provided")
            return False
        
        self._client = AsyncElevenLabs(api_key=api_key)
        self._initialized = True
        logger.info("ElevenLabs TTS initialized")
        return True
    
    async def synthesize(self, text: str) -> bytes:
        """Synthesize using ElevenLabs."""
        if not self.is_ready:
            raise RuntimeError("ElevenLabs TTS not initialized")
        
        audio = await self._client.text_to_speech.convert(
            voice_id=self.config.voice,
            text=text,
            model_id="eleven_multilingual_v2",
            output_format="wav_22050",
        )
        
        # Collect chunks
        chunks = []
        async for chunk in audio:
            chunks.append(chunk)
        return b"".join(chunks)
    
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis using ElevenLabs."""
        if not self.is_ready:
            raise RuntimeError("ElevenLabs TTS not initialized")
        
        audio = await self._client.text_to_speech.convert(
            voice_id=self.config.voice,
            text=text,
            model_id="eleven_multilingual_v2",
            output_format="wav_22050",
        )
        
        async for chunk in audio:
            yield chunk


class TTSManager:
    """Manages TTS providers."""
    
    PROVIDERS = {
        "piper": PiperTTS,
        "coqui": CoquiTTS,
        "kokoro": KokoroTTS,
        "openai": OpenAITTS,
        "elevenlabs": ElevenLabsTTS,
    }
    
    # Voice mappings for OpenAI
    OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    
    def __init__(self, config: TTSConfig = None):
        self.config = config or TTSConfig()
        self._provider: Optional[TTSProvider] = None
        self._initialized = False
    
    @property
    def provider(self) -> Optional[TTSProvider]:
        return self._provider
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._provider is not None and self._provider.is_ready
    
    @property
    def sample_rate(self) -> int:
        if self._provider:
            return self._provider.sample_rate
        return self.config.sample_rate
    
    async def initialize(self) -> bool:
        """Initialize the configured TTS provider."""
        provider_class = self.PROVIDERS.get(self.config.provider)
        if not provider_class:
            logger.error(f"Unknown TTS provider: {self.config.provider}")
            return False
        
        self._provider = provider_class(self.config)
        self._initialized = await self._provider.initialize()
        return self._initialized
    
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio."""
        if not self.is_ready:
            raise RuntimeError("TTS not initialized. Call initialize() first.")
        return await self._provider.synthesize(text)
    
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream synthesis."""
        if not self.is_ready:
            raise RuntimeError("TTS not initialized. Call initialize() first.")
        async for chunk in self._provider.synthesize_stream(text):
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


async def create_tts(config: TTSConfig = None) -> TTSManager:
    """Factory function to create and initialize TTS manager."""
    manager = TTSManager(config)
    await manager.initialize()
    return manager