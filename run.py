#!/usr/bin/env python3
"""
Ultimate Zero-Configuration AI Agent Runner
Usage: python3 run.py

The agent is fully autonomous — no instruction needed.
It observes, explores, and acts on its own.

This script automatically:
1. Detects if running in virtual environment
2. Creates virtual environment if needed
3. Installs all dependencies automatically
4. Restarts itself in the virtual environment
5. Prompts for model selection (Ollama with model options or Google API)
6. Runs the AI agent in perpetual autonomous mode
"""

import sys
import os
import signal
import atexit
import subprocess
import platform
import shutil
import json as _json_mod
import time
from pathlib import Path
from typing import Optional

# Project root & context directory
_PROJECT_ROOT = Path(__file__).parent.resolve()
_CONTEXT_DIR = _PROJECT_ROOT / ".context"
_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

def _emergency_save_context(signum, frame):
    """Save as much context as possible when interrupted by a signal."""
    import json as _j, time as _t
    try:
        data = {
            "status": f"Interrupted by signal {signum}",
            "goal": "", "user_prompt": "", "iteration_count": 0,
            "compressed_context": f"Process interrupted by signal {signum}.",
            "execution_log": [], "timestamp": _t.time(),
            "telegram_mode": False,
            "auxiliary": {"git_diff": "", "metadata": "", "errors": "", "log_tail_lines": 0},
        }
        # Try to grab live context from the agent engine if it exists
        _agent = globals().get("_global_agent_instance")
        if _agent is None:
            # Walk the stack frames looking for an agent instance
            import inspect as _inspect
            for _frame_info in _inspect.stack():
                _locals = _frame_info[0].f_locals
                if "agent" in _locals and hasattr(_locals["agent"], "engine"):
                    _agent = _locals["agent"]
                    break
                # Also check 'self' for signal handler inside a method
                _self = _locals.get("self")
                if _self is not None and hasattr(_self, "engine") and hasattr(_self.engine, "_current_context"):
                    _agent = _self
                    break
        if _agent is not None:
            _engine = getattr(_agent, "engine", None)
            if _engine is not None:
                _ctx = getattr(_engine, "_current_context", None)
                if _ctx is not None:
                    data["goal"] = getattr(_ctx, "current_goal", "") or ""
                    data["user_prompt"] = getattr(_ctx, "user_prompt", "") or ""
                    data["iteration_count"] = getattr(_ctx, "iteration_count", 0) or 0
                    data["telegram_mode"] = getattr(_ctx, "telegram_mode", False) or False
                    _exec_log = getattr(_ctx, "execution_log", None)
                    if _exec_log:
                        data["execution_log"] = _exec_log[-200:]
                    _meta = getattr(_ctx, "metadata", {}) or {}
                    data["auxiliary"]["metadata"] = _j.dumps(_meta, ensure_ascii=False)[:1000]
                    data["restart_provider"] = _meta.get("restart_provider", "")
                    data["restart_model"] = _meta.get("restart_model", "")
                    data["compressed_context"] = (
                        f"Process interrupted by signal {signum}. "
                        f"Last goal: {data['goal']}. "
                        f"Iterations: {data['iteration_count']}."
                    )
        # Try to pull terminal history entries
        try:
            from ai_agent.core_processing.terminal_history import get_terminal_history as _get_th
            _th = _get_th()
            _entries = getattr(getattr(_th, "terminal_session", None), "entries", [])
            if _entries:
                _log_lines = []
                for _e in _entries[-200:]:
                    _ts = _t.strftime("%H:%M:%S", _t.localtime(_e.timestamp))
                    _content = _e.content[:200] if _e.content else ""
                    _log_lines.append(f"[{_ts}] [{_e.entry_type.value}] {_content}")
                if not data["execution_log"]:
                    data["execution_log"] = _log_lines
                data["auxiliary"]["log_tail_lines"] = len(_log_lines)
        except Exception:
            pass
        # Try git diff
        try:
            import subprocess as _sp
            _gd = _sp.run(["git", "diff", "--stat"], capture_output=True, text=True, timeout=10)
            if _gd.returncode == 0 and _gd.stdout.strip():
                data["auxiliary"]["git_diff"] = _gd.stdout.strip()[:2000]
        except Exception:
            pass
        # Collect error lines from execution log
        _errors = [l for l in data["execution_log"]
                   if any(m in l.lower() for m in ("error", "exception", "failed", "traceback"))]
        if _errors:
            data["auxiliary"]["errors"] = "\n".join(_errors)[:2000]
        with open(_CONTEXT_DIR / "exit_state.json", "w", encoding="utf-8") as f:
            _j.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

try:
    signal.signal(signal.SIGINT, _emergency_save_context)
    signal.signal(signal.SIGTERM, _emergency_save_context)
except Exception:
    pass

# ── Initialize resilience engine (global exception handler) ──────────────
# Must be done early, before any other imports that might fail.
try:
    from ai_agent.utils.resilience_engine import get_resilience_engine, ResilienceConfig

    _resilience_config = ResilienceConfig(
        max_retries=3,
        base_delay=2.0,
        backoff_factor=2.0,
        enable_self_healing=True,
        telegram_notify_on_error=True,
        telegram_notify_on_recovery=True,
        install_global_hook=True,
        log_all_errors=True,
        error_log_path="logs/resilience_errors.jsonl",
    )
    _resilience_engine = get_resilience_engine(_resilience_config)
except Exception:
    pass  # If resilience engine fails to init, continue without it

# Global constants
VENV_DIR = "venv"
VENV_RESTART_FLAG = "--__venv_restarted__"
USER_RESTART_FLAG = "--__user_restarted__"
RESTART_ENV_PREFIX = "VEXIS_RESTART_"
RESTART_MODE_ENV = f"{RESTART_ENV_PREFIX}MODE"
RESTART_PROVIDER_ENV = f"{RESTART_ENV_PREFIX}PROVIDER"
RESTART_MODEL_ENV = f"{RESTART_ENV_PREFIX}MODEL"
RESTART_API_KEY_ENV = f"{RESTART_ENV_PREFIX}API_KEY"

PROVIDER_API_KEY_ENV_VARS = {
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "meta": "META_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "microsoft": "AZURE_OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "amazon": "AWS_ACCESS_KEY_ID",
    "cohere": "COHERE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "zhipuai": "ZHIPUAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}



def _get_api_key_for_provider(provider: Optional[str]) -> Optional[str]:
    """Return the active API key for a provider without prompting the user."""
    if not provider or provider == "ollama":
        return None

    try:
        from ai_agent.utils.settings_manager import get_settings_manager

        settings_manager = get_settings_manager()
        try:
            api_key = settings_manager.get_api_key(provider)
            if api_key:
                return api_key
        except Exception:
            pass

        method_name = f"get_{provider}_api_key"
        if hasattr(settings_manager, method_name):
            api_key = getattr(settings_manager, method_name)()
            if api_key:
                return api_key
    except Exception:
        pass

    env_var = PROVIDER_API_KEY_ENV_VARS.get(provider)
    return os.getenv(env_var) if env_var else None


def _restore_restart_settings_from_env():
    """Hydrate in-memory settings from /restart environment overrides."""
    provider = os.getenv(RESTART_PROVIDER_ENV)
    model = os.getenv(RESTART_MODEL_ENV)
    api_key = os.getenv(RESTART_API_KEY_ENV)

    if not provider:
        return

    try:
        from ai_agent.utils.settings_manager import get_settings_manager

        settings_manager = get_settings_manager()
        try:
            settings_manager.set_preferred_provider(provider)
        except Exception:
            pass

        if model:
            try:
                settings_manager.set_model(provider, model)
            except Exception:
                method_name = f"set_{provider}_model"
                if hasattr(settings_manager, method_name):
                    getattr(settings_manager, method_name)(model)
                elif provider == "microsoft" and hasattr(settings_manager, "set_microsoft_model"):
                    settings_manager.set_microsoft_model(model)

        if api_key:
            try:
                settings_manager.set_api_key(provider, api_key)
            except Exception:
                method_name = f"set_{provider}_api_key"
                if hasattr(settings_manager, method_name):
                    getattr(settings_manager, method_name)(api_key)
                elif provider == "microsoft" and hasattr(settings_manager, "set_microsoft_api_key"):
                    settings_manager.set_microsoft_api_key(api_key)

            env_var = PROVIDER_API_KEY_ENV_VARS.get(provider)
            if env_var:
                os.environ[env_var] = api_key
            if provider == "google":
                os.environ.setdefault("GEMINI_API_KEY", api_key)
    except Exception as e:
        print(f"⚠️ Could not restore restart settings: {e}")


def restart_with_current_settings(
    selected_mode: str,
    selected_provider: Optional[str],
    selected_model: Optional[str],
    debug_mode: bool = False,
    max_iterations: Optional[int] = None,
):
    """Replace the current process while preserving runtime provider/model/API settings."""
    env = os.environ.copy()
    env[RESTART_MODE_ENV] = selected_mode
    if selected_provider:
        env[RESTART_PROVIDER_ENV] = selected_provider
    else:
        env.pop(RESTART_PROVIDER_ENV, None)
    if selected_model:
        env[RESTART_MODEL_ENV] = selected_model
    else:
        env.pop(RESTART_MODEL_ENV, None)

    api_key = _get_api_key_for_provider(selected_provider)
    if api_key:
        env[RESTART_API_KEY_ENV] = api_key
        api_env_var = PROVIDER_API_KEY_ENV_VARS.get(selected_provider or "")
        if api_env_var:
            env[api_env_var] = api_key
        if selected_provider == "google":
            env.setdefault("GEMINI_API_KEY", api_key)
    else:
        env.pop(RESTART_API_KEY_ENV, None)

    new_args = [sys.executable, str(_PROJECT_ROOT / "run.py"), USER_RESTART_FLAG, "--no-prompt"]
    if debug_mode:
        new_args.append("--debug")
    if max_iterations is not None:
        new_args.extend(["--max-iterations", str(max_iterations)])

    try:
        os.execve(sys.executable, new_args, env)
    except OSError as e:
        print(f"Fatal: restart failed: {e}")
        sys.exit(1)

def is_in_virtual_environment():
    """Check if currently running in a virtual environment"""
    return (
        hasattr(sys, 'real_prefix') or 
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) or
        os.getenv('VIRTUAL_ENV') is not None
    )

def get_venv_python_path():
    """Get the Python executable path in the virtual environment"""
    project_root = Path(__file__).parent
    venv_path = project_root / VENV_DIR
    
    if not venv_path.exists():
        return None
    
    if platform.system() == "Windows":
        python_exe = venv_path / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = venv_path / "Scripts" / "pythonw.exe"
    else:
        python_exe = venv_path / "bin" / "python"
        if not python_exe.exists():
            python_exe = venv_path / "bin" / "python3"
    
    return str(python_exe) if python_exe.exists() else None

def check_venv_prerequisites():
    """Check if virtual environment creation prerequisites are met"""
    print("Checking virtual environment prerequisites...")
    
    # Test if venv module is available
    try:
        import venv
        print("✓ venv module is available")
        return True
    except ImportError:
        print("✗ venv module is not available")
        return False

def create_virtual_environment():
    """Create a virtual environment with robust error handling"""
    project_root = Path(__file__).parent
    venv_path = project_root / VENV_DIR
    
    print(f"Creating virtual environment at {venv_path}...")
    
    # Remove existing venv if it exists and appears broken
    if venv_path.exists():
        venv_python = get_venv_python_path()
        if venv_python:
            try:
                # Test if existing venv works
                result = subprocess.run([venv_python, "--version"], 
                                      capture_output=True, text=True, timeout=10)
                if result.returncode != 0:
                    print("Existing virtual environment appears broken, recreating...")
                    shutil.rmtree(venv_path)
                else:
                    # Also check that pip is functional inside the venv
                    pip_check = subprocess.run(
                        [venv_python, "-m", "pip", "--version"],
                        capture_output=True, text=True, timeout=10
                    )
                    if pip_check.returncode != 0:
                        print("Existing virtual environment has no pip, recreating...")
                        shutil.rmtree(venv_path)
                    else:
                        print("Virtual environment already exists and is functional")
                        return True
            except Exception:
                print("Existing virtual environment appears broken, recreating...")
                shutil.rmtree(venv_path)
        else:
            print("Removing incomplete virtual environment...")
            shutil.rmtree(venv_path)
    
    try:
        # Create virtual environment
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            
            # Handle specific error cases
            if "ensurepip is not available" in error_msg or "python3-venv" in error_msg:
                print("✗ Virtual environment creation failed: python3-venv package not installed")
                print()
                print("To fix this issue, run one of the following commands:")
                print(f"  sudo apt install python3.{sys.version_info.minor}-venv")
                print("  # or for Ubuntu/Debian systems:")
                print("  sudo apt install python3-venv")
                print()
                print("After installing the package, run this script again.")
                return False
            elif "Permission denied" in error_msg:
                print("✗ Permission denied when creating virtual environment")
                print("Check that you have write permissions to the project directory")
                return False
            else:
                print(f"✗ Failed to create virtual environment: {error_msg}")
                print("Full error details:")
                print(f"  Return code: {result.returncode}")
                print(f"  Stderr: {result.stderr}")
                print(f"  Stdout: {result.stdout}")
                return False
        
        print("✓ Virtual environment created successfully")
        return True
        
    except subprocess.TimeoutExpired:
        print("✗ Virtual environment creation timed out")
        return False
    except Exception as e:
        print(f"✗ Error creating virtual environment: {e}")
        return False

def restart_in_venv():
    """Restart the current script in the virtual environment with robust error handling"""
    venv_python = get_venv_python_path()
    if not venv_python:
        print("Error: Could not find virtual environment Python executable")
        return False
    
    # Add restart flag to prevent infinite loops
    project_root = Path(__file__).parent
    new_argv = [venv_python, str(project_root / "run.py"), VENV_RESTART_FLAG] + sys.argv[1:]
    
    print(f"Restarting in virtual environment: {venv_python}")
    
    try:
        # Use os.execv to replace current process
        # This is more reliable than subprocess on all platforms
        os.execv(venv_python, new_argv)
    except OSError as e:
        print(f"OS error restarting in virtual environment: {e}")
        print("This might be due to permissions or antivirus software.")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error restarting in virtual environment: {e}")
        sys.exit(1)
    
    # This should never be reached if execv succeeds
    return True

def install_dependencies():
    """Install all dependencies in the virtual environment with enhanced error handling"""
    project_root = Path(__file__).parent
    venv_python = get_venv_python_path()
    
    if not venv_python:
        print("Error: Virtual environment Python not found")
        return False
    
    print("Installing dependencies...")

    # Check network connectivity first
    try:
        import socket
        socket.create_connection(("pypi.org", 443), timeout=10)
        print("✓ Network connectivity OK")
    except Exception as e:
        print(f"Warning: Network connectivity issue: {e}")
        print("Dependency installation may fail without internet access.")

    # Ensure pip is available in the venv (bootstrap if missing)
    try:
        pip_check = subprocess.run(
            [venv_python, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if pip_check.returncode != 0:
            print("pip not found in virtual environment, bootstrapping with ensurepip...")
            ensurepip_result = subprocess.run(
                [venv_python, "-m", "ensurepip", "--upgrade"],
                capture_output=True, text=True, timeout=60
            )
            if ensurepip_result.returncode != 0:
                print(f"ensurepip failed: {ensurepip_result.stderr.strip()}")
                print("Attempting to install pip via get-pip.py...")
                import urllib.request
                import tempfile
                getpip_url = "https://bootstrap.pypa.io/get-pip.py"
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.py', delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    urllib.request.urlretrieve(getpip_url, tmp_path)
                    getpip_result = subprocess.run(
                        [venv_python, tmp_path],
                        capture_output=True, text=True, timeout=120
                    )
                    if getpip_result.returncode != 0:
                        print(f"get-pip.py failed: {getpip_result.stderr.strip()}")
                        print("Failed to bootstrap pip. Try deleting the 'venv' directory and running again.")
                        return False
                    print("✓ pip installed via get-pip.py")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            else:
                print("✓ pip bootstrapped via ensurepip")
        else:
            print(f"✓ pip available: {pip_check.stdout.strip()}")
    except Exception as e:
        print(f"Error checking pip: {e}")
        return False

    # Upgrade pip first with retry mechanism
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"Retry {attempt + 1}/{max_retries} upgrading pip...")
            else:
                print("Upgrading pip...")
            
            result = subprocess.run([venv_python, "-m", "pip", "install", "--upgrade", "pip"],
                                  capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                print("✓ pip upgraded")
                break
            else:
                if attempt == max_retries - 1:
                    print(f"pip upgrade failed after {max_retries} attempts: {result.stderr}")
                    print("Continuing with current pip version...")
                else:
                    print(f"pip upgrade attempt {attempt + 1} failed, retrying...")
        except subprocess.TimeoutExpired:
            if attempt == max_retries - 1:
                print("pip upgrade timed out, continuing with current pip version...")
            else:
                print("pip upgrade timed out, retrying...")
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"pip upgrade error: {e}")
                print("Continuing with current pip version...")
            else:
                print(f"pip upgrade error: {e}, retrying...")
    
    # Install from requirements files if they exist
    requirements_files = [
        project_root / "peripherals" / "requirements-core.txt",
        project_root / "peripherals" / "requirements.txt",  # main requirements
        project_root / "peripherals" / "requirements-optional.txt"  # optional cloud SDKs
    ]
    
    for requirements_file in requirements_files:
        if requirements_file.exists():
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        print(f"Retry {attempt + 1}/{max_retries} installing {requirements_file.name}...")
                    else:
                        print(f"Installing from {requirements_file.name}...")
                    
                    result = subprocess.run([venv_python, "-m", "pip", "install", "-r", str(requirements_file)],
                                          capture_output=True, text=True, timeout=600)
                    if result.returncode == 0:
                        print(f"✓ {requirements_file.name} installed")
                        
                        # Handle different requirements files appropriately
                        if requirements_file.name == "requirements-core.txt":
                            print("✓ Core dependencies installed successfully")
                            # Continue to install main requirements
                        elif requirements_file.name == "requirements.txt":
                            print("✓ Main dependencies installed successfully")
                            # Continue to optional dependencies
                            break
                        elif requirements_file.name == "requirements-optional.txt":
                            print("✓ Optional cloud SDKs installed successfully")
                            return True  # All done
                        break
                    else:
                        error_msg = result.stderr.strip()
                        if attempt == max_retries - 1:
                            print(f"{requirements_file.name} installation failed after {max_retries} attempts: {error_msg}")
                            
                            # Provide helpful error messages
                            if "Permission denied" in error_msg:
                                print("Permission denied. Check antivirus software or file permissions.")
                            elif "Could not find a version" in error_msg:
                                print("Package version conflict. Check requirements file compatibility.")
                            elif "Network is unreachable" in error_msg or "Connection failed" in error_msg:
                                print("Network error. Check internet connection.")
                            else:
                                print("See error message above for details.")
                            
                            # Handle failure based on file type
                            if requirements_file.name == "requirements-core.txt":
                                return False  # Critical failure
                            elif requirements_file.name == "requirements.txt":
                                print("⚠️ Main requirements failed, continuing with core only...")
                                # Core requirements were already installed, continue
                                return True  # Core deps are installed, that's enough
                            elif requirements_file.name == "requirements-optional.txt":
                                print("⚠️ Optional cloud SDKs failed to install, continuing...")
                                return True  # Optional failure is OK
                        else:
                            print(f"{requirements_file.name} attempt {attempt + 1} failed, retrying...")
                except subprocess.TimeoutExpired:
                    if attempt == max_retries - 1:
                        print(f"{requirements_file.name} installation timed out")
                        if requirements_file.name == "requirements-core.txt":
                            return False
                        else:
                            print("Continuing without optional dependencies...")
                            return True  # Continue without optional deps
                    else:
                        print(f"{requirements_file.name} installation timed out, retrying...")
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"{requirements_file.name} installation error: {e}")
                        if requirements_file.name == "requirements-core.txt":
                            return False
                        else:
                            print("Continuing without optional dependencies...")
                            return True  # Continue without optional deps
                    else:
                        print(f"{requirements_file.name} installation error: {e}, retrying...")
    
    # Install project in editable mode if pyproject.toml exists
    pyproject_file = project_root / "peripherals" / "pyproject.toml"
    if pyproject_file.exists():
        try:
            print("Installing project in editable mode...")
            result = subprocess.run([venv_python, "-m", "pip", "install", "-e", str(project_root)],
                                  capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                print("✓ project installed")
            else:
                print(f"project installation warning: {result.stderr}")
                print("Project installation failed, but dependencies may still work")
        except subprocess.TimeoutExpired:
            print("project installation timed out")
            print("Project installation failed, but dependencies may still work")
        except Exception as e:
            print(f"project installation error: {e}")
            print("Project installation failed, but dependencies may still work")
    
    return True

def bootstrap_environment():
    """Bootstrap the environment - create venv and install dependencies"""
    print("Bootstrapping environment...")
    
    # Check prerequisites first
    if not check_venv_prerequisites():
        print()
        print("Virtual environment prerequisites not met.")
        print("This is likely because the python3-venv package is not installed.")
        print()
        print("To fix this issue, run one of the following commands:")
        print(f"  sudo apt install python3.{sys.version_info.minor}-venv")
        print("  # or for Ubuntu/Debian systems:")
        print("  sudo apt install python3-venv")
        print()
        print("After installing the package, run this script again.")
        return False
    
    # Create virtual environment
    if not create_virtual_environment():
        print("Failed to create virtual environment")
        return False
    
    # Install dependencies
    if not install_dependencies():
        print("Failed to install dependencies")
        return False
    
    print("✓ Environment bootstrap complete")
    return True

def show_help():
    """Show help message"""
    print("VEXIS-CLI AI Agent Runner")
    print("=" * 50)
    print("Usage: python3 run.py")
    print()
    print("The agent is fully autonomous — no instruction needed.")
    print("It observes, explores, and acts on its own.")
    print()
    print("This script automatically handles:")
    print("  • Virtual environment creation and management")
    print("  • Dependency installation")
    print("  • Model selection (14 AI providers with model options)")
    print("    - Local: Ollama (privacy-focused)")
    print("    - Cloud: OpenAI, Anthropic, Google, xAI, Meta, Groq, DeepSeek, Together, Microsoft, Mistral, Amazon, Cohere, MiniMax")
    print("  • Cross-platform compatibility")
    print("  • Self-bootstrapping")
    print("  • Environment detection and adaptive execution")
    print()
    print("Model Options:")
    print("  🦊 Ollama: Local models (privacy-focused) - Stable")
    print("  🌐 Google: Gemini models (enterprise-grade) - Stable")
    print("  🤖 OpenAI: GPT models (advanced capabilities) - Beta")
    print("  🧠 Anthropic: Claude models (strong reasoning) - Beta")
    print("  🚀 xAI: Grok models (real-time knowledge) - Beta")
    print("  🦙 Meta: Llama models (via Meta API) - Beta")
    print("  ⚡ Groq: Fast inference (Llama/Mixtral) - Beta")
    print("  🔍 DeepSeek: Advanced reasoning models - Beta")
    print("  🤝 Together AI: Open-source model hosting - Beta")
    print("  ☁️ Microsoft: GPT models via Azure - Beta")
    print("  🌍 Mistral AI: Multilingual models - Beta")
    print("  🏭 Amazon Bedrock: Titan/Nova models via AWS - Beta")
    print("  🏢 Cohere: Command models for enterprise - Beta")
    print("  🚀 MiniMax: M2-series models for productivity - Beta")
    print()
    print("Environment Commands:")
    print("  --check, -c         Run environment check and show recommendations")
    print("  --fix               Run environment check and auto-fix issues")
    print("  --install-sdks      Install missing AI provider SDKs")
    print("  --sdk-status        Show AI provider SDK installation status")
    print()
    print("Examples:")
    print("  python3 run.py                              # Start autonomous agent (no instruction)")
    print("  python3 run.py \"Take a screenshot\"")
    print("  python3 run.py \"Open a web browser and search for AI\"")
    print("  python3 run.py --check")
    print("  python3 run.py --install-sdks")
    print()
    print("Options:")
    print("  --help, -h          Show this help message")
    print("  --watchdog          Enable watchdog supervisor (auto-restart on crash)")
    print("  --supervisor        Enable eternal supervisor (maximum resilience)")
    print("  --health-check      Run a self-diagnostic and exit")
    print("  --debug             Enable debug mode")
    print("  --no-prompt         Use saved provider preference without prompting")
    print("  --setting           Force interactive provider/model selection menu")
    print("  --sleep             Compress context and restart immediately")
    print("  --self-heal         Enable enhanced self-healing mode")
    print("  --telegram          Run in Telegram bot mode")
    print("  --discord           Run in Discord bot mode")
    print()
    print("SDK Management:")
    print("  python3 peripherals/manage_sdks.py status          # Show SDK status")
    print("  python3 peripherals/manage_sdks.py install         # Install all missing SDKs")
    print("  python3 peripherals/manage_sdks.py install google  # Install specific SDK")
    print()
    print("Virtual Environment:")
    print("  Automatically creates and uses './venv' directory")
    print("  All dependencies are isolated within the virtual environment")
    print("  No manual setup required - just run and go!")

def check_ollama_login_with_fallback():
    """Check Ollama login with version-aware fallback"""
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    from ai_agent.utils.environment_detector import EnvironmentDetector
    
    detector = EnvironmentDetector()
    ollama_available = detector._detect_ollama_available()
    
    if not ollama_available:
        error_message("Ollama is not installed or not in PATH")
        print(f"{Colors.BRIGHT_CYAN}Please install Ollama first: https://ollama.com/{Colors.RESET}")
        print(f"{Colors.CYAN}Or run with --fix to auto-install{Colors.RESET}")
        return False, "not_installed"
    
    # Check version for cloud model support
    needs_update = detector._detect_needs_ollama_update()
    has_whoami = detector._detect_ollama_has_whoami()
    
    if needs_update:
        warning_message(f"Ollama version is outdated (cloud models require 0.17.0+)")
        print(f"{Colors.CYAN}Local models will work, but cloud models require update.{Colors.RESET}")
        print(f"{Colors.CYAN}Run with --fix to update Ollama automatically.{Colors.RESET}")
        # Return partial success - local models still work
        return True, "local_only"
    
    # Check if signed in (only for newer versions)
    if has_whoami:
        try:
            result = subprocess.run(["ollama", "whoami"],
                                  capture_output=True, text=True, timeout=10)
            # Check if signed in: returncode 0 AND output is not empty AND doesn't say "not signed in"
            output_combined = (result.stdout or "") + (result.stderr or "")
            is_signed_in = (result.returncode == 0 and
                           output_combined.strip() and
                           "not signed in" not in output_combined.lower())

            if is_signed_in:
                success_message("Ollama is signed in")
                return True, "full"
            else:
                warning_message("Ollama is available but you are not signed in.")
                print(f"{Colors.CYAN}Cloud models require signin. Local models will work.{Colors.RESET}")
                print(f"{Colors.CYAN}Run 'ollama signin' to enable cloud models.{Colors.RESET}")
                return True, "needs_signin"
        except Exception:
            return True, "local_only"

    # Old version without whoami - assume local only
    return True, "local_only"

def run_environment_check(fix_mode=False):
    """Run environment detection and optionally fix issues"""
    from ai_agent.utils.environment_detector import detect_and_plan
    from ai_agent.utils.interactive_menu import Colors
    
    env_info, executor = detect_and_plan()
    
    # Save report
    import json
    from dataclasses import asdict
    
    report_path = Path("environment_report.json")
    with open(report_path, 'w') as f:
        json.dump(asdict(env_info), f, indent=2)
    
    print(f"\n📄 Detailed report saved to: {report_path}")
    
    # Execute fix plan if requested
    if fix_mode and executor.execution_plan:
        print(f"\n🔧 Fix mode enabled - executing {len(executor.execution_plan)} steps")
        executor.execute_plan(interactive=True)
    elif executor.execution_plan:
        print(f"\n💡 Run with --fix to automatically address these issues")
    
    return env_info, executor

def update_ollama():
    """Update Ollama to latest version"""
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    import tempfile
    
    print(f"{Colors.CYAN}Updating Ollama...{Colors.RESET}")
    try:
        # Create a temporary file for the install script
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as tmp_script:
            script_path = tmp_script.name
        
        try:
            # Step 1: Download the install script using curl (without shell)
            download_result = subprocess.run(
                ['curl', '-fsSL', 'https://ollama.com/install.sh'],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if download_result.returncode != 0:
                error_message(f"Failed to download Ollama install script: {download_result.stderr}")
                return False
            
            # Write script to temp file
            with open(script_path, 'w') as f:
                f.write(download_result.stdout)
            
            # Make script executable
            os.chmod(script_path, 0o755)
            
            # Step 2: Execute the downloaded script with bash (without shell=True)
            result = subprocess.run(
                ['bash', script_path],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                success_message("Ollama updated successfully")
                return True
            else:
                error_message(f"Ollama update failed: {result.stderr}")
                return False
        finally:
            # Clean up temp file
            try:
                os.unlink(script_path)
            except Exception:
                pass
    except Exception as e:
        error_message(f"Error updating Ollama: {e}")
        return False

def prompt_for_api_key(provider_name, env_var_name, setup_url):
    """Prompt user for API key with standardized messaging"""
    import getpass
    from ai_agent.utils.interactive_menu import Colors, error_message, warning_message
    
    print(f"\n{Colors.BOLD}{Colors.CYAN}{provider_name} API Key Setup{Colors.RESET}")
    print(f"{Colors.CYAN}{'-' * 30}{Colors.RESET}")
    print(f"{Colors.WHITE}Environment variable: {env_var_name}{Colors.RESET}")
    if setup_url:
        print(f"{Colors.BRIGHT_CYAN}You can get one from: {setup_url}{Colors.RESET}")
    print()
    
    while True:
        try:
            api_key = getpass.getpass(f"{Colors.YELLOW}Enter your {provider_name} API key (or press Enter to cancel):{Colors.RESET} ")
            if not api_key.strip():
                warning_message("No API key provided. Configuration cancelled.")
                return None
            
            if len(api_key) < 10:
                error_message("API key seems too short. Please check your key.")
                continue
            
            return api_key
            
        except KeyboardInterrupt:
            print(f"\n{Colors.BRIGHT_YELLOW}Operation cancelled.{Colors.RESET}")
            return None
        except Exception as e:
            error_message(f"Error reading input: {e}")
            return None


def prompt_for_google_api_key():
    """Prompt user for Google API key using standardized prompt"""
    return prompt_for_api_key(
        "Google",
        "GOOGLE_API_KEY or GEMINI_API_KEY",
        "https://aistudio.google.com/app/apikey")

def select_google_model():
    """Prompt user to select Google model using curses arrow keys"""
    from ai_agent.utils.settings_manager import get_settings_manager
    from ai_agent.utils.curses_menu import get_curses_menu
    
    settings_manager = get_settings_manager()
    current_model = settings_manager.get_google_model()
    
    # Use curses-based menu with arrow keys
    menu = get_curses_menu(
        "🚀 Select Gemini Model",
        "Choose your preferred Gemini model:"
    )
    
    menu.add_item(
        "Gemini 3 Flash",
        "Fast and efficient • Cost-effective for most tasks",
        "gemini-3-flash-preview",
        "🚀"
    )
    
    menu.add_item(
        "Gemini 3.1 Pro",
        "Advanced reasoning • Best for complex problem-solving",
        "gemini-3.1-pro-preview",
        "🧠"
    )
    
    selected_model = menu.show()
    
    if selected_model is None:
        return current_model
    
    settings_manager.set_google_model(selected_model)
    return selected_model

def show_config_summary(provider: str, model: str = None):
    """Display a clean configuration summary"""
    from ai_agent.utils.interactive_menu import Colors
    from ai_agent.utils.settings_manager import get_settings_manager
    
    settings_manager = get_settings_manager()
    
    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}{'─' * 50}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_GREEN}✓ Configuration Complete{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{'─' * 50}{Colors.RESET}")
    
    # Provider and model display mapping
    provider_info = {
        "ollama":     ("Ollama (Local)",        settings_manager.get_ollama_model()),
        "google":     ("Google Gemini",         model or settings_manager.get_google_model()),
        "openai":     ("OpenAI",                model or settings_manager.get_openai_model()),
        "anthropic":  ("Anthropic Claude",      model or settings_manager.get_anthropic_model()),
        "xai":        ("xAI Grok",              model or settings_manager.get_xai_model()),
        "meta":       ("Meta Llama",            model or settings_manager.get_meta_model()),
        "groq":       ("Groq",                  model or settings_manager.get_groq_model()),
        "deepseek":   ("DeepSeek",              model or settings_manager.get_deepseek_model()),
        "together":   ("Together AI",           model or settings_manager.get_together_model()),
        "microsoft":  ("Microsoft Azure",       model or settings_manager.get_microsoft_model()),
        "mistral":    ("Mistral AI",            model or settings_manager.get_mistral_model()),
        "amazon":     ("Amazon Bedrock",        model or settings_manager.get_amazon_model()),
        "cohere":     ("Cohere",                model or settings_manager.get_cohere_model()),
        "minimax":    ("MiniMax",               model or settings_manager.get_minimax_model()),
        "zhipuai":    ("ZhipuAI",               model or settings_manager.get_zhipuai_model()),
        "openrouter": ("OpenRouter",            model or settings_manager.get_openrouter_model())
    }
    
    if provider in provider_info:
        provider_name, model_name = provider_info[provider]
        print(f"{Colors.WHITE}  Provider: {Colors.BRIGHT_YELLOW}{provider_name}{Colors.RESET}")
        
        # Format model name for better display
        if model_name:
            display_model = format_model_display_name(provider, model_name)
            print(f"{Colors.WHITE}  Model:    {Colors.BRIGHT_YELLOW}{display_model}{Colors.RESET}")
    else:
        print(f"{Colors.WHITE}  Provider: {Colors.BRIGHT_YELLOW}Unknown Provider{Colors.RESET}")
        print(f"{Colors.WHITE}  Model:    {Colors.BRIGHT_YELLOW}{model or 'Unknown'}{Colors.RESET}")
    
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{'─' * 50}{Colors.RESET}\n")

def format_model_display_name(provider: str, model: str) -> str:
    """Format model names for better display"""
    model_display_map = {
        "google": {
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-3-flash-preview": "Gemini 3 Flash",
            "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
            "gemini-1.5-pro": "Gemini 1.5 Pro",
            "gemini-1.5-flash": "Gemini 1.5 Flash"
        },
        "openai": {
            "gpt-4o": "GPT-4o",
            "gpt-4o-mini": "GPT-4o Mini",
            "gpt-4-turbo": "GPT-4 Turbo",
            "gpt-3.5-turbo": "GPT-3.5 Turbo"
        },
        "anthropic": {
            "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
            "claude-3-opus-20240229": "Claude 3 Opus",
            "claude-3-sonnet-20240229": "Claude 3 Sonnet",
            "claude-3-haiku-20240307": "Claude 3 Haiku"
        },
        "minimax": {
            "minimax-m2.7": "MiniMax M2.7 (Latest)",
            "minimax-m2.5": "MiniMax M2.5",
            "minimax-m2": "MiniMax M2 (Legacy)"
        }
    }
    
    if provider in model_display_map and model in model_display_map[provider]:
        return model_display_map[provider][model]
    
    return model

def configure_google_provider():
    """Configure Google provider with standardized flow: model selection first, then API key"""
    from ai_agent.utils.settings_manager import get_settings_manager
    from ai_agent.utils.interactive_menu import Colors, info_message, warning_message
    
    settings_manager = get_settings_manager()
    
    # Step 1: Select model first
    model = select_google_model()
    if model is None:
        model = settings_manager.get_google_model()
    
    # Step 2: Check for existing API key
    info_message("Configuring Google Gemini Provider")
    
    import os
    existing_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    
    if not existing_key and not settings_manager.has_google_api_key():
        # Prompt for API key after model selection
        api_key = prompt_for_google_api_key()
        if api_key is None:
            warning_message("No API key provided - Google Gemini requires an API key.")
            return None, None
        settings_manager.set_google_api_key(api_key)
    
    settings_manager.set_preferred_provider("google")
    print(f"{Colors.GREEN}✓ Google Gemini configured successfully!{Colors.RESET}")
    return "google", model

def ensure_ollama_model_available(model_name: str) -> bool:
    """Ensure the specified Ollama model is available locally, pull if necessary"""
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    from ai_agent.utils.ollama_error_handler import handle_ollama_error
    
    try:
        # Check if model is already available
        result = subprocess.run(["ollama", "list"], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            available_models = result.stdout.strip().split('\n')
            if len(available_models) > 1:  # First line is header
                model_names = [line.split()[0] for line in available_models[1:] if line.strip()]
                if model_name in model_names:
                    success_message(f"Model {model_name} is already available")
                    return True
        
        # Model not available, try to pull it
        warning_message(f"Model {model_name} not found locally, pulling...")
        print(f"{Colors.CYAN}This may take several minutes depending on model size and network speed.{Colors.RESET}")
        print(f"{Colors.YELLOW}💡 Tip: You can press Ctrl+C to cancel if needed{Colors.RESET}")
        
        # Check available disk space for large models
        try:
            import shutil
            disk_usage = shutil.disk_usage("/")
            free_gb = disk_usage.free / (1024**3)
            if free_gb < 10:  # Less than 10GB free
                print(f"{Colors.YELLOW}⚠️ Low disk space warning: {free_gb:.1f}GB available{Colors.RESET}")
                print(f"{Colors.YELLOW}💡 Consider freeing up space before downloading large models{Colors.RESET}")
        except Exception:
            pass  # Disk space check is optional
        
        # Show progress indicator
        import threading
        import time
        
        stop_spinner = threading.Event()
        def spinner():
            spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
            i = 0
            while not stop_spinner.is_set():
                print(f"{Colors.CYAN}\r{spinner_chars[i % len(spinner_chars)]} Downloading {model_name}...{Colors.RESET}", end='', flush=True)
                time.sleep(0.1)
                i += 1
        
        spinner_thread = threading.Thread(target=spinner)
        spinner_thread.daemon = True
        spinner_thread.start()
        
        pull_result = None
        try:
            pull_result = subprocess.run(["ollama", "pull", model_name], 
                                       capture_output=False, text=True, timeout=600)  # 10 minutes timeout
        except KeyboardInterrupt:
            stop_spinner.set()
            print(f"\n{Colors.YELLOW}⚠ Download cancelled by user{Colors.RESET}")
            return False
        finally:
            stop_spinner.set()
            spinner_thread.join(timeout=0.5)
            print(f"\r{' ' * 50}\r", end='', flush=True)  # Clear spinner line
        
        if pull_result is None or pull_result.returncode != 0:
            return False

        success_message(f"✅ Successfully pulled Ollama model: {model_name}")
        # Show model size info if available
        try:
            size_result = subprocess.run(["ollama", "list"], 
                                      capture_output=True, text=True, timeout=10)
            if size_result.returncode == 0:
                lines = size_result.stdout.strip().split('\n')
                for line in lines[1:]:  # Skip header
                    if model_name in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            size_info = parts[1]
                            print(f"{Colors.GREEN}📊 Model size: {size_info}{Colors.RESET}")
                        break
        except Exception:
            pass  # Size info is optional
        return True

    except subprocess.TimeoutExpired:
        error_message(f"Timeout pulling model {model_name}")
        context = {
            'model_name': model_name,
            'operation': 'pull_model'
        }
        handle_ollama_error(f"Timeout pulling model {model_name}", context, display_to_user=True)
        return False
    except FileNotFoundError:
        error_message("Ollama command not found")
        context = {
            'operation': 'ollama_command'
        }
        handle_ollama_error("Ollama command not found", context, display_to_user=True)
        return False
    except Exception as e:
        error_message(f"Error ensuring model availability: {e}")
        context = {
            'model_name': model_name,
            'operation': 'ensure_model'
        }
        handle_ollama_error(str(e), context, display_to_user=True)
        return False

def configure_ollama_provider():
    """Configure Ollama provider - delegates to the new unified config flow."""
    from ai_agent.utils.config_flow import configure_provider_and_model
    provider, model, _ = configure_provider_and_model()
    return (provider, model) if provider else (None, None)

def select_model_provider(_recursion_depth: int = 0):
    """Main configuration - uses the new unified config flow.

    Flow: select provider -> enter API key -> fetch live models -> select model.
    Every provider includes a 'Custom Model' option to enter any model name.
    """
    from ai_agent.utils.config_flow import configure_provider_and_model, sync_selection_to_config

    if _recursion_depth > 5:
        from ai_agent.utils.interactive_menu import error_message
        error_message("Too many configuration attempts. Please try again later.")
        return None, None

    provider, model, api_key = configure_provider_and_model()

    if provider is None:
        return None, None

    # Sync to settings and config.yaml
    sync_selection_to_config(provider, model, api_key)

    # For Ollama, ensure the model is pulled locally
    if provider == "ollama":
        if not ensure_ollama_model_available(model):
            from ai_agent.utils.interactive_menu import warning_message
            warning_message("Model will be pulled on first use")

    show_config_summary(provider, model)
    return provider, model


def select_model_with_arrows(provider_name: str, models: list) -> Optional[str]:
    """Select model using arrow keys in a curses menu with categorization"""
    from ai_agent.utils.curses_menu import get_curses_menu
    
    # Categorize models for OpenAI provider
    if provider_name.lower() == "openai":
        return select_openai_model_with_categories(models)
    
    menu = get_curses_menu(
        f"🤖 {provider_name.upper()} Model Selection",
        "Choose your preferred model using arrow keys:"
    )
    
    # Add models to menu with descriptions
    model_descriptions = {
        # GPT-5.4 Series (2026 Latest)
        "gpt-5.4": "GPT-5.4 • OpenAI flagship • 1M context • Best reasoning & coding",
        "gpt-5.4-mini": "GPT-5.4 Mini • Strong mini model • Coding & computer use",
        "gpt-5.4-nano": "GPT-5.4 Nano • Cheapest GPT-5.4 • High volume tasks",

        # GPT-4.1 Series
        "gpt-4.1": "GPT-4.1 • 1M context • Smarter & more efficient",
        "gpt-4.1-mini": "GPT-4.1 Mini • Fast & cost-effective",
        "gpt-4.1-nano": "GPT-4.1 Nano • Ultra-fast • Cheapest",

        # Reasoning Models
        "o3": "O3 • Advanced reasoning • STEM & complex tasks • 200K context",
        "o4-mini": "O4 Mini • Fast reasoning • Cost-effective • 200K context",
        "o3-mini": "O3 Mini • Efficient reasoning • 200K context",

        # Anthropic Claude 4.6 (Latest)
        "claude-opus-4-6-20260219": "Claude Opus 4.6 • Most capable • 1M context • Agent teams",
        "claude-sonnet-4-6-20260219": "Claude Sonnet 4.6 • Near-Opus performance • Balanced",
        "claude-opus-4-5-20251125": "Claude Opus 4.5 • Outperforms humans on coding exams",
        "claude-sonnet-4-5-20251125": "Claude Sonnet 4.5 • Efficient & capable",

        # Google Gemini 3.1 (Latest)
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro • 2M context • Advanced agentic coding",
        "gemini-3-flash-preview": "Gemini 3 Flash • Frontier performance • Cost-effective",
        "gemini-3.1-flash-lite-preview": "Gemini 3.1 Flash Lite • Ultra-efficient • New",
        "gemini-2.5-pro": "Gemini 2.5 Pro • 1M context • Advanced reasoning",
        "gemini-2.5-flash": "Gemini 2.5 Flash • Fast & efficient",

        # xAI Grok 4.1 (Latest)
        "grok-4.1": "Grok 4.1 • State-of-the-art • #1 on LMArena • Real-time",
        "grok-4.1-fast": "Grok 4.1 Fast • Quick responses • Dec 2025",
        "grok-4.1-thinking": "Grok 4.1 Thinking • Deep reasoning mode",

        # Meta Llama 4 (Latest)
        "llama-4-scout-17b-16e-instruct": "Llama 4 Scout • 10M context • 17B active • Text",
        "llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick • 1M context • 128 experts • Text",

        # Together AI Llama 4
        "meta-llama/Llama-4-Scout-17B-16E-Instruct": "Llama 4 Scout • Together hosted • 10M context",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct": "Llama 4 Maverick • Together hosted • 1M context",

        # DeepSeek
        "deepseek-chat": "DeepSeek Chat • General conversation",
        "deepseek-coder": "DeepSeek Coder • Code generation specialist",
        "deepseek-reasoner": "DeepSeek Reasoner • Advanced reasoning",

        # Groq
        "llama-3.3-70b-versatile": "Llama 3.3 70B • Groq hosted • Ultra-fast",
        "llama-3.1-8b-instant": "Llama 3.1 8B • Groq hosted • Low latency",
        "mixtral-8x7b-32768": "Mixtral 8x7B • Groq hosted • MoE architecture",

        # Mistral
        "mistral-large-latest": "Mistral Large • Latest version • Strong capabilities",
        "mistral-medium-latest": "Mistral Medium • Balanced performance",
        "mistral-small-latest": "Mistral Small • Fast & efficient",

        # Cohere
        "command-r-plus": "Command R+ • Cohere's best • Long context",
        "command-r": "Command R • Balanced performance",
        "command": "Command • Legacy Cohere model",

        # Zhipu AI (GLM)
        "glm-5": "GLM-5 • Zhipu AI latest • 744B parameters • Advanced coding",
        "glm-5.1": "GLM-5.1 • Zhipu AI enhanced • Feb 2026 release",
        "glm-4-plus": "GLM-4 Plus • Strong general performance",
        "glm-4": "GLM-4 • Base model • Capable generalist",

        # MiniMax
        "MiniMax-Text-01": "MiniMax Text-01 • Latest general model",
        "abab6.5s": "ABAB 6.5S • MiniMax chat model",
    }
    
    # Add each model to the menu
    for model in models:
        description = model_descriptions.get(model, f"{model} • Standard model")
        if "new" in description.lower():
            icon = "✨"  # Special icon for new models
        elif "latest" in description.lower() or "newest" in description.lower():
            icon = "🚀"
        else:
            icon = "🧠"
        menu.add_item(model, description, model, icon)
    
    selected_model = menu.show()
    return selected_model


def select_openai_model_with_categories(models: list) -> Optional[str]:
    """Select OpenAI model using categorized menu"""
    from ai_agent.utils.curses_menu import get_curses_menu
    
    menu = get_curses_menu(
        "🤖 OpenAI Model Selection",
        "Choose your preferred OpenAI model:"
    )
    
    # Separate models by category
    latest_models = []
    legacy_models = []
    
    for model in models:
        if model in ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.3-codex", "gpt-oss-20b", "gpt-oss-120b"]:
            latest_models.append(model)
        else:
            legacy_models.append(model)
    
    # Add latest models directly to menu (no category)
    for model in latest_models:
        description = get_model_description(model)
        if "new" in description.lower():
            icon = "✨"  # Special icon for new models
        elif "latest" in description.lower() or "newest" in description.lower():
            icon = "🚀"
        else:
            icon = "🧠"
        menu.add_item(model, description, model, icon)
    
    # Add Legacy Models category (all legacy models in one category)
    if legacy_models:
        menu.add_item(
            "📚 Legacy Models",
            f"Older models organized by type ({len(legacy_models)} models)",
            "category_legacy",
            "📚"
        )
    
    selected_category = menu.show()
    
    if selected_category == "category_legacy":
        return show_models_with_subcategories("Legacy Models", legacy_models, "📚")
    elif selected_category in latest_models:
        return selected_category
    else:
        return None


def get_model_description(model: str) -> str:
    """Get description for a specific model"""
    model_descriptions = {
        # GPT-5.4 Series (2026 Latest)
        "gpt-5.4": "GPT-5.4 • OpenAI flagship • 1M context • Best reasoning & coding",
        "gpt-5.4-mini": "GPT-5.4 Mini • Strong mini model • Coding & computer use",
        "gpt-5.4-nano": "GPT-5.4 Nano • Cheapest GPT-5.4 • High volume tasks",

        # GPT-4.1 Series
        "gpt-4.1": "GPT-4.1 • 1M context • Smarter & more efficient",
        "gpt-4.1-mini": "GPT-4.1 Mini • Fast & cost-effective",
        "gpt-4.1-nano": "GPT-4.1 Nano • Ultra-fast • Cheapest",

        # Reasoning Models
        "o3": "O3 • Advanced reasoning • STEM & complex tasks • 200K context",
        "o4-mini": "O4 Mini • Fast reasoning • Cost-effective • 200K context",
        "o3-mini": "O3 Mini • Efficient reasoning • 200K context",

        # Anthropic Claude 4.6 (Latest)
        "claude-opus-4-6-20260219": "Claude Opus 4.6 • Most capable • 1M context • Agent teams",
        "claude-sonnet-4-6-20260219": "Claude Sonnet 4.6 • Near-Opus performance • Balanced",
        "claude-opus-4-5-20251125": "Claude Opus 4.5 • Outperforms humans on coding exams",
        "claude-sonnet-4-5-20251125": "Claude Sonnet 4.5 • Efficient & capable",

        # Google Gemini 3.1 (Latest)
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro • 2M context • Advanced agentic coding",
        "gemini-3-flash-preview": "Gemini 3 Flash • Frontier performance • Cost-effective",
        "gemini-3.1-flash-lite-preview": "Gemini 3.1 Flash Lite • Ultra-efficient • New",
        "gemini-2.5-pro": "Gemini 2.5 Pro • 1M context • Advanced reasoning",
        "gemini-2.5-flash": "Gemini 2.5 Flash • Fast & efficient",

        # xAI Grok 4.1 (Latest)
        "grok-4.1": "Grok 4.1 • State-of-the-art • #1 on LMArena • Real-time",
        "grok-4.1-fast": "Grok 4.1 Fast • Quick responses • Dec 2025",
        "grok-4.1-thinking": "Grok 4.1 Thinking • Deep reasoning mode",

        # Meta Llama 4 (Latest)
        "llama-4-scout-17b-16e-instruct": "Llama 4 Scout • 10M context • 17B active • Text",
        "llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick • 1M context • 128 experts • Text",

        # Together AI Llama 4
        "meta-llama/Llama-4-Scout-17B-16E-Instruct": "Llama 4 Scout • Together hosted • 10M context",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct": "Llama 4 Maverick • Together hosted • 1M context",

        # DeepSeek
        "deepseek-chat": "DeepSeek Chat • General conversation",
        "deepseek-coder": "DeepSeek Coder • Code generation specialist",
        "deepseek-reasoner": "DeepSeek Reasoner • Advanced reasoning",

        # Groq
        "llama-3.3-70b-versatile": "Llama 3.3 70B • Groq hosted • Ultra-fast",
        "llama-3.1-8b-instant": "Llama 3.1 8B • Groq hosted • Low latency",
        "mixtral-8x7b-32768": "Mixtral 8x7B • Groq hosted • MoE architecture",

        # Mistral
        "mistral-large-latest": "Mistral Large • Latest version • Strong capabilities",
        "mistral-medium-latest": "Mistral Medium • Balanced performance",
        "mistral-small-latest": "Mistral Small • Fast & efficient",

        # Cohere
        "command-r-plus": "Command R+ • Cohere's best • Long context",
        "command-r": "Command R • Balanced performance",
        "command": "Command • Legacy Cohere model",

        # Zhipu AI (GLM)
        "glm-5": "GLM-5 • Zhipu AI latest • 744B parameters • Advanced coding",
        "glm-5.1": "GLM-5.1 • Zhipu AI enhanced • Feb 2026 release",
        "glm-4-plus": "GLM-4 Plus • Strong general performance",
        "glm-4": "GLM-4 • Base model • Capable generalist",

        # MiniMax
        "MiniMax-Text-01": "MiniMax Text-01 • Latest general model",
        "abab6.5s": "ABAB 6.5S • MiniMax chat model",

        # OpenRouter Custom Models
        "Other Models": "🔧 Enter custom model name • Any official OpenRouter model",
    }

    return model_descriptions.get(model, f"{model} • Standard model")


def show_models_in_category(category_name: str, models: list, category_icon: str) -> Optional[str]:
    """Show models within a specific category with sub-categorization"""
    from ai_agent.utils.curses_menu import get_curses_menu
    
    # For legacy categories, further subdivide by generation
    if category_name in ["O Series Models", "GPT Series Models"]:
        return show_models_with_subcategories(category_name, models, category_icon)
    
    menu = get_curses_menu(
        f"{category_icon} {category_name}",
        "Select your preferred model:"
    )
    
    # Model descriptions for OpenAI models
    model_descriptions = {
        # GPT-5.4 Series (2026 Latest)
        "gpt-5.4": "GPT-5.4 • OpenAI flagship • 1M context • Best reasoning & coding",
        "gpt-5.4-mini": "GPT-5.4 Mini • Strong mini model • Coding & computer use",
        "gpt-5.4-nano": "GPT-5.4 Nano • Cheapest GPT-5.4 • High volume tasks",

        # GPT-4.1 Series
        "gpt-4.1": "GPT-4.1 • 1M context • Smarter & more efficient",
        "gpt-4.1-mini": "GPT-4.1 Mini • Fast & cost-effective",
        "gpt-4.1-nano": "GPT-4.1 Nano • Ultra-fast • Cheapest",

        # Reasoning Models
        "o3": "O3 • Advanced reasoning • STEM & complex tasks • 200K context",
        "o4-mini": "O4 Mini • Fast reasoning • Cost-effective • 200K context",
        "o3-mini": "O3 Mini • Efficient reasoning • 200K context",
    }
    
    # Add models to menu
    for model in models:
        description = model_descriptions.get(model, f"{model} • Standard model")
        if "new" in description.lower():
            icon = "✨"  # Special icon for new models
        elif "latest" in description.lower() or "newest" in description.lower():
            icon = "🚀"
        else:
            icon = "🧠"
        menu.add_item(model, description, model, icon)
    
    selected_model = menu.show()
    return selected_model


def show_models_with_subcategories(category_name: str, models: list, category_icon: str) -> Optional[str]:
    """Show models with subcategories for Legacy Models"""
    from ai_agent.utils.curses_menu import get_curses_menu
    
    menu = get_curses_menu(
        f"{category_icon} {category_name}",
        "Choose model type:"
    )
    
    # Subdivide Legacy Models by type
    o_series_models = [m for m in models if m.startswith("o") and not m.startswith("omni")]
    gpt_series_models = [m for m in models if m.startswith("gpt") and not m.startswith("omni")]
    codex_models = [m for m in models if "codex" in m]
    other_models = [m for m in models if not (m.startswith("o") and not m.startswith("omni")) and not m.startswith("gpt") and "codex" not in m]
    
    if o_series_models:
        menu.add_item(
            "🧠 O Series Models",
            f"O1, O3, O4 reasoning models ({len(o_series_models)} models)",
            "subcategory_o_series",
            "🧠"
        )
    if gpt_series_models:
        menu.add_item(
            "💬 GPT Series Models",
            f"GPT-3, GPT-4, GPT-5 legacy models ({len(gpt_series_models)} models)",
            "subcategory_gpt_series",
            "💬"
        )
    if codex_models:
        menu.add_item(
            "💻 Codex Models",
            f"Code generation models ({len(codex_models)} models)",
            "subcategory_codex",
            "💻"
        )
    if other_models:
        menu.add_item(
            "🔧 Other Models",
            f"Specialized and utility models ({len(other_models)} models)",
            "subcategory_other",
            "🔧"
        )
    
    selected_subcategory = menu.show()
    
    if selected_subcategory == "subcategory_o_series":
        return show_o_series_subcategories(o_series_models)
    elif selected_subcategory == "subcategory_gpt_series":
        return show_gpt_series_subcategories(gpt_series_models)
    elif selected_subcategory == "subcategory_codex":
        return show_models_in_category("Codex Models", codex_models, "💻")
    elif selected_subcategory == "subcategory_other":
        return show_models_in_category("Other Models", other_models, "🔧")
    else:
        return None


def show_o_series_subcategories(models: list) -> Optional[str]:
    """Show O Series models subdivided by generation"""
    from ai_agent.utils.curses_menu import get_curses_menu
    
    menu = get_curses_menu(
        "🧠 O Series Models",
        "Choose O Series generation:"
    )
    
    o1_models = [m for m in models if m.startswith("o1")]
    o3_models = [m for m in models if m.startswith("o3")]
    o4_models = [m for m in models if m.startswith("o4")]
    
    if o1_models:
        menu.add_item(
            "🔹 O1 Series",
            f"First generation reasoning models ({len(o1_models)} models)",
            "subcategory_o1",
            "🔹"
        )
    if o3_models:
        menu.add_item(
            "🔹 O3 Series",
            f"Advanced reasoning models ({len(o3_models)} models)",
            "subcategory_o3",
            "🔹"
        )
    if o4_models:
        menu.add_item(
            "🔹 O4 Series",
            f"Next generation reasoning models ({len(o4_models)} models)",
            "subcategory_o4",
            "🔹"
        )
    
    selected_subcategory = menu.show()
    
    if selected_subcategory == "subcategory_o1":
        return show_models_in_category("O1 Series", o1_models, "🔹")
    elif selected_subcategory == "subcategory_o3":
        return show_models_in_category("O3 Series", o3_models, "🔹")
    elif selected_subcategory == "subcategory_o4":
        return show_models_in_category("O4 Series", o4_models, "🔹")
    else:
        return None


def show_gpt_series_subcategories(models: list) -> Optional[str]:
    """Show GPT Series models subdivided by generation"""
    from ai_agent.utils.curses_menu import get_curses_menu
    
    menu = get_curses_menu(
        "💬 GPT Series Models",
        "Choose GPT Series generation:"
    )
    
    gpt3_models = [m for m in models if "gpt-3.5" in m or (m.startswith("gpt-3") and "3.5" not in m)]
    gpt4_models = [m for m in models if "gpt-4" in m]
    gpt5_legacy_models = [m for m in models if "gpt-5" in m and m not in ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.3-codex"]]
    
    if gpt3_models:
        menu.add_item(
            "🔹 GPT-3 Series",
            f"Third generation models ({len(gpt3_models)} models)",
            "subcategory_gpt3",
            "🔹"
        )
    if gpt4_models:
        menu.add_item(
            "🔹 GPT-4 Series",
            f"Fourth generation models ({len(gpt4_models)} models)",
            "subcategory_gpt4",
            "🔹"
        )
    if gpt5_legacy_models:
        menu.add_item(
            "🔹 GPT-5 Legacy",
            f"Fifth generation legacy models ({len(gpt5_legacy_models)} models)",
            "subcategory_gpt5",
            "🔹"
        )
    
    selected_subcategory = menu.show()
    
    if selected_subcategory == "subcategory_gpt3":
        return show_models_in_category("GPT-3 Series", gpt3_models, "🔹")
    elif selected_subcategory == "subcategory_gpt4":
        return show_models_in_category("GPT-4 Series", gpt4_models, "🔹")
    elif selected_subcategory == "subcategory_gpt5":
        return show_models_in_category("GPT-5 Legacy", gpt5_legacy_models, "🔹")
    else:
        return None


def get_valid_api_key(prompt):
    """Get and validate API key from user input"""
    from ai_agent.utils.interactive_menu import Colors, warning_message
    
    while True:
        api_key = input(prompt).strip()
        if not api_key:
            return None
        
        if len(api_key) < 10:
            warning_message("API key seems too short. Please check and try again.")
            continue
        
        return api_key


def _restore_terminal_history(project_root: Path):
    """Restore terminal history from the latest terminal_history/*.json session file.

    Returns a list of formatted log lines suitable for injection into the
    resume instruction, or an empty list if nothing was found.
    """
    try:
        _th_dir = project_root / "peripherals" / "terminal_history"
        if not _th_dir.exists():
            return []
        # Find the most recently modified .json session file
        _sessions = sorted(
            [f for f in _th_dir.glob("*.json") if not f.name.endswith(".bak")],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not _sessions:
            return []
        _latest = _sessions[0]
        import json as _json
        with open(_latest, "r", encoding="utf-8") as _f:
            _data = _json.load(_f)
        _entries = _data.get("entries", [])
        if not _entries:
            return []
        _log_lines = []
        for _e in _entries[-200:]:
            _ts_raw = _e.get("timestamp", 0)
            try:
                _ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(_ts_raw)))
            except Exception:
                _ts = str(_ts_raw)
            _etype = _e.get("entry_type", "?")
            _content = _e.get("content", "")[:300]
            _rc = _e.get("return_code")
            _rc_str = f" (exit={_rc})" if _rc is not None else ""
            _log_lines.append(f"[{_ts}] [{_etype}]{_rc_str} {_content}")
        print(f"✅ Restored {len(_log_lines)} terminal history entries from {_latest.name}")
        return _log_lines
    except Exception as _exc:
        print(f"⚠️ Could not restore terminal history: {_exc}")
        return []


def _startup_cleanup():
    """Run startup cleanup: remove stale temp files and check disk space."""
    import shutil as _shutil
    from pathlib import Path as _Path

    _project_root = _Path(__file__).parent.resolve()
    # Use the already-defined module-level _PROJECT_ROOT if available
    try:
        _project_root = _PROJECT_ROOT
    except NameError:
        pass

    # Remove stale temp files in .context/
    _ctx_dir = _project_root / ".context"
    if _ctx_dir.exists():
        for _pattern in ["*.tmp", "*.bak", "*.swp"]:
            for _f in _ctx_dir.glob(_pattern):
                try:
                    _f.unlink()
                except Exception:
                    pass

    # Check disk space
    try:
        _usage = _shutil.disk_usage(str(_project_root))
        _free_gb = _usage.free / (1024 ** 3)
        if _free_gb < 1:
            print(f"⚠️  Low disk space: {_free_gb:.1f}GB free")
    except Exception:
        pass


def _reset_config_yaml():
    """Reset config.yaml to a clean default state (used by --setting)."""
    import yaml as _yaml

    config_path = Path(__file__).parent.resolve() / "config.yaml"
    clean = {
        "api": {
            "preferred_provider": "",
            "api_keys": {
                "google": "",
                "groq": "",
                "openai": "",
                "anthropic": "",
                "xai": "",
                "meta": "",
                "mistral": "",
                "microsoft": "",
                "cohere": "",
                "deepseek": "",
                "together": "",
                "minimax": "",
                "zhipuai": "",
                "openrouter": "",
            },
            "local_endpoint": "http://localhost:11434",
            "local_model": "llama3.2:3b",
            "models": {
                "ollama": "llama3.2:3b",
                "google": "gemini-3.1-pro-preview",
                "groq": "llama-3.3-70b-versatile",
                "openai": "gpt-4o",
                "anthropic": "claude-opus-4-6-20260219",
                "xai": "grok-4.1",
                "meta": "llama-4-scout-17b-16e-instruct",
                "mistral": "mistral-large-latest",
                "microsoft": "gpt-4o",
                "amazon": "anthropic.claude-opus-4-6-20260219-v1:0",
                "cohere": "command-r-plus",
                "deepseek": "deepseek-chat",
                "together": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
                "minimax": "MiniMax-Text-01",
                "zhipuai": "glm-5",
                "openrouter": "openrouter/owl-alpha",
            },
            "timeout": 120,
            "max_retries": 3,
        },
        "security": {
            "enable_command_blocking": False,
            "enable_confirmation_prompts": False,
            "enable_sudo_warning": False,
            "enable_shell_pipe_warning": False,
            "enable_sandbox": False,
        },
        "execution": {
            "safety_mode": True,
            "dry_run": False,
            "verify_commands": True,
            "command_timeout": 1800,
            "task_timeout": 7200,
            "max_iterations": 500,
            "auto_recovery": True,
            "show_thought_log": True,
        },
        "logging": {
            "level": "INFO",
            "file": "vexis.log",
            "json_format": False,
            "console": True,
        },
        "cache": {
            "enabled": True,
            "max_size": 1000,
            "ttl": 3600,
            "persist_to_disk": True,
        },
        "cost": {
            "daily_budget": None,
            "monthly_budget": None,
            "per_request_budget": None,
            "warning_threshold": 0.8,
            "critical_threshold": 0.95,
        },
        "performance": {
            "max_concurrent_tasks": 1,
            "memory_limit_mb": 1024,
        },
        "user": {
            "name": "",
            "preferred_style": "detailed",
            "auto_confirm": False,
            "show_progress": True,
        },
        "telegram": {
            "enabled": False,
            "bot_token": "",
            "bot_username": "",
            "api_id": 0,
            "api_hash": "",
            "session_name": "vexis_telegram",
            "authorized_users": [],
            "allowed_user_ids": [],
            "enable_input_listener": True,
            "max_history_length": 50,
            "bot_name": "VEXIS Agent",
        },
        "discord": {
            "enabled": False,
            "bot_token": "",
            "authorized_users": [],
            "allowed_user_ids": [],
            "max_history_length": 50,
            "bot_name": "VEXIS Agent",
        },
        "custom_system_prompt": "",
    }
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            _yaml.dump(clean, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print("config.yaml has been reset to defaults.")
    except Exception as e:
        print(f"Could not reset config.yaml: {e}")


def _run_health_check():
    """Run a self-diagnostic and print results."""
    import platform as _plat
    import shutil as _shutil

    print("=" * 60)
    print("🩺 VEXIS-CLI Self-Diagnostic")
    print("=" * 60)

    # Python version
    print(f"Python:    {sys.version}")
    print(f"Platform:  {_plat.system()} {_plat.release()} ({_plat.machine()})")
    print(f"PID:       {os.getpid()}")

    # Disk
    try:
        _usage = _shutil.disk_usage(str(Path(__file__).parent))
        _free = _usage.free / (1024 ** 3)
        _total = _usage.total / (1024 ** 3)
        _pct = (_usage.used / _usage.total) * 100
        _status = "✅" if _free > 5 else ("⚠️ " if _free > 1 else "❌")
        print(f"Disk:      {_status} {_free:.1f}GB free / {_total:.1f}GB total ({_pct:.0f}% used)")
    except Exception as e:
        print(f"Disk:      ❌ Error: {e}")

    # Memory
    try:
        import psutil as _ps
        _mem = _ps.virtual_memory()
        _status = "✅" if _mem.percent < 85 else ("⚠️ " if _mem.percent < 95 else "❌")
        print(f"Memory:    {_status} {_mem.available / (1024**3):.1f}GB available ({_mem.percent:.0f}% used)")
    except Exception:
        print("Memory:    ⚠️ psutil not available")

    # Virtual environment
    _in_venv = (
        hasattr(sys, 'real_prefix') or
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) or
        os.getenv('VIRTUAL_ENV') is not None
    )
    print(f"Venv:      {'✅' if _in_venv else '❌'} {'Yes' if _in_venv else 'No'}")

    # .context/ state
    _ctx_dir = Path(__file__).parent / ".context"
    _sleep_state = _ctx_dir / "sleep_state.json"
    _exit_state = _ctx_dir / "exit_state.json"
    if _sleep_state.exists():
        print(f"Sleep:     ✅ sleep_state.json exists")
    else:
        print(f"Sleep:     —  (no sleep_state.json)")
    if _exit_state.exists():
        print(f"Exit:      ✅ exit_state.json exists")
    else:
        print(f"Exit:      —  (no exit_state.json)")

    # Heartbeat
    _hb = _ctx_dir / "watchdog_heartbeat.json"
    if _hb.exists():
        try:
            import json as _json
            _data = _json.loads(_hb.read_text())
            _age = time.time() - _data.get("timestamp", 0)
            _status = "✅" if _age < 600 else "⚠️ "
            print(f"Heartbeat: {_status} last beat {_age:.0f}s ago (PID {_data.get('pid', '?')})")
        except Exception:
            print("Heartbeat: ⚠️  corrupt file")
    else:
        print("Heartbeat: —  (no heartbeat file)")

    # Restart counter
    _rc = _ctx_dir / "watchdog_restarts.json"
    if _rc.exists():
        try:
            import json as _json
            _data = _json.loads(_rc.read_text())
            _count = _data.get("count", 0)
            _status = "✅" if _count < 5 else "⚠️ "
            print(f"Restarts:  {_status} {_count} in current window")
        except Exception:
            print("Restarts:  ⚠️  corrupt counter")
    else:
        print("Restarts:  —  (no restart counter)")

    print("=" * 60)


def main():
    """Main entry point"""
    # Check for help flag first
    if "--help" in sys.argv or "-h" in sys.argv:
        show_help()
        sys.exit(0)

    # Run startup cleanup (stale files, disk check) — always runs
    _startup_cleanup()

    # Handle --health-check self-diagnostic
    if "--health-check" in sys.argv:
        _run_health_check()
        sys.exit(0)

    # Handle --supervisor flag - start eternal supervisor
    if "--supervisor" in sys.argv:
        sys.argv.remove("--supervisor")
        print("🛡️ Starting Eternal Supervisor - Agent will never stop")
        try:
            from ai_agent.utils.eternal_supervisor import start_eternal_agent
            start_eternal_agent(agent_args=sys.argv[1:])
        except ImportError as e:
            print(f"⚠️ Supervisor not available: {e}")
            print("Falling back to normal mode...")
        except Exception as e:
            print(f"❌ Supervisor failed: {e}")
            sys.exit(1)
        return
    
    # Handle --sleep flag - compress context and restart
    _sleep_requested = False
    if "--sleep" in sys.argv:
        sys.argv.remove("--sleep")
        _sleep_requested = True
        print("🛏 Sleep requested – will trigger sleep workflow on startup")

    # Handle --self-heal flag - enable enhanced self-healing mode
    _self_heal_mode = False
    if "--self-heal" in sys.argv:
        sys.argv.remove("--self-heal")
        _self_heal_mode = True
        print("🩹 Enhanced self-healing mode enabled")

    # Check for environment check/fix flags (run before venv setup)
    if "--check" in sys.argv or "-c" in sys.argv:
        print("🔍 Running environment check...")
        run_environment_check(fix_mode=False)
        sys.exit(0)
    
    if "--fix" in sys.argv:
        print("🔧 Running environment check with auto-fix...")
        run_environment_check(fix_mode=True)
        sys.exit(0)
    
    # Check if we've already restarted in venv
    if VENV_RESTART_FLAG in sys.argv:
        # Remove the restart flag for clean processing
        sys.argv.remove(VENV_RESTART_FLAG)
        print("✓ Running in virtual environment")
    else:
        # Not in venv or not restarted yet
        if not is_in_virtual_environment():
            print("Not in virtual environment")
            
            # Check if venv exists and is functional
            venv_python = get_venv_python_path()
            if venv_python:
                try:
                    result = subprocess.run([venv_python, "--version"], 
                                          capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        print("Virtual environment found, restarting...")
                        restart_in_venv()
                        return  # This should never execute if restart works
                except Exception:
                    pass
            
            # No working venv found, create one
            if bootstrap_environment():
                print("Restarting in new virtual environment...")
                restart_in_venv()
                return  # This should never execute if restart works
            else:
                print("Failed to bootstrap environment")
                sys.exit(1)
        else:
            print("✓ Already in virtual environment")
    
    # Determine the project root (where run.py lives) so that .context/
    # is always found regardless of the current working directory.
    _project_root = Path(__file__).parent.resolve()

    # Point the context manager at the project-root .context/ directory
    # Do this early, before any context_manager functions are called.
    try:
        import importlib, sys as _sys
        _src_str = str(_project_root / "src")
        if _src_str not in _sys.path:
            _sys.path.insert(0, _src_str)
        _cm = importlib.import_module("ai_agent.core_processing.context_manager")
        _cm.set_context_dir(_project_root / ".context")
    except Exception:
        pass

    # Check for sleep-restart state (after venv setup, before src import)
    _sleep_state = None
    _exit_state = None
    try:
        from ai_agent.core_processing.autonomous_loop_engine import (
            AutonomousLoopEngine,
        )
        _sleep_state = AutonomousLoopEngine.check_and_handle_sleep_restart(
            project_root=_project_root
        )
        if _sleep_state:
            print("✅ Restored context from sleep: " + str(_sleep_state.get("goal", "")))
    except Exception:
        pass  # Non-critical; continue if import/restore fails

    # Check for exit-state restore (Ctrl+C / SIGTERM / exit command).
    # Always check this — even if sleep_state was found — because
    # exit_state may be newer (e.g. the user Ctrl+C'd during a sleep
    # restart).  We use the newest of the two.
    try:
        import json as _json
        _exit_state_file = _project_root / ".context" / "exit_state.json"
        if _exit_state_file.exists():
            with open(_exit_state_file, "r", encoding="utf-8") as _f:
                _exit_state = _json.load(_f)
            print("✅ Restored context from previous exit: " + str(_exit_state.get("goal", "")))
    except Exception:
        _exit_state = None

    # Pick the newest state if both exist
    if _sleep_state and _exit_state:
        _sleep_ts = _sleep_state.get("timestamp", 0) or 0
        _exit_ts = _exit_state.get("timestamp", 0) or 0
        if _exit_ts > _sleep_ts:
            _sleep_state = None
        else:
            _exit_state = None

    # NOTE: Do NOT delete exit_state.json here — it must survive until
    # the agent has actually consumed the context.  If the process exits
    # between this point and agent startup (e.g. user cancels provider
    # selection), the context would otherwise be lost forever.
    # The agent (five_phase_app.py) clears the file after consuming it.

    # Restore terminal history from terminal_history/*.json so that the
    # agent can see what happened in the previous session even if the
    # exit_state.json was created by the signal handler (which has no
    # access to the full execution log).
    _terminal_history_log = _restore_terminal_history(_project_root)

    # Always display full context summary from .context/ if available
    _context_displayed = False
    if _sleep_state or _exit_state:
        try:
            from ai_agent.core_processing.context_manager import display_context_in_terminal
            display_context_in_terminal()
            _context_displayed = True
        except Exception:
            pass
    if not _context_displayed:
        try:
            from ai_agent.core_processing.context_manager import context_files_exist, get_context_summary
            if context_files_exist():
                print(get_context_summary())
        except Exception:
            pass

    # Display the plain-text context_log.txt if it exists
    _context_log_file = _project_root / ".context" / "context_log.txt"
    if _context_log_file.exists():
        try:
            print("\n" + "=" * 60)
            print("  📋 CONTEXT LOG — .context/context_log.txt")
            print("=" * 60)
            print(_context_log_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Display restored terminal history in the terminal
    if _terminal_history_log:
        try:
            print("\n" + "=" * 60)
            print("  📋 TERMINAL HISTORY — restored from terminal_history/")
            print("=" * 60)
            for _line in _terminal_history_log[-80:]:
                print(f"  {_line}")
        except Exception:
            pass

    # Build the resume instruction from compressed sleep/exit context.
    _resume_instruction = None
    _restore_state = _sleep_state or _exit_state
    if _restore_state or _terminal_history_log:
        _compressed = _restore_state.get("compressed_context", "") if _restore_state else ""
        _goal = _restore_state.get("goal", "") if _restore_state else ""
        _iterations = _restore_state.get("iteration_count", 0) if _restore_state else 0
        _aux = _restore_state.get("auxiliary", {}) if _restore_state else {}
        _errors = _aux.get("errors", "(none)")
        _git_diff = _aux.get("git_diff", "(none)")

        # If compressed_context is empty, fall back to context_log.txt
        if not _compressed:
            _clog = _project_root / ".context" / "context_log.txt"
            if _clog.exists():
                try:
                    _compressed = _clog.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

        if _restore_state:
            _source = "sleep" if _sleep_state else "exit"
            _resume_instruction = (
                f"I have just been restarted (previously exited via {_source}). "
                "I must resume working immediately.\n\n"
                f"## Compressed Context from Before {_source.capitalize()}\n"
                f"{_compressed}\n\n"
                "## Summary\n"
                f"- Goal before {_source}: {_goal}\n"
                f"- Iterations completed: {_iterations}\n"
                f"- Errors encountered: {_errors}\n"
                f"- Git changes: {_git_diff}\n\n"
            )
        else:
            _resume_instruction = (
                "I have just been restarted after a previous session. "
                "I must resume working immediately.\n\n"
            )

        # Append terminal history to the resume instruction
        if _terminal_history_log:
            _history_block = "\n".join(_terminal_history_log[-100:])
            _resume_instruction += (
                "## TERMINAL EXECUTION LOG (from previous session)\n"
                "The following is the actual terminal command/output log from the "
                "previous session. Use this to understand what was being done.\n\n"
                f"{_history_block}\n\n"
            )

        _resume_instruction += (
            "Resume work immediately from where I left off. "
            "Do not ask questions — act.\n\n"
            "## TELEGRAM REPORTING (MANDATORY)\n"
            "You are in Telegram mode. Send progress updates via telegram() "
            "every 5-10 iterations. NEVER go more than 10 iterations without "
            "telegram(). The user is waiting for your reports."
        )

        # Also restore provider/model from the state if env not already set
        if _restore_state:
            if not os.getenv(RESTART_PROVIDER_ENV) and _restore_state.get("restart_provider"):
                os.environ[RESTART_PROVIDER_ENV] = _restore_state["restart_provider"]
            if not os.getenv(RESTART_MODEL_ENV) and _restore_state.get("restart_model"):
                os.environ[RESTART_MODEL_ENV] = _restore_state["restart_model"]

    # At this point, we're running in a virtual environment
    # Add src to Python path (if not already added earlier)
    current_dir = Path(__file__).parent
    src_dir = current_dir / "src"
    _src_str = str(src_dir)
    if _src_str not in sys.path:
        sys.path.insert(0, _src_str)

    # Point the context manager at the project-root .context/ directory
    # so that context files are always found regardless of CWD.
    # (May have been set earlier via importlib; this ensures it's correct.)
    try:
        from ai_agent.core_processing.context_manager import set_context_dir
        set_context_dir(_project_root / ".context")
    except Exception:
        pass

    # Validate arguments - allow running without instruction for interactive/Telegram modes
    _restore_restart_settings_from_env()
    if USER_RESTART_FLAG in sys.argv:
        sys.argv.remove(USER_RESTART_FLAG)
        print("✓ Restarted with previous provider, model, and API settings")
    
    # Filter out flags to get the actual instruction
    instruction_args = []
    skip_next_arg = False
    flags_with_values = {"--max-iterations", "--instruction-file"}
    for arg in sys.argv[1:]:
        if skip_next_arg:
            skip_next_arg = False
            continue
        if arg in flags_with_values:
            skip_next_arg = True
            continue
        if arg.startswith("--"):
            continue
        instruction_args.append(arg)
    instruction = " ".join(instruction_args) if instruction_args else None

    # Save original instruction before resume overwrite (needed for /restart detection)
    _original_instruction = instruction

    # If resuming from sleep, the compressed context IS the instruction.
    # This takes priority so the agent immediately continues its work.
    if _resume_instruction:
        instruction = _resume_instruction

    # Parse --instruction-file if provided
    if "--instruction-file" in sys.argv:
        try:
            idx = sys.argv.index("--instruction-file")
            if idx + 1 < len(sys.argv):
                instruction_file = sys.argv[idx + 1]
                if Path(instruction_file).exists():
                    instruction = Path(instruction_file).read_text(encoding="utf-8").strip()
                else:
                    print(f"⚠️ Instruction file not found: {instruction_file}")
        except (ValueError, IndexError):
            pass

    # Handle --sleep: instruct the agent to sleep immediately
    if _sleep_requested and not _resume_instruction:
        instruction = (
            "Execute sleep immediately. "
            "Your very first command must be: sleep\n"
            "Do nothing else - just execute sleep to compress context and restart."
        )
        print("Sleep instruction injected - agent will sleep on first iteration")
    
    # Allow SDK management commands without instruction
    sdk_only_commands = ["--install-sdks", "--sdk-status"]
    if any(flag in sys.argv for flag in sdk_only_commands):
        # SDK management commands don't need instruction
        pass
    
    # Check for debug mode
    debug_mode = "--debug" in sys.argv
    
    # Check for SDK installation request
    if "--install-sdks" in sys.argv:
        print("🔧 Installing missing AI provider SDKs...")
        try:
            import subprocess
            result = subprocess.run([sys.executable, str(current_dir / "peripherals" / "manage_sdks.py"), "install"], 
                                  capture_output=False, text=True, cwd=current_dir)
            if result.returncode == 0:
                print("✅ SDK installation completed")
            else:
                print("⚠️ Some SDK installations may have failed")
        except Exception as e:
            print(f"❌ Failed to run SDK installation: {e}")
        print()
    
    # Check for SDK status request
    if "--sdk-status" in sys.argv:
        print("🔍 Checking AI provider SDK status...")
        try:
            import subprocess
            subprocess.run([sys.executable, str(current_dir / "peripherals" / "manage_sdks.py"), "status"], 
                         capture_output=False, text=True, cwd=current_dir)
        except Exception as e:
            print(f"❌ Failed to check SDK status: {e}")
        sys.exit(0)
    
    # Mode selection - always Telegram mode
    selected_mode = "telegram"
    
    # Check for --setting flag to force reconfiguration
    force_reconfigure = "--setting" in sys.argv

    # When --setting is used, reset config.yaml to a clean state first
    if force_reconfigure:
        _reset_config_yaml()
    
    # Model selection - only prompt if not using --no-prompt flag, or if --setting is used
    selected_provider = os.getenv(RESTART_PROVIDER_ENV)
    selected_model = os.getenv(RESTART_MODEL_ENV)
    if selected_provider:
        print(f"\nUsing restart provider: {selected_provider}")
        if selected_model:
            print(f"Using restart model: {selected_model}")
    
    if (selected_provider is None or force_reconfigure) and "--no-prompt" not in sys.argv:
        if force_reconfigure and selected_provider:
            print(f"\n⚙️  Forcing reconfiguration (current provider: {selected_provider})")
        result = select_model_provider()
        if isinstance(result, tuple) and len(result) == 2:
            selected_provider, selected_model = result
        else:
            selected_provider = result
        print(f"\nUsing provider: {selected_provider}")
        if selected_model:
            print(f"Using model: {selected_model}")
    elif selected_provider is None:
        # Try to get provider from config.yaml first
        try:
            from ai_agent.utils.config import ConfigManager
            config_path = current_dir / "config.yaml"
            config_manager = ConfigManager(str(config_path)) if config_path.exists() else None
            if config_manager:
                config = config_manager.load_config()
                if hasattr(config, 'api') and hasattr(config.api, 'preferred_provider'):
                    selected_provider = config.api.preferred_provider
                    if selected_provider:
                        # Get model from config
                        if selected_provider == "ollama" and hasattr(config.api, 'local_model'):
                            selected_model = config.api.local_model
                        elif hasattr(config.api, 'local_model'):
                            # Use local_model as default for any provider
                            selected_model = config.api.local_model
                        else:
                            # Default model for OpenRouter if not specified
                            if selected_provider == "openrouter":
                                selected_model = "openai/gpt-4o"
                            else:
                                selected_model = None
                        print(f"\nUsing provider from config.yaml: {selected_provider}")
                        if selected_model:
                            print(f"Using model from config.yaml: {selected_model}")
        except Exception as e:
            print(f"⚠️ Could not load config for provider selection: {e}")
        
        # Fallback to saved preferences if config didn't provide provider or model
        if not selected_provider or not selected_model:
            from ai_agent.utils.settings_manager import get_settings_manager
            settings_manager = get_settings_manager()
            if not selected_provider:
                selected_provider = settings_manager.get_preferred_provider()
            if not selected_model and selected_provider:
                selected_model = settings_manager.get_model(selected_provider)
            if selected_provider:
                print(f"\nUsing saved provider preference: {selected_provider}")
            if selected_model:
                print(f"Using saved model: {selected_model}")

        # If still no provider, we cannot proceed
        if not selected_provider:
            # Try to auto-detect Ollama for --no-prompt mode
            if "--no-prompt" in sys.argv:
                from ai_agent.utils.environment_detector import EnvironmentDetector
                detector = EnvironmentDetector()
                if detector._detect_ollama_available():
                    selected_provider = "ollama"
                    from ai_agent.utils.settings_manager import get_settings_manager
                    settings_manager = get_settings_manager()
                    selected_model = settings_manager.get_ollama_model()
                    print(f"\n🦊 Auto-detected Ollama - using local models (provider: {selected_provider}, model: {selected_model})")
                else:
                    print("\n❌ No AI provider configured and Ollama not available.")
                    print("   Please run without --no-prompt to configure a provider,")
                    print("   or install Ollama for local models: https://ollama.com/")
                    sys.exit(1)
            else:
                print("\n❌ No AI provider configured. Please run without --no-prompt to configure a provider,")
                print("   or set a provider in config.yaml (api.preferred_provider).")
                sys.exit(1)

    # Handle instruction
    if _original_instruction and _original_instruction.strip() == "/restart":
        print("🔄 Restarting with current settings...")
        restart_with_current_settings(selected_mode, selected_provider, selected_model, debug_mode, max_iterations=None)
    elif instruction:
        print(f"\nAI Agent executing: {instruction}")
    
    # Telegram mode: instruction may come from sleep/exit resume state.
    # Preserve it so the agent gets resume context on startup.
    if instruction:
        print(f"\nAI Agent starting in Telegram mode with resume context...")
    else:
        print(f"\nAI Agent starting in Telegram mode...")
    print("Waiting for messages from Telegram bot...")
    
    max_iterations = 0  # 0 = unlimited (forced sleep at 1000 iterations)
    
    # Parse max-iterations if provided
    if "--max-iterations" in sys.argv:
        try:
            idx = sys.argv.index("--max-iterations")
            if idx + 1 < len(sys.argv):
                max_iterations = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass
    
    try:
        from ai_agent.user_interface.five_phase_app import AutonomousAIAgent
        from ai_agent.external_integration.telegram_bot import create_telegram_bot
        from ai_agent.external_integration.discord_bot import create_discord_bot
        
        # Create Telegram bot (optional — agent always runs in Telegram mode)
        telegram_bot = None
        config_path = current_dir / "config.yaml"
        try:
            telegram_bot = create_telegram_bot(str(config_path) if config_path.exists() else None)
        except Exception:
            telegram_bot = None
        if telegram_bot:
            print("✓ Telegram bot initialized")

        # Create Discord bot (optional)
        discord_bot = None
        try:
            discord_bot = create_discord_bot(str(config_path) if config_path.exists() else None)
        except Exception:
            discord_bot = None
        if discord_bot:
            print("✓ Discord bot initialized")

        # Determine which bot to use: prefer Discord if both are available
        active_bot = discord_bot or telegram_bot
        if not active_bot:
            print("⚠️ No messaging bot available — running in autonomous mode without bot")
        
        # Create agent with selected provider and model
        config_path = current_dir / "config.yaml"
        agent = AutonomousAIAgent(
            provider=selected_provider,
            model=selected_model,
            config_path=str(config_path) if config_path.exists() else None,
            telegram_bot=active_bot,
            discord_bot=discord_bot,
        )
        
        # Read timeouts from config.yaml so they are determined solely by the config file
        command_timeout = 1800
        task_timeout = 7200
        try:
            from ai_agent.utils.config import ConfigManager
            if config_path.exists():
                cfg_mgr = ConfigManager(str(config_path))
                cfg = cfg_mgr.load_config()
                if hasattr(cfg, 'execution'):
                    command_timeout = getattr(cfg.execution, 'command_timeout', 1800)
                    task_timeout = getattr(cfg.execution, 'task_timeout', 7200)
        except Exception:
            pass
        
        # Run the instruction with autonomous loop options
        options = {
            "debug": debug_mode,
            "command_timeout": command_timeout,
            "task_timeout": task_timeout,
            "self_heal": _self_heal_mode,
        }
        
        # Always set Telegram mode environment variable
        os.environ['VEXIS_TELEGRAM_MODE'] = 'true'

        # Start the messaging bot(s) and handle messages
        if active_bot:
            bot_label = "Discord" if discord_bot else "Telegram"
            print(f"\n📱 Starting {bot_label} bot mode...")
            print("Send commands to your bot to control the AI agent.")
            print("Press Ctrl+C to stop the bot.")

            # message_callback is not needed: handle_message now spawns
            # _handle_message_task directly, which calls queue_message
            # via the engine's telegram() command.  Keep it unset.
            active_bot.set_message_callback(None)

            def process_restart(user_id: int):
                """Restart from bot after acknowledging the command."""
                restart_with_current_settings(selected_mode, selected_provider, selected_model, debug_mode, max_iterations)

            active_bot.set_restart_callback(process_restart)

            # Create a shared conversation history so bot messages
            # are visible to the autonomous loop's thinking phase.
            from ai_agent.external_integration.telegram_bot import ConversationHistory
            shared_history = ConversationHistory(user_id=0, max_length=50)
            active_bot.set_shared_conversation_history(shared_history)

            # Wire up the message injection: when the bot receives a
            # message, also push it into the engine's execution log.
            active_bot.set_user_message_callback(agent.engine.add_user_message)

            # Start the agent in autonomous boot on a background thread.
            # The agent runs its own loop; bot messages feed into
            # the log passively and the agent picks them up on its own.
            import threading as _th

            def _run_agent():
                try:
                    agent.run_autonomous_boot(
                        options,
                        conversation_history=shared_history,
                        telegram_bot=active_bot,
                        initial_instruction=instruction,
                    )
                except KeyboardInterrupt:
                    pass

            agent_thread = _th.Thread(target=_run_agent, daemon=True)
            agent_thread.start()

            # If both Discord and Telegram are available, run Telegram in a
            # background thread too so users on both platforms can interact.
            telegram_thread = None
            if discord_bot and telegram_bot:
                def _run_telegram():
                    try:
                        telegram_bot.start_bot()
                    except Exception:
                        pass
                telegram_thread = _th.Thread(target=_run_telegram, daemon=True)
                telegram_thread.start()

            # Start the active bot (blocking)
            try:
                active_bot.start_bot()
            except KeyboardInterrupt:
                print(f"\n\nStopping {bot_label} bot...")
                try:
                    _ctx = getattr(agent.engine, "_current_context", None)
                    if _ctx is not None:
                        _pr = Path(__file__).parent.resolve()
                        agent.engine._handle_exit(_ctx, fast=True, project_root=_pr)
                except Exception:
                    pass
                active_bot.stop_bot()
                if telegram_thread:
                    telegram_bot.stop_bot()
                print("Bot stopped.")
                sys.exit(0)
        else:
            # No messaging bot — run the agent directly in autonomous mode
            print("\n🤖 Running in autonomous mode (no messaging bot)...")
            print("Press Ctrl+C to stop.")
            try:
                agent.run_autonomous_boot(
                    options,
                    telegram_bot=None,
                    initial_instruction=instruction,
                )
            except KeyboardInterrupt:
                print("\n\nStopping agent...")
                try:
                    _ctx = getattr(agent.engine, "_current_context", None)
                    if _ctx is not None:
                        _pr = Path(__file__).parent.resolve()
                        agent.engine._handle_exit(_ctx, fast=True, project_root=_pr)
                except Exception:
                    pass
                print("Agent stopped.")


    except ImportError as e:
        print(f"Import error: {e}")
        print("This suggests a dependency issue. The virtual environment may not be set up correctly.")
        print("Try deleting the 'venv' directory and running again.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        if debug_mode:
            import traceback
            traceback.print_exc()
        try:
            if 'agent' in locals():
                _ctx = getattr(agent.engine, "_current_context", None)
                if _ctx is not None:
                    _pr = Path(__file__).parent.resolve()
                    agent.engine._handle_exit(_ctx, fast=True, project_root=_pr)
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
