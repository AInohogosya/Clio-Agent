"""
Voice Conversation Manager

Orchestrates the complete voice conversation pipeline:
VAD -> STT -> Camera Capture -> LLM -> TTS -> Playback
"""

import asyncio
import logging
import time
import base64
import json
from dataclasses import dataclass, field
from typing import Optional, Callable, AsyncGenerator, Dict, Any, List
from enum import Enum
from pathlib import Path
import uuid

from .vad import EnergyVAD, VADConfig, AsyncVAD
from .stt import STTManager, STTConfig
from .tts import TTSManager, TTSConfig
from .camera import CameraManager, CameraConfig
from ..llm import LLMManager, LLMConfig, LLMMessage
from ..context import ConversationContext, ContextManager

logger = logging.getLogger(__name__)


class VoiceState(Enum):
    """Voice conversation states."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class VoiceConfig:
    """Configuration for voice conversation."""
    # VAD
    vad_config: VADConfig = field(default_factory=VADConfig)
    # STT
    stt_config: STTConfig = field(default_factory=STTConfig)
    # TTS
    tts_config: TTSConfig = field(default_factory=TTSConfig)
    # Camera
    camera_config: CameraConfig = field(default_factory=CameraConfig)
    # LLM
    llm_config: LLMConfig = field(default_factory=LLMConfig)
    # Conversation
    system_prompt: str = """You are a helpful AI assistant in a voice conversation. 
Keep your responses concise and natural - speak as you would in a real conversation.
Limit responses to 2-3 sentences unless the user asks for more detail.
Be friendly, conversational, and engaging."""
    voice_system_prompt: str = """You are a helpful AI assistant in a voice conversation with camera input.
Keep your responses BRIEF and CONVERSATIONAL - 1-2 sentences max.
You can see what the user is showing via camera. Comment naturally on what you see.
Speak as you would in a real voice chat - casual, friendly, concise."""
    max_history: int = 20
    auto_capture_camera: bool = True
    capture_on_speech_end: bool = True
    # Audio playback
    sample_rate: int = 16000
    # Callbacks
    on_state_change: Optional[Callable[[VoiceState], None]] = None
    on_transcription: Optional[Callable[[str], None]] = None
    on_response: Optional[Callable[[str], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None


@dataclass
class VoiceMessage:
    """A message in the voice conversation."""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    audio_duration: Optional[float] = None
    image_base64: Optional[str] = None


class VoiceConversation:
    """Main voice conversation manager."""
    
    def __init__(self, config: VoiceConfig = None):
        self.config = config or VoiceConfig()
        self._state = VoiceState.IDLE
        self._running = False
        
        # Components
        self._vad: Optional[AsyncVAD] = None
        self._stt: Optional[STTManager] = None
        self._tts: Optional[TTSManager] = None
        self._camera: Optional[CameraManager] = None
        self._llm: Optional[LLMManager] = None
        self._context: Optional[ConversationContext] = None
        
        # Audio playback
        self._audio_player = None
        self._playback_task: Optional[asyncio.Task] = None
        
        # Conversation history
        self._history: List[VoiceMessage] = []
        self._current_utterance = ""
        
        # Tasks
        self._vad_task: Optional[asyncio.Task] = None
        self._processing_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self._state_callbacks: List[Callable[[VoiceState], None]] = []
        if self.config.on_state_change:
            self._state_callbacks.append(self.config.on_state_change)
    
    @property
    def state(self) -> VoiceState:
        return self._state
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def history(self) -> List[VoiceMessage]:
        return self._history
    
    def add_state_callback(self, callback: Callable[[VoiceState], None]):
        """Add a state change callback."""
        self._state_callbacks.append(callback)
    
    def _set_state(self, state: VoiceState):
        """Set state and notify callbacks."""
        if self._state != state:
            self._state = state
            logger.info(f"Voice state: {state.value}")
            for cb in self._state_callbacks:
                try:
                    cb(state)
                except Exception as e:
                    logger.error(f"State callback error: {e}")
    
    async def initialize(self) -> bool:
        """Initialize all components."""
        logger.info("Initializing voice conversation components...")
        
        # Initialize VAD
        self._vad = AsyncVAD(self.config.vad_config)
        
        # Initialize STT
        self._stt = STTManager(self.config.stt_config)
        if not await self._stt.initialize():
            logger.error("Failed to initialize STT")
            return False
        
        # Initialize TTS
        self._tts = TTSManager(self.config.tts_config)
        if not await self._tts.initialize():
            logger.error("Failed to initialize TTS")
            return False
        
        # Initialize Camera
        self._camera = CameraManager(self.config.camera_config)
        if not await self._camera.initialize():
            logger.warning("Camera initialization failed, continuing without camera")
            self._camera = None
        
        # Initialize LLM
        self._llm = LLMManager(self.config.llm_config)
        if not await self._llm.initialize():
            logger.error("Failed to initialize LLM")
            return False
        
        # Initialize Context
        self._context = ConversationContext(
            system_prompt=self.config.system_prompt,
            max_messages=self.config.max_history
        )
        
        # Initialize audio player
        await self._init_audio_player()
        
        logger.info("Voice conversation initialized successfully")
        return True
    
    async def _init_audio_player(self):
        """Initialize audio playback."""
        try:
            import pyaudio
            self._audio_player = pyaudio.PyAudio()
        except ImportError:
            logger.warning("pyaudio not available, audio playback disabled")
            self._audio_player = None
    
    async def start(self):
        """Start voice conversation."""
        if self._running:
            return
        
        if not self._vad or not self._stt or not self._tts or not self._llm:
            raise RuntimeError("Not initialized. Call initialize() first.")
        
        self._running = True
        self._set_state(VoiceState.LISTENING)
        
        # Start VAD listening
        await self._vad.start()
        self._vad_task = asyncio.create_task(self._vad_listen_loop())
        
        logger.info("Voice conversation started")
    
    async def stop(self):
        """Stop voice conversation."""
        if not self._running:
            return
        
        self._running = False
        self._set_state(VoiceState.STOPPED)
        
        # Stop VAD
        if self._vad:
            await self._vad.stop()
        
        # Cancel tasks
        if self._vad_task:
            self._vad_task.cancel()
            try:
                await self._vad_task
            except asyncio.CancelledError:
                pass
        
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        
        # Stop audio playback
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
        
        # Release resources
        if self._camera:
            self._camera.release()
        
        if self._audio_player:
            self._audio_player.terminate()
        
        logger.info("Voice conversation stopped")
    
    async def _vad_listen_loop(self):
        """Main VAD listening loop."""
        try:
            async for utterance in self._vad:
                if not self._running:
                    break
                
                # Process utterance
                self._processing_task = asyncio.create_task(
                    self._process_utterance(utterance)
                )
                # Don't await - let it run in background
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"VAD loop error: {e}")
            self._set_state(VoiceState.ERROR)
            if self.config.on_error:
                self.config.on_error(e)
    
    async def _process_utterance(self, audio_data: bytes):
        """Process a single utterance: STT -> Camera -> LLM -> TTS -> Playback."""
        try:
            self._set_state(VoiceState.PROCESSING)
            
            # 1. Speech to Text
            logger.debug("Transcribing audio...")
            text = await self._stt.transcribe(audio_data, self.config.sample_rate)
            
            if not text or not text.strip():
                logger.debug("Empty transcription, skipping")
                self._set_state(VoiceState.LISTENING)
                return
            
            text = text.strip()
            logger.info(f"User said: {text}")
            
            # Callback
            if self.config.on_transcription:
                self.config.on_transcription(text)
            
            # Add to history
            self._history.append(VoiceMessage(role="user", content=text))
            self._context.add_message("user", text)
            
            # 2. Capture camera frame (simultaneously with STT or after)
            image_base64 = None
            if self.config.auto_capture_camera and self._camera and self._camera.is_ready:
                if self.config.capture_on_speech_end:
                    image_base64 = await self._camera.get_frame_base64_for_llm()
            
            # 3. Get LLM response
            self._set_state(VoiceState.PROCESSING)
            
            # Use voice-optimized system prompt if camera captured
            system_prompt = self.config.voice_system_prompt if image_base64 else self.config.system_prompt
            
            # Prepare messages for LLM
            messages = [LLMMessage(role="system", content=system_prompt)]
            
            # Add conversation history
            for msg in self._history[-(self.config.max_history-1):]:
                messages.append(LLMMessage(role=msg.role, content=msg.content))
            
            # If we have an image, add it to the last user message
            if image_base64:
                # Modify last user message to include image
                if messages and messages[-1].role == "user":
                    messages[-1] = LLMMessage(
                        role="user",
                        content=[
                            {"type": "text", "text": text},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                        ]
                    )
            
            response = await self._llm.chat(messages)
            
            if not response:
                logger.warning("Empty LLM response")
                self._set_state(VoiceState.LISTENING)
                return
            
            logger.info(f"Assistant: {response}")
            
            # Add to history
            self._history.append(VoiceMessage(
                role="assistant", 
                content=response,
                image_base64=image_base64
            ))
            self._context.add_message("assistant", response)
            
            # Callback
            if self.config.on_response:
                self.config.on_response(response)
            
            # 4. Text to Speech
            self._set_state(VoiceState.SPEAKING)
            
            audio_data = await self._tts.synthesize(response)
            
            # 5. Play audio
            await self._play_audio(audio_data)
            
            # Done
            self._set_state(VoiceState.LISTENING)
            
        except Exception as e:
            logger.error(f"Error processing utterance: {e}")
            self._set_state(VoiceState.ERROR)
            if self.config.on_error:
                self.config.on_error(e)
            self._set_state(VoiceState.LISTENING)
    
    async def _play_audio(self, audio_data: bytes):
        """Play audio data."""
        if not self._audio_player:
            logger.warning("No audio player available")
            return
        
        try:
            # Write to temp WAV file and play
            import tempfile
            import wave
            
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
                
                # Write WAV header
                with wave.open(tmp_path, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self._tts.sample_rate)
                    wf.writeframes(audio_data)
            
            # Play using pyaudio
            def play():
                import pyaudio
                p = pyaudio.PyAudio()
                with wave.open(tmp_path, 'rb') as wf:
                    stream = p.open(
                        format=p.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True
                    )
                    data = wf.readframes(1024)
                    while data:
                        stream.write(data)
                        data = wf.readframes(1024)
                    stream.stop_stream()
                    stream.close()
                p.terminate()
                
                # Cleanup
                try:
                    import os
                    os.unlink(tmp_path)
                except:
                    pass
            
            # Run in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, play)
            
        except Exception as e:
            logger.error(f"Audio playback error: {e}")
    
    async def send_text(self, text: str):
        """Send text message directly (bypass voice)."""
        if not self._running:
            await self.start()
        
        # Add to history
        self._history.append(VoiceMessage(role="user", content=text))
        self._context.add_message("user", text)
        
        # Get response
        messages = [LLMMessage(role="system", content=self.config.system_prompt)]
        for msg in self._history[-(self.config.max_history-1):]:
            messages.append(LLMMessage(role=msg.role, content=msg.content))
        
        response = await self._llm.chat(messages)
        
        if response:
            self._history.append(VoiceMessage(role="assistant", content=response))
            self._context.add_message("assistant", response)
            
            if self.config.on_response:
                self.config.on_response(response)
            
            # TTS and play
            audio_data = await self._tts.synthesize(response)
            await self._play_audio(audio_data)
        
        return response
    
    def get_conversation_context(self) -> str:
        """Get formatted conversation context."""
        return self._context.get_context_for_prompt()
    
    def clear_history(self):
        """Clear conversation history."""
        self._history.clear()
        if self._context:
            self._context.clear()
    
    def register_tool(self, tool_name: str, tool_func):
        """Register a tool for voice tool calling."""
        self._tool_caller.register_tool(tool_name, tool_func)


class VoiceToolCaller:
    """Tool caller for voice conversation - enables AI to call tools during voice chat."""
    
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
    
    def register_tool(self, name: str, func: Callable):
        """Register a tool function."""
        self._tools[name] = func
    
    async def call_tool(self, name: str, **kwargs) -> Any:
        """Call a registered tool."""
        if name not in self._tools:
            raise ValueError(f"Tool not found: {name}")
        
        tool = self._tools[name]
        if asyncio.iscoroutinefunction(tool):
            return await tool(**kwargs)
        else:
            return tool(**kwargs)
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible tool definitions."""
        # This would integrate with the tool registry
        return []


async def create_voice_conversation(config: VoiceConfig = None) -> VoiceConversation:
    """Factory function to create and initialize voice conversation."""
    conv = VoiceConversation(config)
    await conv.initialize()
    return conv