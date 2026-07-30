"""
Clio-Agent-2 - Autonomous AI Agent

A fully functional, autonomous AI agent with multi-platform support.
Everything is reachable from a single command (run.py at the project root):

    python3 run.py            # Auto-setup + launch CLI (configures on first run)
    python3 run.py --telegram # Run Telegram bot
    python3 run.py --discord  # Run Discord bot
    python3 run.py --all      # Run all configured interfaces
    python3 run.py setup      # (or --setup) Configure API keys + default model
    python3 run.py status     # (or --status) Show configuration status

On the very first run (no API key / model configured) the launcher walks you
through setup interactively so the whole experience is just one command.
Use --no-setup to skip the first-run prompt and launch immediately.
"""

import asyncio
import atexit
import logging
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path

# ============================================================================
# SYSTEM DETECTION AND COMPATIBILITY
# ============================================================================

def detect_system_info():
    """Detect system information for compatibility checks."""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "is_venv": (
            hasattr(sys, "real_prefix") or
            (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix) or
            os.environ.get("VIRTUAL_ENV") is not None
        ),
    }


def check_system_requirements():
    """Check if system meets minimum requirements."""
    print("=" * 60)
    print("🔍 System Compatibility Check")
    print("=" * 60)

    system_info = detect_system_info()

    # Check Python version (minimum 3.8)
    python_version = tuple(map(int, platform.python_version_tuple()[:2]))
    if python_version < (3, 8):
        print(f"❌ Python version too old: {platform.python_version()}")
        print("   Minimum required: Python 3.8+")
        return False

    print(f"✅ Python Version: {platform.python_version()}")
    print(f"✅ Operating System: {system_info['os']} {system_info['os_version']}")
    print(f"✅ Architecture: {system_info['machine']}")

    # Check write permissions
    project_root = Path(__file__).parent
    try:
        test_file = project_root / ".write_test"
        test_file.touch()
        test_file.unlink()
        print("✅ Write permissions: OK")
    except Exception as e:
        print(f"⚠️  Write permissions issue: {e}")

    print("=" * 60)
    return True


# ============================================================================
# VIRTUAL ENVIRONMENT MANAGEMENT
# ============================================================================

def ensure_virtual_environment():
    """
    Ensure a virtual environment exists and is being used.
    If not running in a venv, create one and re-run this script within it.
    """
    # Check if already running in a virtual environment
    in_venv = (
        hasattr(sys, "real_prefix") or
        (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix) or
        os.environ.get("VIRTUAL_ENV") is not None
    )

    if in_venv:
        print(f"✅ Running in virtual environment: {sys.prefix}")
        return True

    # Not in a virtual environment - create/use one
    project_root = Path(__file__).parent
    venv_path = project_root / ".venv"

    print("=" * 60)
    print("🐍 Virtual Environment Setup")
    print("=" * 60)

    venv_needs_recreation = False

    if not venv_path.exists():
        print(f"📦 Creating virtual environment at: {venv_path}")
        try:
            subprocess.check_call([sys.executable, "-m", "venv", str(venv_path)])
            print("✅ Virtual environment created successfully!")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to create virtual environment: {e}")
            _handle_venv_creation_failure(project_root)
            sys.exit(1)
    else:
        print(f"✅ Virtual environment already exists at: {venv_path}")

        # Verify the venv has a valid Python executable
        venv_python = _get_venv_python(venv_path)
        if not venv_python or not venv_python.exists():
            print("⚠️  Existing venv is missing Python executable - recreating...")
            venv_needs_recreation = True

    # Recreate venv if needed
    if venv_needs_recreation:
        print("📦 Removing broken virtual environment...")
        import shutil
        try:
            shutil.rmtree(str(venv_path))
        except Exception as e:
            print(f"❌ Failed to remove broken venv: {e}")
            sys.exit(1)

        print(f"📦 Creating new virtual environment at: {venv_path}")
        try:
            subprocess.check_call([sys.executable, "-m", "venv", str(venv_path)])
            print("✅ Virtual environment created successfully!")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to create virtual environment: {e}")
            _handle_venv_creation_failure(project_root)
            sys.exit(1)

    # Determine the Python executable in the virtual environment
    venv_python = _get_venv_python(venv_path)

    if not venv_python or not venv_python.exists():
        print("❌ Python executable not found in venv!")
        _diagnose_venv_issue(venv_path)
        sys.exit(1)

    # Install dependencies in the virtual environment
    print("📦 Installing dependencies in virtual environment...")
    requirements_path = project_root / "requirements.txt"
    if requirements_path.exists():
        try:
            # Upgrade pip first
            subprocess.check_call([
                str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"
            ])
            # Install requirements
            subprocess.check_call([
                str(venv_python), "-m", "pip", "install",
                "-r", str(requirements_path), "--quiet"
            ])
            print("✅ Dependencies installed successfully!")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install dependencies: {e}")
            _handle_dependency_installation_failure(venv_python, requirements_path)
            sys.exit(1)
    else:
        print(f"❌ requirements.txt not found at {requirements_path}")
        sys.exit(1)

    # Re-run this script in the virtual environment
    print("🔄 Restarting in virtual environment...")
    print("=" * 60)

    # Set the VIRTUAL_ENV environment variable
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv_path)

    # Get the path separator
    path_sep = ";" if os.name == "nt" else ":"

    # Update PATH to include venv binaries
    venv_scripts = venv_path / ("Scripts" if os.name == "nt" else "bin")
    current_path = env.get("PATH", "")
    env["PATH"] = f"{venv_scripts}{path_sep}{current_path}"

    # Execute the script in the virtual environment
    os.execve(str(venv_python), [str(venv_python), __file__] + sys.argv[1:], env)

    # This line should never be reached due to execve
    return False


def _get_venv_python(venv_path):
    """Get the Python executable path in the virtual environment."""
    if os.name == "nt":  # Windows
        return venv_path / "Scripts" / "python.exe"
    else:  # Unix/Linux/macOS
        bin_dir = venv_path / "bin"

        # Try multiple possible Python executable names in order of preference
        python_names = [
            "python3",
            "python",
        ]

        # Add version-specific names (e.g., python3.12, python3.11)
        py_version = platform.python_version_tuple()
        python_names.append(f"python{py_version[0]}.{py_version[1]}")
        python_names.append(f"python{py_version[0]}")

        for name in python_names:
            venv_python = bin_dir / name
            if venv_python.exists():
                return venv_python

        # If none found, return the default python3 path
        return bin_dir / "python3"


def _handle_venv_creation_failure(project_root):
    """Handle virtual environment creation failure with helpful suggestions."""
    print("\n💡 Troubleshooting Suggestions:")
    system = platform.system()

    if system == "Linux":
        print("  Ubuntu/Debian: sudo apt-get install python3-venv python3-pip")
        print("  Fedora/RHEL: sudo dnf install python3-venv python3-pip")
        print("  Arch Linux: sudo pacman -S python-virtualenv python-pip")
    elif system == "Darwin":  # macOS
        print("  macOS: brew install python3")
        print("  Or use pyenv: brew install pyenv && pyenv install 3.11.0")
    elif system == "Windows":
        print("  Windows: Ensure Python is installed with 'Add to PATH' option")
        print("  Reinstall Python from https://python.org if needed")

    print("\n  Alternative: Manual setup")
    print(f"  cd {project_root}")
    print("  python3 -m venv .venv")
    print("  source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate")
    print("  pip install -r requirements.txt")


def _diagnose_venv_issue(venv_path):
    """Diagnose virtual environment issues."""
    print(f"\n🔍 Diagnosing venv at: {venv_path}")

    bin_dir = venv_path / ("bin" if os.name != "nt" else "Scripts")
    if bin_dir.exists():
        print(f"\nAvailable files in {bin_dir.name}/:")
        for f in sorted(bin_dir.iterdir()):
            if f.is_file():
                print(f"   - {f.name}")
    else:
        print(f"  ⚠️  {bin_dir} directory does not exist!")

    # Check if venv was properly created
    pyvenv_cfg = venv_path / "pyvenv.cfg"
    if pyvenv_cfg.exists():
        print("\n✅ pyvenv.cfg exists")
    else:
        print("  ⚠️  pyvenv.cfg missing - venv may be corrupted")
        print("  Try removing .venv and running again")


def _handle_dependency_installation_failure(venv_python, requirements_path):
    """Handle dependency installation failure with fallback options."""
    print("\n💡 Trying alternative installation methods...")

    # Try installing without cache
    try:
        print("  Attempting installation with --no-cache-dir...")
        subprocess.check_call([
            str(venv_python), "-m", "pip", "install",
            "-r", str(requirements_path), "--no-cache-dir", "--quiet"
        ])
        print("✅ Installation succeeded with --no-cache-dir!")
        return
    except subprocess.CalledProcessError:
        pass

    # Try upgrading pip first
    try:
        print("  Attempting to upgrade pip first...")
        subprocess.check_call([
            str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"
        ])
        subprocess.check_call([
            str(venv_python), "-m", "pip", "install",
            "-r", str(requirements_path), "--quiet"
        ])
        print("✅ Installation succeeded after pip upgrade!")
        return
    except subprocess.CalledProcessError:
        pass

    print("\n❌ All installation methods failed.")
    print("\n💡 Manual resolution steps:")
    print("  1. Activate venv: source .venv/bin/activate")
    print("  2. Update pip: pip install --upgrade pip")
    print("  3. Install packages individually to identify the problematic one")
    print("  4. Check network connectivity and firewall settings")


def ensure_dependencies():
    """Ensure all required dependencies are installed (called within venv)."""
    print("🔧 Verifying dependencies...")

    # Only check for packages that are actually in requirements.txt (not commented)
    required_packages = [
        "python-dotenv",
        "openai",
        "httpx",
        "aiohttp",
        "rich",
        "prompt_toolkit",
        "requests",
        "tiktoken"
    ]

    missing_packages = []
    for package in required_packages:
        try:
            # Try to import the package
            if package == "python-dotenv":
                __import__("dotenv")
            elif package == "google-generativeai":
                __import__("google.generativeai")
            elif package == "python-telegram-bot":
                __import__("telegram")
            elif package == "discord.py":
                __import__("discord")
            else:
                __import__(package.replace("-", "_"))
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print(f"📦 Installing missing packages: {', '.join(missing_packages)}")
        requirements_path = Path(__file__).parent / "requirements.txt"
        if requirements_path.exists():
            try:
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install",
                    "-r", str(requirements_path), "--quiet"
                ])
                print("✅ Dependencies installed successfully!")
            except subprocess.CalledProcessError as e:
                print(f"❌ Failed to install dependencies: {e}")
                print("Please run: pip install -r requirements.txt")
                sys.exit(1)
        else:
            print(f"❌ requirements.txt not found at {requirements_path}")
            sys.exit(1)
    else:
        print("✅ All dependencies are installed!")


def ensure_config_directory():
    """Ensure config directory exists."""
    config_dir = Path(__file__).parent / "config"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def ensure_env_file(config_dir):
    """Ensure .env file exists with template values."""
    env_file = config_dir / ".env"
    env_example = config_dir / ".env.example"

    if not env_file.exists():
        if env_example.exists():
            # Copy from example
            with open(env_example) as f:
                content = f.read()
            with open(env_file, 'w') as f:
                f.write(content)
            print(f"✅ Created .env file from template at: {env_file}")
            print("⚠️  Please edit config/.env with your API keys before using LLM features!")
        else:
            # Create minimal .env
            minimal_env = """# Clio-Agent-2 Configuration
# Edit this file with your API keys

# LLM Provider Keys (choose one or more)
OPENAI_API_KEY=your_openai_api_key_here
GOOGLE_API_KEY=your_google_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
GROK_API_KEY=your_grok_api_key_here
DEEPSEEK_API_KEY=your_deepseek_api_key_here
# MISTRAL_API_KEY=your_mistral_api_key_here
# GROQ_API_KEY=your_groq_api_key_here
# PERPLEXITY_API_KEY=your_perplexity_api_key_here
# TOGETHER_API_KEY=your_together_api_key_here
# FIREWORKS_API_KEY=your_fireworks_api_key_here
# NVIDIA_API_KEY=your_nvidia_api_key_here
# QWEN_API_KEY=your_qwen_api_key_here
# HUGGINGFACE_API_KEY=your_huggingface_api_key_here
# DEEPINFRA_API_KEY=your_deepinfra_api_key_here
# OLLAMA_API_KEY=your_ollama_api_key_here

# Bot Tokens (optional - for Telegram/Discord/WhatsApp interfaces)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# WhatsApp Business API Configuration (optional - for WhatsApp interface)
WHATSAPP_PHONE_NUMBER_ID=your_whatsapp_phone_number_id_here
WHATSAPP_ACCESS_TOKEN=your_whatsapp_access_token_here
WHATSAPP_APP_SECRET=your_meta_app_secret_here
WHATSAPP_WEBHOOK_VERIFY_TOKEN=your_webhook_verify_token_here
WHATSAPP_WEBHOOK_URL=https://your-domain.com
WHATSAPP_WEBHOOK_PORT=8080

# Settings
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4o-mini
THINKING_INTERVAL=5
MAX_CONTEXT_LINES=1000
# Guardrail: keep the underlying LLM provider/model from changing
# unexpectedly (defaults to true when this line is absent too).
LLM_SETTINGS_LOCKED=true
"""
            with open(env_file, 'w') as f:
                f.write(minimal_env)
            print(f"✅ Created minimal .env file at: {env_file}")
            print("⚠️  Please edit config/.env with your API keys before using LLM features!")
    else:
        print("✅ .env file already exists!")

    return env_file


def setup_environment():
    """Complete environment setup."""
    print("=" * 60)
    print("🚀 Clio-Agent-2: Automatic Environment Setup")
    print("=" * 60)

    # Step 1: Ensure dependencies
    ensure_dependencies()

    # Step 2: Ensure config directory
    config_dir = ensure_config_directory()
    print(f"✅ Config directory ready at: {config_dir}")

    # Step 3: Ensure .env file
    env_file = ensure_env_file(config_dir)

    # Step 4: Load environment variables
    from dotenv import load_dotenv
    load_dotenv(env_file)
    print("✅ Environment variables loaded!")

    print("=" * 60)
    print("🎉 Environment setup complete!")
    print("=" * 60)
    print()


# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Step 1: Check system requirements
try:
    check_system_requirements()
except Exception as e:
    print(f"⚠️  System check warning: {e}")
    print("Continuing anyway...")

# Step 2: Auto-setup virtual environment FIRST (before any other setup)
try:
    ensure_virtual_environment()
except Exception as e:
    print(f"⚠️  Virtual environment setup warning: {e}")
    print("Continuing with current environment...")

# Step 3: Auto-setup environment before any other imports
try:
    setup_environment()
except Exception as e:
    print(f"⚠️  Environment setup warning: {e}")
    print("Continuing with minimal configuration...")

# Step 4: Import the rest of the application
try:
    from config.settings import Config, config
except Exception as e:
    print(f"⚠️  Failed to import config: {e}")
    # Create a minimal fallback config class
    class MinimalConfig:
        def __init__(self):
            self.openai_api_key = None
            self.google_api_key = None
            self.anthropic_api_key = None
            self.openrouter_api_key = None
            self.grok_api_key = None
            self.deepseek_api_key = None
            self.search_api_key = None
            self.telegram_bot_token = None
            self.discord_bot_token = None
            self.default_llm_provider = "openai"
            self.current_model = ""
            self.llm_settings_locked = False  # fallback path: allow changes
            self.context_log_max_lines = 1000
            self.agent_name = "Clio-Agent-2"
            self.autonomous_mode = True
            self.thinking_interval = 5.0
        def validate_api_keys(self):
            return {
                "openai": False, "google": False, "anthropic": False,
                "openrouter": False, "grok": False, "deepseek": False,
                "mistral": False, "groq": False, "perplexity": False,
                "together": False, "fireworks": False, "nvidia": False,
                "qwen": False, "huggingface": False, "deepinfra": False,
                "ollama": False,
            }
        def to_dict(self):
            return {"default_llm_provider": self.default_llm_provider, "current_model": self.current_model}
    config = MinimalConfig()
    Config = MinimalConfig

try:
    from core.llm_router import SUPPORTED_PROVIDERS, LLMRouter
except Exception as e:
    print(f"⚠️  Failed to import LLMRouter: {e}")
    # Create a minimal fallback LLMRouter class
    class LLMRouter:
        def __init__(self, cfg):
            self._default_provider = "openai"
            self._current_model = ""
            self.llm_settings_locked = False  # fallback path: allow changes
        def get_available_providers(self):
            return []
        async def chat(self, messages, **kwargs):
            return "LLM service unavailable. Please configure API keys."
        async def list_all_models(self):
            return {}

try:
    from core.agent import ClioAgent
except Exception as e:
    print(f"⚠️  Failed to import ClioAgent: {e}")
    ClioAgent = None


_active_agent = None


def create_agent():
    """Create and configure the Clio-Agent-2 instance."""
    if ClioAgent is None:
        print("⚠️  Cannot create agent: ClioAgent module failed to load")
        return None

    # Initialize LLM router
    llm_router = LLMRouter(config)

    # Check if any providers are configured
    available_providers = llm_router.get_available_providers()
    if not available_providers:
        print("⚠️  Warning: No LLM providers configured!")
        print("Please set at least one API key in config/.env")
        try:
            supported = ", ".join(SUPPORTED_PROVIDERS)
        except Exception:
            supported = "OpenAI, Google, Anthropic, OpenRouter, Grok, DeepSeek"
        print(f"Supported providers: {supported} (plus any custom 'Other' provider)")

    try:
        # Create agent instance
        agent = ClioAgent(config, llm_router)
        global _active_agent
        _active_agent = agent
        return agent
    except Exception as e:
        print(f"⚠️  Failed to create agent: {e}")
        return None


def _is_token_configured(token):
    """Return True only if `token` is a real, usable token."""
    if not token or not str(token).strip():
        return False
    stripped = str(token).strip()
    lowered = stripped.lower()
    if lowered.startswith("your_") or "placeholder" in lowered:
        return False
    if "<" in stripped or ">" in stripped:
        return False
    return True



def _is_real_secret(value) -> bool:
    """Return True only if `value` is a usable, non-placeholder secret.

    Beyond the checks in :func:`_is_token_configured`, this also rejects the
    common "looks real but isn't" placeholders that ship in the template
    ``.env.example`` (e.g. ``sk-your-actual-openai-api-key-here`` or
    ``your_openai_api_key_here``) so a freshly-seeded config is correctly
    treated as "not configured" and the first-run setup screen is shown.
    """
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    lowered = s.lower()
    if (
        "your_" in lowered
        or lowered.startswith("your-")
        or "sk-your" in lowered
        or "placeholder" in lowered
        or "example" in lowered
        or "xxxx" in lowered
    ):
        return False
    if "<" in s or ">" in s:
        return False
    return True




async def run_cli():
    """Run the CLI interface."""
    try:
        from interfaces.cli import CLIInterface
    except Exception as e:
        print(f"⚠️  CLI interface unavailable: {e}")
        print("Running in minimal mode...")
        # Minimal fallback - just keep running
        while True:
            user_input = await asyncio.get_running_loop().run_in_executor(None, input, "You: ")
            if user_input.lower() in ('exit', 'quit'):
                break
            print("Agent: CLI interface not available. Please install required dependencies.")
        return

    agent = create_agent()
    if agent is None:
        print("⚠️  Cannot start CLI: Agent creation failed")
        return

    interface = CLIInterface(agent)

    try:
        await interface.start()
    except Exception as e:
        print(f"⚠️  CLI error: {e}")


async def run_telegram(replace: bool = False):
    """Run the Telegram bot interface.

    ``replace`` (``--replace``) lets this instance take over from an already
    running one on the same machine by terminating it first.
    """
    try:
        from interfaces.telegram import TelegramInterface
    except Exception as e:
        print(f"⚠️  Telegram interface unavailable: {e}")
        print("Install with: pip install python-telegram-bot")
        return

    if not _is_token_configured(config.telegram_bot_token):
        print("❌ Telegram bot token not configured!")
        print("Set TELEGRAM_BOT_TOKEN in config/.env")
        print("💡 Run 'python3 run.py setup' to configure interactively")
        return

    # Guard against two polling instances on the same machine. Telegram allows
    # only ONE getUpdates session per bot token; a second instance fails with
    # ``telegram.error.Conflict`` (HTTP 409). This lock refuses to start while
    # a live instance holds it (and auto-reclaims a stale lock left by a dead
    # process). With ``replace`` we evict the existing instance first.
    from utils.instance_lock import SingleInstanceLock, format_lock_hint
    lock = SingleInstanceLock("telegram")
    if not lock.acquire(blocking=False, force=replace):
        print(format_lock_hint("telegram"))
        return

    agent = create_agent()
    if agent is None:
        print("⚠️  Cannot start Telegram: Agent creation failed")
        lock.release()
        return

    interface = TelegramInterface(agent, config.telegram_bot_token)

    try:
        await interface.start()
    except Exception as e:
        print(f"⚠️  Telegram error: {e}")
    finally:
        # Release the lock so a later restart (or --replace) can acquire it.
        lock.release()


async def run_discord():
    """Run the Discord bot interface."""
    try:
        from interfaces.discord import DiscordInterface
    except Exception as e:
        print(f"⚠️  Discord interface unavailable: {e}")
        print("Install with: pip install discord.py")
        return

    if not _is_token_configured(config.discord_bot_token):
        print("❌ Discord bot token not configured!")
        print("Set DISCORD_BOT_TOKEN in config/.env")
        return

    agent = create_agent()
    if agent is None:
        print("⚠️  Cannot start Discord: Agent creation failed")
        return

    interface = DiscordInterface(agent, config.discord_bot_token)

    try:
        await interface.start()
    except Exception as e:
        # Give actionable guidance for the most common Discord connection
        # failures instead of a single opaque message. ``discord`` is
        # guaranteed to be importable here (the ``DiscordInterface`` import
        # above would have failed otherwise).
        import discord

        if isinstance(e, discord.LoginFailure):
            print("❌ Discord login failed: the bot token is invalid or has been reset.")
            print("   Generate a fresh token in the Discord Developer Portal and set")
            print("   DISCORD_BOT_TOKEN in config/.env")
        elif isinstance(e, discord.PrivilegedIntentsRequired):
            print("❌ Discord refused the connection: a required *privileged* intent is disabled.")
            print("   Enable 'MESSAGE CONTENT INTENT' for your bot at")
            print("   https://discord.com/developers/applications → your app → Bot →")
            print("   Privileged Gateway Intents, then restart.")
        else:
            print(f"⚠️  Discord error: {e}")


async def run_whatsapp():
    """Run the WhatsApp Business API interface."""
    try:
        from interfaces.whatsapp import run_whatsapp as whatsapp_run
    except Exception as e:
        print(f"⚠️  WhatsApp interface unavailable: {e}")
        print("Install with: pip install pywa")
        return

    # Check required configuration
    required = [
        ("WHATSAPP_PHONE_NUMBER_ID", config.whatsapp_phone_number_id),
        ("WHATSAPP_ACCESS_TOKEN", config.whatsapp_access_token),
        ("WHATSAPP_APP_SECRET", config.whatsapp_app_secret),
        ("WHATSAPP_WEBHOOK_VERIFY_TOKEN", config.whatsapp_webhook_verify_token),
        ("WHATSAPP_WEBHOOK_URL", config.whatsapp_webhook_url),
    ]

    missing = [name for name, value in required if not value]
    if missing:
        print("❌ WhatsApp configuration incomplete!")
        print("Missing required settings:")
        for name in missing:
            print(f"  - {name}")
        print("\n💡 Run 'python3 run.py setup' to configure interactively")
        print("   Or set the following environment variables:")
        for name in missing:
            print(f"   export {name}=your_value")
        return

    agent = create_agent()
    if agent is None:
        print("⚠️  Cannot start WhatsApp: Agent creation failed")
        return

    try:
        await whatsapp_run()
    except Exception as e:
        print(f"⚠️  WhatsApp error: {e}")


async def run_all(replace: bool = False):
    """Run all configured interfaces concurrently."""
    tasks = []

    # Always run CLI
    tasks.append(run_cli())

    # Run Telegram if configured (check for non-empty token)
    if _is_token_configured(config.telegram_bot_token):
        tasks.append(run_telegram(replace=replace))

    # Run Discord if configured (check for non-empty token)
    if _is_token_configured(config.discord_bot_token):
        tasks.append(run_discord())

    # Run WhatsApp if configured (check for required settings)
    if (config.whatsapp_phone_number_id and config.whatsapp_access_token and
        config.whatsapp_app_secret and config.whatsapp_webhook_verify_token and
        config.whatsapp_webhook_url):
        tasks.append(run_whatsapp())

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"⚠️  Error running interfaces: {e}")


def _flush_contexts():
    """Best-effort flush of every active agent's context + settings to disk.

    Safe to call from signal handlers, ``atexit``, or a ``finally`` block.
    Never raises: a failure to persist one agent must not block the others.
    """
    agent = _active_agent
    if agent is None:
        return
    try:
        agent.stop()
    except Exception:
        pass
    try:
        agent.save_context_sync()
    except Exception as e:
        print(f"⚠️  Could not save context: {e}")
    try:
        agent.persist_settings()
    except Exception:
        pass


def _emergency_save_and_exit(signum, frame):
    """POSIX fallback (e.g. Windows / no running loop): save then exit."""
    print("\n\n👋 Received shutdown signal — saving context before exit...")
    _flush_contexts()
    # Exit without further Python cleanup; context is already persisted.
    os._exit(0)


def _install_fallback_signal_handlers():
    """Plain ``signal.signal`` handlers for platforms/loops without asyncio
    signal support (e.g. Windows, or before the loop is running)."""
    try:
        signal.signal(signal.SIGINT, _emergency_save_and_exit)
        signal.signal(signal.SIGTERM, _emergency_save_and_exit)
    except (ValueError, OSError, AttributeError):
        pass


async def _run_interface(coro):
    """Run a top-level interface coroutine with graceful signal handling.

    On SIGINT/SIGTERM we flush the context log and *cancel* the running task so
    that ``asyncio.run`` unwinds cleanly. We deliberately do **not** call
    ``loop.stop()``: stopping the loop while its main future is still pending
    makes ``run_until_complete`` raise

        RuntimeError: Event loop stopped before Future completed.

    which previously surfaced as a bogus "Fatal error" and a non-zero exit code.
    Cancelling the task lets the coroutine (and each interface's own cleanup)
    finish normally, and the resulting ``CancelledError`` is swallowed here
    because a signal-triggered shutdown is expected, not an error.
    """
    loop = asyncio.get_running_loop()
    main_task = asyncio.ensure_future(coro)

    def _on_shutdown_signal():
        print("\n\n👋 Received shutdown signal — saving context before exit...")
        _flush_contexts()
        main_task.cancel()

    installed_signals = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_shutdown_signal)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            # e.g. Windows: fall back to plain signal handlers and stop trying.
            _install_fallback_signal_handlers()
            break

    try:
        return await main_task
    except asyncio.CancelledError:
        # Graceful shutdown requested via signal — expected, not an error.
        return None
    finally:
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass


def setup_logging():
    """Configure logging to stderr + a file so failures (LLM errors, Telegram
    update errors, network issues) are visible instead of failing silently."""
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(
            logging.FileHandler(
                Path(__file__).parent / "clio_agent.log", encoding="utf-8"
            )
        )
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers)
    # Quiet down noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def is_config_ready(cfg) -> bool:
    """Return True when a usable API key + model are both configured.

    "Usable" means: a non-empty model is set (not a placeholder) and the
    provider named by DEFAULT_LLM_PROVIDER actually has a *real* (non-placeholder)
    API key. This deliberately rejects the placeholder values that ship in the
    template ``.env.example`` (e.g. ``sk-your-actual-openai-api-key-here``)
    so a freshly-seeded config is correctly seen as "not ready" and the
    first-run setup screen is shown instead of launching a broken agent.
    """
    provider = (getattr(cfg, "default_llm_provider", "") or "").strip().lower()
    model = (getattr(cfg, "current_model", "") or "").strip()
    if not model or model.lower().startswith("your_") or "<" in model or ">" in model:
        return False
    status = cfg.validate_api_keys()
    if not status.get(provider):
        return False
    # Reject placeholder secrets (e.g. sk-your-actual-...-here).
    api_key = getattr(cfg, "get_api_key", lambda p: None)(provider)
    if not _is_real_secret(api_key):
        return False
    return True


def run_setup_only():
    """Run the interactive configuration wizard and exit (single command: setup)."""
    from config.setup_env import interactive_setup
    try:
        interactive_setup(prompt_for_model=True)
        print("\n🎉 Configuration complete!")
        print("Launch Clio-Agent-2 anytime with:  python3 run.py")
    except (EOFError, KeyboardInterrupt):
        print("\n⚠️  Setup cancelled.")


def print_status():
    """Print configuration status and readiness (single command: status)."""
    status = config.validate_api_keys()
    print("=" * 60)
    print("📋 Clio-Agent-2 Configuration Status")
    print("=" * 60)
    for provider, ok in status.items():
        mark = "✅" if ok else "❌"
        print(f"  {mark} {provider}")
    print()
    provider = (config.default_llm_provider or "").strip()
    model = (config.current_model or "").strip()
    print(f"  Default provider: {provider or '(not set)'}")
    print(f"  Default model:    {model or '(not set)'}")
    print(f"  Autonomous mode:  {'on' if config.autonomous_mode else 'off'}")
    print()
    if is_config_ready(config):
        print("✅ Ready: an API key and model are configured.")
    else:
        print("⚠️  Not ready: run 'python3 run.py setup' to configure.")
    print("=" * 60)


def auto_configure_if_needed():
    """On first run with no usable config, open the configuration screen.

    The "configuration screen" is the single, navigable place to set
    everything the agent needs: large-scale model API keys (OpenAI, Google,
    Anthropic, OpenRouter, Grok, DeepSeek, Mistral, Groq, Perplexity, Together,
    Fireworks, NVIDIA, NVIDIA KIM, Qwen, HuggingFace, DeepInfra, Ollama — and any
    custom "Other" provider you add), the Search API key, the Telegram/Discord bot
    tokens, and the default model.

    Runs only in an interactive terminal. Non-interactive environments (CI,
    piping) get a short hint instead so the launch can still proceed.
    """
    if is_config_ready(config):
        return
    if not sys.stdin.isatty():
        print("\n⚠️  No model / API key configured yet.")
        print("   Run 'python3 run.py setup' in a terminal to open the")
        print("   configuration screen, or edit config/.env directly")
        print("   (OPENAI_API_KEY, DEFAULT_MODEL, ...).")
        return
    print("\n🛠️  First-time setup — opening the Clio-Agent-2 configuration screen.")
    print("   (Set your API keys + default model here, then choose 'Done'.)\n")
    from config.setup_env import interactive_setup
    try:
        interactive_setup(prompt_for_model=True)
    except (EOFError, KeyboardInterrupt):
        print("\n⚠️  Skipped interactive setup; continuing with current config.")
    # Reload the (possibly updated) global config instance.
    getattr(config, "reload", lambda: None)()


def main():
    """Main entry point for Clio-Agent-2."""
    import argparse

    # Configure logging first so every subsequent message is captured.
    setup_logging()

    # Register an atexit flush (covers normal exits, including __EXIT__ and
    # any exception path) so the context log is always persisted. Per-signal
    # handlers are installed by ``_run_interface`` once the asyncio loop exists,
    # so SIGINT/SIGTERM cancel the running task (a graceful shutdown) instead of
    # stopping the loop out from under ``asyncio.run``.
    atexit.register(_flush_contexts)

    # Ignore SIGHUP so the bot keeps running when its SSH/login shell is closed
    # (otherwise an overnight session drop would terminate the process).
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (ValueError, AttributeError, OSError):
        pass  # Not available on every platform (e.g. Windows).

    parser = argparse.ArgumentParser(
        description="Clio-Agent-2 - Autonomous AI Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Single-command usage:
  python run.py                 # Auto-setup + launch CLI (configures on first run)
  python run.py --telegram     # Run Telegram bot
  python run.py --discord      # Run Discord bot
  python run.py --whatsapp     # Run WhatsApp bot (receives prompts via WhatsApp)
  python run.py --all          # Run all configured interfaces
  python run.py setup          # (or --setup) Configure API keys + default model
  python run.py status         # (or --status) Show configuration status
        """
    )

    parser.add_argument(
        "command",
        nargs="?",
        choices=["setup", "config", "configure", "status", "help"],
        default=None,
        help="Shortcut for --setup / --status / --help (positional). "
             "'setup', 'config' and 'configure' all open the configuration screen.",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Run Telegram bot interface"
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        help="Run Discord bot interface"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all configured interfaces"
    )
    parser.add_argument(
        "--whatsapp",
        action="store_true",
        help="Run the WhatsApp Business API bot that receives prompts via WhatsApp. "
             "With no extra arguments it launches the bot (webhook-based receiving "
             "mode). Supply a phone number (+123...) as an extra argument to instead "
             "send a one-off message via the WhatsApp Cloud API.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Telegram: take over from an already running instance on this "
             "machine (kills it first). Use when a stale/orphaned bot process "
             "is still polling the token and causing 409 Conflict errors."
    )
    parser.add_argument(
        "--setup", "--configure",
        action="store_true",
        dest="setup",
        help="Open the configuration screen (API keys, model, tokens), then exit"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show configuration status and exit"
    )
    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="Skip first-run interactive setup and launch immediately"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to custom .env configuration file"
    )

    args, extras = parser.parse_known_args()

    # Single-command helpers take priority over launching an interface.
    if args.command == "help" or (len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help")):
        parser.print_help()
        return
    if args.command == "status" or args.status:
        print_status()
        return
    if args.command in ("setup", "config", "configure") or args.setup:
        # Non-interactive single command: `run.py setup --openai sk-...
        # --provider openai --model gpt-4o` forwards the flags to the
        # configuration screen's batch (non-interactive) mode.
        if extras:
            from config.setup_env import apply_overrides_from_argv
            applied = apply_overrides_from_argv(extras)
            if applied:
                print("\n🎉 Configuration applied!")
                print("Launch Clio-Agent-2 anytime with:  python3 run.py")
            return
        run_setup_only()
        return

    # Load custom config if specified
    if args.config:
        global config
        try:
            config = Config(args.config)
        except Exception as e:
            print(f"⚠️  Failed to load custom config: {e}")

    # First-run auto-configuration (interactive). Skipped with --no-setup.
    if not getattr(args, "no_setup", False):
        auto_configure_if_needed()

    # Print startup banner
    print("""
╔════════════════════════════════════════╗
║       🤖 Clio-Agent-2 Starting         ║
╚════════════════════════════════════════╝
    """)

    # Show configuration status
    try:
        api_status = config.validate_api_keys()
        print("📋 Configuration Status:")
        for provider, is_configured in api_status.items():
            status = "✓" if is_configured else "✗"
            print(f"  {status} {provider}")
        print()
    except Exception as e:
        print(f"⚠️  Could not validate API keys: {e}")
        print()

    # Determine which interface(s) to run
    try:
        if args.all:
            print("🚀 Starting all interfaces...\n")
            asyncio.run(_run_interface(run_all(replace=args.replace)))
        elif args.whatsapp:
            if extras:
                # Legacy message sending mode: phone number + optional message
                print("📱 Sending WhatsApp message...")
                phone_number = extras[0]
                message = " ".join(extras[1:]) if len(extras) > 1 else "Message from Clio-Agent"
                script_path = Path(__file__).parent / "whatsapp_service.js"
                subprocess.run(["node", str(script_path), phone_number, message], check=True)
                sys.exit(0)
            else:
                # New bot mode: run the WhatsApp Business API interface
                print("📱 Starting WhatsApp Business API bot...\n")
                asyncio.run(_run_interface(run_whatsapp()))
        elif args.telegram:
            print("📱 Starting Telegram bot...\n")
            asyncio.run(_run_interface(run_telegram(replace=args.replace)))
        elif args.discord:
            print("🎮 Starting Discord bot (Beta)...\n")
            asyncio.run(_run_interface(run_discord()))
        else:
            print("💻 Starting CLI interface...\n")
            asyncio.run(_run_interface(run_cli()))
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except RuntimeError as e:
        # A signal-triggered shutdown is a *graceful* exit, not a crash. In the
        # rare event a stopped loop still bubbles up this specific RuntimeError,
        # treat it as a normal goodbye instead of a fatal error + exit code 1.
        if "Event loop stopped before Future completed" in str(e):
            print("\n\n👋 Goodbye!")
        else:
            print(f"\n⚠️  Fatal error: {e}")
            print("The program encountered an unexpected error but will exit gracefully.")
            sys.exit(1)
    except Exception as e:
        print(f"\n⚠️  Fatal error: {e}")
        print("The program encountered an unexpected error but will exit gracefully.")
        sys.exit(1)
    finally:
        # Last chance to flush the context log before the process ends.
        _flush_contexts()


if __name__ == "__main__":
    main()
