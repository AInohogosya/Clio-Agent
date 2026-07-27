"""
Main Application Entry Point

Launches the Clio Agent with Voice Conversation capabilities.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = PROJECT_ROOT / "src"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_PATH))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "clio_agent.log")
    ]
)

logger = logging.getLogger(__name__)


def setup_environment():
    """Setup environment variables and paths."""
    # Load .env if exists
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            logger.info("Loaded .env file")
        except ImportError:
            pass
    
    # Set default config path
    os.environ.setdefault("CLIO_CONFIG", str(PROJECT_ROOT / "config.yaml"))


async def run_voice_demo():
    """Run voice conversation demo."""
    from ai_agent.voice.conversation import VoiceConversation, VoiceConfig
    from ai_agent.voice.vad import VADConfig
    from ai_agent.voice.stt import STTConfig
    from ai_agent.voice.tts import TTSConfig
    from ai_agent.voice.camera import CameraConfig
    from ai_agent.llm import LLMConfig
    from ai_agent.context import ConversationContext
    
    # Create configuration
    config = VoiceConfig(
        vad_config=VADConfig(
            energy_threshold=0.01,
            silence_threshold=0.005,
            max_silence_duration_ms=1000
        ),
        stt_config=STTConfig(
            provider="local",
            model="base",
            language="en"
        ),
        tts_config=TTSConfig(
            provider="piper",
            model="en_US-lessac-medium",
            speed=1.0
        ),
        camera_config=CameraConfig(
            width=1280,
            height=720,
            fps=30
        ),
        llm_config=LLMConfig(
            provider="ollama",
            model="llama3.2:3b",
            temperature=0.7,
            max_tokens=500
        ),
        system_prompt="You are a helpful AI assistant in a voice conversation. Keep responses brief and conversational - 1-2 sentences max.",
        voice_system_prompt="You are a helpful AI assistant in a voice conversation with camera input. Keep responses BRIEF and CONVERSATIONAL - 1-2 sentences max. Comment naturally on what you see.",
        on_state_change=lambda s: print(f"State: {s}"),
        on_transcription=lambda t: print(f"You: {t}"),
        on_response=lambda r: print(f"Assistant: {r}"),
        on_error=lambda e: print(f"Error: {e}")
    )
    
    # Create and run
    conv = VoiceConversation(config)
    
    try:
        if await conv.initialize():
            print("Voice conversation initialized!")
            print("Starting... Speak into microphone.")
            await conv.start()
            
            # Keep running
            while conv.is_running:
                await asyncio.sleep(1)
        else:
            print("Failed to initialize voice conversation")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await conv.stop()


async def run_gui():
    """Run the GUI application."""
    from gui.app import run
    run()


def main():
    """Main entry point."""
    setup_environment()
    
    import argparse
    parser = argparse.ArgumentParser(description="Clio Agent - AI Assistant with Voice")
    parser.add_argument("--voice", action="store_true", help="Run voice conversation demo")
    parser.add_argument("--gui", action="store_true", help="Run GUI application (default)")
    parser.add_argument("--mock-camera", action="store_true", help="Use mock camera")
    parser.add_argument("--config", type=str, help="Config file path")
    
    args = parser.parse_args()
    
    if args.config:
        os.environ["CLIO_CONFIG"] = args.config
    
    if args.mock_camera:
        os.environ["CLIO_MOCK_CAMERA"] = "1"
    
    if args.voice:
        asyncio.run(run_voice_demo())
    else:
        run()


if __name__ == "__main__":
    main()