#!/usr/bin/env python3
"""
Clio-Agent-2 launcher — the single command you need.

Start the project from the repository root with just:

    python3 run.py            # Auto-setup + launch CLI (configures on first run)
    python3 run.py --telegram # Run Telegram bot
    python3 run.py --discord  # Run Discord bot
    python3 run.py --whatsapp # Run WhatsApp Business API bot
    python3 run.py --all      # Run all configured interfaces
    python3 run.py setup      # (or --setup / config / configure) Open the
                         #   configuration screen, then exit
    python3 run.py status     # (or --status) Show configuration status

On the very first run (no API key / model configured) the launcher walks you
through setup interactively, so the entire experience is just one command.
Use --no-setup to skip the first-run prompt and launch immediately.

This thin wrapper locates the real entry point (clio_agent_2/main.py) and
re-executes it with the same command-line arguments. This means the full
automatic environment setup performed by main.py (virtual environment creation,
dependency installation, configuration file generation) still runs exactly as if
main.py had been launched directly.

The launcher relies only on the Python standard library, so it works in any
environment (Linux/macOS/Windows) without installing any extra dependencies.
"""

import os
import sys
from pathlib import Path


def main():
    # Resolve the repository root (directory containing this file).
    repo_root = Path(__file__).resolve().parent
    entry_point = repo_root / "clio_agent_2" / "main.py"

    if not entry_point.exists():
        print(
            "❌ Could not find the Clio-Agent-2 entry point at:\n"
            f"   {entry_point}\n"
            "Please make sure this launcher sits in the project root, next to "
            "the 'clio_agent_2' package.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Re-execute main.py with the same arguments. os.execve replaces the current
    # process, so signals (e.g. Ctrl+C) and the exit code propagate normally and
    # no extra parent process is left running.
    # os.execve is available on both Unix and Windows, and we only use the
    # standard library, keeping this launcher portable across environments.
    python_executable = sys.executable
    os.execve(
        python_executable,
        [python_executable, str(entry_point)] + sys.argv[1:],
        os.environ.copy(),
    )


if __name__ == "__main__":
    main()
