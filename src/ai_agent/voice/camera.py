"""
Camera Manager for Voice Conversation

Provides async camera access with frame capture for multimodal AI.
"""

import asyncio
import logging
import cv2
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, AsyncGenerator
import base64
import threading
import time

logger = logging.getLogger(__name__)


@dataclass
class CameraConfig:
    """Camera configuration."""
    device_id: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    format: str = "MJPG"  # MJPG, YUYV, etc.
    # For LLM vision API
    max_image_size: int = 1024
    jpeg_quality: int = 85


class CameraManager:
    """Async camera manager for capturing frames."""
    
    def __init__(self, config: CameraConfig = None):
        self.config = config or CameraConfig()
        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        self._initialized = False
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self._cap is not None and self._cap.isOpened()
    
    async def initialize(self) -> bool:
        """Initialize camera."""
        if self._initialized:
            return True
        
        try:
            # Try different backends
            backends = [
                cv2.CAP_AVFOUNDATION,  # macOS
                cv2.CAP_V4L2,          # Linux
                cv2.CAP_DSHOW,         # Windows
                cv2.CAP_ANY            # Auto
            ]
            
            for backend in backends:
                self._cap = cv2.VideoCapture(self.config.device_id, backend)
                if self._cap.isOpened():
                    break
                self._cap.release()
            
            if not self._cap or not self._cap.isOpened():
                logger.error(f"Could not open camera device {self.config.device_id}")
                return False
            
            # Set properties
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            self._cap.set(cv2.CAP_PROP_FPS, self.config.fps)
            
            if self.config.format:
                fourcc = cv2.VideoWriter_fourcc(*self.config.format)
                self._cap.set(cv2.CAP_PROP_FOURCC, fourcc)
            
            # Verify settings
            actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
            
            logger.info(f"Camera initialized: {actual_width}x{actual_height} @ {actual_fps}fps")
            
            # Start capture thread
            self._running = True
            self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()
            
            # Wait for first frame
            for _ in range(50):  # 50 * 20ms = 1 second
                with self._frame_lock:
                    if self._frame is not None:
                        break
                await asyncio.sleep(0.02)
            
            self._initialized = self._frame is not None
            return self._initialized
            
        except Exception as e:
            logger.error(f"Camera initialization failed: {e}")
            await self.release()
            return False
    
    def _capture_loop(self):
        """Background capture loop."""
        while self._running and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if ret:
                with self._frame_lock:
                    self._frame = frame
            else:
                time.sleep(0.01)
    
    async def release(self):
        """Release camera resources."""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        self._initialized = False
        logger.info("Camera released")
    
    def get_frame(self) -> Optional[np.ndarray]:
        """Get latest frame (BGR)."""
        with self._frame_lock:
            if self._frame is not None:
                return self._frame.copy()
        return None
    
    def get_frame_rgb(self) -> Optional[np.ndarray]:
        """Get latest frame as RGB."""
        frame = self.get_frame()
        if frame is not None:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return None
    
    def get_frame_jpeg(self, quality: int = None) -> Optional[bytes]:
        """Get latest frame as JPEG bytes."""
        frame = self.get_frame()
        if frame is None:
            return None
        
        quality = quality or self.config.jpeg_quality
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        ret, buffer = cv2.imencode('.jpg', frame, encode_params)
        
        if ret:
            return buffer.tobytes()
        return None
    
    def get_frame_base64(self, quality: int = None, max_size: int = None) -> Optional[str]:
        """Get latest frame as base64 encoded JPEG for LLM."""
        frame = self.get_frame()
        if frame is None:
            return None
        
        max_size = max_size or self.config.max_image_size
        quality = quality or self.config.jpeg_quality
        
        # Resize if needed
        h, w = frame.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_w = int(w * scale)
            new_h = int(h * scale)
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        ret, buffer = cv2.imencode('.jpg', frame, encode_params)
        
        if ret:
            return base64.b64encode(buffer.tobytes()).decode('utf-8')
        return None
    
    async def capture_frame_base64(self, max_size: int = None, quality: int = None) -> Optional[str]:
        """Async capture frame as base64."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.get_frame_base64, quality, max_size
        )
    
    async def save_frame(self, path: Path, quality: int = 95) -> bool:
        """Save current frame to file."""
        frame = self.get_frame()
        if frame is None:
            return False
        
        try:
            if path.suffix.lower() in ('.jpg', '.jpeg'):
                params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            else:
                params = []
            return cv2.imwrite(str(path), frame, params)
        except Exception as e:
            logger.error(f"Failed to save frame: {e}")
            return False
    
    @staticmethod
    def list_cameras(max_devices: int = 10) -> list:
        """List available camera devices."""
        cameras = []
        for i in range(max_devices):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                cameras.append({
                    "device_id": i,
                    "width": width,
                    "height": height,
                    "fps": fps
                })
                cap.release()
        return cameras
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


class MockCameraManager:
    """Mock camera for testing without hardware."""
    
    def __init__(self, config: CameraConfig = None):
        self.config = config or CameraConfig()
        self._initialized = True
    
    @property
    def is_ready(self) -> bool:
        return True
    
    async def initialize(self) -> bool:
        return True
    
    async def release(self):
        pass
    
    def get_frame(self) -> Optional[np.ndarray]:
        # Generate test pattern
        frame = np.zeros((self.config.height, self.config.width, 3), dtype=np.uint8)
        # Add gradient
        for y in range(self.config.height):
            frame[y, :, 0] = int(255 * y / self.config.height)
            frame[y, :, 1] = 128
            frame[y, :, 2] = int(255 * (1 - y / self.config.height))
        # Add text
        cv2.putText(frame, "MOCK CAMERA", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 
                    1, (255, 255, 255), 2)
        return frame
    
    def get_frame_rgb(self) -> Optional[np.ndarray]:
        frame = self.get_frame()
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    
    def get_frame_jpeg(self, quality: int = None) -> Optional[bytes]:
        frame = self.get_frame()
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality or 85])
        return buffer.tobytes() if ret else None
    
    def get_frame_base64(self, quality: int = None, max_size: int = None) -> Optional[str]:
        import base64
        jpeg = self.get_frame_jpeg(quality)
        return base64.b64encode(jpeg).decode('utf-8') if jpeg else None
    
    async def capture_frame_base64(self, max_size: int = None, quality: int = None) -> Optional[str]:
        return self.get_frame_base64(quality, max_size)
    
    async def save_frame(self, path: Path, quality: int = 95) -> bool:
        frame = self.get_frame()
        return cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


async def create_camera(config: CameraConfig = None, mock: bool = False) -> CameraManager:
    """Factory function to create camera manager."""
    if mock:
        return MockCameraManager(config)
    manager = CameraManager(config)
    await manager.initialize()
    return manager