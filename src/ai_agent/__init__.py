"""
Clio-Agent-1 AI Agent System
Autonomous Loop Architecture with 16+ AI provider support.
"""

import os as _os
import sys as _sys
from pathlib import Path as _Path

# Ensure the project root is on sys.path so that packages living outside
# src/ (e.g. external_integration/) can be imported via relative references
# (from ..external_integration import ...) that expect them inside ai_agent.
# This is the canonical fix for setups where external_integration/ sits at
# the project root rather than under src/ai_agent/.
_THIS_FILE = _Path(__file__).resolve()
_AI_AGENT_DIR = _THIS_FILE.parent  # src/ai_agent/
_PROJECT_ROOT = _AI_AGENT_DIR.parent.parent  # project root
_EXTERNAL_DIR = _PROJECT_ROOT / "external_integration"
_EXT_LINK = _AI_AGENT_DIR / "external_integration"

# Create a symlink (or junction on Windows) if the external_integration
# directory is not already reachable inside the ai_agent package.
if _EXTERNAL_DIR.is_dir() and not _EXT_LINK.exists():
    try:
        _EXT_LINK.symlink_to(_os.path.relpath(_EXTERNAL_DIR, _AI_AGENT_DIR))
    except OSError:
        # On Windows without symlink privileges or other restricted
        # environments, fall back to adding the project root to sys.path
        # so that 'import external_integration' works as a top-level package.
        _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
        if _PROJECT_ROOT_STR not in _sys.path:
            _sys.path.insert(0, _PROJECT_ROOT_STR)
elif not _EXTERNAL_DIR.is_dir() and not _EXT_LINK.exists():
    # external_integration doesn't exist at all — add project root anyway
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in _sys.path:
        _sys.path.insert(0, _PROJECT_ROOT_STR)

__version__ = "3.0.0"
__author__ = "Clio Agent 1 Project"
__description__ = "Autonomous loop AI-powered terminal automation system"

from .core_processing.autonomous_loop_engine import AutonomousLoopEngine
from .platform_abstraction.platform_detector import PlatformDetector
from .external_integration.model_runner import ModelRunner

__all__ = [
    "AutonomousLoopEngine",
    "PlatformDetector",
    "ModelRunner",
]
