"""
VEXIS-CLI GUI — Application Entry Point
"""

import sys
import os

# Add project root and src to path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase

from gui.theme import Theme
from gui.main_window import MainWindow


def run():
    """Launch the VEXIS-CLI GUI application."""

    # Enable high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("VEXIS")
    app.setApplicationVersion("3.0.0")
    app.setOrganizationName("VEXIS")

    # Apply global stylesheet
    app.setTheme = lambda: None  # placeholder
    app.setStyleSheet(Theme.stylesheet())

    # Set application font
    app_font = Theme.font(Theme.FONT_SIZE_MD)
    app.setFont(app_font)

    # Create and show main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    run()
