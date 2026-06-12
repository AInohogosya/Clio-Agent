"""
Automatic SDK Installer for VEXIS-CLI

Detects missing SDK dependencies and offers to install them automatically.
Similar to the virtual environment setup, this handles SDK installation gracefully.
"""

import subprocess
import sys
import importlib
from typing import Dict, List, Optional, Tuple
from .logger import get_logger

logger = get_logger(__name__)

# Mapping of providers to their required SDK packages
PROVIDER_SDKS = {
    "google": {
        "package": "google-genai",
        "import_name": "google.genai",
        "install_command": "pip install google-genai",
        "description": "Google Gemini AI (GenAI SDK)"
    },
    "openai": {
        "package": "openai",
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "OpenAI GPT models"
    },
    "anthropic": {
        "package": "anthropic",
        "import_name": "anthropic",
        "install_command": "pip install anthropic",
        "description": "Anthropic Claude models"
    },
    "xai": {
        "package": "openai",  # xAI uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "xAI Grok models (uses OpenAI SDK)"
    },
    "meta": {
        "package": "openai",  # Meta uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "Meta Llama models (uses OpenAI SDK)"
    },
    "mistral": {
        "package": "mistralai",
        "import_name": "mistralai",
        "install_command": "pip install mistralai",
        "description": "Mistral AI models"
    },
    "microsoft": {
        "package": "openai",  # Azure OpenAI uses OpenAI SDK
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "Microsoft Azure OpenAI (uses OpenAI SDK)"
    },
    "amazon": {
        "package": "boto3",
        "import_name": "boto3",
        "install_command": "pip install boto3",
        "description": "Amazon Bedrock (AWS SDK)"
    },
    "cohere": {
        "package": "cohere",
        "import_name": "cohere",
        "install_command": "pip install cohere",
        "description": "Cohere AI models"
    },
    "deepseek": {
        "package": "openai",  # DeepSeek uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "DeepSeek models (uses OpenAI SDK)"
    },
    "groq": {
        "package": "openai",  # Groq uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "Groq fast models (uses OpenAI SDK)"
    },
    "together": {
        "package": "openai",  # Together AI uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "Together AI models (uses OpenAI SDK)"
    },
    "zhipuai": {
        "package": "openai",  # ZhipuAI uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "Z.ai/ZhipuAI GLM models (uses OpenAI SDK)"
    },
    "minimax": {
        "package": "openai",  # MiniMax uses OpenAI-compatible API
        "import_name": "openai",
        "install_command": "pip install openai",
        "description": "MiniMax M2-series models (uses OpenAI SDK)"
    }
}

class SDKInstaller:
    """Handles automatic installation of missing SDK dependencies"""
    
    def __init__(self, auto_install: bool = False):
        self.auto_install = auto_install
        self.failed_installs = set()
    
    def check_sdk_availability(self, provider: str) -> bool:
        """Check if a provider's SDK is available"""
        if provider not in PROVIDER_SDKS:
            return False
        
        sdk_info = PROVIDER_SDKS[provider]
        try:
            importlib.import_module(sdk_info["import_name"])
            return True
        except ImportError:
            return False
    
    def get_missing_sdks(self, providers: List[str]) -> Dict[str, Dict]:
        """Get information about missing SDKs for given providers"""
        missing = {}
        for provider in providers:
            if not self.check_sdk_availability(provider):
                missing[provider] = PROVIDER_SDKS[provider]
        return missing
    
    def install_sdk(self, provider: str, interactive: bool = True) -> bool:
        """Install SDK for a specific provider with enhanced error handling"""
        if provider not in PROVIDER_SDKS:
            logger.error(f"Unknown provider: {provider}")
            return False
        
        if provider in self.failed_installs:
            logger.warning(f"Already attempted to install SDK for {provider}")
            return False
        
        sdk_info = PROVIDER_SDKS[provider]
        
        if interactive:
            print(f"\n📦 Installing SDK for {sdk_info['description']}")
            print(f"   Command: {sdk_info['install_command']}")
            
            try:
                choice = input("Proceed with installation? (y/n): ").lower().strip()
                if choice not in ['y', 'yes']:
                    print("Installation cancelled.")
                    return False
            except (KeyboardInterrupt, EOFError):
                print("\nInstallation cancelled.")
                return False
        
        print(f"🔄 Installing {sdk_info['package']}...")
        
        # Enhanced installation with multiple strategies
        install_strategies = [
            sdk_info["install_command"].split(),  # Original command
            ["pip", "install", "--no-cache-dir", sdk_info["package"]],  # No cache
            ["pip", "install", "--timeout", "60", sdk_info["package"]],  # Shorter timeout
        ]
        
        for strategy_idx, install_cmd in enumerate(install_strategies):
            try:
                if strategy_idx > 0:
                    print(f"🔄 Trying alternative installation strategy {strategy_idx + 1}...")
                
                result = subprocess.run(
                    install_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout
                )
                
                if result.returncode == 0:
                    print(f"✅ Successfully installed {sdk_info['package']}")
                    
                    # Verify installation
                    if self.check_sdk_availability(provider):
                        print(f"✅ SDK for {provider} is now available")
                        return True
                    else:
                        print(f"⚠️ Installation completed but SDK still not available")
                        return False
                else:
                    error_msg = result.stderr.strip()
                    
                    # Check for common issues
                    if "Permission denied" in error_msg:
                        print(f"❌ Permission denied installing {sdk_info['package']}")
                        print("   Try running in a virtual environment or check permissions")
                        break  # Don't retry permission errors
                    elif "Could not find a version" in error_msg or "404" in error_msg:
                        print(f"❌ Package {sdk_info['package']} not found or incompatible")
                        break  # Don't retry package not found
                    elif "Network is unreachable" in error_msg or "Connection failed" in error_msg or "SSL" in error_msg:
                        if strategy_idx < len(install_strategies) - 1:
                            print(f"⚠️ Network issue, trying next strategy...")
                            continue
                        else:
                            print(f"❌ Network error installing {sdk_info['package']}")
                            print("   Check your internet connection")
                    elif strategy_idx < len(install_strategies) - 1:
                        print(f"⚠️ Installation failed, trying next strategy...")
                        continue
                    else:
                        print(f"❌ Failed to install {sdk_info['package']}")
                        print(f"   Error: {error_msg[:200]}")
                        
            except subprocess.TimeoutExpired:
                if strategy_idx < len(install_strategies) - 1:
                    print(f"⚠️ Installation timed out, trying next strategy...")
                    continue
                else:
                    print(f"❌ Installation timed out for {sdk_info['package']}")
            except Exception as e:
                if strategy_idx < len(install_strategies) - 1:
                    print(f"⚠️ Installation error, trying next strategy...")
                    continue
                else:
                    print(f"❌ Installation failed: {e}")
        
        # All strategies failed
        self.failed_installs.add(provider)
        return False
    
    def install_missing_sdks(self, providers: List[str], interactive: bool = True) -> Dict[str, bool]:
        """Install missing SDKs for multiple providers"""
        missing = self.get_missing_sdks(providers)
        
        if not missing:
            print("✅ All required SDKs are already installed")
            return {}
        
        print(f"\n📦 Missing SDKs detected for {len(missing)} provider(s):")
        for provider, sdk_info in missing.items():
            print(f"   • {provider}: {sdk_info['description']}")
        
        if not interactive and not self.auto_install:
            print("Use --install-sdks flag to install automatically")
            return {}
        
        results = {}
        for provider in missing:
            print(f"\n--- Installing {provider} SDK ---")
            results[provider] = self.install_sdk(provider, interactive)
        
        successful = sum(1 for success in results.values() if success)
        print(f"\n📊 Installation summary: {successful}/{len(missing)} successful")
        
        return results
    
    def get_installation_commands(self, providers: List[str]) -> List[str]:
        """Get installation commands for missing SDKs"""
        missing = self.get_missing_sdks(providers)
        commands = []
        for sdk_info in missing.values():
            if sdk_info["install_command"] not in commands:
                commands.append(sdk_info["install_command"])
        return commands
    
    def show_provider_status(self, providers: List[str]):
        """Show installation status for providers"""
        print("\n🔍 Provider SDK Status:")
        print("=" * 50)
        
        for provider in providers:
            if provider not in PROVIDER_SDKS:
                print(f"❌ {provider}: Unknown provider")
                continue
            
            available = self.check_sdk_availability(provider)
            sdk_info = PROVIDER_SDKS[provider]
            
            status = "✅ Available" if available else "❌ Missing"
            print(f"{status} {provider}: {sdk_info['description']}")
            
            if not available:
                print(f"   Install: {sdk_info['install_command']}")

def create_installer(auto_install: bool = False) -> SDKInstaller:
    """Create an SDK installer instance"""
    return SDKInstaller(auto_install=auto_install)
