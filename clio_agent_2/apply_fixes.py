#!/usr/bin/env python3
"""apply_fixes.py - self-contained, no external dependencies.

FIX 1: `python3 main.py --telegram` errors on a fresh install.
  setup_environment() seeds config/.env with the NON-EMPTY placeholder
  TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here. The old check
  `not token or not token.strip()` let the placeholder through, so the
  bot tried to connect with a bogus token and Telegram rejected it.
  We add `_is_token_configured()` (also rejects your_.../<...>) and use
  it in run_telegram()/run_discord()/run_all(). We also swap the
  deprecated `asyncio.get_event_loop()` (raises RuntimeError on Python
  3.12+/3.14 with no running loop) for `asyncio.get_running_loop()`.

FIX 2: `python3 run.py setup` crashes with
  "ModuleNotFoundError: No module named 'dotenv'".
  config/settings.py does a HARD top-level `from dotenv import
  load_dotenv`. When run with a Python lacking python-dotenv (e.g. the
  system Python, not the venv), importing Config crashes the script.
  We make that import OPTIONAL and add a tiny pure-stdlib .env loader
  fallback, so token loading works in every environment with no new deps.

Usage:  python3 apply_fixes.py   (run from the project root)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _patch(path, replacements, label):
    if not path.exists():
        print("  [WARN] %s: file %s does not exist — skipped" % (label, path.name))
        return
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        count = text.count(old)
        if count == 0:
            print("  [WARN] %s: pattern not found (already patched?), skipped:\n        %r" % (label, old))
            continue
        text = text.replace(old, new)
        print("  [OK]   %s: replaced %d occurrence(s) of:\n        %r" % (label, count, old))
    if text != original:
        backup = path.with_suffix(path.suffix + ".pre_apply_fix")
        print("  -> backing up to %s" % backup.name)
        backup.write_text(original, encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        print("  -> wrote %s\n" % path.name)
    else:
        print("  -> no changes made to %s\n" % path.name)


HELPER = '''def _is_token_configured(token):
    """Return True only if `token` is a real, usable token."""
    if not token or not str(token).strip():
        return False
    stripped = str(token).strip()
    lowered = stripped.lower()
    if lowered.startswith("your_") or "placeholder" in lowered:
        return False
    if "<" in stripped or ">" in stripped:
        return False
    return True


'''


DOTENV_BLOCK = '''try:
    from dotenv import load_dotenv
except ImportError:
    import os as _os

    def load_dotenv(dotenv_path=None, override=False, **_kwargs):
        """Load a .env file into os.environ using only the standard library."""
        candidates = [dotenv_path] if dotenv_path else [".env", "config/.env"]
        for _path in candidates:
            if not _path or not _os.path.isfile(_path):
                continue
            try:
                with open(_path, "r", encoding="utf-8") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if not _line or _line.startswith("#") or "=" not in _line:
                            continue
                        _k, _, _v = _line.partition("=")
                        _k, _v = _k.strip(), _v.strip().strip('"').strip("'} ")
                        if _k and (override or _k not in _os.environ):
                            _os.environ[_k] = _v
            except OSError:
                pass
            return True
        return False

'''


def _is_token_configured(token):
    """Return True only if `token` is a real, usable token."""
    if not token or not str(token).strip():
        return False
    stripped = str(token).strip()
    lowered = stripped.lower()
    if lowered.startswith("your_") or "placeholder" in lowered:
        return False
    if "<" in stripped or ">" in stripped:
        return False
    if "xxxx" in lowered:
        return False
    if lowered.startswith("sk-your"):
        return False
    return True


def main():
    print("Applying Clio-Agent-2 fixes...\n")

    main_py = ROOT / "main.py"
    main_repls = [
        (
            "async def run_cli():",
            HELPER + "async def run_cli():",
        ),
        (
            "    if not config.telegram_bot_token or not config.telegram_bot_token.strip():",
            "    if not _is_token_configured(config.telegram_bot_token):",
        ),
        (
            "    if not config.discord_bot_token:",
            "    if not _is_token_configured(config.discord_bot_token):",
        ),
        (
            "    if config.telegram_bot_token and config.telegram_bot_token.strip():",
            "    if _is_token_configured(config.telegram_bot_token):",
        ),
        (
            "    if config.discord_bot_token and config.discord_bot_token.strip():",
            "    if _is_token_configured(config.discord_bot_token):",
        ),
        (
            "asyncio.get_event_loop()",
            "asyncio.get_running_loop()",
        ),
    ]
    print("[main.py]")
    _patch(main_py, main_repls, "main.py")

    settings_py = ROOT / "config" / "settings.py"
    print("[config/settings.py]")
    _patch(
        settings_py,
        [
            (
                "from dotenv import load_dotenv",
                DOTENV_BLOCK.rstrip("\n"),
            ),
        ],
        "settings.py",
    )

    tg_py = ROOT / "interfaces" / "telegram.py"
    print("[interfaces/telegram.py]")
    _patch(
        tg_py,
        [
            (
                "asyncio.get_event_loop().time(),",
                "asyncio.get_running_loop().time(),",
            ),
        ],
        "telegram.py",
    )

    dc_py = ROOT / "interfaces" / "discord.py"
    print("[interfaces/discord.py]")
    _patch(
        dc_py,
        [
            (
                "asyncio.get_event_loop().time(),",
                "asyncio.get_running_loop().time(),",
            ),
        ],
        "discord.py",
    )

    cli_py = ROOT / "interfaces" / "cli.py"
    print("[interfaces/cli.py]")
    _patch(
        cli_py,
        [
            (
                "asyncio.get_event_loop()",
                "asyncio.get_running_loop()",
            ),
        ],
        "cli.py",
    )

    print("Done. Set a token with: python3 run.py setup --telegram-token YOUR_TOKEN")
    print("Then run:              python3 run.py --telegram")


if __name__ == "__main__":
    main()
