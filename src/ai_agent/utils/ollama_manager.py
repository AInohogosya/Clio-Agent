"""
Ollama Model Manager for VEXIS-1.1 AI Agent
Handles model validation, installation, and management
"""

import subprocess
import json
import time
from typing import Optional, List, Dict, Any
from ..utils.logger import get_logger
from .model_definitions import (
    MODEL_FAMILIES, PREDEFINED_MODELS, get_model_families, 
    get_subfamilies, get_models_in_subfamily, get_model_hierarchy_path, get_predefined_models
)
from .ollama_error_handler import handle_ollama_error


class OllamaManager:
    """Manages Ollama models with validation and installation"""
    
    def get_model_families(self):
        """Get available model families with names and descriptions"""
        return get_model_families()
    
    def __init__(self):
        self.logger = get_logger("ollama_manager")
        self.endpoint = "http://localhost:11434"
    
    def check_ollama_available(self) -> bool:
        """Check if Ollama is available and running"""
        try:
            response = subprocess.run(["ollama", "--version"], 
                                    capture_output=True, text=True, timeout=10)
            return response.returncode == 0
        except Exception:
            return False
    
    def get_installed_models(self) -> List[str]:
        """Get list of installed models"""
        try:
            response = subprocess.run(["ollama", "list"], 
                                    capture_output=True, text=True, timeout=30)
            if response.returncode == 0:
                lines = response.stdout.strip().split('\n')
                models = []
                for line in lines[1:]:  # Skip header line
                    if line.strip():
                        # Extract model name from the first column
                        model_name = line.split()[0]
                        models.append(model_name)
                return models
            else:
                self.logger.error(f"Failed to list models: {response.stderr}")
                return []
        except Exception as e:
            self.logger.error(f"Error listing models: {e}")
            return []
    
    def is_model_installed(self, model_name: str) -> bool:
        """Check if a specific model is installed"""
        installed_models = self.get_installed_models()
        return model_name in installed_models
    
    def validate_model(self, model_name: str) -> Dict[str, Any]:
        """Validate if a model name exists and is available"""
        result = {
            "valid": False,
            "installed": False,
            "error": None,
            "model_name": model_name
        }
        
        # Check if it's a predefined model
        is_predefined = model_name in PREDEFINED_MODELS
        
        # Check if model is installed
        installed = self.is_model_installed(model_name)
        result["installed"] = installed
        
        if installed:
            result["valid"] = True
        elif is_predefined:
            # Predefined models are considered valid even if not installed
            result["valid"] = True
        else:
            # For custom models, try to validate by checking if it exists in Ollama
            try:
                # Try to get model info to see if it exists
                response = subprocess.run(["ollama", "show", model_name], 
                                        capture_output=True, text=True, timeout=30)
                if response.returncode == 0:
                    result["valid"] = True
                else:
                    result["error"] = f"Model '{model_name}' not found in Ollama"
            except Exception as e:
                result["error"] = f"Error validating model: {e}"
        
        return result
    
    def install_model(self, model_name: str) -> Dict[str, Any]:
        """Install a model if not already installed"""
        result = {
            "success": False,
            "installed": False,
            "error": None,
            "model_name": model_name
        }
        
        # Check if already installed
        if self.is_model_installed(model_name):
            result["success"] = True
            result["installed"] = True
            return result
        
        # Validate model first
        validation = self.validate_model(model_name)
        if not validation["valid"]:
            result["error"] = validation.get("error", "Invalid model name")
            return result
        
        try:
            self.logger.info(f"Installing model: {model_name}")
            print(f"Installing {model_name}... This may take a few minutes.")
            
            # Run ollama pull
            process = subprocess.Popen(
                ["ollama", "pull", model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                universal_newlines=True
            )
            
            # Show progress
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    print(f"\r{output.strip()}", end="", flush=True)
            
            # Get the final result
            return_code = process.poll()
            
            if return_code == 0:
                result["success"] = True
                result["installed"] = True
                print(f"\n✓ Successfully installed {model_name}")
                self.logger.info(f"Successfully installed model: {model_name}")
            else:
                stderr = process.stderr.read()
                result["error"] = f"Installation failed: {stderr}"
                print(f"\n✗ Failed to install {model_name}: {stderr}")
                self.logger.error(f"Failed to install model {model_name}: {stderr}")
                
                # Use enhanced error handling for installation failures
                context = {
                    'model_name': model_name,
                    'operation': 'install_model'
                }
                try:
                    handle_ollama_error(stderr or "Installation failed", context, display_to_user=True)
                except Exception as handler_error:
                    self.logger.warning(f"Enhanced error handler failed: {handler_error}")
                
        except subprocess.TimeoutExpired:
            result["error"] = "Installation timed out"
            print(f"\n✗ Installation of {model_name} timed out")
            
            # Use enhanced error handling for timeout
            context = {
                'model_name': model_name,
                'operation': 'install_model'
            }
            try:
                handle_ollama_error(f"Installation of {model_name} timed out", context, display_to_user=True)
            except Exception as handler_error:
                self.logger.warning(f"Enhanced error handler failed: {handler_error}")
                
        except Exception as e:
            result["error"] = f"Installation error: {e}"
            print(f"\n✗ Error installing {model_name}: {e}")
            self.logger.error(f"Error installing model {model_name}: {e}")
            
            # Use enhanced error handling for general errors
            context = {
                'model_name': model_name,
                'operation': 'install_model'
            }
            try:
                handle_ollama_error(str(e), context, display_to_user=True)
            except Exception as handler_error:
                self.logger.warning(f"Enhanced error handler failed: {handler_error}")
        
        return result
    
    def get_subfamilies(self, family_key: str):
        """Get subfamilies for a specific model family"""
        return get_subfamilies(family_key)
    
    def get_models_in_subfamily(self, family_key: str, subfamily_key: str):
        """Get models in a specific subfamily"""
        return get_models_in_subfamily(family_key, subfamily_key)
    
    def display_model_families(self) -> Optional[str]:
        """Display available model families using curses-based arrow key menu"""
        try:
            from .curses_menu import get_curses_menu
            
            families = self.get_model_families()
            menu = get_curses_menu(
                "🤖 Select Model Family",
                "Choose the AI model family you want to explore:"
            )
            
            for key, data in families.items():
                icon = data.get("icon", "🤖")
                menu.add_item(
                    data['name'],
                    data['description'],
                    key,
                    icon
                )
            
            return menu.show()
            
        except ImportError:
            # If curses not available, return None (no fallback to numbers)
            return None
    
    def _test_cursor_positioning(self) -> bool:
        """Test if cursor positioning works in current terminal"""
        try:
            # Simple test: try to position cursor and check if it works
            print("\033[2J\033[H", end="", flush=True)  # Clear screen
            print("Test line 1")
            print("\033[5;0H", end="", flush=True)  # Move to line 5
            print("Test line 5")
            
            # If cursor positioning works, we should see "Test line 5" below
            # But we can't easily detect this programmatically
            # So we'll use a heuristic based on terminal type
            
            import os
            term = os.environ.get('TERM', '').lower()
            
            # Common terminals that don't support cursor positioning well
            problematic_terms = ['linux', 'vt100', 'ansi', 'dumb']
            
            # If it's a known problematic terminal, use fallback
            if any(pt in term for pt in problematic_terms):
                return False
            
            # Also use fallback if TERM is very basic
            if len(term) < 3 or term == 'unknown':
                return False
            
            return True  # Assume cursor positioning works
            
        except Exception:
            return True  # Safer to use fallback if there's any error
    
    def _fallback_model_families(self) -> Optional[str]:
        """Fallback method using original number-based selection"""
        families = self.get_model_families()
        
        print("\n=== Available Model Families ===")
        family_keys = list(families.keys())
        
        for i, (key, data) in enumerate(families.items(), 1):
            icon = data.get("icon", "🤖")
            print(f"{i}. {icon} {data['name']} - {data['description']}")
        
        while True:
            try:
                choice = input(f"\nSelect model family (1-{len(family_keys)}) or 'q' to quit: ").strip()
                if choice.lower() == 'q':
                    return None
                
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(family_keys):
                    selected_key = family_keys[choice_idx]
                    print(f"\nSelected: {families[selected_key]['name']}")
                    return selected_key
                else:
                    print("Invalid selection. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number.")
    
    def display_subfamilies(self, family_key: str) -> Optional[str]:
        """Display subfamilies using curses-based arrow key menu"""
        try:
            from .curses_menu import get_curses_menu
            
            subfamilies = self.get_subfamilies(family_key)
            if not subfamilies:
                return None
            
            families = self.get_model_families()
            family_name = families[family_key]['name']
            menu = get_curses_menu(
                f"🔷 {family_name} Subfamilies",
                f"Choose the {family_name} model series:"
            )
            
            for key, data in subfamilies.items():
                icon = data.get("icon", "🌐")
                menu.add_item(
                    data['name'],
                    data['description'],
                    key,
                    icon
                )
            
            return menu.show()
            
        except ImportError:
            return None
    
    def _fallback_subfamilies(self, family_key: str) -> Optional[str]:
        """Fallback method using original number-based selection"""
        subfamilies = self.get_subfamilies(family_key)
        if not subfamilies:
            return None
        
        families = self.get_model_families()
        family_name = families[family_key]['name']
        print(f"\n=== {family_name} Subfamilies ===")
        subfamily_keys = list(subfamilies.keys())
        
        for i, (key, data) in enumerate(subfamilies.items(), 1):
            icon = data.get("icon", "🌐")
            print(f"{i}. {icon} {data['name']} - {data['description']}")
        
        while True:
            try:
                choice = input(f"\nSelect subfamily (1-{len(subfamily_keys)}) or 'q' to quit: ").strip()
                if choice.lower() == 'q':
                    return None
                
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(subfamily_keys):
                    selected_key = subfamily_keys[choice_idx]
                    print(f"\nSelected: {subfamilies[selected_key]['name']}")
                    return selected_key
                else:
                    print("Invalid selection. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number.")
    
    def display_models_in_subfamily(self, family_key: str, subfamily_key: str) -> Optional[str]:
        """Display models using curses-based arrow key menu"""
        try:
            from .curses_menu import get_curses_menu
            
            models = self.get_models_in_subfamily(family_key, subfamily_key)
            if not models:
                return None
            
            subfamilies = self.get_subfamilies(family_key)
            subfamily_name = subfamilies[subfamily_key]["name"]
            menu = get_curses_menu(
                f"🧠 {subfamily_name} Models",
                f"Select your preferred {subfamily_name} model:"
            )
            
            for key, model_data in models.items():
                icon = model_data.get("icon", "🧠")
                description = model_data.get("desc", key)
                
                menu.add_item(
                    model_data.get("name", key),
                    description,
                    key,
                    icon
                )
            
            return menu.show()
            
        except ImportError:
            return None
    
    def _fallback_models_in_subfamily(self, family_key: str, subfamily_key: str) -> Optional[str]:
        """Fallback method using original number-based selection"""
        models = self.get_models_in_subfamily(family_key, subfamily_key)
        if not models:
            return None
        
        subfamilies = self.get_subfamilies(family_key)
        subfamily_name = subfamilies[subfamily_key]["name"]
        print(f"\n=== {subfamily_name} Models ===")
        model_keys = list(models.keys())
        
        for i, (key, model_data) in enumerate(models.items(), 1):
            icon = model_data.get("icon", "🧠")
            name = model_data.get("name", key)
            description = model_data.get("desc", key)
            print(f"{i}. {icon} {name} - {description}")
        
        while True:
            try:
                choice = input(f"\nSelect model (1-{len(model_keys)}) or 'q' to quit: ").strip()
                if choice.lower() == 'q':
                    return None
                
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(model_keys):
                    selected_key = model_keys[choice_idx]
                    selected_model = models[selected_key]
                    print(f"\nSelected: {selected_model.get('name', selected_key)}")
                    print(f"Description: {selected_model.get('desc', selected_key)}")
                    return selected_key
                else:
                    print("Invalid selection. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number.")
    
    def demo_hierarchical_selection(self):
        """Demo method to show how hierarchical selection works"""
        print("🚀 Demo: Hierarchical Model Selection")
        print("This shows how the selection process works step by step.\n")
        
        # Show the structure first
        print("📋 Available Model Structure:")
        families = self.get_model_families()
        for family_key, family_data in families.items():
            icon = family_data.get("icon", "🤖")
            print(f"  📁 {icon} {family_data['name']} ({family_key})")
            subfamilies = self.get_subfamilies(family_key)
            for subfamily_key, subfamily_data in subfamilies.items():
                models = self.get_models_in_subfamily(family_key, subfamily_key)
                model_count = len(models) if models else 0
                subfamily_icon = subfamily_data.get("icon", "📂")
                print(f"    📂 {subfamily_icon} {subfamily_data['name']} ({subfamily_key}) - {model_count} models")
        
        print("\n" + "="*50)
        print("Now starting interactive selection...")
        print("="*50)
        
        # Start the interactive selection
        return self.interactive_model_selection()
    
    def interactive_model_selection(self) -> Optional[str]:
        """Interactive hierarchical model selection"""
        print("🤖 Welcome to Ollama Model Selection!")
        print("Let's choose the perfect model for your needs.\n")
        
        # Step 1: Select family
        family_key = self.display_model_families()
        if not family_key:
            print("Model selection cancelled.")
            return None
        
        # Step 2: Select subfamily
        subfamily_key = self.display_subfamilies(family_key)
        if not subfamily_key:
            print("Model selection cancelled.")
            return None
        
        # Step 3: Select specific model
        model_key = self.display_models_in_subfamily(family_key, subfamily_key)
        if not model_key:
            print("Model selection cancelled.")
            return None
        
        # Show final selection
        hierarchy = self.get_model_hierarchy_path(model_key)
        if hierarchy:
            print(f"\n🎉 Final Selection:")
            print(f"Family: {hierarchy['family_name']}")
            print(f"Subfamily: {hierarchy['subfamily_name']}")
            print(f"Model: {hierarchy['model']}")
            print(f"Description: {hierarchy['description']}")
        
        return model_key
    
    def get_model_hierarchy_path(self, model_name: str):
        """Get the hierarchy path for a specific model"""
        return get_model_hierarchy_path(model_name)
    
    def get_predefined_models(self) -> Dict[str, str]:
        """Get predefined models with descriptions"""
        return get_predefined_models()
    
    def suggest_model_installation(self, model_name: str) -> bool:
        """Prompt user to install a model if not installed"""
        if not self.is_model_installed(model_name):
            validation = self.validate_model(model_name)
            if validation["valid"]:
                print(f"\nModel '{model_name}' is not installed.")
                response = input("Would you like to install it now? (y/n): ").lower().strip()
                if response.startswith('y'):
                    install_result = self.install_model(model_name)
                    return install_result["success"]
                else:
                    print("Installation cancelled.")
                    return False
            else:
                print(f"\n✗ Invalid model: {validation.get('error', 'Unknown error')}")
                return False
        return True


# Global ollama manager instance
_ollama_manager: Optional[OllamaManager] = None


def get_ollama_manager() -> OllamaManager:
    """Get global ollama manager instance"""
    global _ollama_manager
    
    if _ollama_manager is None:
        _ollama_manager = OllamaManager()
    
    return _ollama_manager
