# Directory Structure

```
Clio-Agent-2/
├── run.py                       # The ONLY command you run (re-launches main.py)
├── README.md                    # Project README
├── docs/                        # ← This documentation tree
├── config/                      # ⚠️ stray/legacy copy — NOT used; edit clio_agent_2/config
└── clio_agent_2/
    ├── main.py                  # Real entry point: auto-setup, venv, deps, launch
    ├── requirements.txt         # Python dependencies
    ├── apply_fixes.py           # One-off patch script (see below)
    ├── meta_controller.py       # Stuck-detection watchdog + meta-LLM
    ├── config/
    │   ├── .env                 # ★ CANONICAL config (keys, model, tokens)
    │   ├── .env.example         # Template copied on first run
    │   ├── config.yaml          # Readable mirror (no secrets)
    │   ├── settings.py          # Config loading / persistence
    │   └── (setup via `python3 run.py setup`)
    ├── core/
    │   ├── agent.py             # The brain: message handling + autonomous loop
    │   ├── context_manager.py   # Context log + compression + persistence
    │   ├── llm_router.py        # Multi-provider LLM routing
    │   └── retry.py             # Generic retry-with-backoff helper
    ├── interfaces/
    │   ├── cli.py               # Terminal interface
    │   ├── telegram.py          # Telegram bot
    │   └── discord.py           # Discord bot (Beta)
    ├── tools/
    │   └── tool_registry.py     # File/web/search/shell/thinking/say tools
    └── utils/
        └── __init__.py          # Utility functions
```

## Notes

- **`config/` vs `clio_agent_2/config/`.** Only `clio_agent_2/config/` is used by
  the running agent. The root `config/` is a leftover and is ignored — a common
  source of confusion.
- **`clio_agent_2/config/.env`** is the canonical configuration file. Edit it
  directly, or use `/configure`, `/config`, or the wizard.
- **`clio_agent_2/config/config.yaml`** is a generated, secret-free mirror for
  easy reading. It is written by `Config` whenever settings change.
- **`apply_fixes.py`** is a self-contained, no-dependency patch script that fixes
  known issues on a fresh install (token placeholder bypass, `get_event_loop`
  deprecation, optional `dotenv` import). Run it from the project root:
  `python3 clio_agent_2/apply_fixes.py`.

See also: [Entry Point & Auto-Setup](entry-point.md), [Configuration Reference](../CONFIGURATION.md).
