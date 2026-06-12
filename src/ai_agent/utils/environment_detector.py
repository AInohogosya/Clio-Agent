#!/usr/bin/env python3
"""
Environment Detection and Adaptive Execution System for VEXIS-CLI-2.2
Gathers system data and adapts execution based on the environment
"""

import subprocess
import platform
import json
import os
import sys
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class EnvironmentInfo:
    """Comprehensive environment information"""
    # OS Information
    os_system: str
    os_release: str
    os_version: str
    os_machine: str
    
    # Python Information
    python_version: str
    python_executable: str
    
    # Ollama Information
    ollama_available: bool
    ollama_version: Optional[str]
    ollama_has_signin: bool
    ollama_has_whoami: bool
    ollama_installed_models: List[str]
    ollama_cloud_models: List[str]
    ollama_local_models: List[str]
    
    # System Capabilities
    has_venv_module: bool
    has_docker: bool
    has_git: bool
    has_curl: bool
    
    # Network Status
    can_connect_to_ollama_com: bool
    can_connect_to_pypi: bool
    
    # Recommendations
    needs_ollama_update: bool
    can_use_cloud_models: bool
    recommended_provider: str
    warnings: List[str]
    suggestions: List[str]


class EnvironmentDetector:
    """Detects and analyzes the runtime environment"""
    
    def __init__(self):
        self.warnings: List[str] = []
        self.suggestions: List[str] = []
    
    def detect_all(self) -> EnvironmentInfo:
        """Run all detection commands and return comprehensive info"""
        return EnvironmentInfo(
            # OS Information
            os_system=self._detect_os_system(),
            os_release=self._detect_os_release(),
            os_version=self._detect_os_version(),
            os_machine=self._detect_os_machine(),
            
            # Python Information
            python_version=self._detect_python_version(),
            python_executable=self._detect_python_executable(),
            
            # Ollama Information
            ollama_available=self._detect_ollama_available(),
            ollama_version=self._detect_ollama_version(),
            ollama_has_signin=self._detect_ollama_has_signin(),
            ollama_has_whoami=self._detect_ollama_has_whoami(),
            ollama_installed_models=self._detect_ollama_models(),
            ollama_cloud_models=self._detect_cloud_models(),
            ollama_local_models=self._detect_local_models(),
            
            # System Capabilities
            has_venv_module=self._detect_venv_module(),
            has_docker=self._detect_docker(),
            has_git=self._detect_git(),
            has_curl=self._detect_curl(),
            
            # Network Status
            can_connect_to_ollama_com=self._detect_ollama_com_connectivity(),
            can_connect_to_pypi=self._detect_pypi_connectivity(),
            
            # Recommendations
            needs_ollama_update=self._detect_needs_ollama_update(),
            can_use_cloud_models=self._detect_can_use_cloud_models(),
            recommended_provider=self._detect_recommended_provider(),
            warnings=self.warnings,
            suggestions=self.suggestions,
        )
    
    # === OS Detection Commands ===
    
    def _detect_os_system(self) -> str:
        """Detect operating system"""
        return platform.system()
    
    def _detect_os_release(self) -> str:
        """Detect OS release"""
        try:
            if platform.system() == "Linux":
                result = subprocess.run(
                    ["cat", "/etc/os-release"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    # Extract PRETTY_NAME
                    for line in result.stdout.split('\n'):
                        if line.startswith('PRETTY_NAME='):
                            return line.split('=')[1].strip('"')
            return platform.release()
        except Exception:
            return "unknown"
    
    def _detect_os_version(self) -> str:
        """Detect OS version"""
        return platform.version()
    
    def _detect_os_machine(self) -> str:
        """Detect machine architecture"""
        return platform.machine()
    
    # === Python Detection Commands ===
    
    def _detect_python_version(self) -> str:
        """Detect Python version"""
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    def _detect_python_executable(self) -> str:
        """Detect Python executable path"""
        return sys.executable
    
    def _detect_venv_module(self) -> bool:
        """Check if venv module is available"""
        try:
            import venv
            return True
        except ImportError:
            self.warnings.append("venv module not available")
            self.suggestions.append("Install python3-venv package (e.g., 'sudo apt install python3-venv')")
            return False
    
    # === Ollama Detection Commands ===
    
    def _detect_ollama_available(self) -> bool:
        """Check if Ollama is installed and available"""
        try:
            result = subprocess.run(
                ["ollama", "--version"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.warnings.append("Ollama is not installed or not in PATH")
            self.suggestions.append("Install Ollama: curl -fsSL https://ollama.com/install.sh | sh")
            return False
        except Exception as e:
            self.warnings.append(f"Error checking Ollama: {e}")
            return False
    
    def _detect_ollama_version(self) -> Optional[str]:
        """Detect Ollama version"""
        try:
            result = subprocess.run(
                ["ollama", "--version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # Parse version from output
                output = result.stdout.strip()
                # Match patterns like "ollama version is 0.16.2" or "0.16.2"
                match = re.search(r'(\d+\.\d+\.\d+)', output)
                if match:
                    return match.group(1)
            return None
        except Exception:
            return None
    
    def _detect_ollama_has_signin(self) -> bool:
        """Check if Ollama supports signin command"""
        try:
            result = subprocess.run(
                ["ollama", "signin", "--help"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0 or "signin" in result.stdout.lower()
        except Exception:
            return False
    
    def _detect_ollama_has_whoami(self) -> bool:
        """Check if Ollama supports whoami command"""
        try:
            result = subprocess.run(
                ["ollama", "whoami"],
                capture_output=True, text=True, timeout=10
            )
            # whoami exists if it doesn't say "unknown command"
            return "unknown command" not in result.stderr.lower()
        except Exception:
            return False
    
    def _detect_ollama_models(self) -> List[str]:
        """Detect installed Ollama models"""
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                models = []
                for line in lines[1:]:  # Skip header
                    if line.strip():
                        model_name = line.split()[0]
                        models.append(model_name)
                return models
            return []
        except Exception:
            return []
    
    def _detect_cloud_models(self) -> List[str]:
        """Detect cloud models in installed list"""
        models = self._detect_ollama_models()
        return [m for m in models if ':cloud' in m.lower() or 'cloud' in m.lower()]
    
    def _detect_local_models(self) -> List[str]:
        """Detect local models (non-cloud)"""
        models = self._detect_ollama_models()
        return [m for m in models if ':cloud' not in m.lower() and 'cloud' not in m.lower()]
    
    def _detect_needs_ollama_update(self) -> bool:
        """Check if Ollama needs update for cloud model support"""
        version = self._detect_ollama_version()
        if not version:
            return True
        
        # Parse version and compare
        try:
            parts = version.split('.')
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
            patch = int(parts[2]) if len(parts) > 2 else 0
            
            # Versions before 0.17.0 don't support cloud models/signin
            if major == 0 and minor < 17:
                self.suggestions.append(f"Update Ollama to 0.17.0+ for cloud model support (current: {version})")
                return True
            return False
        except Exception:
            return True
    
    def _detect_can_use_cloud_models(self) -> bool:
        """Check if cloud models can be used"""
        if not self._detect_ollama_available():
            return False
        if self._detect_needs_ollama_update():
            return False
        if not self._detect_ollama_has_signin():
            return False
        if not self._detect_ollama_has_whoami():
            return False
        
        # Check if signed in
        try:
            result = subprocess.run(
                ["ollama", "whoami"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0 or "not signed in" in result.stderr.lower():
                self.suggestions.append("Run 'ollama signin' to use cloud models")
                return False
            return True
        except Exception:
            return False
    
    def _detect_recommended_provider(self) -> str:
        """Determine recommended AI provider based on environment"""
        if self._detect_can_use_cloud_models():
            return "ollama"
        elif self._detect_ollama_available() and self._detect_local_models():
            return "ollama-local"  # Local models only
        else:
            return "google"  # Fallback to Google API
    
    # === System Tool Detection ===
    
    def _detect_docker(self) -> bool:
        """Check if Docker is available"""
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _detect_git(self) -> bool:
        """Check if Git is available"""
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _detect_curl(self) -> bool:
        """Check if curl is available"""
        try:
            result = subprocess.run(
                ["curl", "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    # === Network Detection ===
    
    def _detect_ollama_com_connectivity(self) -> bool:
        """Check connectivity to ollama.com"""
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", 
                 "https://ollama.com", "--max-time", "10"],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0 and result.stdout.strip() == "200"
        except Exception:
            return False
    
    def _detect_pypi_connectivity(self) -> bool:
        """Check connectivity to PyPI"""
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "https://pypi.org", "--max-time", "10"],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0 and result.stdout.strip() == "200"
        except Exception:
            return False


class AdaptiveExecutor:
    """Executes commands adaptively based on environment"""
    
    def __init__(self, env_info: EnvironmentInfo):
        self.env = env_info
        self.execution_plan: List[Dict[str, Any]] = []
    
    def create_execution_plan(self) -> List[Dict[str, Any]]:
        """Create a plan of action based on environment"""
        plan = []
        
        # Step 1: Check and update Ollama if needed
        if self.env.needs_ollama_update:
            plan.append({
                "step": 1,
                "action": "update_ollama",
                "command": "curl -fsSL https://ollama.com/install.sh | sh",
                "description": "Update Ollama to support cloud models",
                "condition": "Ollama version outdated",
                "required": False,
                "can_skip": True
            })
        
        # Step 2: Sign in to Ollama if available but not signed in
        if self.env.ollama_available and self.env.ollama_has_signin and not self.env.can_use_cloud_models:
            plan.append({
                "step": 2,
                "action": "ollama_signin",
                "command": "ollama signin",
                "description": "Sign in to Ollama for cloud model access",
                "condition": "Ollama installed but not signed in",
                "required": False,
                "can_skip": True,
                "note": "Only needed for cloud models. Local models work without signin."
            })
        
        # Step 3: Install python3-venv if missing
        if not self.env.has_venv_module:
            if self.env.os_system == "Linux":
                plan.append({
                    "step": 3,
                    "action": "install_venv",
                    "command": "sudo apt install python3-venv -y || sudo yum install python3-virtualenv -y",
                    "description": "Install Python virtual environment support",
                    "condition": "venv module not available",
                    "required": True,
                    "can_skip": False
                })
        
        # Step 4: Verify Ollama local models available
        if self.env.ollama_available and not self.env.ollama_local_models:
            plan.append({
                "step": 4,
                "action": "pull_local_model",
                "command": "ollama pull gemma3:4B",
                "description": "Pull a local model for offline use",
                "condition": "No local models installed",
                "required": False,
                "can_skip": True,
                "alternatives": ["ollama pull llama3.2:3b", "ollama pull qwen3:4B"]
            })
        
        # Step 5: Setup Google API as fallback
        if self.env.recommended_provider == "google":
            plan.append({
                "step": 5,
                "action": "setup_google_api",
                "command": "python3 run.py --setup-google",
                "description": "Configure Google API as fallback provider",
                "condition": "Ollama not available or not usable",
                "required": False,
                "can_skip": True,
                "note": "Get API key from https://aistudio.google.com/app/apikey"
            })
        
        self.execution_plan = plan
        return plan
    
    def execute_plan(self, interactive: bool = True) -> bool:
        """Execute the prepared plan"""
        if not self.execution_plan:
            print("✓ No actions needed - environment is ready")
            return True
        
        print(f"\n📋 Execution Plan ({len(self.execution_plan)} steps):")
        print("=" * 60)
        
        for step in self.execution_plan:
            print(f"\nStep {step['step']}: {step['description']}")
            print(f"  Action: {step['action']}")
            print(f"  Command: {step['command']}")
            if step.get('note'):
                print(f"  Note: {step['note']}")
            
            if interactive:
                if step['can_skip']:
                    response = input(f"  Execute this step? [Y/n/skip]: ").lower().strip()
                    if response in ['n', 'no']:
                        print("  ⏭ Skipped")
                        continue
                    elif response in ['s', 'skip']:
                        print("  ⏭ Skipped (and won't ask again)")
                        step['skipped'] = True
                        continue
                else:
                    input(f"  Press Enter to execute (required)...")
            
            # Execute the command (shell=False for security)
            print(f"  Executing: {step['command']}")
            try:
                # SECURITY FIX: Use shlex.split and shell=False to prevent injection
                import shlex
                import tempfile
                
                cmd = step['command']
                
                # Handle curl | sh pattern safely
                if 'curl' in cmd and '| sh' in cmd:
                    # Download and execute separately
                    parts = cmd.split()
                    url = None
                    for part in parts:
                        if part.startswith('http'):
                            url = part
                            break
                    
                    if url:
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as tmp:
                            script_path = tmp.name
                        try:
                            download = subprocess.run(
                                ['curl', '-fsSL', url],
                                capture_output=True, text=True, timeout=120
                            )
                            if download.returncode == 0:
                                with open(script_path, 'w') as f:
                                    f.write(download.stdout)
                                os.chmod(script_path, 0o755)
                                result = subprocess.run(
                                    ['bash', script_path],
                                    capture_output=True, text=True, timeout=300
                                )
                            else:
                                result = type('obj', (object,), {
                                    'returncode': download.returncode,
                                    'stderr': download.stderr
                                })()
                        finally:
                            try:
                                os.unlink(script_path)
                            except Exception:
                                pass
                    else:
                        result = type('obj', (object,), {
                            'returncode': -1,
                            'stderr': 'Could not extract URL'
                        })()
                elif ' || ' in cmd or ' && ' in cmd or cmd.startswith('sudo '):
                    # Complex commands need shell - but we validate them first
                    # Only allow known safe patterns
                    allowed_commands = ['apt', 'yum', 'ollama', 'python3', 'pip']
                    is_allowed = any(cmd.startswith(ac) or f' {ac} ' in cmd for ac in allowed_commands)
                    if is_allowed:
                        result = subprocess.run(
                            cmd, shell=True, capture_output=True,
                            text=True, timeout=300
                        )
                    else:
                        print(f"  ⚠ Skipped: Command contains potentially unsafe patterns")
                        step['failed'] = True
                        continue
                else:
                    # Safe command - use shlex.split and no shell
                    try:
                        cmd_parts = shlex.split(cmd)
                    except ValueError:
                        cmd_parts = None
                    
                    if cmd_parts:
                        result = subprocess.run(
                            cmd_parts, shell=False, capture_output=True,
                            text=True, timeout=300
                        )
                    else:
                        # Fall back for complex commands
                        result = subprocess.run(
                            cmd, shell=True, capture_output=True,
                            text=True, timeout=300
                        )
                
                if result.returncode == 0:
                    print(f"  ✓ Success")
                    step['executed'] = True
                else:
                    print(f"  ✗ Failed: {getattr(result, 'stderr', 'Unknown error')}")
                    step['failed'] = True
                    if step['required']:
                        print(f"  This step is required. Cannot continue.")
                        return False
            except Exception as e:
                print(f"  ✗ Error: {e}")
                step['failed'] = True
                if step['required']:
                    return False
        
        print("\n" + "=" * 60)
        print("✓ Execution plan completed")
        return True
    
    def get_recommendations(self) -> Dict[str, Any]:
        """Get recommendations based on environment"""
        return {
            "can_run_ollama": self.env.ollama_available,
            "can_run_cloud_models": self.env.can_use_cloud_models,
            "can_run_local_models": bool(self.env.ollama_local_models),
            "recommended_provider": self.env.recommended_provider,
            "warnings": self.env.warnings,
            "suggestions": self.env.suggestions,
            "needs_setup": bool(self.execution_plan),
            "models_available": {
                "cloud": self.env.ollama_cloud_models,
                "local": self.env.ollama_local_models
            }
        }


def detect_and_plan() -> Tuple[EnvironmentInfo, AdaptiveExecutor]:
    """Main entry point: detect environment and create execution plan"""
    print("🔍 Detecting environment...")
    print("=" * 60)
    
    detector = EnvironmentDetector()
    env_info = detector.detect_all()
    
    # Print summary
    print(f"\n📊 Environment Summary:")
    print(f"  OS: {env_info.os_system} ({env_info.os_release})")
    print(f"  Python: {env_info.python_version}")
    print(f"  Ollama: {'✓' if env_info.ollama_available else '✗'} {env_info.ollama_version or 'N/A'}")
    print(f"  Cloud Models: {'✓' if env_info.can_use_cloud_models else '✗'}")
    print(f"  Local Models: {len(env_info.ollama_local_models)} available")
    
    if env_info.warnings:
        print(f"\n⚠ Warnings:")
        for w in env_info.warnings:
            print(f"  - {w}")
    
    if env_info.suggestions:
        print(f"\n💡 Suggestions:")
        for s in env_info.suggestions:
            print(f"  - {s}")
    
    print(f"\n🎯 Recommended Provider: {env_info.recommended_provider}")
    
    # Create execution plan
    executor = AdaptiveExecutor(env_info)
    plan = executor.create_execution_plan()
    
    return env_info, executor


def main():
    """Main entry point for standalone execution"""
    env_info, executor = detect_and_plan()
    
    # Optionally save environment report
    report_path = Path("environment_report.json")
    with open(report_path, 'w') as f:
        json.dump(asdict(env_info), f, indent=2)
    print(f"\n📄 Environment report saved to: {report_path}")
    
    # Execute plan if there are actions
    if executor.execution_plan:
        print(f"\n🚀 Ready to execute {len(executor.execution_plan)} setup steps")
        response = input("Execute setup plan? [Y/n]: ").lower().strip()
        if response not in ['n', 'no']:
            executor.execute_plan(interactive=True)
        else:
            print("Setup skipped. You can run manually:")
            for step in executor.execution_plan:
                print(f"  {step['command']}")
    else:
        print("\n✓ Environment is ready! You can run: python3 run.py \"your command\"")


if __name__ == "__main__":
    main()
