# Entry Point & Auto-Setup

The user-facing command is always `python3 run.py` (from the project root). It is
a thin, **standard-library-only** wrapper that re-executes the real entry point
with the same arguments.

## `run.py`

- Resolves the repo root (the directory containing `run.py`).
- Confirms `clio_agent_2/main.py` exists; exits with an error if not.
- Uses `os.execve` to replace the current process with
  `python <repo>/clio_agent_2/main.py [args...]`, preserving the environment.
- Because it `exec`s, signals (Ctrl+C) and the exit code propagate normally, and
  no extra parent process is left running.

This means the full automatic environment setup still runs exactly as if
`main.py` had been launched directly.

## `clio_agent_2/main.py` — the real entry point

`main.py` performs, in order:

1. **System detection & compatibility** (`detect_system_info`,
   `check_system_requirements`) — prints OS, Python version, architecture, and
   checks write permissions. Requires Python ≥ 3.8.
2. **Virtual environment** (`ensure_virtual_environment`) — if not already in a
   venv, it creates `.venv` and re-runs the script inside it.
3. **Dependency installation** — installs `clio_agent_2/requirements.txt` into the
   venv if needed.
4. **Configuration** — creates `clio_agent_2/config/.env` from the template; on the
   first run (no real API key + model) opens the interactive setup screen
   (`auto_configure_if_needed`). Use `--no-setup` to skip.
5. **Launch** — prints a banner and the configuration status, then runs the chosen
   interface via `asyncio.run(...)`.

## CLI flags & subcommands

| Invocation | Effect |
|------------|--------|
| `python3 run.py` | Auto-setup + launch CLI (configures on first run). |
| `python3 run.py --telegram` | Run the Telegram bot. |
| `python3 run.py --discord` | Run the Discord bot (Beta). |
| `python3 run.py --all` | Run all configured interfaces. |
| `python3 run.py setup` / `config` / `configure` | Open the setup screen, then exit. |
| `python3 run.py status` / `--status` | Show configuration status, then exit. |
| `python3 run.py --no-setup` | Skip first-run setup and launch immediately. |
| `python3 run.py --config <path>` | Use a custom `.env` file. |
| `python3 run.py help` / `-h` / `--help` | Print help. |

When `setup`/`config`/`configure` is given extra flags (e.g.
`--openai sk-... --provider openai --model gpt-4o`), those are forwarded to the
setup screen's **batch (non-interactive)** mode via
`apply_overrides_from_argv`.

## Fatal-error handling

If an interface raises an unexpected exception, `main.py` prints a graceful
message and exits. On `KeyboardInterrupt` (Ctrl+C) or `SIGTERM`, the
running loop is stopped and **the context log is flushed to disk** before the
process exits (via the signal handler, an `atexit` hook, and the run-loop
`finally`), so the latest context survives the restart.

See also: [Setup Wizard](configuration/setup-wizard.md), [Getting Started](../getting-started.md).
