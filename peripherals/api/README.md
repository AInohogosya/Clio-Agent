# VEXIS Unified LLM API

A unified, provider-agnostic interface for Large Language Model APIs supporting 13+ providers including Google Gemini, OpenAI, Anthropic, xAI, Meta, Mistral AI, Microsoft Azure, Amazon Bedrock, Cohere, DeepSeek, Groq, Together AI, and Ollama.

## Directory Structure

```
api/
├── __init__.py          # Package initialization and exports
├── base.py              # Abstract base class and shared types
├── google_client.py     # Google Gemini adapter (google-genai SDK)
├── openai_client.py     # OpenAI adapter (openai SDK)
├── anthropic_client.py  # Anthropic Claude adapter (anthropic SDK)
├── xai_client.py        # xAI Grok adapter (OpenAI-compatible)
├── meta_client.py       # Meta Llama adapter (OpenAI-compatible)
├── mistral_client.py    # Mistral AI adapter (mistralai SDK)
├── microsoft_client.py  # Microsoft Azure adapter (openai SDK)
├── amazon_client.py     # Amazon Bedrock adapter (boto3)
├── cohere_client.py     # Cohere adapter (cohere SDK)
├── deepseek_client.py   # DeepSeek adapter (OpenAI-compatible)
├── groq_client.py       # Groq adapter (OpenAI-compatible)
├── together_client.py   # Together AI adapter (OpenAI-compatible)
└── usage_example.py     # Usage examples and demonstrations
```

## Quick Start

```python
from api import create_client, GenerationConfig

# Create a client
client = create_client("google", api_key="your-api-key")

# Generate text
response = client.generate("Hello, how are you?")
print(response.content)
```

## Architecture

### BaseLLM (Abstract Interface)

All LLM clients inherit from `BaseLLM` which defines the common interface:

```python
class BaseLLM(ABC):
    @abstractmethod
    def generate(self, prompt: str, config: GenerationConfig) -> LLMResponse:
        pass
    
    @abstractmethod
    def generate_stream(self, prompt: str, config: GenerationConfig) -> Iterator[str]:
        pass
```

### Unified Types

- **GenerationConfig**: Normalizes parameters across providers (max_tokens, temperature, etc.)
- **LLMResponse**: Standardized response format with metadata
- **ModelInfo**: Model capabilities and specifications
- **ProviderType**: Enum for supported providers

### Adapter Pattern

Each provider implements the BaseLLM interface while handling provider-specific details:

| Unified Param | Google | OpenAI |
|--------------|--------|--------|
| max_tokens | max_output_tokens | max_tokens |
| temperature | temperature (0-2) | temperature (0-2) |
| system_instruction | system_instruction | messages[0] (role=system) |
| stop_sequences | stop_sequences | stop |

## Supported Providers

### Google Gemini (google-genai)

**Installation:**
```bash
pip install google-genai
```

**Environment Variables:**
- `GOOGLE_API_KEY` or `GEMINI_API_KEY`

**Latest Models:**
- `gemini-2.5-flash` (default) - Fast, efficient
- `gemini-2.5-pro` - Advanced reasoning
- `gemini-2.0-flash` - Balanced performance

**Features:**
- 1M token context window
- Vision support
- Streaming
- JSON mode

### OpenAI

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `OPENAI_API_KEY`

**Latest Models:**
- `gpt-5.4` (default) - Latest flagship
- `gpt-5.4-pro` - Research-grade
- `gpt-5.4-mini` (New) - Cost-optimized reasoning
- `gpt-5.4-nano` (New) - Ultra-lightweight
- `gpt-4o` - Multimodal

**Features:**
- 1M context window
- Vision support
- Function calling
- JSON mode
- Streaming

### Anthropic Claude

**Installation:**
```bash
pip install anthropic
```

**Environment Variables:**
- `ANTHROPIC_API_KEY`

**Latest Models:**
- `claude-opus-4-6-20251120` (default) - Most capable
- `claude-sonnet-4-6-20251120` - Balanced
- `claude-haiku-4-2025-01-15` - Fast

**Features:**
- 200K context window
- Strong reasoning
- Streaming

### xAI Grok

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `XAI_API_KEY`

**Latest Models:**
- `grok-4-2025-11-04` (default) - Latest flagship
- `grok-4-mini` - Efficient
- `grok-3-mini` - Fast

**Features:**
- 32K context window
- Real-time knowledge
- Streaming

### Meta Llama

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `META_API_KEY`

**Latest Models:**
- `llama-4-scout-17b-16e-instruct` (default) - Efficient
- `llama-4-maverick-17b-128e-instruct` - Powerful
- `llama-3.3-70b-instruct` - Strong performance

**Features:**
- 1M context window
- Latest Llama models
- Streaming

### Mistral AI

**Installation:**
```bash
pip install mistralai
```

**Environment Variables:**
- `MISTRAL_API_KEY`

**Latest Models:**
- `mistral-large-3` (default) - Latest flagship
- `mistral-small-3.1` - Efficient
- `codestral-2501` - Code generation
- `pixtral-large-2411` - Vision

**Features:**
- 1M context window
- Multilingual
- Vision support
- Streaming

### Microsoft Azure OpenAI

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`

**Latest Models:**
- `gpt-5.4` (default) - Latest flagship
- `gpt-5.4-pro` - Research-grade
- `gpt-5.4-mini` (New) - Cost-optimized
- `gpt-5.4-nano` (New) - Ultra-lightweight
- `gpt-4o` - Powerful general purpose

**Features:**
- Enterprise-grade
- Azure integration
- Streaming

### Amazon Bedrock

**Installation:**
```bash
pip install boto3
```

**Environment Variables:**
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION_NAME`

**Latest Models:**
- `amazon.nova-pro-v1` (default) - Multimodal
- `amazon.nova-lite-v1` - Cost-effective
- `amazon.titan-text-premium-v1` - Premium

**Features:**
- 300K context window
- AWS integration
- Enterprise security

### Cohere

**Installation:**
```bash
pip install cohere
```

**Environment Variables:**
- `COHERE_API_KEY`

**Latest Models:**
- `command-a-03-2025` (default) - Most performant
- `command-r-plus-08-2024` - Enhanced reasoning
- `command-r-08-2024` - Strong reasoning

**Features:**
- 128K context window
- Enterprise-focused
- RAG capabilities

### DeepSeek

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `DEEPSEEK_API_KEY`

**Latest Models:**
- `deepseek-chat` (default) - General purpose
- `deepseek-reasoner` - Advanced reasoning
- `deepseek-coder` - Code generation

**Features:**
- 131K context window
- Strong reasoning
- Cost-effective

### Groq

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `GROQ_API_KEY`

**Latest Models:**
- `llama-3.3-70b-versatile` (default) - High performance
- `llama-3.1-70b-instruct` - Strong reasoning
- `llama-3.1-8b-instant` - Fast
- `mixtral-8x7b-32768` - Mixture of experts

**Features:**
- 128K context window
- Fast inference
- Streaming

### Together AI

**Installation:**
```bash
pip install openai
```

**Environment Variables:**
- `TOGETHER_API_KEY`

**Latest Models:**
- `Meta-Llama-3.3-70B-Instruct` (default) - High performance
- `Meta-Llama-3.1-405B-Instruct` - Large model
- `Qwen/Qwen2.5-72B-Instruct` - Multilingual
- `deepseek-ai/DeepSeek-V3` - Advanced reasoning

**Features:**
- 128K context window
- Open-source models
- Cost-effective

### Ollama (Local)

**Installation:**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3.2:3b
```

**Environment Variables:**
- None required for local models

**Latest Models:**
- `llama3.2:3b` (default) - Efficient
- `gemma3:4b` - Lightweight
- `qwen2.5:3b` - Multilingual
- `deepseek-r1:7b` - Strong reasoning

**Features:**
- Local processing
- Privacy-first
- No API keys needed
- Custom models supported

## Usage Examples

### Basic Generation

```python
from api import create_client

client = create_client("google", api_key="...")
response = client.generate("Tell me a joke")
print(response.content)
```

### With Configuration

```python
from api import GenerationConfig

config = GenerationConfig(
    max_tokens=500,
    temperature=0.7,
    system_instruction="You are a helpful tutor."
)

response = client.generate(
    "Explain quantum computing",
    config=config
)
```

### Streaming

```python
for chunk in client.generate_stream("Write a story"):
    print(chunk, end="", flush=True)
```

### Async

```python
response = await client.generate_async("Hello")
```

### Factory Pattern

```python
from api import LLMFactory, ProviderType

client = LLMFactory.create(ProviderType.OPENAI, api_key="...")
```

## Adding a New Provider

To add a new LLM provider:

1. Create a new file: `api/new_provider_client.py`
2. Implement `BaseLLM` interface
3. Register with the factory:

```python
from api.base import LLMFactory, ProviderType

class NewProviderClient(BaseLLM):
    # ... implementation
    pass

LLMFactory.register(ProviderType.NEW_PROVIDER, NewProviderClient)
```

## API Reference

### BaseLLM Methods

| Method | Description |
|--------|-------------|
| `generate(prompt, config)` | Non-streaming text generation |
| `generate_stream(prompt, config)` | Streaming text generation |
| `generate_async(prompt, config)` | Async text generation |
| `generate_stream_async(prompt, config)` | Async streaming |
| `list_models()` | List available models |
| `get_model_info(model_id)` | Get model details |
| `count_tokens(text)` | Count tokens in text |
| `is_available()` | Check if provider is configured |

### LLMResponse Fields

| Field | Description |
|-------|-------------|
| `success` | Boolean indicating success/failure |
| `content` | Generated text content |
| `model` | Model ID used |
| `provider` | Provider name |
| `tokens_used` | Total tokens consumed |
| `prompt_tokens` | Input tokens |
| `completion_tokens` | Output tokens |
| `cost` | Estimated cost in USD |
| `latency` | Response time in seconds |
| `error` | Error message if failed |

## Dependencies

**Core:**
- `google-genai` - For Google Gemini support
- `openai` - For OpenAI, xAI, Meta, DeepSeek, Groq, Together AI, Microsoft Azure
- `anthropic` - For Anthropic Claude support
- `mistralai` - For Mistral AI support
- `cohere` - For Cohere support
- `boto3` - For Amazon Bedrock support

**Optional:**
- `tiktoken` - For accurate token counting (OpenAI)
- `ollama` - For local model support

## Migration from Legacy Code

Old code using direct API calls:

```python
# Old approach (google_provider.py)
import requests
response = requests.post(url, json=payload)
```

New unified approach:

```python
# New approach
from api import create_client
client = create_client("google", api_key="...")
response = client.generate(prompt)
```

## Testing

Run the examples:

```bash
cd /path/to/VEXIS-CLI-2.2
python -m api.usage_example
```

## License

Same as VEXIS-CLI-2.2 project
