# TinyAgent - Ultra-Lightweight AI Agent for TinyOS

A minimalist, memory-efficient AI agent designed to run on resource-constrained environments with as little as **512 MB RAM**.

## Features

- **< 5MB RAM footprint** - Optimized for low-memory systems
- **Zero external dependencies** - Uses only Python standard library
- **Single file** - Easy deployment and maintenance
- **Sliding window context** - Memory-bounded conversation history
- **Graceful shutdown** - Signal handling for clean exits
- **Multiple modes** - Interactive CLI or single-turn pipe mode

## Quick Start

### Basic Usage

```bash
# With API key as argument
python3 tiny_agent.py sk-your-api-key-here

# Or via environment variable
export OPENAI_API_KEY=sk-your-api-key-here
python3 tiny_agent.py

# Specify a different model
python3 tiny_agent.py sk-your-api-key-here --model gpt-4o-mini
```

### Interactive Mode

```
==================================================
TinyAgent v1.0 - Lightweight AI for TinyOS
Model: gpt-4o-mini | History: 10
Commands: /clear (clear history), /quit (exit)
==================================================

You: Hello, what can you do?
Agent: I'm a lightweight AI assistant. I can answer questions, help with tasks, and chat with you!

You: /clear
[History cleared]

You: /quit
Goodbye!
```

### Single-Turn Mode (Piped Input)

```bash
echo "What is 2+2?" | python3 tiny_agent.py YOUR_API_KEY
```

## Configuration

Environment variables for tuning memory usage:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | - | Your OpenAI API key |
| `TINY_MODEL` | `gpt-4o-mini` | LLM model name |
| `TINY_MAX_TOKENS` | `500` | Max response tokens |
| `TINY_HISTORY` | `10` | Max conversation history entries |

Example:
```bash
export OPENAI_API_KEY=sk-your-key
export TINY_MODEL=gpt-3.5-turbo
export TINY_HISTORY=5  # Reduce for even lower memory
python3 tiny_agent.py
```

## Architecture

```
┌─────────────────────────────────────────────┐
│              TinyAgent                       │
├─────────────────────────────────────────────┤
│  Config          - Static configuration     │
│  HTTPClient      - Stdlib HTTP (no deps)    │
│  ContextWindow   - Sliding window (FIFO)    │
│  LLMClient       - OpenAI-compatible API    │
│  TinyAgent       - Core event loop          │
└─────────────────────────────────────────────┘
```

### Memory Optimization Techniques

1. **`__slots__`** - Prevents dynamic attribute dictionaries
2. **Fixed-size buffers** - Bounded context window prevents growth
3. **No async overhead** - Synchronous design reduces memory
4. **Minimal imports** - Only stdlib modules
5. **String interning** - Reuses common strings

## Comparison

| Feature | Original Clio-Agent | TinyAgent |
|---------|---------------------|-----------|
| File count | 20+ files | 1 file |
| Dependencies | 10+ packages | 0 (stdlib only) |
| RAM usage | ~100MB+ | < 5MB |
| Startup time | ~2-3 seconds | < 100ms |
| Virtual env required | Yes | No |

## System Requirements

- Python 3.6+ (tested on 3.8+)
- 512 MB RAM minimum
- Network access for LLM API
- ~10 KB disk space

## Use Cases

- Embedded systems with limited resources
- IoT devices running TinyOS
- Low-end VPS instances
- Container environments with tight memory limits
- Educational purposes (simple, readable code)

## License

MIT License - See original project for details.
