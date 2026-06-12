#!/usr/bin/env python3
"""
VEXIS-CLI GUI Launcher
Entry point for the .app bundle.
"""

import sys
import os

# Ensure the project root is in the path
if getattr(sys, 'frozen', False):
    # Running as bundled app
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
else:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "src"))

from gui.app import run

if __name__ == "__main__":
    run()
