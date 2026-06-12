"""
Integration Example: Using the Unified API in main.py

This example shows how to integrate the new unified API structure
into the existing VEXIS codebase.
"""

import os
from typing import Optional, Dict, Any

# Import the unified API
from api import (
    LLMFactory, ProviderType, GenerationConfig, 
    LLMResponse, create_client
)


def simple_main_example():
    """
    Simple example of using the unified API in a main script.
    
    This replaces direct API calls with the unified interface.
    """
    
    # Get API keys from environment
    google_api_key = os.getenv("GOOGLE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    
    # Choose provider based on availability
    if google_api_key:
        print("Using Google Gemini")
        client = create_client("google", api_key=google_api_key)
    elif openai_api_key:
        print("Using OpenAI")
        client = create_client("openai", api_key=openai_api_key)
    else:
        print("No API key configured")
        return
    
    # Generate response
    response = client.generate("What is the capital of Japan?")
    
    if response.success:
        print(f"Answer: {response.content}")
        print(f"Model: {response.model}")
        print(f"Tokens: {response.tokens_used}")
    else:
        print(f"Error: {response.error}")


class AIAssistant:
    """
    Example class that uses the unified API.
    
    This could be integrated into the existing VEXIS architecture.
    """
    
    def __init__(self, provider: str = "google", api_key: Optional[str] = None):
        """
        Initialize the AI Assistant.
        
        Args:
            provider: Provider name ("google", "openai")
            api_key: API key (uses env var if not provided)
        """
        self.provider = provider
        
        # Get API key if not provided
        if not api_key:
            if provider == "google":
                api_key = os.getenv("GOOGLE_API_KEY")
            elif provider == "openai":
                api_key = os.getenv("OPENAI_API_KEY")
        
        # Create client
        self.client = create_client(provider, api_key=api_key)
        
        print(f"AI Assistant initialized with {provider}")
    
    def ask(self, question: str, **kwargs) -> str:
        """
        Ask a question and get a response.
        
        Args:
            question: The question to ask
            **kwargs: Additional generation options
            
        Returns:
            Response text
        """
        # Build configuration from kwargs
        config = GenerationConfig(
            max_tokens=kwargs.get("max_tokens", 1000),
            temperature=kwargs.get("temperature", 0.7),
            system_instruction=kwargs.get("system_instruction")
        )
        
        response = self.client.generate(question, config=config)
        
        if response.success:
            return response.content
        else:
            return f"Error: {response.error}"
    
    def stream_ask(self, question: str, **kwargs):
        """
        Ask a question and stream the response.
        
        Yields text chunks as they are generated.
        """
        config = GenerationConfig(
            max_tokens=kwargs.get("max_tokens", 1000),
            temperature=kwargs.get("temperature", 0.7)
        )
        
        for chunk in self.client.generate_stream(question, config=config):
            yield chunk


def integration_with_existing_config(config: Dict[str, Any]):
    """
    Example of integrating with existing VEXIS configuration.
    
    This shows how to bridge the existing config with the new API.
    """
    
    # Get preferred provider from config - must be explicitly set
    preferred_provider = config.get("preferred_provider")
    if not preferred_provider:
        raise ValueError("No preferred_provider configured. Please set it in your configuration.")
    
    # Map to unified API
    if preferred_provider == "google":
        api_key = config.get("google_api_key")
        client = create_client("google", api_key=api_key)
    elif preferred_provider == "openai":
        api_key = config.get("openai_api_key")
        client = create_client("openai", api_key=api_key)
    else:
        # Fall back to existing Ollama implementation
        from src.ai_agent.external_integration.ollama_provider import SimpleOllamaProvider
        
        endpoint = config.get("local_endpoint", "http://localhost:11434")
        client = SimpleOllamaProvider(endpoint=endpoint)
        
        # Note: Ollama doesn't use the unified interface yet
        # This would need an adapter to implement BaseLLM
        print("Using Ollama (existing implementation)")
        return None
    
    return client


def compare_providers_example():
    """
    Example: Compare responses from different providers.
    
    This demonstrates the unified interface's power - same code,
    different providers.
    """
    
    question = "What is artificial intelligence?"
    
    # Get API keys
    google_key = os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    # Test with both providers
    results = []
    
    if google_key:
        google_client = create_client("google", api_key=google_key)
        google_response = google_client.generate(question)
        results.append(("Google Gemini", google_response))
    
    if openai_key:
        openai_client = create_client("openai", api_key=openai_key)
        openai_response = openai_client.generate(question)
        results.append(("OpenAI", openai_response))
    
    # Display results
    print("=" * 60)
    print("Provider Comparison")
    print("=" * 60)
    
    for name, response in results:
        print(f"\n{name}:")
        print(f"  Model: {response.model}")
        print(f"  Latency: {response.latency:.2f}s")
        print(f"  Tokens: {response.tokens_used}")
        print(f"  Cost: ${response.cost}")
        print(f"  Response: {response.content[:200]}...")


def error_handling_example():
    """
    Example: Robust error handling with the unified API.
    """
    
    try:
        # Try to create client
        client = create_client("google", api_key="invalid-key")
        
        # Check if available
        if not client.is_available():
            print("Client not properly configured")
            return
        
        # Generate with error handling
        response = client.generate("Hello")
        
        if not response.success:
            print(f"Generation failed: {response.error}")
            return
        
        print(f"Success: {response.content}")
        
    except ValueError as e:
        print(f"Configuration error: {e}")
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Run: pip install google-genai")


def advanced_configuration_example():
    """
    Example: Advanced configuration options.
    """
    
    # Create client with custom settings
    client = LLMFactory.create(
        ProviderType.OPENAI,
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=120,  # Custom timeout
        max_retries=3,  # Custom retries
        organization="your-org-id",  # Organization ID
        project="your-project-id"  # Project ID
    )
    
    # Advanced generation config
    config = GenerationConfig(
        max_tokens=2000,
        temperature=0.5,
        top_p=0.9,
        top_k=40,
        stop_sequences=["END", "STOP"],
        seed=42,  # For reproducibility
        system_instruction="You are a precise technical assistant.",
        extra_params={
            # Provider-specific params passed through
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1
        }
    )
    
    response = client.generate(
        "Explain neural networks",
        config=config
    )
    
    print(response.content)


def integration_with_model_runner():
    """
    Example: How to integrate with existing ModelRunner.
    
    This shows how the new unified API could be used within
    the existing VEXIS ModelRunner class.
    """
    
    class UnifiedModelRunner:
        """
        Example of integrating unified API with ModelRunner pattern.
        """
        
        def __init__(self, config: Dict[str, Any]):
            self.config = config
            self.preferred_provider = config.get("preferred_provider")
            if not self.preferred_provider:
                raise ValueError("No preferred_provider configured. Please set it in your configuration.")
            
            # Initialize appropriate client
            if self.preferred_provider == "google":
                self.llm_client = create_client(
                    "google",
                    api_key=config.get("google_api_key")
                )
            elif self.preferred_provider == "openai":
                self.llm_client = create_client(
                    "openai",
                    api_key=config.get("openai_api_key")
                )
            else:
                self.llm_client = None
        
        def run_task(self, prompt: str, **kwargs) -> Dict[str, Any]:
            """
            Run a task using the unified API.
            
            This replaces the old ModelRunner.run_model() method.
            """
            if not self.llm_client:
                return {
                    "success": False,
                    "error": "No LLM client configured"
                }
            
            # Build config
            config = GenerationConfig(
                max_tokens=kwargs.get("max_tokens", 5000),
                temperature=kwargs.get("temperature", 1.0)
            )
            
            # Generate
            response = self.llm_client.generate(prompt, config=config)
            
            # Return in familiar format
            return {
                "success": response.success,
                "content": response.content,
                "model": response.model,
                "provider": response.provider,
                "tokens_used": response.tokens_used,
                "cost": response.cost,
                "latency": response.latency,
                "error": response.error
            }
    
    # Example usage
    config = {
        "preferred_provider": "google",
        "google_api_key": os.getenv("GOOGLE_API_KEY")
    }
    
    runner = UnifiedModelRunner(config)
    result = runner.run_task("Generate a list of CLI commands for file management")
    
    if result["success"]:
        print(f"Result: {result['content']}")
        print(f"Model: {result['model']}")
        print(f"Latency: {result['latency']:.2f}s")
    else:
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    print("=" * 60)
    print("VEXIS Unified API Integration Examples")
    print("=" * 60)
    
    # Uncomment examples to run:
    
    # simple_main_example()
    
    # assistant = AIAssistant(provider="google")
    # print(assistant.ask("What is Python?"))
    
    # compare_providers_example()
    
    # error_handling_example()
    
    # integration_with_model_runner()
    
    print("\nExamples loaded. Uncomment function calls to run.")
