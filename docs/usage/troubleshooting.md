# Usage → Troubleshooting

Common problems and how to fix them.

## Virtual environment issues

If automatic venv creation fails, set it up manually:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r clio_agent_2/requirements.txt
python3 run.py
```

## Dependency installation issues

```bash
pip install --upgrade pip
pip install -r clio_agent_2/requirements.txt --no-cache-dir
```

## `ModuleNotFoundError: No module named 'dotenv'`

`config/settings.py` makes the `python-dotenv` import **optional** (with a stdlib
fallback loader), but if you hit this with a bare system Python, run inside the
venv (the auto-setup creates one) or `pip install python-dotenv`.

## Telegram/Discord won't start on a fresh install

A placeholder token (`your_telegram_bot_token_here`) used to slip through and cause
a connect failure. `apply_fixes.py` adds `_is_token_configured`, which rejects
placeholders. Ensure a **real** token is set, then:

```bash
python3 run.py setup --telegram-token 123456:ABC
python3 run.py --telegram
```

## `asyncio` "get_event_loop" / no running loop errors

On Python 3.12+/3.14, `asyncio.get_event_loop()` raises when there's no running
loop. `apply_fixes.py` swaps it for `asyncio.get_running_loop()` across
`main.py` and the interfaces. Re-run it if you still see this:

```bash
python3 clio_agent_2/apply_fixes.py
```

## "Thinking loop won't start"

You **must** set `CURRENT_MODEL` (and a configured provider). Verify with:

```bash
python3 run.py status
```

## Two `config/` folders

Only `clio_agent_2/config/` is used. The root-level `config/` is a stray/legacy
copy and is ignored. Edit the one under `clio_agent_2/`.

## Prompt injection / unexpected behavior

If the agent acts on instructions from fetched web content, remember the **LLM
settings lock** blocks *model* switching but **not tool use**. Re-lock with
`/llm_lock` and review [Safety](../operations/safety.md).

See also: [Setup Wizard](../configuration/setup-wizard.md), [Known Limitations](../operations/known-limitations.md).
