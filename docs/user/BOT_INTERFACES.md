# Bot Interfaces

Setting up and using Telegram, Discord, and WhatsApp with Clio-Agent-2.

---

## 🤖 Overview

Clio-Agent-2 supports four interfaces, all sharing the same backend agent:

| Interface | Status | How to Launch |
|-----------|--------|--------------|
| **CLI** | Stable | `python3 run.py` |
| **Telegram** | Stable | `python3 run.py --telegram` |
| **Discord** | Beta | `python3 run.py --discord` |
| **WhatsApp** | Beta | `python3 run.py --whatsapp` |

Run multiple interfaces simultaneously with `python3 run.py --all`.

---

## 📱 Telegram

### Setup

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the **bot token** (format: `123456789:ABCdefGhIJKlmNoPQRsTUVwxYZ`).
4. Configure it:

```bash
python3 run.py setup --telegram-token 123456789:ABCdef...
```

Or at runtime:

```text
/configure → Telegram Bot Token
```

### Features

- ✅ All slash commands supported
- ✅ Proactive autonomous messages
- ✅ 409 conflict recovery (auto-retries with backoff)
- ⚠️ Only one polling instance per bot token (enforced via PID lock)

### Running

```bash
python3 run.py --telegram
```

If a previous instance is stuck:

```bash
python3 run.py --telegram --replace  # evicts the stale instance
```

### Markdown Note

Telegram messages are aggressively sanitized to avoid rendering issues. Code blocks are protected; other Markdown may be escaped.

---

## 🎮 Discord (Beta)

### Setup

1. Go to https://discord.com/developers/applications
2. Create a new application, then add a bot under **Bot** settings.
3. Copy the **bot token**.
4. **Enable Message Content Intent** (Privileged Gateway Intents → MESSAGE CONTENT INTENT).
5. Invite the bot to your server with appropriate permissions.

Configure:

```bash
python3 run.py setup --discord-token YOUR_DISCORD_TOKEN
```

### Features

- ✅ Slash commands: `/status`, `/help`, `/settings`, `/models`, `/think`, `/context`
- ✅ Rich embeds for autonomous mode messages
- ✅ Direct messages work without bot mention
- ⚠️ In server channels: the bot must be @mentioned
- ⚠️ Beta — fewer features than CLI/Telegram

### Running

```bash
python3 run.py --discord
```

---

## 💬 WhatsApp Business API (Beta)

### Prerequisites

- A Meta Developer Account
- A WhatsApp Business Account
- A verified webhook URL (HTTPS required)

### Setup

Configure these in `clio_agent_2/config/.env` or via `/configure`:

```env
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_APP_SECRET=...
WHATSAPP_WEBHOOK_VERIFY_TOKEN=...
WHATSAPP_WEBHOOK_URL=https://your-domain.com
WHATSAPP_WEBHOOK_PORT=8080
```

Requirements:
- The `WHATSAPP_WEBHOOK_URL` must be publicly reachable (NGROK or a real server)
- Port 8080 is the default; change `WHATSAPP_WEBHOOK_PORT` if needed

### Running

```bash
# Start the webhook bot
python3 run.py --whatsapp

# Send a one-off message (legacy Node.js service)
python3 run.py --whatsapp +1234567890 "Hello from Clio-Agent!"
```

### Features

- ✅ Webhook-based receiving
- ✅ Auto message splitting (max 4096 chars)
- ⚠️ Beta — fewer features than CLI/Telegram
- ⚠️ Requires HTTPS endpoint for webhooks

---

## 🛠️ Running Multiple Interfaces

```bash
python3 run.py --all
```

This starts **CLI always**, plus any configured interface (Telegram, Discord, WhatsApp if tokens are set). All interfaces share one agent — so the context memory, tools, and current LLM configuration are the same across all channels.

---

## ⚡ Troubleshooting Bots

| Problem | Solution |
|---------|----------|
| Telegram 409 Conflict | Another instance is polling the token — try `--replace` |
| Discord login failure | Token is invalid — regenerate in Developer Portal |
| Discord "Privileged Intents Required" | Enable Message Content Intent |
| WhatsApp webhook not receiving | Verify URL is HTTPS and publicly reachable |
| Bot not responding | Check `python3 run.py status` for token validation |

---

## 🧭 Related Docs

- [CLI Guide](CLI_GUIDE.md) — all commands
- [Configuration Guide](CONFIGURATION.md) — setting bot tokens
- [Troubleshooting](TROUBLESHOOTING.md) — more help
