# Quick Start

Get Clio-Agent-2 running in four simple steps.

---

## ⚡ The One-Command Setup

From the project root directory, run:

```bash
python3 run.py
```

That's it. The launcher will:

1. ✅ Check system compatibility (Python version, write permissions)
2. 📦 Create a virtual environment (`.venv`) if one doesn't exist
3. 🔧 Install all dependencies automatically
4. 🔑 Create the `.env` config file from a template
5. ⚙️ Open the **interactive configuration screen** on first run
6. 🚀 Launch the agent

After the first run, `python3 run.py` just starts the agent — setup is skipped unless something went wrong. Use `--no-setup` to skip the first-run prompt:

```bash
python3 run.py --no-setup
```

---

## 🖥️ Choosing an Interface

Pass one of these flags to select your interface:

```bash
python3 run.py                  # Terminal CLI (default)
python3 run.py --telegram       # Telegram bot
python3 run.py --discord        # Discord bot (Beta)
python3 run.py --whatsapp       # WhatsApp Business API bot
python3 run.py --all            # All configured interfaces at once
```

---

## ⚙️ First-Run Configuration

On first launch, an interactive configuration screen appears by default (unless you use `--no-setup`).

### Option A — Interactive Screen (Recommended)

Just run `python3 run.py setup` or launch and let it appear. Walk through it with your keyboard.

### Option B — Non-Interactive (Scripts/Containers)

```bash
python3 run.py setup \
  --openai sk-YOUR-OPENAI-KEY \
  --provider openai \
  --model gpt-4o
```

All per-provider flags exist (e.g. `--google`, `--anthropic`, `--openrouter`, etc.), plus `--telegram-token`, `--discord-token`, `--search`, and `--custom` (JSON).

### Option C — Edit `.env` Directly

```bash
# Open in your editor
nano clio_agent_2/config/.env
```

The critical fields:

```env
# Required — set at least one
OPENAI_API_KEY=sk-...

# Required — no built-in default!
DEFAULT_MODEL=gpt-4o

# Optional but recommended
TELEGRAM_BOT_TOKEN=123456:ABC
DISCORD_BOT_TOKEN=...
AUTONOMOUS_MODE=true
THINKING_INTERVAL=5.0
```

---

## ✅ Verify It Works

```bash
# Check configuration status
python3 run.py status
```

You should see a green "✅ Ready" indicator.

Then:

```bash
# Launch the CLI
python3 run.py
```

Type a message to the agent — if it replies, you're all set.

---

## 🔧 Manual Setup (Advanced)

If the automatic launcher fails, you can set up the environment yourself:

```bash
# 1. Create a virtual environment
python3 -m venv .venv

# 2. Activate it
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r clio_agent_2/requirements.txt

# 4. Set up configuration
python3 run.py setup

# 5. Launch
python3 run.py
```

---

## 📂 What gets created?

| Path | Purpose |
|------|---------|
| `.venv/` | Python virtual environment |
| `clio_agent_2/config/.env` | Your secrets and settings |
| `clio_agent_2/config/config.yaml` | Human-readable settings mirror |
| `clio_agent_2/data/context.json` | Agent memory (persisted) |
| `clio_agent_2/data/context.json.bak` | Context backup |

---

## 🧭 Next Steps

- Read [First Launch & Configuration](FIRST_LAUNCH.md) for a deeper walkthrough
- See [CLI Guide](CLI_GUIDE.md) for all available commands
- Check [Safety & Best Practices](SAFETY.md) before enabling autonomous mode
