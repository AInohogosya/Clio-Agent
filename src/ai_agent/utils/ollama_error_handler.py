"""
Enhanced Ollama Error Handler for VEXIS-CLI-2.2
Provides user-friendly guidance for common Ollama issues including:
- Permission errors
- Model name errors  
- Sign-in issues
- Network connectivity problems
- Installation issues
"""

import subprocess
import os
import platform
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass

# Optional logger import to avoid circular dependencies
try:
    from ..utils.logger import get_logger
    HAS_LOGGER = True
except ImportError:
    HAS_LOGGER = False
    get_logger = None


@dataclass
class OllamaError:
    """Structured Ollama error information"""
    error_type: str
    user_message: str
    technical_message: str
    resolution_steps: List[str]
    can_retry: bool
    severity: str  # "low", "medium", "high", "critical"


class OllamaErrorHandler:
    """Enhanced error handler for Ollama with user-friendly guidance"""
    
    def __init__(self):
        if HAS_LOGGER:
            self.logger = get_logger("ollama_error_handler")
        else:
            self.logger = None
        
        # Common error patterns and their handlers
        self.error_patterns = {
            # Permission errors
            r'permission denied|Permission denied|access denied|Access denied': self._handle_permission_error,
            r'not signed in|authentication required|signin required': self._handle_signin_error,
            
            # Cloud model / authorization errors
            r'401|unauthorized|access.*denied|signin_url': self._handle_cloud_auth_error,
            
            # Model errors
            r'model .* not found|model .* does not exist|unknown model': self._handle_model_not_found,
            r'pull.*failed|download.*failed|network.*error': self._handle_pull_error,
            
            # Connection errors
            r'connection refused|connection failed|cannot connect': self._handle_connection_error,
            r'timeout|timed out': self._handle_timeout_error,
            
            # Installation errors
            r'command not found|not recognized|ollama.*not found': self._handle_installation_error,
            r'version.*outdated|update.*required': self._handle_version_error,
            
            # Browser/signin errors
            r'browser.*not.*open|cannot.*open.*browser|failed.*to.*open': self._handle_browser_error,
            
            # Generic errors
            r'error|failed|exception': self._handle_generic_error,
        }
    
    def analyze_error(self, error_message: str, context: Optional[Dict[str, Any]] = None) -> OllamaError:
        """
        Analyze an error message and return structured error information with user guidance
        
        Args:
            error_message: The error message to analyze
            context: Additional context (e.g., command being run, model name, etc.)
            
        Returns:
            OllamaError with user-friendly guidance
        """
        context = context or {}
        error_lower = error_message.lower()
        
        if self.logger:
            self.logger.debug(f"Analyzing Ollama error: {error_message}")
        
        # Try to match specific error patterns
        for pattern, handler in self.error_patterns.items():
            if re.search(pattern, error_lower, re.IGNORECASE):
                try:
                    return handler(error_message, context)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Error handler failed for pattern {pattern}: {e}")
                    continue
        
        # Fallback to generic error
        return self._handle_generic_error(error_message, context)
    
    def _handle_permission_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle permission-related errors"""
        system = platform.system().lower()
        
        resolution_steps = [
            "🔧 **Permission Required**: Ollama needs proper permissions to function correctly.",
        ]
        
        if system == "darwin":  # macOS
            resolution_steps.extend([
                "1. **Grant Full Disk Access**:",
                "   - Open System Preferences > Security & Privacy > Privacy",
                "   - Click 'Full Disk Access' and add Terminal",
                "   - Also add your terminal app (iTerm2, Terminal, etc.)",
                "",
                "2. **Reset Ollama Permissions**:",
                "   ```bash",
                "   sudo rm -rf ~/.ollama",
                "   ollama serve",
                "   ```",
                "",
                "3. **Alternative: Use Homebrew**:",
                "   ```bash",
                "   brew reinstall ollama",
                "   ```",
            ])
        elif system == "linux":
            resolution_steps.extend([
                "1. **Check User Permissions**:",
                "   ```bash",
                "   sudo usermod -a -G ollama $USER",
                "   # Log out and log back in",
                "   ```",
                "",
                "2. **Fix Ollama Directory Permissions**:",
                "   ```bash",
                "   sudo chown -R $USER:$USER ~/.ollama",
                "   sudo chmod -R 755 ~/.ollama",
                "   ```",
                "",
                "3. **Restart Ollama Service**:",
                "   ```bash",
                "   sudo systemctl restart ollama",
                "   ```",
            ])
        else:  # Windows
            resolution_steps.extend([
                "1. **Run as Administrator**:",
                "   - Right-click Command Prompt/PowerShell",
                "   - Select 'Run as administrator'",
                "",
                "2. **Check Windows Defender/Firewall**:",
                "   - Add Ollama to Windows Defender exclusions",
                "   - Allow Ollama through Windows Firewall",
                "",
                "3. **Reinstall Ollama**:",
                "   - Download latest installer from ollama.com",
                "   - Run installer as administrator",
            ])
        
        return OllamaError(
            error_type="permission_error",
            user_message="🚫 Ollama doesn't have the necessary permissions to complete this operation.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=False,
            severity="high"
        )
    
    def _handle_signin_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle Ollama signin errors"""
        resolution_steps = [
            "🔐 **Ollama Sign-In Required**: You need to sign in to use cloud models.",
            "",
            "1. **Sign in to Ollama**:",
            "   ```bash",
            "   ollama signin",
            "   ```",
            "",
            "2. **If browser doesn't open automatically**:",
            "   - Copy the URL from terminal output",
            "   - Open it manually in your browser",
            "   - Complete the sign-in process",
            "",
            "3. **Verify sign-in status**:",
            "   ```bash",
            "   ollama whoami",
            "   ```",
            "",
            "4. **Alternative: Use local models only**:",
            "   - Choose local models like 'gemma3:4B' or 'llama3.2:3b'",
            "   - These work without sign-in",
        ]
        
        return OllamaError(
            error_type="signin_error",
            user_message="🔐 You need to sign in to Ollama to access cloud models.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="medium"
        )
    
    def _handle_cloud_auth_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle cloud model authentication errors (401 unauthorized)"""
        model_name = context.get('model_name', 'unknown')
        
        resolution_steps = [
            "🔐 **Cloud Model Authentication Required**: This model requires Ollama sign-in.",
            "",
            "1. **Sign in to Ollama**:",
            "   ```bash",
            "   ollama signin",
            "   ```",
            "",
            "2. **Or use local models instead** (no sign-in required):",
            "   - `gemma3:4b` - Fast, efficient",
            "   - `llama3.2:3b` - Good balance of speed and quality",
            "   - `llama3.2:1b` - Ultra-lightweight",
            "",
            "3. **Check your Ollama version** (cloud models require 0.17.0+):",
            "   ```bash",
            "   ollama --version",
            "   ```",
            "",
            "4. **If you're not signed in**: Cloud models won't work without authentication.",
        ]
        
        return OllamaError(
            error_type="cloud_auth_error",
            user_message=f"🔐 Model '{model_name}' requires Ollama sign-in. Use local models or sign in to Ollama.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=False,
            severity="high"
        )
    
    def _handle_model_not_found(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle model not found errors"""
        model_name = context.get('model_name', 'unknown')
        
        # Extract model name from error message if not in context
        if model_name == 'unknown':
            match = re.search(r'model ["\']?([^"\':\s]+)["\']?', error_message, re.IGNORECASE)
            if match:
                model_name = match.group(1)
        
        resolution_steps = [
            f"🤖 **Model Not Found**: The model '{model_name}' is not available.",
            "",
            "1. **Check available models**:",
            "   ```bash",
            "   ollama list",
            "   ```",
            "",
            "2. **Pull the model if it exists**:",
            "   ```bash",
            f"   ollama pull {model_name}",
            "   ```",
            "",
            "3. **Try popular alternative models**:",
            "   - `gemma3:4B` (lightweight, fast)",
            "   - `llama3.2:3b` (small, efficient)", 
            "   - `qwen3:4B` (multilingual)",
            "   - `mistral:7b` (balanced)",
            "",
            "4. **Browse all available models**:",
            "   Visit: https://ollama.com/library",
        ]
        
        return OllamaError(
            error_type="model_not_found",
            user_message=f"🤖 The model '{model_name}' is not available locally or in the Ollama library.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="medium"
        )
    
    def _handle_pull_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle model download/pull errors"""
        resolution_steps = [
            "📥 **Model Download Failed**: Unable to download the model.",
            "",
            "1. **Check internet connection**:",
            "   ```bash",
            "   ping ollama.com",
            "   ```",
            "",
            "2. **Try pulling again with retry**:",
            "   ```bash",
            "   ollama pull <model-name>",
            "   ```",
            "",
            "3. **Check available disk space**:",
            "   - Models can be several GB in size",
            "   - Ensure you have at least 10GB free space",
            "",
            "4. **Try a smaller model**:",
            "   - `gemma3:1b` (~1GB)",
            "   - `llama3.2:1b` (~1GB)",
            "",
            "5. **If behind corporate firewall**:",
            "   - May need to configure proxy settings",
            "   - Try from a different network",
        ]
        
        return OllamaError(
            error_type="pull_error",
            user_message="📥 Failed to download the model. Check your internet connection and disk space.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="medium"
        )
    
    def _handle_connection_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle connection errors"""
        resolution_steps = [
            "🔌 **Connection Failed**: Cannot connect to Ollama service.",
            "",
            "1. **Check if Ollama is running**:",
            "   ```bash",
            "   ps aux | grep ollama",
            "   ```",
            "",
            "2. **Start Ollama service**:",
            "   ```bash",
            "   ollama serve",
            "   # Or in background:",
            "   ollama serve &",
            "   ```",
            "",
            "3. **Check if port 11434 is available**:",
            "   ```bash",
            "   lsof -i :11434",
            "   netstat -an | grep 11434",
            "   ```",
            "",
            "4. **Restart Ollama completely**:",
            "   ```bash",
            "   pkill ollama",
            "   ollama serve",
            "   ```",
            "",
            "5. **If using Docker**:",
            "   ```bash",
            "   docker run -d -p 11434:11434 ollama/ollama",
            "   ```",
        ]
        
        return OllamaError(
            error_type="connection_error",
            user_message="🔌 Cannot connect to Ollama. Make sure the service is running.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="high"
        )
    
    def _handle_timeout_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle timeout errors"""
        resolution_steps = [
            "⏱️ **Operation Timed Out**: The operation took too long to complete.",
            "",
            "1. **For model operations**:",
            "   - Large models can take 10+ minutes to download",
            "   - Try with a smaller model first",
            "",
            "2. **Increase timeout**:",
            "   - Some operations need more time",
            "   - Try again during off-peak hours",
            "",
            "3. **Check system resources**:",
            "   - High CPU/RAM usage can cause timeouts",
            "   - Close other applications",
            "",
            "4. **Network issues**:",
            "   - Slow internet can cause download timeouts",
            "   - Try from a different network",
        ]
        
        return OllamaError(
            error_type="timeout_error",
            user_message="⏱️ The operation timed out. This might be due to slow network or large model size.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="medium"
        )
    
    def _handle_installation_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle installation errors"""
        system = platform.system().lower()
        
        resolution_steps = [
            "❌ **Ollama Not Installed**: Ollama is not installed or not in your PATH.",
            "",
        ]
        
        if system == "darwin":  # macOS
            resolution_steps.extend([
                "1. **Install with Homebrew (recommended)**:",
                "   ```bash",
                "   brew install ollama",
                "   ```",
                "",
                "2. **Or download directly**:",
                "   - Visit: https://ollama.com/download/mac",
                "   - Download and run the installer",
            ])
        elif system == "linux":
            resolution_steps.extend([
                "1. **Install with official script**:",
                "   ```bash",
                "   curl -fsSL https://ollama.com/install.sh | sh",
                "   ```",
                "",
                "2. **Or manually**:",
                "   - Visit: https://ollama.com/download/linux",
                "   - Follow the manual installation guide",
            ])
        else:  # Windows
            resolution_steps.extend([
                "1. **Download Windows installer**:",
                "   - Visit: https://ollama.com/download/windows",
                "   - Download and run the .exe installer",
                "",
                "2. **Or use WSL**:",
                "   ```bash",
                "   wsl --install",
                "   # Then follow Linux instructions in WSL",
                "   ```",
            ])
        
        resolution_steps.extend([
            "",
            "3. **Verify installation**:",
            "   ```bash",
            "   ollama --version",
            "   ```",
            "",
            "4. **Add to PATH if needed**:",
            "   - Make sure Ollama is in your system PATH",
            "   - Restart your terminal after installation",
        ])
        
        return OllamaError(
            error_type="installation_error",
            user_message="❌ Ollama is not installed or not accessible from your command line.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=False,
            severity="critical"
        )
    
    def _handle_version_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle version-related errors"""
        resolution_steps = [
            "🔄 **Ollama Update Required**: Your Ollama version is outdated.",
            "",
            "1. **Update Ollama**:",
            "   ```bash",
            "   curl -fsSL https://ollama.com/install.sh | sh",
            "   ```",
            "",
            "2. **Or use package manager**:",
            "   ```bash",
            "   # macOS with Homebrew",
            "   brew upgrade ollama",
            "   ```",
            "",
            "3. **Verify new version**:",
            "   ```bash",
            "   ollama --version",
            "   # Should be 0.17.0 or higher for cloud models",
            "   ```",
            "",
            "4. **Restart Ollama service**:",
            "   ```bash",
            "   pkill ollama && ollama serve",
            "   ```",
        ]
        
        return OllamaError(
            error_type="version_error",
            user_message="🔄 Your Ollama version is outdated. Update to access the latest features.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=False,
            severity="medium"
        )
    
    def _handle_browser_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle browser opening errors"""
        resolution_steps = [
            "🌐 **Browser Error**: Cannot open browser for Ollama sign-in.",
            "",
            "1. **Manual sign-in**:",
            "   - Run: `ollama signin`",
            "   - Copy the URL shown in terminal",
            "   - Paste it in your browser manually",
            "",
            "2. **Check default browser**:",
            "   ```bash",
            "   # macOS",
            "   echo $BROWSER",
            "   # Linux",
            "   xdg-settings get default-web-browser",
            "   ```",
            "",
            "3. **Set browser manually** (if needed):",
            "   ```bash",
            "   export BROWSER=chrome  # or firefox, safari, etc.",
            "   ```",
            "",
            "4. **Try different browser**:",
            "   - Chrome, Firefox, Safari, Edge",
            "   - Some browsers may have security restrictions",
        ]
        
        return OllamaError(
            error_type="browser_error",
            user_message="🌐 Cannot open browser for sign-in. You can complete the process manually.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="low"
        )
    
    def _handle_generic_error(self, error_message: str, context: Dict[str, Any]) -> OllamaError:
        """Handle generic/unknown errors"""
        resolution_steps = [
            "⚠️ **Unexpected Error**: An unexpected error occurred with Ollama.",
            "",
            "1. **Basic troubleshooting**:",
            "   ```bash",
            "   ollama --version",
            "   ollama list",
            "   ```",
            "",
            "2. **Restart Ollama**:",
            "   ```bash",
            "   pkill ollama",
            "   ollama serve",
            "   ```",
            "",
            "3. **Check system logs**:",
            "   ```bash",
            "   # macOS/Linux",
            "   tail -f ~/.ollama/logs/server.log",
            "   # Or check system logs",
            "   ```",
            "",
            "4. **Reinstall as last resort**:",
            "   ```bash",
            "   # Backup models first",
            "   cp -r ~/.ollama ~/.ollama.backup",
            "   # Reinstall",
            "   curl -fsSL https://ollama.com/install.sh | sh",
            "   ```",
            "",
            "5. **Get help**:",
            "   - Visit: https://github.com/ollama/ollama/issues",
            "   - Check Discord community: https://discord.gg/ollama",
        ]
        
        return OllamaError(
            error_type="generic_error",
            user_message=f"⚠️ An unexpected error occurred.\n\n[Original Error: {error_message}]\n\nTry the troubleshooting steps below.",
            technical_message=error_message,
            resolution_steps=resolution_steps,
            can_retry=True,
            severity="medium"
        )
    
    def format_error_for_display(self, error: OllamaError) -> str:
        """Format error for user-friendly display"""
        try:
            from .interactive_menu import Colors
        except ImportError:
            # Fallback colors if interactive_menu not available
            class Colors:
                BRIGHT_RED = ""
                RESET = ""
                WHITE = ""
                YELLOW = ""
                CYAN = ""
                GREEN = ""
                BRIGHT_CYAN = ""
        except Exception:
            # Any other import error, use fallback colors
            class Colors:
                BRIGHT_RED = ""
                RESET = ""
                WHITE = ""
                YELLOW = ""
                CYAN = ""
                GREEN = ""
                BRIGHT_CYAN = ""
        
        lines = [
            f"{Colors.BRIGHT_RED}{'='*60}{Colors.RESET}",
            f"{Colors.BRIGHT_RED}🚨 OLLAMA ERROR: {error.error_type.upper()}{Colors.RESET}",
            f"{Colors.BRIGHT_RED}{'='*60}{Colors.RESET}",
            "",
            f"{Colors.WHITE}{error.user_message}{Colors.RESET}",
            "",
            f"{Colors.YELLOW}📋 Technical Details:{Colors.RESET}",
            f"{Colors.CYAN}{error.technical_message}{Colors.RESET}",
            "",
            f"{Colors.GREEN}🔧 Resolution Steps:{Colors.RESET}",
        ]
        
        for step in error.resolution_steps:
            if step.startswith('```'):
                lines.append(f"{Colors.BRIGHT_CYAN}{step}{Colors.RESET}")
            elif step.startswith('1.') or step.startswith('2.') or step.startswith('3.') or step.startswith('4.') or step.startswith('5.'):
                lines.append(f"{Colors.WHITE}{step}{Colors.RESET}")
            elif step.startswith('-'):
                lines.append(f"{Colors.CYAN}  {step}{Colors.RESET}")
            elif step.strip() == '':
                lines.append(step)
            else:
                lines.append(f"{Colors.WHITE}{step}{Colors.RESET}")
        
        lines.extend([
            "",
            f"{Colors.YELLOW}🔄 Can Retry: {'Yes' if error.can_retry else 'No'}{Colors.RESET}",
            f"{Colors.YELLOW}⚠️  Severity: {error.severity.upper()}{Colors.RESET}",
            f"{Colors.BRIGHT_RED}{'='*60}{Colors.RESET}",
        ])
        
        return '\n'.join(lines)
    
    def should_retry(self, error: OllamaError) -> bool:
        """Determine if the operation should be retried"""
        return error.can_retry
    
    def get_severity(self, error: OllamaError) -> str:
        """Get error severity for logging/alerting purposes"""
        return error.severity


# Global error handler instance
_error_handler: Optional[OllamaErrorHandler] = None


def get_ollama_error_handler() -> OllamaErrorHandler:
    """Get global Ollama error handler instance"""
    global _error_handler
    
    if _error_handler is None:
        _error_handler = OllamaErrorHandler()
    
    return _error_handler


def handle_ollama_error(error_message: str, context: Optional[Dict[str, Any]] = None, 
                       display_to_user: bool = True) -> Tuple[OllamaError, bool]:
    """
    Handle an Ollama error with user-friendly guidance
    
    Args:
        error_message: The error message to handle
        context: Additional context about the error
        display_to_user: Whether to display the error to the user
        
    Returns:
        Tuple of (OllamaError, should_retry)
    """
    handler = get_ollama_error_handler()
    error = handler.analyze_error(error_message, context)
    
    if display_to_user:
        formatted_error = handler.format_error_for_display(error)
        print(formatted_error)
    
    return error, handler.should_retry(error)
