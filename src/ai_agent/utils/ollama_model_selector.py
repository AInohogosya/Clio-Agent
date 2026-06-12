"""
Ollama Model Selection UI for VEXIS-1.1 AI Agent
Using curses-based arrow key navigation
"""

from typing import Optional
try:
    from .curses_menu import get_curses_hierarchical_menu, success_message, error_message, warning_message
except ImportError:
    # Fallback for direct execution
    from curses_menu import get_curses_hierarchical_menu, success_message, error_message, warning_message


def select_ollama_model() -> Optional[str]:
    """Interactive hierarchical menu for selecting Ollama models using arrow keys"""
    from .settings_manager import get_settings_manager
    
    settings_manager = get_settings_manager()
    
    # Use curses-based hierarchical selector
    try:
        print("🦊 Initializing Ollama model selector...")
        selector = get_curses_hierarchical_menu()
        print("✓ Selector initialized, showing menu...")
        
        selected_model = selector.show()
        
        if selected_model is None:
            print("⚠ No model selected (user cancelled or selection failed)")
            return settings_manager.get_ollama_model()
        
        # Save selection
        settings_manager.set_ollama_model(selected_model)
        success_message(f"Selected model: {selected_model}")
        return selected_model
        
    except ImportError as e:
        error_message(f"Curses menu not available: {e}")
        return None
    except Exception as e:
        error_message(f"Selection failed: {e}")
        import traceback
        traceback.print_exc()
        # Fallback: show current model
        current_model = settings_manager.get_ollama_model()
        warning_message(f"Using current model: {current_model}")
        return current_model
