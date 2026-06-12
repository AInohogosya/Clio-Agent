#!/usr/bin/env python3
"""
Example usage of the hierarchical Ollama model selection
"""

# This is how you would use the hierarchical selection in your code:

def example_usage():
    """Example of how to use the hierarchical model selection"""
    
    # Import the manager (when dependencies are resolved)
    # from ai_agent.utils.ollama_manager import OllamaManager
    
    print("📋 Example: Using Hierarchical Model Selection")
    print("=" * 50)
    
    # Create manager instance
    # manager = OllamaManager()
    
    print("""
# Method 1: Interactive selection (step-by-step)
selected_model = manager.interactive_model_selection()
if selected_model:
    print(f"User selected: {selected_model}")
    # Proceed with model installation or usage
    
# Method 2: Demo mode (shows structure then interactive)
selected_model = manager.demo_hierarchical_selection()

# Method 3: Manual step-by-step control
family = manager.display_model_families()  # Show: Google, Microsoft, Alibaba, Meta, Other
if family:
    subfamily = manager.display_subfamilies(family)  # Show: Phi-3, Phi-4 (for Microsoft)
    if subfamily:
        model = manager.display_models_in_subfamily(family, subfamily)  # Show: phi3:mini, phi3:medium, etc.
        if model:
            print(f"Final selection: {model}")

# Method 4: Get hierarchy info for a known model
hierarchy = manager.get_model_hierarchy_path("phi3:mini")
print(hierarchy)
# Output: {'family': 'microsoft', 'family_name': 'Microsoft', 
#          'subfamily': 'phi3', 'subfamily_name': 'Phi-3',
#          'model': 'phi3:mini', 'description': '3.8B parameters • Lightweight • 4K context'}

# Method 5: Browse structure programmatically
families = manager.get_model_families()
for family_key, family_info in families.items():
    print(f"Family: {family_info['name']}")
    subfamilies = manager.get_subfamilies(family_key)
    for subfamily_key, subfamily_info in subfamilies.items():
        models = manager.get_models_in_subfamily(family_key, subfamily_key)
        print(f"  {subfamily_info['name']}: {len(models)} models")
""")

if __name__ == "__main__":
    example_usage()
