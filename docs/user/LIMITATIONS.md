# Limitations & Known Issues

An honest catalog of Clio-Agent-2's current limitations. These are not bugs — they are acknowledged design tradeoffs and known weaknesses.

---

## 🚨 Critical Limitations to Be Aware Of

### 1. No sandbox around shell commands 🔥

`shell_command` runs anything, as you, with no allow-list or confirmation. This is the single biggest risk.

**Design choice:** "True agent" feel, but the onus is on you to run in a safe environment.

---

### 2. Autonomous mode is ON by default 💸

Every launch starts the thinking loop by default, costing API quota/money and acting without your input.

**Design choice:** The agent is meant to feel alive and proactive. You can disable it with `/stop` or `AUTONOMOUS_MODE=false`.

---

### 3. No default model — you must set `DEFAULT_MODEL` ⚙️

There is no built-in default. If unset, the agent runs but the autonomous loop won't start.

---

### 4. Memory is lossy 🧠

Context is compressed (summarized) once it exceeds `CONTEXT_LOG_MAX_LINES`. Very old details can be lost. The log is durably persisted and restored on restart; the residual risk is a hard crash within ~10 entries of the last write.

**Mitigation:** Use `/restore_context` if something critical was lost; `/clear_context` is now recoverable.

---

### 5. The LLM lock guards the model, not the tools 🔒

Provider/model changes are blocked by default (good), but tool actions — including `shell_command` — are not similarly gated. Prompt-injection could still steer tool usage through content fetched from the web.

---

### 6. Stuck/repetition detection is basic 🔁

It only catches exact action signature repeats within a small recent window (4+ of the same in 6 recent). Varied or slow loops may escape detection.

---

### 7. Compression failures are silently swallowed ⚡

If LLM summarization errors, the agent logs "Compression failed" and continues with uncompressed context rather than alerting you or halting.

---

### 8. Discord and WhatsApp are Beta 🧪

Both interfaces expose fewer features than CLI/Telegram:
- Fewer slash commands
- Less rich message formatting
- More edge cases in message handling

---

### 9. Requires internet + paid API key 🌐

The agent cannot run fully offline (except against a local Ollama/OpenAI-compatible server). It needs real internet and a real paid API key for LLM calls.

---

### 10. Two `config/` folders can confuse you 📁

Only `clio_agent_2/config/.env` is used. The `config/` folder at the repository root is **legacy** and ignored by the program.

---

### 11. Prompt-injection exposure 🦠

The agent can read web pages and act on content from infected sites. While the model lock prevents silent provider changes, tool injection is not gated in the same way.

---

## 🐛 Known Bugs or Edge Cases

| Issue | Description | Workaround |
|-------|-------------|------------|
| Telegram 409 Conflict | Two instances polling the same token | Use `--replace` flag |
| Context may "forget" early agreements | Compression collapses old context | Restate important context |
| Long WhatsApp responses get split | Hard-capped at 4096 chars | Receives gracefully |
| Multi-tool output drops | Rarely some multi-tool JSON shapes missed | Use simpler tool calls |
| Console input blocking on SIGINT | On some platforms `Ctrl+C` timing matters | Press once, wait briefly |

---

## 📊 Technical Limitations

- **Memory window:** The hot rolling window is ~50 entries in context. Anything older is summarized.
- **Tool iterations cap:** Max 5 consecutive tool rounds per turn (prevents runaway loops).
- **Context token budget:** ~4000 tokens used for context history (half of `MAX_CONTEXT_TOKENS`).
- **Circuit breaker:** 5 consecutive LLM failures pauses the loop until `/resume`.
- **Timeouts:** Shell commands have bounded timeout with 5 retry attempts.
- **Telegram lock:** PID-file single-instance lock prevents duplicate polling.

---

## 🗺️ Roadmap Gaps (Not Planned but Noted)

- No multi-user (room/shared-agent) support — the agent is single-context.
- No persistent task/queue system beyond the current LLM-driven loop.
- No built-in rate-limiting or spend-cap controls (use provider-level controls).
- No web UI (CLI + bot interfaces only).

---

## 🧭 Related Docs

- [Safety & Best Practices](SAFETY.md) — mitigating risks
- [Troubleshooting](TROUBLESHOOTING.md) — fixing common issues
- [FAQ](FAQ.md) — more answers
