#!/usr/bin/env python3
"""
Model Availability Checker for VEXIS-CLI
Checks if required models are available and provides helpful guidance
"""

import subprocess
import sys
import json
from typing import List, Dict, Optional
from pathlib import Path


class ModelChecker:
    """Check availability of AI models"""
    
    def __init__(self):
        self.ollama_available = self._check_ollama_installation()
        self.available_models = self._get_available_models() if self.ollama_available else []
    
    def _check_ollama_installation(self) -> bool:
        """Check if Ollama is installed"""
        try:
            result = subprocess.run(['ollama', '--version'], 
                                  capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _get_available_models(self) -> List[str]:
        """Get list of available Ollama models"""
        try:
            result = subprocess.run(['ollama', 'list'], 
                                  capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[1:]  # Skip header
                models = []
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if parts:
                            model_name = parts[0]
                            # Normalize model name (add :latest if no tag)
                            if ':' not in model_name:
                                model_name = f"{model_name}:latest"
                            models.append(model_name)
                return models
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        return []
    
    def check_model(self, model_name: str) -> Dict[str, any]:
        """Check if a specific model is available"""
        # Normalize the requested model name
        normalized_name = model_name
        if ':' not in normalized_name:
            normalized_name = f"{normalized_name}:latest"
        
        # Check against normalized available models
        available_normalized = []
        for available_model in self.available_models:
            if ':' not in available_model:
                available_normalized.append(f"{available_model}:latest")
            else:
                available_normalized.append(available_model)
        
        return {
            'model': model_name,
            'available': normalized_name in available_normalized or model_name in self.available_models,
            'ollama_installed': self.ollama_available,
            'suggestions': self._get_model_suggestions(model_name)
        }
    
    def _get_model_suggestions(self, model_name: str) -> List[str]:
        """Get suggestions for missing models"""
        suggestions = []
        
        if not self.ollama_available:
            suggestions.append("Install Ollama: curl -fsSL https://ollama.com/install.sh | sh")
            return suggestions
        
        # Common model alternatives
        model_families = {
            'llama': ['llama3.2:latest', 'llama3.2:3b', 'llama3.2:1b'],
            'gemma': ['gemma2:2b', 'gemma3:4b'],
            'phi': ['phi3:mini', 'phi3:small'],
            'qwen': ['qwen2.5:7b', 'qwen2.5:3b']
        }
        
        # Find family and suggest alternatives
        for family, models in model_families.items():
            if family in model_name.lower():
                for alt_model in models:
                    if alt_model in self.available_models:
                        suggestions.append(f"Use available model: {alt_model}")
                    else:
                        suggestions.append(f"Install model: ollama pull {alt_model}")
                break
        
        if not suggestions:
            # Generic suggestions
            suggestions.append("Install a lightweight model: ollama pull llama3.2:1b")
            suggestions.append("Or install a capable model: ollama pull llama3.2:3b")
        
        return suggestions
    
    def get_status_report(self) -> str:
        """Get comprehensive status report"""
        report = []
        
        if not self.ollama_available:
            report.append("❌ Ollama is not installed")
            report.append("Install with: curl -fsSL https://ollama.com/install.sh | sh")
        else:
            report.append("✅ Ollama is installed")
            
            if self.available_models:
                report.append(f"✅ Found {len(self.available_models)} models:")
                for model in self.available_models[:5]:  # Show first 5
                    report.append(f"  - {model}")
                if len(self.available_models) > 5:
                    report.append(f"  ... and {len(self.available_models) - 5} more")
            else:
                report.append("⚠️  No models found")
                report.append("Install a model: ollama pull llama3.2:3b")
        
        return '\n'.join(report)


def main():
    """Main function for standalone usage"""
    if len(sys.argv) > 1:
        # Check specific model
        model_name = sys.argv[1]
        checker = ModelChecker()
        result = checker.check_model(model_name)
        
        print(f"Model: {result['model']}")
        print(f"Available: {'✅' if result['available'] else '❌'}")
        print(f"Ollama Installed: {'✅' if result['ollama_installed'] else '❌'}")
        
        if result['suggestions']:
            print("\nSuggestions:")
            for suggestion in result['suggestions']:
                print(f"  • {suggestion}")
    else:
        # Show status report
        checker = ModelChecker()
        print("=== Model Status Report ===")
        print(checker.get_status_report())


if __name__ == "__main__":
    main()