"""
Voice Conversation Package

Provides complete voice conversation capabilities including:
- Voice Activity Detection (VAD)
- Speech-to-Text (STT) with multiple providers
- Text-to-Speech (TTS) with multiple providers
- Camera capture for multimodal input
- Voice conversation orchestration
"""

from .vad import VADConfig, EnergyVAD, AsyncVAD, create_vad
from .stt import STTConfig, STTManager, create_stt, LocalWhisperSTT, OpenAIWhisperSTT, GroqWhisperSTT
from .tts import TTSConfig, TTSManager, create_tts, PiperTTS, CoquiTTS, KokoroTTS, OpenAITTS, ElevenLabsTTS
from .camera import CameraConfig, CameraManager, MockCameraManager, create_camera
from .conversation import VoiceConfig, VoiceConversation, VoiceState, VoiceMessage, create_voice_conversation, VoiceToolCaller

__all__ = [
    # VAD
    "VADConfig",
    "EnergyVAD", 
    "AsyncVAD",
    "create_vad",
    # STT
    "STTConfig",
    "STTManager",
    "create_stt",
    "LocalWhisperSTT",
    "OpenAIWhisperSTT",
    "GroqWhisperSTT",
    # TTS
    "TTSConfig",
    "TTSManager",
    "create_tts",
    "PiperTTS",
    "CoquiTTS",
    "KokoroTTS",
    "OpenAITTS",
    "ElevenLabsTTS",
    # Camera
    "CameraConfig",
    "CameraManager",
    "MockCameraManager",
    "create_camera",
    # Conversation
    "VoiceConfig",
    "VoiceConversation",
    "VoiceState",
    "VoiceMessage",
    "create_voice_conversation",
    "VoiceToolCaller",
]