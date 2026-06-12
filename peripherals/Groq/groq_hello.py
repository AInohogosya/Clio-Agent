import os
from groq import Groq
from groq_models import GROQ_MODEL_FAMILIES, PRODUCTION_MODELS, PREVIEW_MODELS, get_model_info

def get_initial_selection():
    """Prompt user to select initial provider (Ollama, Google, or Groq)"""
    print("Please select your initial provider:")
    print("1. Ollama")
    print("2. Google") 
    print("3. Groq")
    
    while True:
        choice = input("Enter your choice (1-3): ").strip()
        if choice == "1":
            return "ollama"
        elif choice == "2":
            return "google"
        elif choice == "3":
            return "groq"
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")

def select_company():
    """Let user select a company/model family"""
    print("\n" + "="*60)
    print("SELECT MODEL COMPANY/FAMILY")
    print("="*60)
    
    # Sort companies by priority
    sorted_companies = sorted(
        GROQ_MODEL_FAMILIES.items(), 
        key=lambda x: x[1]["priority"]
    )
    
    for i, (company_key, company_data) in enumerate(sorted_companies, 1):
        icon = company_data["icon"]
        name = company_data["name"]
        desc = company_data["description"]
        print(f"{i}. {icon} {name}")
        print(f"   {desc}")
        print()
    
    while True:
        try:
            choice = input(f"Enter company choice (1-{len(sorted_companies)}): ").strip()
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(sorted_companies):
                return list(sorted_companies)[choice_idx]
            else:
                print(f"Invalid choice. Please enter 1-{len(sorted_companies)}.")
        except ValueError:
            print("Please enter a valid number.")

def select_subfamily(company_key, company_data):
    """Let user select a subfamily within the chosen company"""
    print(f"\n" + "="*60)
    print(f"SELECT {company_data['name'].upper()} SUBFAMILY")
    print("="*60)
    
    subfamilies = company_data["subfamilies"]
    
    for i, (sub_key, sub_data) in enumerate(subfamilies.items(), 1):
        icon = sub_data["icon"]
        name = sub_data["name"]
        desc = sub_data["description"]
        print(f"{i}. {icon} {name}")
        print(f"   {desc}")
        print()
    
    while True:
        try:
            choice = input(f"Enter subfamily choice (1-{len(subfamilies)}): ").strip()
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(subfamilies):
                return list(subfamilies.items())[choice_idx]
            else:
                print(f"Invalid choice. Please enter 1-{len(subfamilies)}.")
        except ValueError:
            print("Please enter a valid number.")

def select_model(subfamily_key, subfamily_data):
    """Let user select a specific model within the subfamily"""
    print(f"\n" + "="*60)
    print(f"SELECT {subfamily_data['name'].upper()} MODEL")
    print("="*60)
    
    models = subfamily_data["models"]
    
    for i, (model_key, model_data) in enumerate(models.items(), 1):
        icon = model_data["icon"]
        name = model_data["name"]
        desc = model_data["desc"]
        
        # Mark production vs preview models
        status = ""
        if model_key in PRODUCTION_MODELS:
            status = " 🟢 PRODUCTION"
        elif model_key in PREVIEW_MODELS:
            status = " 🟡 PREVIEW"
            
        print(f"{i}. {icon} {name}{status}")
        print(f"   {desc}")
        print(f"   Model ID: {model_key}")
        print()
    
    while True:
        try:
            choice = input(f"Enter model choice (1-{len(models)}): ").strip()
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(models):
                return list(models.items())[choice_idx]
            else:
                print(f"Invalid choice. Please enter 1-{len(models)}.")
        except ValueError:
            print("Please enter a valid number.")

def progressive_model_selection():
    """Guide user through progressive model selection"""
    print("\n🔍 GROQ MODEL SELECTION")
    print("Let's choose your model step by step...")
    
    # Step 1: Select company
    company_key, company_data = select_company()
    
    # Step 2: Select subfamily
    subfamily_key, subfamily_data = select_subfamily(company_key, company_data)
    
    # Step 3: Select specific model
    model_key, model_data = select_model(subfamily_key, subfamily_data)
    
    return model_key, model_data

def ask_groq(message, model="llama-3.1-8b-instant"):
    """Send message to Groq API and return response"""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Please set GROQ_API_KEY environment variable")
    
    client = Groq(api_key=api_key)
    
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": message}],
            model=model,
            temperature=0.7,
            max_tokens=1000,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"Groq API error: {e}")

if __name__ == "__main__":
    print("Welcome to VEXIS Groq Integration!")
    print("This program allows you to call GPT-OSS 120B via Groq.")
    print()
    
    # Get initial selection
    initial_provider = get_initial_selection()
    print(f"\nYou selected: {initial_provider.upper()}")
    print("Note: This is your initial selection setting.")
    print()
    
    # Model selection
    print("\nWould you like to:")
    print("1. Use default model (llama-3.1-8b-instant)")
    print("2. Choose model interactively")
    
    while True:
        model_choice = input("Enter choice (1-2): ").strip()
        if model_choice == "1":
            selected_model = "llama-3.1-8b-instant"
            print(f"Using default model: {selected_model}")
            break
        elif model_choice == "2":
            selected_model, model_data = progressive_model_selection()
            print(f"\n✅ Selected model: {model_data['name']}")
            print(f"   Description: {model_data['desc']}")
            print(f"   Model ID: {selected_model}")
            break
        else:
            print("Invalid choice. Please enter 1 or 2.")
    
    print()
    
    # For now, we'll still use Groq API regardless of initial selection
    # In a full implementation, you would route to different APIs based on selection
    print("Testing Groq API connection...")
    try:
        response = ask_groq("Hello! Please introduce yourself and your capabilities.", model=selected_model)
        print(f"\n🤖 Response from {model_data['name'] if 'model_data' in locals() else selected_model}:")
        print("-" * 60)
        print(response)
        print("-" * 60)
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n💡 Tip: Make sure your GROQ_API_KEY is set correctly:")
        print("   export GROQ_API_KEY='your_api_key_here'")
