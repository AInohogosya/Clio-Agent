#!/usr/bin/env python3
"""
Groq Provider Setup and Configuration
Handles Groq API key prompting, model selection, and provider configuration
"""

import getpass
from typing import Optional, Tuple

def prompt_for_groq_api_key():
    """Prompt user for Groq API key and handle saving"""
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    
    print(f"\n{Colors.BOLD}{Colors.CYAN}Groq API Key Setup{Colors.RESET}")
    print(f"{Colors.CYAN}{'-' * 25}{Colors.RESET}")
    print(f"{Colors.WHITE}To use Groq's high-speed inference API, you need an API key.{Colors.RESET}")
    print(f"{Colors.BRIGHT_CYAN}You can get one from: https://console.groq.com/keys{Colors.RESET}")
    print()
    
    while True:
        try:
            api_key = getpass.getpass(f"{Colors.YELLOW}Enter your Groq API key (or press Enter to cancel):{Colors.RESET} ")
            if not api_key.strip():
                warning_message("No API key provided. Skipping Groq API setup.")
                return None
            
            # Basic validation (Groq API keys are typically 39 characters starting with 'gsk_')
            if len(api_key) < 20 or not api_key.startswith('gsk_'):
                error_message("Invalid API key format. Groq API keys start with 'gsk_'")
                continue
            
            # Ask if user wants to save the key
            save_key = input(f"{Colors.CYAN}Save this API key for future use? (y/n):{Colors.RESET} ").lower().strip()
            should_save = save_key.startswith('y')
            
            return api_key, should_save
            
        except KeyboardInterrupt:
            print(f"\n{Colors.BRIGHT_YELLOW}Operation cancelled.{Colors.RESET}")
            return None
        except Exception as e:
            error_message(f"Error reading input: {e}")
            return None

def select_groq_model():
    """Prompt user to select Groq model using curses arrow keys"""
    from ai_agent.utils.settings_manager import get_settings_manager
    from ai_agent.utils.curses_menu import get_curses_menu
    
    settings_manager = get_settings_manager()
    current_model = settings_manager.get_groq_model()
    
    # Use curses-based menu with arrow keys
    menu = get_curses_menu(
        "⚡ Select Groq Model",
        "Choose your preferred Groq model:"
    )
    
    menu.add_item(
        "Llama 3.1 8B Instant",
        "Fast inference • 8B parameters • 128K context",
        "llama-3.1-8b-instant",
        "⚡"
    )
    
    menu.add_item(
        "Llama 3.3 70B Versatile",
        "High performance • 70B parameters • 128K context",
        "llama-3.3-70b-versatile",
        "🧠"
    )
    
    menu.add_item(
        "GPT-OSS 120B",
        "OpenAI's flagship • Browser search • Code execution • 120B parameters",
        "openai/gpt-oss-120b",
        "👑"
    )
    
    menu.add_item(
        "GPT-OSS 20B",
        "OpenAI's efficient model • Browser search • Code execution • 20B parameters",
        "openai/gpt-oss-20b",
        "🚀"
    )
    
    selected_model = menu.show()
    
    if selected_model is None:
        return current_model
    
    settings_manager.set_groq_model(selected_model)
    return selected_model

def configure_groq_provider():
    """Configure Groq provider with API key and model selection"""
    from ai_agent.utils.settings_manager import get_settings_manager
    from ai_agent.utils.interactive_menu import Colors
    
    settings_manager = get_settings_manager()
    
    # Check if API key already exists
    if not settings_manager.has_groq_api_key():
        # Prompt for API key
        result = prompt_for_groq_api_key()
        if result is None:
            return None, None
        
        api_key, should_save = result
        settings_manager.set_groq_api_key(api_key, should_save)
    
    
    # Select model
    model = select_groq_model()
    if model is None:
        model = settings_manager.get_groq_model()
    
    settings_manager.set_preferred_provider("groq")
    return "groq", model
