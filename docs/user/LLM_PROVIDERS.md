# LLM Providers

Everything about choosing, configuring, and switching between AI models.

---

## 🧠 Supported Providers

Clio-Agent-2 talks to 15+ built-in providers and any custom OpenAI-compatible endpoint.

### Built-in Providers

| Provider | Environment Variable | Compatible API Style | Notes |
|----------|---------------------|----------------------|-------|
| **OpenAI** | `OPENAI_API_KEY` | Native | Default provider |
| **Google (Gemini)** | `GOOGLE_API_KEY` | Native | Default model: `gemini-1.5-pro` |
| **Anthropic** | `ANTHROPIC_API_KEY` | Native | Claude models |
| **OpenRouter** | `OPENROUTER_API_KEY` | OpenAI-compatible | Also sets `OPENROUTER_HTTP_REFERER` / `OPENROUTER_APP_NAME` |
| **Grok (xAI)** | `GROK_API_KEY` | OpenAI-compatible | |
| **DeepSeek** | `DEEPSEEK_API_KEY` | OpenAI-compatible | |
| **Mistral** | `MISTRAL_API_KEY` | OpenAI-compatible | |
| **Groq** | `GROQ_API_KEY` | OpenAI-compatible | Ultra-low latency |
| **Perplexity** | `PERPLEXITY_API_KEY` | OpenAI-compatible | Search-augmented |
| **Together** | `TOGETHER_API_KEY` | OpenAI-compatible | Many open-weight models |
| **Fireworks** | `FIREWORKS_API_KEY` | OpenAI-compatible | Llama, Mixtral, etc. |
| **NVIDIA NIM** | `NIM_API_KEY` | OpenAI-compatible | Includes reasoning/thinking models |
| **Qwen (Alibaba)** | `QWEN_API_KEY` | OpenAI-compatible | |
| **HuggingFace** | `HUGGINGFACE_API_KEY` | OpenAI-compatible | Inference API |
| **DeepInfra** | `DEEPINFRA_API_KEY` | OpenAI-compatible | |
| **Ollama (local)** | *none required* | OpenAI-compatible | Uses `http://localhost:11434` |

### Custom "Other" Providers

Any OpenAI-compatible API (LM Studio, vLLM, localAI, enterprise gateways) can be added:

```env
CUSTOM_1_NAME=my-custom
CUSTOM_1_BASE_URL=http://localhost:8000/v1
CUSTOM_1_API_KEY=sk-optional
```

---

## ⚙️ Setting Up a Provider

### Quick Setup

The fastest way is through the configuration screen:

```bash
python3 run.py setup
```

Or set the key directly via the CLI once running:

```text
/configure → "LLM Provider Keys" → paste your key
```

### Non-Interactive Setup

```bash
python3 run.py setup --openai sk-... --provider openai --model gpt-4o
```

Per-provider flags: `--openai`, `--google`, `--anthropic`, `--openrouter`, `--grok`, `--deepseek`, `--mistral`, `--groq`, `--perplexity`, `--together`, `--fireworks`, `--nim`, `--qwen`, `--huggingface`, `--deepinfra`.

---

## 🔀 Switching Providers

Have multiple keys configured and switch between them in real time:

```text
/llm_providers           # Show all configured providers
/llm_models openai       # List models for a specific provider
/llm_default google gemini-1.5-pro  # Switch active provider + model
/llm_lock               # Re-lock after changing
```

### Default Models by Provider

| Provider | Default Model |
|----------|--------------|
| OpenAI | `gpt-4o` |
| Google | `gemini-1.5-pro` |
| Anthropic | `claude-sonnet-4-20250514` |
| OpenRouter | varies |
| Ollama | `llama3` |

---

## 🔒 LLM Settings Lock (Guardrail)

By default (`LLM_SETTINGS_LOCKED=true`), the active provider and model are **locked** — they cannot change without your explicit action.

```text
/llm_unlock                  # Allow changes for this session
/llm_default google gemini-1.5-flash  # Change the model
/llm_lock                    # Re-lock
```

**Why this exists:** The agent can fetch web pages, and a malicious page could try prompt-injection to silently switch your provider. This lock prevents that. The lock state is persisted to `.env` so it survives restarts.

**What the lock does NOT protect:** It only guards the *LLM provider/model* setting. Tool actions (including `shell_command`) are not locked.

---

## 📋 Custom Provider Details

### Custom Provider Structure

Each custom provider needs:

| Variable | Required | Description |
|----------|----------|-------------|
| `CUSTOM_N_NAME` | ✅ | Display name (e.g. `lm-studio`) |
| `CUSTOM_N_BASE_URL` | ✅ | API base URL (e.g. `http://localhost:1234/v1`) |
| `CUSTOM_N_API_KEY` | ❌ | API key (send empty string if not needed) |
| `CUSTOM_N_MAX_TOKENS` | ❌ | Response token limit |
| `CUSTOM_N_CONTEXT_WINDOW` | ❌ | Token context window (for estimation) |

Multiple custom providers: `CUSTOM_1_*`, `CUSTOM_2_*`, `CUSTOM_3_*`, etc.

### Example: LM Studio

```env
CUSTOM_1_NAME=lm-studio
CUSTOM_1_BASE_URL=http://localhost:1234/v1
CUSTOM_1_API_KEY=
CUSTOM_1_MAX_TOKENS=2048
CUSTOM_1_CONTEXT_WINDOW=4096
```

---

## 💡 Choosing a Provider

- **Best quality (paid):** OpenAI `gpt-4o`, Anthropic `claude-sonnet-4`, Google `gemini-1.5-pro`
- **Fastest (paid):** Groq (ultra-low latency inference)
- **Best value:** OpenRouter (routing between many providers)
- **Free/local:** Ollama + any local model (no internet needed after download)
- **Enterprise/custom:** "Other" provider pointing to your internal serving infrastructure

---

## ⚠️ Limitations

- Switching providers resets context-specific model state.
- Some providers have token limits that may cause issues with long autonomous loops.
- NVIDIA NIM supports reasoning/thinking models — regular providers may not.
- Multi-provider searching uses the **first configured provider that supports model listing.**

---

## 🧭 Related Docs

- [Configuration Guide](CONFIGURATION.md) — all settings explained
- [CLI Guide](CLI_GUIDE.md) — slash commands for LLM management
- [Troubleshooting](TROUBLESHOOTING.md) — common provider errors
