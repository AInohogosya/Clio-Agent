#!/usr/bin/env python3
"""
Clio-Agent global entry point.

This module is the console_scripts entry point defined in pyproject.toml.
It locates run.py in the project root and delegates to it via os.execv,
replacing the current process so that run.py's signal handling and
process management work correctly.
"""

import sys
import os
from pathlib import Path


def main():
    """Delegate to run.py in the project root."""
    # This file lives in src/ (added to sys.path by the editable install).
    # The project root is one level up.
    project_root = Path(__file__).parent.parent.resolve()
    run_py = project_root / "run.py"

    if not run_py.exists():
        print("Error: run.py not found in", project_root, file=sys.stderr)
        sys.exit(1)

    # Replace this process with run.py so that signal handlers,
    # os.execv restarts, and PID-based logic all work correctly.
    os.execv(sys.executable, [sys.executable, str(run_py)] + sys.argv[1:])


if __name__ == "__main__":
    main()
