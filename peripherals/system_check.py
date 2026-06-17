#!/usr/bin/env python3
"""
Comprehensive System Check for Clio-Agent-1
Validates all components and provides detailed diagnostics
"""

import subprocess
import sys
import os
import importlib.util
from pathlib import Path
from typing import Dict, List, Tuple


class SystemChecker:
    """Comprehensive system validation"""
    
    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.issues = []
        self.warnings = []
        self.successes = []
    
    def log(self, status: str, message: str, details: str = ""):
        """Log a check result"""
        if status == "success":
            self.successes.append(f"✅ {message}")
            if details:
                self.successes.append(f"   {details}")
        elif status == "warning":
            self.warnings.append(f"⚠️  {message}")
            if details:
                self.warnings.append(f"   {details}")
        elif status == "error":
            self.issues.append(f"❌ {message}")
            if details:
                self.issues.append(f"   {details}")
    
    def check_python_version(self):
        """Check Python version compatibility"""
        version = sys.version_info
        if version.major >= 3 and version.minor >= 8:
            self.log("success", f"Python {version.major}.{version.minor}.{version.micro} is compatible")
        else:
            self.log("error", f"Python {version.major}.{version.minor}.{version.micro} is too old", "Requires Python 3.8+")
    
    def check_virtual_environment(self):
        """Check if running in virtual environment"""
        if sys.prefix != sys.base_prefix:
            self.log("success", "Running in virtual environment", f"Path: {sys.prefix}")
        else:
            self.log("warning", "Not in virtual environment", "Consider creating one for isolation")
    
    def check_dependencies(self):
        """Check Python dependencies"""
        requirements_file = self.project_root / "peripherals" / "requirements.txt"
        if not requirements_file.exists():
            self.log("error", "requirements.txt not found")
            return
        
        # Read requirements
        try:
            with open(requirements_file, 'r') as f:
                requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            
            missing = []
            for req in requirements:
                package = req.split('>=')[0].split('==')[0].split('[')[0]
                try:
                    importlib.import_module(package.replace('-', '_'))
                    self.log("success", f"Package {package} is installed")
                except ImportError:
                    missing.append(package)
                    self.log("error", f"Package {package} is missing")
            
            if not missing:
                self.log("success", "All dependencies are installed")
        except Exception as e:
            self.log("error", f"Failed to check dependencies: {e}")
    
    def check_ollama_installation(self):
        """Check Ollama installation and status"""
        try:
            result = subprocess.run(['ollama', '--version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version = result.stdout.strip()
                self.log("success", f"Ollama is installed: {version}")
                
                # Check if Ollama is running
                try:
                    result = subprocess.run(['ollama', 'list'], 
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')[1:]  # Skip header
                        models = [line.split()[0] for line in lines if line.strip()]
                        if models:
                            self.log("success", f"Found {len(models)} models", f"Examples: {', '.join(models[:3])}")
                        else:
                            self.log("warning", "Ollama is running but no models installed")
                    else:
                        self.log("warning", "Ollama may not be running properly")
                except subprocess.TimeoutExpired:
                    self.log("warning", "Ollama command timed out")
                except Exception as e:
                    self.log("warning", f"Failed to check Ollama status: {e}")
            else:
                self.log("error", "Ollama command failed", result.stderr)
        except FileNotFoundError:
            self.log("error", "Ollama is not installed", "Install with: curl -fsSL https://ollama.com/install.sh | sh")
        except subprocess.TimeoutExpired:
            self.log("error", "Ollama command timed out")
        except Exception as e:
            self.log("error", f"Failed to check Ollama: {e}")
    
    def check_project_structure(self):
        """Check project structure"""
        required_files = [
            "run.py",
            "peripherals/requirements.txt",
            "src/ai_agent/__init__.py",
            "src/ai_agent/core_processing/__init__.py",
            "src/ai_agent/external_integration/__init__.py",
        ]
        
        for file_path in required_files:
            full_path = self.project_root / file_path
            if full_path.exists():
                self.log("success", f"File exists: {file_path}")
            else:
                self.log("error", f"Required file missing: {file_path}")
    
    def check_configuration(self):
        """Check configuration files"""
        config_files = [
            "config.yaml",
        ]
        
        for config_file in config_files:
            config_path = self.project_root / config_file
            if config_path.exists():
                self.log("success", f"Config file exists: {config_file}")
            else:
                self.log("warning", f"Config file missing: {config_file}")
    
    def check_import_structure(self):
        """Check if core modules can be imported"""
        test_imports = [
            ("ai_agent.utils.logger", "Logger module"),
            ("ai_agent.utils.config", "Config module"),
            ("ai_agent.utils.exceptions", "Exceptions module"),
            ("ai_agent.external_integration.model_runner", "Model runner"),
            ("ai_agent.core_processing.task_generator", "Task generator"),
        ]
        
        # Add src to path for imports
        src_path = str(self.project_root / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
        
        for module_name, description in test_imports:
            try:
                importlib.import_module(module_name)
                self.log("success", f"Can import: {description}")
            except ImportError as e:
                self.log("error", f"Failed to import: {description}", str(e))
            except Exception as e:
                self.log("error", f"Error importing: {description}", str(e))
    
    def check_permissions(self):
        """Check file permissions"""
        key_files = ["run.py", "requirements.txt"]
        
        for file_name in key_files:
            file_path = self.project_root / file_name
            if file_path.exists():
                if os.access(file_path, os.R_OK):
                    self.log("success", f"Can read: {file_name}")
                else:
                    self.log("error", f"Cannot read: {file_name}")
    
    def run_all_checks(self) -> Dict[str, List[str]]:
        """Run all system checks"""
        print("🔍 Running comprehensive system check...\n")
        
        self.check_python_version()
        self.check_virtual_environment()
        self.check_dependencies()
        self.check_ollama_installation()
        self.check_project_structure()
        self.check_configuration()
        self.check_import_structure()
        self.check_permissions()
        
        return {
            "successes": self.successes,
            "warnings": self.warnings,
            "errors": self.issues
        }
    
    def print_summary(self):
        """Print check summary"""
        results = self.run_all_checks()
        
        # Print results
        if results["successes"]:
            print("\n✅ SUCCESSFUL CHECKS:")
            for item in results["successes"]:
                print(f"  {item}")
        
        if results["warnings"]:
            print("\n⚠️  WARNINGS:")
            for item in results["warnings"]:
                print(f"  {item}")
        
        if results["errors"]:
            print("\n❌ ERRORS:")
            for item in results["errors"]:
                print(f"  {item}")
        
        # Summary
        total_checks = len(results["successes"]) + len(results["warnings"]) + len(results["errors"])
        success_rate = (len(results["successes"]) / total_checks) * 100 if total_checks > 0 else 0
        
        print(f"\n📊 SUMMARY:")
        print(f"   Total checks: {total_checks}")
        print(f"   Successful: {len(results['successes'])}")
        print(f"   Warnings: {len(results['warnings'])}")
        print(f"   Errors: {len(results['errors'])}")
        print(f"   Success rate: {success_rate:.1f}%")
        
        if results["errors"]:
            print(f"\n🚨 CRITICAL ISSUES FOUND:")
            print(f"   Please fix the errors before running Clio-Agent-1")
            return False
        elif results["warnings"]:
            print(f"\n⚠️  MINOR ISSUES FOUND:")
            print(f"   Clio-Agent-1 should work, but consider fixing warnings")
            return True
        else:
            print(f"\n🎉 ALL CHECKS PASSED!")
            print(f"   Clio-Agent-1 is ready to run")
            return True


def main():
    """Main function"""
    checker = SystemChecker()
    ready = checker.print_summary()
    
    if not ready:
        print(f"\n🔧 SUGGESTED FIXES:")
        print(f"   1. Install missing dependencies: pip install -r requirements.txt")
        print(f"   2. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh")
        print(f"   3. Run setup script: python3 setup.py")
        sys.exit(1)
    else:
        print(f"\n🚀 READY TO RUN:")
        print(f"   python3 run.py \"your instruction here\"")
        sys.exit(0)


if __name__ == "__main__":
    main()
