"""
Voice Activity Detection (VAD) Module

Detects voice activity from microphone input using energy-based VAD.
Triggers recording when volume rises above threshold and stops when it falls.
"""

import asyncio
import logging
import numpy as np
import pyaudio
from dataclasses import dataclass
from typing import Callable, Optional, AsyncGenerator
from collections import deque
import threading
import time

logger = logging.getLogger(__name__)


@dataclass
class VADConfig:
    """Configuration for Voice Activity Detection."""
    sample_rate: int = 16000
    frame_duration_ms: int = 30  # Frame duration in ms
    padding_duration_ms: int = 300  # Padding before/after speech
    energy_threshold: float = 0.01  # Energy threshold for speech detection
    silence_threshold: float = 0.005  # Lower threshold for end of speech
    min_speech_duration_ms: int = 300  # Minimum speech duration
    max_silence_duration_ms: int = 1000  # Max silence before ending
    channels: int = 1
    chunk_size: int = 1024


class EnergyVAD:
    """Energy-based Voice Activity Detection."""
    
    def __init__(self, config: VADConfig = None):
        self.config = config or VADConfig()
        self._audio = None
        self._stream = None
        self._running = False
        self._callback: Optional[Callable[[bytes], None]] = None
        
        # Calculated values
        self.frame_size = int(self.config.sample_rate * self.config.frame_duration_ms / 1000)
        self.padding_frames = int(self.config.padding_duration_ms / self.config.frame_duration_ms)
        self.min_speech_frames = int(self.config.min_speech_duration_ms / self.config.frame_duration_ms)
        self.max_silence_frames = int(self.config.max_silence_duration_ms / self.config.frame_duration_ms)
        
        # State
        self._buffer = deque(maxlen=self.padding_frames)
        self._speech_frames = []
        self._silence_frames = 0
        self._in_speech = False
        
    def _calculate_energy(self, frame: bytes) -> float:
        """Calculate normalized energy of audio frame."""
        audio_data = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        if len(audio_data) == 0:
            return 0.0
        # Normalize to [-1, 1]
        audio_data = audio_data / 32768.0
        # RMS energy
        energy = np.sqrt(np.mean(audio_data ** 2))
        return float(energy)
    
    def _process_frame(self, frame: bytes) -> Optional[bytes]:
        """Process a single audio frame. Returns complete utterance or None."""
        energy = self._calculate_energy(frame)
        
        is_speech = energy > self.config.energy_threshold
        
        if not self._in_speech:
            # Not currently in speech - buffer frames for padding
            self._buffer.append(frame)
            
            if is_speech:
                # Speech started
                self._in_speech = True
                self._speech_frames = list(self._buffer)
                self._buffer.clear()
                self._silence_frames = 0
            return None
        else:
            # In speech
            self._speech_frames.append(frame)
            
            if is_speech:
                self._silence_frames = 0
            else:
                self._silence_frames += 1
                
            # Check if speech ended
            if self._silence_frames >= self.max_silence_frames:
                # Check minimum speech duration
                if len(self._speech_frames) >= self.min_speech_frames:
                    utterance = b''.join(self._speech_frames)
                    self._reset_state()
                    return utterance
                else:
                    # Too short, treat as noise
                    self._reset_state()
            return None
    
    def _reset_state(self):
        """Reset VAD state."""
        self._in_speech = False
        self._speech_frames = []
        self._silence_frames = 0
        self._buffer.clear()
    
    def set_callback(self, callback: Callable[[bytes], None]):
        """Set callback for complete utterances."""
        self._callback = callback
    
    def start(self):
        """Start VAD processing."""
        if self._running:
            return
            
        self._audio = pyaudio.PyAudio()
        self._stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=self.config.channels,
            rate=self.config.sample_rate,
            input=True,
            frames_per_buffer=self.frame_size,
            stream_callback=self._audio_callback
        )
        self._running = True
        self._stream.start_stream()
        logger.info("VAD started")
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback."""
        if self._running:
            utterance = self._process_frame(in_data)
            if utterance and self._callback:
                # Run callback in separate thread to avoid blocking audio
                threading.Thread(target=self._callback, args=(utterance,), daemon=True).start()
        return (in_data, pyaudio.paContinue)
    
    def stop(self):
        """Stop VAD processing."""
        self._running = False
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._audio:
            self._audio.terminate()
        logger.info("VAD stopped")


class AsyncVAD:
    """Async wrapper for VAD that yields utterances."""
    
    def __init__(self, config: VADConfig = None):
        self.vad = EnergyVAD(config)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
    def _on_utterance(self, audio_data: bytes):
        """Callback from VAD thread."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._queue.put(audio_data), self._loop)
    
    async def start(self):
        """Start async VAD."""
        self._loop = asyncio.get_running_loop()
        self.vad.set_callback(self._on_utterance)
        self.vad.start()
    
    async def stop(self):
        """Stop async VAD."""
        self.vad.stop()
    
    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        """Async iterator yielding utterances."""
        await self.start()
        try:
            while True:
                utterance = await self._queue.get()
                yield utterance
        finally:
            await self.stop()


def create_vad(config: VADConfig = None) -> EnergyVAD:
    """Factory function to create VAD instance."""
    return EnergyVAD(config)