"""
Usage Examples for VEXIS Unified API

This file demonstrates how to use the unified API interface
with different LLM providers (Google Gemini, OpenAI).
"""

import os
from api import (
    LLMFactory, ProviderType, GenerationConfig, 
    ResponseFormat, create_client
)


def example_basic_generation():
    """Example: Basic text generation with different providers"""
    
    # Example with Google Gemini
    google_client = create_client(
        "google", 
        api_key=os.getenv("GOOGLE_API_KEY")
    )
    
    response = google_client.generate("Tell me a fun fact about space")
    if response.success:
        print(f"Google Response: {response.content}")
        print(f"Tokens used: {response.tokens_used}")
        print(f"Cost: ${response.cost}")
    else:
        print(f"Error: {response.error}")
    
    # Example with OpenAI
    openai_client = create_client(
        "openai",
        api_key=os.getenv("OPENAI_API_KEY")
    )
    
    response = openai_client.generate("Tell me a fun fact about space")
    if response.success:
        print(f"OpenAI Response: {response.content}")


def example_with_configuration():
    """Example: Using generation configuration"""
    
    client = create_client("google", api_key=os.getenv("GOOGLE_API_KEY"))
    
    # Configure generation parameters
    config = GenerationConfig(
        max_tokens=500,
        temperature=0.7,  # More deterministic
        top_p=0.95,
        system_instruction="You are a helpful science tutor. Be concise."
    )
    
    response = client.generate(
        "Explain quantum computing in simple terms",
        config=config
    )
    
    print(response.content)


def example_streaming():
    """Example: Streaming response"""
    
    client = create_client("openai", api_key=os.getenv("OPENAI_API_KEY"))
    
    print("Streaming response:")
    for chunk in client.generate_stream("Write a short poem about coding"):
        print(chunk, end="", flush=True)
    print()  # Newline at end


def example_async_usage():
    """Example: Async usage"""
    import asyncio
    
    async def main():
        client = create_client("google", api_key=os.getenv("GOOGLE_API_KEY"))
        
        # Async generation
        response = await client.generate_async("What is machine learning?")
        print(response.content)
        
        # Async streaming
        print("\nStreaming:")
        async for chunk in client.generate_stream_async("Count from 1 to 10"):
            print(chunk, end="", flush=True)
    
    asyncio.run(main())


def example_list_models():
    """Example: List available models"""
    
    client = create_client("google", api_key=os.getenv("GOOGLE_API_KEY"))
    
    models = client.list_models()
    print("Available models:")
    for model in models:
        print(f"  - {model.id}: {model.name}")
        print(f"    Context: {model.context_window} tokens")
        print(f"    Vision: {model.supports_vision}")


def example_model_info():
    """Example: Get specific model information"""
    
    client = create_client("openai", api_key=os.getenv("OPENAI_API_KEY"))
    
    info = client.get_model_info("gpt-4o")
    if info:
        print(f"Model: {info.name}")
        print(f"Context window: {info.context_window}")
        print(f"Max output: {info.max_output_tokens}")
        print(f"Capabilities: {', '.join(info.capabilities)}")


def example_count_tokens():
    """Example: Count tokens"""
    
    client = create_client("openai", api_key=os.getenv("OPENAI_API_KEY"))
    
    text = "Hello, world! This is a test sentence."
    count = client.count_tokens(text)
    print(f"Token count: {count}")


def example_factory_pattern():
    """Example: Using the factory pattern directly"""
    
    # Using factory with explicit ProviderType
    client = LLMFactory.create(
        ProviderType.GOOGLE,
        api_key=os.getenv("GOOGLE_API_KEY")
    )
    
    response = client.generate("Hello from factory pattern!")
    print(response.content)


def example_vision_input():
    """Example: Vision/multimodal input (for supported models)"""
    
    client = create_client("openai", api_key=os.getenv("OPENAI_API_KEY"))
    
    # Load image data
    with open("image.png", "rb") as f:
        image_data = f.read()
    
    # Generate with image
    config = GenerationConfig(max_tokens=1000)
    response = client.generate(
        "Describe this image in detail",
        config=config,
        image_data=image_data,
        image_format="png"
    )
    
    print(response.content)


def example_error_handling():
    """Example: Proper error handling"""
    
    try:
        # This will fail without API key
        client = create_client("openai", api_key="")
        
        response = client.generate("Hello")
        
        if not response.success:
            print(f"Generation failed: {response.error}")
            return
        
        print(f"Success: {response.content}")
        
    except ValueError as e:
        print(f"Configuration error: {e}")
    except ImportError as e:
        print(f"Missing dependency: {e}")


def example_json_mode():
    """Example: JSON response format"""
    
    client = create_client("openai", api_key=os.getenv("OPENAI_API_KEY"))
    
    config = GenerationConfig(
        max_tokens=500,
        temperature=0.1,
        response_format=ResponseFormat.JSON
    )
    
    response = client.generate(
        'Generate a JSON object with "name" and "age" fields',
        config=config
    )
    
    if response.success:
        print(f"JSON response: {response.content}")
        # Parse the JSON
        import json
        data = json.loads(response.content)
        print(f"Name: {data['name']}, Age: {data['age']}")


def example_provider_comparison():
    """Example: Compare responses from different providers"""
    
    prompt = "What is the capital of France?"
    
    # Test with Google
    google = create_client("google", api_key=os.getenv("GOOGLE_API_KEY"))
    google_response = google.generate(prompt)
    
    # Test with OpenAI
    openai = create_client("openai", api_key=os.getenv("OPENAI_API_KEY"))
    openai_response = openai.generate(prompt)
    
    print("Google:")
    print(f"  Content: {google_response.content}")
    print(f"  Latency: {google_response.latency:.2f}s")
    print(f"  Tokens: {google_response.tokens_used}")
    print(f"  Cost: ${google_response.cost}")
    
    print("\nOpenAI:")
    print(f"  Content: {openai_response.content}")
    print(f"  Latency: {openai_response.latency:.2f}s")
    print(f"  Tokens: {openai_response.tokens_used}")
    print(f"  Cost: ${openai_response.cost}")


if __name__ == "__main__":
    # Run examples
    print("=" * 50)
    print("VEXIS Unified API Examples")
    print("=" * 50)
    
    # Uncomment the examples you want to run:
    
    # example_basic_generation()
    # example_with_configuration()
    # example_streaming()
    # example_list_models()
    # example_factory_pattern()
    # example_provider_comparison()
    
    print("\nExamples loaded. Uncomment function calls in __main__ to run.")
