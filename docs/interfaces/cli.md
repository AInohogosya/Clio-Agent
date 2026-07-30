# Interfaces → CLI (`interfaces/cli.py`)

The terminal interface is the default and most feature-complete way to run
Clio-Agent-2. Launch with `python3 run.py` (no flag).

## Features

- **Rich terminal output** — formatted prompts, banners, and colored messages.
- **Slash commands** — the full command set works here (see
  [Slash Commands](../usage/slash-commands.md)).
- **Live response delivery** — registers a callback so autonomous `say` messages
  appear as they are produced.
- **Async loop** — driven by `asyncio.run(run_cli())`.

## Typical session

```text
💻 Starting CLI interface...
You: summarize the README
Clio: <reply>
You: /context 20
You: /stop        # disable autonomous mode
You: /exit        # quit
```

## Special CLio-only commands

Some commands are most natural in the CLI, including:

- `/configure` — open the configuration screen (applies changes live).
- `/reconfigure` — interactive reconfiguration wizard.
- `/status`, `/settings` — inspect state.
- `/llm_models`, `/llm_search <query>` — browse models.
- `/llm_default`, `/llm_lock`, `/llm_unlock` — manage the provider/model guardrail.
- `/start`, `/stop` — toggle autonomous mode.
- `/clear_context` — wipe the context log (irreversible).

See the [Slash Commands](../usage/slash-commands.md) index for the complete list.

## Notes

- Uses `rich` and `prompt-toolkit` (see `requirements.txt`).
- Ctrl+C triggers a clean shutdown (`main.py` kills the process afterward).
- The CLI is the reference implementation other interfaces mirror.

See also: [Interfaces Overview](overview.md), [Autonomous Mode](../usage/autonomous-mode.md).
