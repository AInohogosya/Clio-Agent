# Installation Guide

This guide covers everything you need to know before installing Clio-Agent-2.

---

## 📋 System Requirements

### Required

| Requirement | Details |
|-------------|---------|
| **Python** | 3.10 or newer (tested on 3.10 through 3.14) |
| **Operating System** | Linux, macOS, or Windows |
| **Internet Connection** | Required for setup, LLM API calls, and web search |
| **LLM API Key** | At least one paid API key from a supported provider |

### Recommended Hardware

- Modern CPU (x86_64 or arm64)
- 2 GB minimum RAM
- Stable internet connection

---

## 🐍 Supported Python Versions

Clio-Agent-2 requires **Python 3.10+**. The launcher will verify your version on first run and refuse to continue if it's too old.

```bash
# Check your Python version
python3 --version
```

If you need to install a newer version:
- **macOS**: `brew install python@3.12`
- **Ubuntu/Debian**: `sudo apt install python3.12`
- **Windows**: Download from https://www.python.org/downloads/

---

## 🔑 Supported LLM Providers

You need **at least one** API key from the list below:

| Provider | Env Variable | Default Model |
|----------|--------------|---------------|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| Google (Gemini) | `GOOGLE_API_KEY` | `gemini-1.5-pro` |
| Anthropic | `ANTHROPIC_API_KEY` | — |
| OpenRouter | `OPENROUTER_API_KEY` | — |
| Grok (xAI) | `GROK_API_KEY` | — |
| DeepSeek | `DEEPSEEK_API_KEY` | — |
| Mistral | `MISTRAL_API_KEY` | — |
| Groq | `GROQ_API_KEY` | — |
| Perplexity | `PERPLEXITY_API_KEY` | — |
| Together | `TOGETHER_API_KEY` | — |
| Fireworks | `FIREWORKS_API_KEY` | — |
| NVIDIA NIM | `NIM_API_KEY` | — |
| Qwen (Alibaba) | `QWEN_API_KEY` | — |
| HuggingFace | `HUGGINGFACE_API_KEY` | — |
| DeepInfra | `DEEPINFRA_API_KEY` | — |
| Ollama (local) | none required (localhost) | — |
| Custom "Other" | `CUSTOM_*_...` | any OpenAI-compatible endpoint |

You can configure **multiple providers** and switch between them at any time.

---

## 🤖 Optional: Bot Tokens

If you want to use Telegram, Discord, or WhatsApp (instead of just the CLI):

| Interface | What You Need |
|-----------|--------------|
| **Telegram** | A bot token from [@BotFather](https://t.me/botfather) |
| **Discord** | A bot token from [Discord Developer Portal](https://discord.com/developers/applications) |
| **WhatsApp** | Meta Developer Account + WhatsApp Business Account credentials |

---

## ⚡ Pre-Installation Checklist

Before running `python3 run.py`, make sure:

- [ ] Python 3.10+ is installed
- [ ] You have write access to the project directory
- [ ] You have at least one LLM API key ready
- [ ] (Optional) Your bot tokens are ready if using non-CLI interfaces

---

## 🚀 Next Steps

Once these requirements are met, proceed to [Quick Start](QUICK_START.md).
