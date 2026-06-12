"""
VEXIS-CLI AI Agent System
Autonomous Loop Architecture with 16+ AI provider support.
"""

__version__ = "3.0.0"
__author__ = "VEXIS Project"
__description__ = "Autonomous loop AI-powered terminal automation system"

from .core_processing.autonomous_loop_engine import AutonomousLoopEngine
from .platform_abstraction.platform_detector import PlatformDetector
from .external_integration.model_runner import ModelRunner

__all__ = [
    "AutonomousLoopEngine",
    "PlatformDetector",
    "ModelRunner",
]
