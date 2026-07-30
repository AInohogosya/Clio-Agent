# Operations → Safety

Clio-Agent-2 is powerful, and a few behaviors are easy to underestimate. Please
understand these before running it.

## 1. It can run shell commands on your computer

The `shell_command` tool executes arbitrary commands with the privileges of **the
user that launched it**. There is **no sandbox, allow-list, or confirmation
prompt**. Combined with web access and autonomous mode, a poorly-worded task or a
malicious web page could cause it to run destructive commands (e.g. deleting
files).

**Mitigations:**

- Run it on a **dedicated/least-privilege account**, or in a **container/VM you
  don't mind wiping**.
- Disable autonomous mode (`AUTONOMOUS_MODE=false` or `/stop`) for an on-demand
  assistant.
- Think carefully about what you let it do.

## 2. Autonomous mode is ON by default

Every launch starts a background "thinking" loop that can take actions and
**proactively message you** without input. This calls the model repeatedly (every
few seconds by default), which **costs money and uses API quota**. Disable it if you
want a purely on-demand assistant.

## 3. It needs an API key and internet

You must supply at least one paid LLM provider key, and the machine needs internet
access for setup and for the agent to work.

## 4. Prompt-injection exposure

Because the agent can fetch web pages and run commands, malicious content it reads
could try to steer its actions. The **LLM settings lock** limits *model* switching
but **not tool use**. Treat fetched content as untrusted.

## 5. The lock guards the model, not the tools

Provider/model changes are blocked by default (good), but tool actions — including
`shell_command` — are not similarly gated.

## Recommended safe posture

- Least-privilege account or container.
- `AUTONOMOUS_MODE=false` unless you specifically want it.
- Keep `LLM_SETTINGS_LOCKED=true` (`/llm_lock`).
- Review outputs; treat web-fetched content as untrusted.

See also: [Autonomous Mode](../usage/autonomous-mode.md), [LLM Settings Lock](../configuration/llm-lock.md),
[Shell Tool](../tools/shell-tools.md).
