# Safety & Best Practices

Critical information for using Clio-Agent-2 safely.

---

## ⚠️ The Three Biggest Risks

Read these before you do anything else.

### 1. Shell Command Has No Sandbox 🔥

The `shell_command` tool runs **arbitrary commands with your user privileges**. There is:

- ✅ No sandbox
- ✅ No allow-list
- ✅ No confirmation prompt
- ✅ No automatic destructive-command filter

A poorly worded request or a malicious fetched URL could cause the agent to run destructive commands (e.g., `rm -rf ~`).

**Mitigation:**
- Run the agent on a **dedicated / least-privilege account**
- Use a **container or VM** you don't mind wiping
- Treat it like giving a human assistant SSH access to your machine

### 2. Autonomous Mode Is ON by Default 💸

On every first launch, the agent starts running a background loop that calls the LLM repeatedly (every `THINKING_INTERVAL` seconds). This:

- Costs **money** (API calls every few seconds)
- Acts **without your input**
- May **shell out commands** on its own

**Mitigation:**
- Set `AUTONOMOUS_MODE=false` if you want a purely on-demand assistant
- Use `/stop` in a session to disable it
- Set a longer `THINKING_INTERVAL` to reduce cost

### 3. Web Access + Tool Use = Prompt Injection Exposure 🌐

Because the agent can **fetch web pages** and **run commands**, malicious content from a fetched page could attempt prompt injection to steer its actions.

**Mitigation:**
- The **LLM settings lock** (`/llm_lock`) limits model switching
- Be cautious about which tasks you give the agent
- Review what it's doing via `/context` regularly

---

## ✅ Safety Checklist Before First Use

- [ ] I understand `shell_command` runs as my user, with no sandbox
- [ ] I have set a real default model (`DEFAULT_MODEL`)
- [ ] I have enabled `LLM_SETTINGS_LOCKED=true` (the default)
- [ ] If using autonomous mode: I am running in a controlled environment
- [ ] Chat interfaces (Telegram, Discord, WhatsApp) have restricted bot access

---

## 🔒 LLM Settings Lock

The lock prevents the model and provider from changing unexpectedly:

```text
/llm_unlock                  # One-time allow
/llm_default google gemini-1.5-pro  # Change model
/llm_lock                    # Re-secure
```

The lock state persists in `.env` across restarts.

**What the lock protects:** Provider and model selection.
**What the lock does NOT protect:** Tool usage (including `shell_command`).

---

## 🛠️ Permissions and Environment Best Practices

1. **Use a dedicated account** rather than your main login
2. **Docker / VM approach** (most secure):
   ```bash
   docker run -it --rm \
     -v $(pwd):/agent \
     python:3.12 bash
   # Then run python3 run.py inside
   ```
3. **Least privilege** — don't run the agent as root
4. **Separate API keys** — use keys with limited scope where possible
5. **Monitor costs** — set up billing alerts on your LLM provider accounts
6. **Back up data** — the agent modifies files in your home directory; keep backups

---

## 🔑 API Key Security

- `.env` is in `clio_agent_2/config/.env` — never commit it to version control
- `.gitignore` should include `clio_agent_2/config/.env`
- If you accidentally expose a key, rotate it immediately at the provider dashboard
- Strongly prefer keys with minimal scopes (read-only where possible)

---

## 🤖 Autonomous Mode: Special Considerations

When running autonomously:

- **Shell commands run on their own** — review `/context` periodically
- **API costs accumulate** — monitor usage
- **The agent can message you proactively** — this is normal, not a bug
- **It never asks for confirmation** — if it's about to do something big, it just does it

If you notice concerning behavior: run `/stop`, review `/context`, and investigate before re-enabling.

---

## 🚨 If Something Goes Wrong

| Symptom | Likely Cause | Action |
|---------|-------------|--------|
| Files deleted unexpectedly | Autonomous loop + shell command | Review `/context`, check backup |
| High API bill | Autonomous mode running long | `/stop`, review settings |
| Agent acting strangely | Prompt injection via web content | `/stop`, `/context` to audit |
| Model switched without consent | LLM lock not enabled | Enable `/llm_lock` |

---

## 🧭 Related Docs

- [Autonomous Mode](AUTONOMOUS_MODE.md) — how the loop works
- [Troubleshooting](TROUBLESHOOTING.md) — common issues and fixes
- [Limitations](LIMITATIONS.md) — known risks
