# FAQ

Frequently asked questions about Clio-Agent-2.

---

## 🚀 Getting Started

### Q: Do I need an AI API key?
**A:** Yes. You need at least one API key from a supported LLM provider (OpenAI, Google, Anthropic, etc.). The agent won't do anything useful without one.

### Q: Does it work offline?
**A:** Not by default — it needs internet for LLM API calls. However, if you use **Ollama** with a locally hosted model, it can work offline after the initial setup.

### Q: How much does it cost?
**A:** It depends on how much you use it and which provider you choose. In autonomous mode, it calls the LLM every `THINKING_INTERVAL` seconds (default: 5). Groq offers free/fast inference; OpenAI/Anthropic charge per token. Set up a billing alert at your provider.

### Q: Is my data sent to the cloud?
**A:** Yes, if you use a cloud LLM provider. Your messages, context, and file contents are sent to whichever provider you've configured. If you use Ollama locally, data stays on your machine.

### Q: How do I update to the latest version?
**A:** Pull the latest code with git, then re-run `python3 run.py` to install any new dependencies.

---

## ⚙️ Configuration

### Q: Which `config/.env` file do I edit?
**A:** Always `clio_agent_2/config/.env`. The `config/.env` at the repository root is legacy and ignored by the program.

### Q: Why isn't there a default model?
**A:** Intentional design choice. Forces you to explicitly choose your LLM rather than silently using one that might not fit your needs or budget.

### Q: Can I use multiple LLM providers at once?
**A:** Yes. Configure multiple API keys. Switch between them with `/llm_default <provider> [model]`.

### Q: What's the "LLM settings lock"?
**A:** It's a guardrail (`LLM_SETTINGS_LOCKED=true`) that prevents the active provider and model from being changed without explicit `/llm_unlock`. Protects against prompt-injection attempts. Persistent across restarts.

### Q: What's `.env.example` for?
**A:** It's a template with placeholder values. On first run, it's copied to `.env` so you have a starting point. The placeholders are smart-detected as "not configured," so the setup screen still appears when it should.

---

## 🤖 Using the Agent

### Q: What is the "say" tool?
**A:** The `say` tool is the **only** way the agent delivers user-facing output. You won't see its natural-language text directly — only `say` calls produce visible messages. It might run `/think` internally without telling you.

### Q: Can the agent edit files?
**A:** Yes — via `edit_file`, `write_file`, and `append_file`. `edit_file` is careful: it refuses ambiguous edits that match multiple parts of a file.

### Q: Can it run shell commands?
**A:** Yes, via `shell_command`. This runs with zero sandboxing — treat it like giving someone SSH access to your machine.

### Q: Why doesn't it respond immediately when I message it?
**A:** This is intentional. The agent is described as a "human-like" agent — it prioritizes its own tasks and may reply later. If you need immediate on-demand responses, disable autonomous mode and minimize tool usage.

### Q: How do I see what the agent has been doing?
**A:** Run `/context` (or `/context 50`) to see recent entries. Also check `clio_agent.log` for log output.

---

## 🌐 Interfaces

### Q: Telegram vs Discord vs WhatsApp — which should I use?
**A:**
- **CLI**: Best for local use, development, debugging
- **Telegram**: Best for mobile/chat-style interaction, most feature-complete
- **Discord**: Good for community/server use, Beta status
- **WhatsApp**: For business or customer-facing use, Beta status

### Q: Can the agent message me out of the blue?
**A:** Yes, in autonomous mode. It can send proactive messages — to share progress, ask questions, or just say hi. This is by design.

### Q: How do I stop autonomous messages?
**A:** Run `/stop` or set `AUTONOMOUS_MODE=false` in `.env`. Restart the agent.

---

## 🔧 Troubleshooting

### Q: "No LLM providers configured" error?
**A:** Your API key is a placeholder. Set a real one with `python3 run.py setup --openai sk-...`.

### Q: Agent exits immediately after Ctrl+C?
**A:** Normal — `Ctrl+C` saves context and exits cleanly. Use `/exit` for a deliberate exit that also persists state.

### Q: Chat bot not responding?
**A:** Check `python3 run.py status`. Likely causes: token is still a placeholder, lock is on with no model set, or the interface is crashing. Check `clio_agent.log` for errors.

### Q: Context/context.json got corrupted?
**A:** Agent auto-creates a `.bak` backup. If corrupted, restore it manually:
```bash
cp clio_agent_2/data/context.json.bak clio_agent_2/data/context.json
```

---

## 🔒 Safety

### Q: Is it safe to let the agent run unattended?
**A:** Only if it runs in a sandboxed environment (container, VM, or least-privilege account). The shell command has no safety rails.

### Q: Can prompt injection attack the agent?
**A:** The LLM lock blocks model switching via injection. Tool injection (steering tool calls through web content) is not blocked at the same level. Use cautiously with web access + shell.

### Q: Can I limit what the agent can do?
**A:** Indirectly — you can turn off the shell command (modify `tools/tool_registry.py`, or don't invoke it). You cannot restrict specific commands via config.

---

## 🧭 Related Docs

- [Troubleshooting](TROUBLESHOOTING.md) — more fixes
- [Safety & Best Practices](SAFETY.md) — security guidance
- [Limitations](LIMITATIONS.md) — known constraints
