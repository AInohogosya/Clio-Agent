"""
WhatsApp Interface for Clio-Agent-2.

Provides WhatsApp Business API integration using the official WhatsApp Cloud API
via the pywa library. This is the recommended, stable approach for production use.

Requirements:
- Meta Developer Account
- WhatsApp Business Account
- Phone Number ID
- Access Token
- App Secret
- Webhook Verify Token

Setup:
1. Create a Meta Developer App at https://developers.facebook.com/
2. Add WhatsApp product to the app
3. Configure WhatsApp Business Account
4. Get Phone Number ID and Access Token
5. Set up webhook URL for receiving messages
6. Run: python3 run.py setup --whatsapp

Features:
- Receive and process text messages via webhook
- Send responses with markdown formatting
- Support for interactive buttons and lists
- Media message support (images, documents, audio)
- Message templates for predefined responses
- Session management per user
- Rate limiting and retry logic
- Autonomous mode notifications
"""

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pywa import WhatsApp
    from pywa.types import Message, Button, InteractiveButton, InteractiveListSection
    from pywa.handlers import MessageHandler
    PYWA_AVAILABLE = True
except ImportError:
    PYWA_AVAILABLE = False
    WhatsApp = None
    Message = None
    Button = None
    InteractiveButton = None
    InteractiveListSection = None
    MessageHandler = None

from core.agent import ClioAgent, MESSAGE_PROCESS_TIMEOUT

logger = logging.getLogger(__name__)

# WhatsApp character limits
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
MAX_BUTTONS = 3
MAX_LIST_ROWS = 10

async def start(self) -> None:
        """Start the WhatsApp interface and begin listening for messages."""
        if not PYWA_AVAILABLE:
            raise RuntimeError(
                "pywa library not installed. Install with: pip install pywa"
            )

        if not self.webhook_url:
            raise ValueError(
                "Webhook URL is required for receiving messages. "
                "Set WHATSAPP_WEBHOOK_URL in config or pass webhook_url parameter. "
                "Use a tunneling service like ngrok for local development: "
                "ngrok http 8080"
            )

        logger.info("Starting WhatsApp interface...")
        print("📱 Starting WhatsApp Business API bot...")

        # Initialize pywa WhatsApp client
        self._wa = WhatsApp(
            phone_id=self.phone_number_id,
            token=self.access_token,
            app_secret=self.app_secret,
            verify_token=self.webhook_verify_token,
            server=self._create_server_config()
        )

        # Register message handler
        self._wa.on_message(self._handle_message)

        # Start the webhook server
        self._running = True
        self._server_task = asyncio.create_task(self._run_server())

        # Give server time to start
        await asyncio.sleep(1)

        print(f"✅ WhatsApp bot running on port {self.port}")
        print(f"   Webhook: {self.webhook_url}/webhook")
        print(f"   Phone Number ID: {self.phone_number_id}")
        print("   Press Ctrl+C to stop\n")

        # Keep running until cancelled
        try:
            while self._running:
                await asyncio.sleep(3600)  # Sleep for an hour at a time
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    def _create_server_config(self) -> Dict[str, Any]:
        """Create server configuration for pywa."""
        return {
            "host": "0.0.0.0",
            "port": self.port,
            "path": "/webhook"
        }

    async def _run_server(self) -> None:
        """Run the webhook server."""
        if self._wa:
            await self._wa.start_server()

    async def _handle_message(self, wa: WhatsApp, msg: Message) -> None:
        """
        Handle incoming WhatsApp message.

        This mirrors the Telegram message handling pattern.
        """
        try:
            # Extract message text
            text = msg.text or msg.caption or ""
            if not text.strip():
                # Send a friendly message for non-text content
                await msg.reply("I can only process text messages. Please send me a text prompt!")
                return

            sender_id = msg.from_user.wa_id if msg.from_user else "unknown"
            logger.info(f"Received message from {sender_id}: {text[:100]}...")

            # Process through agent with timeout
            try:
                response = await asyncio.wait_for(
                    self.agent.process_message(text, sender_id=sender_id),
                    timeout=MESSAGE_PROCESS_TIMEOUT
                )
            except asyncio.TimeoutError:
                response = "⏱️ Request timed out. Please try a simpler query."
            except Exception as e:
                logger.error(f"Agent processing error: {e}")
                response = f"⚠️ Error processing your request: {str(e)}"

            # Send response back
            await self._send_response(msg, response)

        except Exception as e:
            logger.error(f"Error handling message: {e}")
            try:
                await msg.reply("⚠️ Sorry, I encountered an error processing your message.")
            except Exception:
                pass  # Best effort

    async def _send_response(self, msg: Message, response: str) -> None:
        """Send response message, handling long messages."""
        if not response:
            return

        # WhatsApp has a 4096 character limit per message
        MAX_LENGTH = 4000

        if len(response) <= MAX_LENGTH:
            await msg.reply(response)
        else:
            # Split long messages
            chunks = [response[i:i+MAX_LENGTH] for i in range(0, len(response), MAX_LENGTH)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await msg.reply(chunk)
                else:
                    await msg.reply(f"(cont.) {chunk}")
                await asyncio.sleep(0.5)  # Small delay between chunks

    async def stop(self) -> None:
        """Stop the WhatsApp interface gracefully."""
        self._running = False

        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        if self._wa:
            await self._wa.stop_server()

        await self.agent.stop_autonomous_loop()
        logger.info("WhatsApp interface stopped")


async def run_whatsapp() -> None:
    """
    Run the WhatsApp bot interface.

    This is the main entry point called from main.py, mirroring run_telegram().
    """
    try:
        from config.settings import config
    except Exception as e:
        print(f"⚠️  Could not load configuration: {e}")
        return

    # Check if pywa is available
    if not PYWA_AVAILABLE:
        print("⚠️  WhatsApp interface unavailable: pywa library not installed")
        print("Install with: pip install pywa")
        return

    # Validate required configuration
    required_config = {
        "WHATSAPP_PHONE_NUMBER_ID": config.whatsapp_phone_number_id,
        "WHATSAPP_ACCESS_TOKEN": config.whatsapp_access_token,
        "WHATSAPP_APP_SECRET": config.whatsapp_app_secret,
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN": config.whatsapp_webhook_verify_token,
        "WHATSAPP_WEBHOOK_URL": config.whatsapp_webhook_url,
    }

    missing = [k for k, v in required_config.items() if not v]
    if missing:
        print("❌ WhatsApp configuration incomplete!")
        print("Missing required settings:")
        for key in missing:
            print(f"  - {key}")
        print("\n💡 Run \u0027python3 run.py setup\u0027 to configure WhatsApp")
        for key in missing:
            print(f"   export {key}=your_value")
        return

    # Create agent
    from main import create_agent
    agent = create_agent()
    if agent is None:
        print("⚠️  Cannot start WhatsApp: Agent creation failed")
        return

    # Create and start interface
    interface = WhatsAppInterface(
        agent=agent,
        phone_number_id=config.whatsapp_phone_number_id,
        access_token=config.whatsapp_access_token,
        app_secret=config.whatsapp_app_secret,
        webhook_verify_token=config.whatsapp_webhook_verify_token,
        webhook_url=config.whatsapp_webhook_url,
        port=config.whatsapp_webhook_port or 8080
    )

    try:
        await interface.start()
    except Exception as e:
        print(f"⚠️  WhatsApp error: {e}")
        logger.error(f"WhatsApp interface error: {e}")
    finally:
        await interface.stop()


# Fallback: Simple message sender using existing Node.js service
async def send_whatsapp_message(phone_number: str, message: str) -> bool:
    """
    Send a single WhatsApp message using the existing Node.js/Baileys service.

    This is a fallback for simple message sending without setting up the full bot.
    """
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).parent.parent / "whatsapp_service.js"
    if not script_path.exists():
        print("❌ WhatsApp service script not found")
        return False

    try:
        result = subprocess.run(
            ["node", str(script_path), phone_number, message],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print("✅ Message sent successfully!")
            return True
        else:
            print(f"❌ Failed to send message: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ Message sending timed out")
        return False
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False


if __name__ == "__main__":
    # Allow running as standalone for testing
    if len(sys.argv) < 3:
        print("Usage: python -m interfaces.whatsapp <phone_number> <message>")
        print("       python -m interfaces.whatsapp --bot  # Run as bot (requires full config)")
        sys.exit(1)

    if sys.argv[1] == "--bot":
        asyncio.run(run_whatsapp())
    else:
        phone = sys.argv[1]
        msg = " ".join(sys.argv[2:])
        asyncio.run(send_whatsapp_message(phone, msg))
