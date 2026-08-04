# Troubleshooting

Solutions to the most common issues with Clio-Agent-2.

---

## 🚀 Setup Issues

### The first-run setup screen never appeared

**Cause:** You already have a real API key and a real default model configured, so the agent considers itself "ready."

**Fix:** Open it manually:
```bash
python3 run.py setup
# or inside the CLI:
/configure
```

---

### "No LLM providers configured" / LLM calls fail

**Cause:** Your API key is still a placeholder (e.g. `your_openai_api_key_here`) or `DEFAULT_MODEL` is empty.

**Fix:**
```bash
python3 run.py setup --openai sk-... --provider openai --model gpt-4o
```

---

### The autonomous loop doesn't start

**Cause:** There is no built-in default model. You must set `DEFAULT_MODEL` explicitly.

**Fix:**
```text
/llm_default openai gpt-4o
```
Or set it in `.env`:
```env
DEFAULT_MODEL=gpt-4o
DEFAULT_LLM_PROVIDER=openai
```

---

### "Python version too old"

**Cause:** Clio-Agent-2 needs Python 3.10+.

**Fix:** Upgrade Python and re-run:
```bash
# macOS
brew install python@3.12

# Ubuntu/Debian
sudo apt install python3.12

# Then
python3 --version  # should show 3.10+
python3 run.py
```

---

### Virtual environment creation failed

**Cause:** `python3-venv` package is not installed on your system.

**Fix:**
```bash
# Ubuntu/Debian
sudo apt-get install python3-venv python3-pip

# Fedora/RHEL
sudo dnf install python3-venv python3-pip

# macOS — usually not needed, but if broken:
brew reinstall python3

# Then set up manually:
cd /path/to/Clio-Agent
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r clio_agent_2/requirements.txt
python3 run.py --no-setup
```

---

### Dependency installation problems

**Fix:**
```bash
source .venv/bin/activate
pip install --upgrade pip
pip install -r clio_agent_2/requirements.txt --no-cache-dir
```

---

## 🌐 Interface Issues

### Telegram won't connect

**Cause:** Token is still a placeholder or another instance is already polling.

**Fix:**
```bash
# Set real token
python3 run.py setup --telegram-token 123456:ABC

# Or evict the stale instance
python3 run.py --telegram --replace
```

---

### Discord login failed

**Cause:** Token is invalid or has been reset.

**Fix:** Generate a fresh token in the Discord Developer Portal, then set `DISCORD_BOT_TOKEN` in `.env`.

---

### Discord "Privileged Intents Required"

**Cause:** Message Content Intent is disabled for your bot.

**Fix:**
1. Go to https://discord.com/developers/applications
2. Your Application → Bot → Privileged Gateway Intents
3. Enable **Message Content Intent**
4. Restart the agent

---

### WhatsApp webhook not receiving messages

**Checklist:**
- `WHATSAPP_WEBHOOK_URL` is a publicly reachable **HTTPS** URL
- Port in `WHATSAPP_WEBHOOK_PORT` is open
- All required `WHATSAPP_*` env vars are set correctly
- The webhook was verified by Meta

---

## 🧠 Context & Memory Issues

### Context seems wrong or empty

**Fix:**
- Run `/context` (or `/context 50`) to inspect recent entries
- Auto-compression summarizes entries past `CONTEXT_LOG_MAX_LINES` (default 1000) — this is expected behavior
- Run `/clear_context` to wipe, then `/restore_context` to recover

---

### Context was accidentally cleared

The agent **backs up context before clearing**:
```text
/restore_context
```

This restores the last cleared backup.

---

## ⏱️ Timeout Issues

### Shell command "timed out"

Commands are retried up to 5 times on transient timeouts. If it still fails:

- The command genuinely took too long
- Run it manually to check
- Raise the `timeout` parameter in the tool call

---

## ⚡ Performance Issues

### Agent is slow to respond

Possible causes:
- The LLM provider is slow (try switching to Groq for speed)
- Context is very large (run `/clear_context` if acceptable)
- `MAX_TOOL_ITERATIONS` is being hit (the agent is doing many tool calls per turn)

---

### High API costs

- `AUTONOMOUS_MODE=true` costs money every few seconds — try `/stop` or `AUTONOMOUS_MODE=false`
- Use Groq for free or cheaper inference
- Set up billing alerts with your LLM provider

---

## 🐛 Other Common Issues

### I edited `.env` but nothing changed

**Cause:** You edited the wrong `config/` folder.

**Fix:** Edit `clio_agent_2/config/.env` — NOT the one at the repository root. Restart the agent or use `/configure` for live updates.

---

### Two `config/` folders are confusing

This is intentional but easy to trip over:

- ✅ `clio_agent_2/config/.env` — **the real one, used by the agent**
- ❌ `config/` at repository root — legacy, ignored

---

### "Compression failed" appears in logs

**Cause:** The LLM summarization of old context entries errored. This is gracefully handled — the agent continues with uncompressed context rather than halting.

**Fix:** No action needed unless context becomes unmanageably large.

---

### The agent just keeps repeating itself

**Cause:** The basic stuck-detection hasn't caught it (it only catches exact signature repeats).

**Help it:** Send a fresh message redirecting it, or check `/context` to see what's happening.

---

### Stuck in a runaway tool loop

After `MAX_TOOL_ITERATIONS` (5) consecutive tool rounds, the agent halts that turn. If it's still stuck:

- Run `/clear_context` to reset its working memory
- Restart the agent

---

## 📞 Getting More Help

- Check the [Limitations & Known Issues](LIMITATIONS.md)
- Open an issue on the GitHub repository
- Review the [FAQ](FAQ.md)

---

## 🧭 Related Docs

- [Limitations](LIMITATIONS.md) — known weaknesses
- [FAQ](FAQ.md) — more answers
- [Safety & Best Practices](SAFETY.md) — stay safe
