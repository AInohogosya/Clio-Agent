#!/usr/bin/env python3
"""
Clio-Agent global entry point.

This module is the console_scripts entry point defined in pyproject.toml.
It locates run.py in the project root and delegates to it, replacing
the current process so that run.py's signal handling and process
management work correctly.

On Unix, uses os.execv() for true process replacement (same PID).
On Windows, uses os.execv() which creates a new process; the parent
exits immediately after.
"""

import sys
import os
from pathlib import Path

from ai_agent.utils.platform_compat import is_windows


def main():
    """Delegate to run.py in the project root."""
    # This file lives in src/ (added to sys.path by the editable install).
    # The project root is one level up.
    project_root = Path(__file__).parent.parent.resolve()
    run_py = project_root / "run.py"

    if not run_py.exists():
        print("Error: run.py not found in", project_root, file=sys.stderr)
        sys.exit(1)

    # On Unix, os.execv replaces the current process (same PID).
    # On Windows, os.execv creates a new process; we exit the parent
    # immediately to avoid a lingering parent process.
    args = [sys.executable, str(run_py)] + sys.argv[1:]
    if is_windows():
        # On Windows, spawn the child and exit the parent
        import subprocess
        proc = subprocess.Popen(args, cwd=str(project_root))
        # Exit immediately — the child runs independently
        sys.exit(proc.pid if proc.pid else 0)
    else:
        os.execv(sys.executable, args)


if __name__ == "__main__":
    main()
