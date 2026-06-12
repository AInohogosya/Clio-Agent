"""
VEXIS-CLI GUI — Main Window
Premium dark-themed main window with sidebar navigation.
"""

import sys
import os
import subprocess
import threading
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QLabel, QPushButton, QFrame,
    QScrollArea, QTextEdit, QLineEdit, QSizePolicy,
    QSpacerItem, QToolButton, QMenu, QApplication,
    QGraphicsDropShadowEffect, QDialog, QComboBox,
    QCheckBox, QSpinBox, QGroupBox, QFormLayout,
    QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QProgressBar,
    QSplitter, QPlainTextEdit, QToolTip
)
from PyQt6.QtCore import (
    Qt, QSize, QTimer, pyqtSignal, QObject, QThread,
    QPropertyAnimation, QEasingCurve, QRect, QPoint,
    QParallelAnimationGroup, QSequentialAnimationGroup
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QBrush,
    QPen, QLinearGradient, QRadialGradient, QIcon,
    QAction, QKeySequence, QShortcut, QCursor,
    QTextCursor, QPalette
)

from gui.theme import Theme
from gui.resources import IconProvider


# ── Signal Bridge for Thread-Safe UI Updates ─────────────────────
class SignalBridge(QObject):
    """Bridge for emitting signals from worker threads to the UI."""
    message_received = pyqtSignal(str, str)  # role, content
    status_changed = pyqtSignal(str)
    terminal_output = pyqtSignal(str)
    agent_started = pyqtSignal()
    agent_stopped = pyqtSignal()
    error_occurred = pyqtSignal(str)
    thinking_started = pyqtSignal()
    thinking_stopped = pyqtSignal()


# ── Custom Widgets ───────────────────────────────────────────────

class SidebarButton(QPushButton):
    """Custom sidebar navigation button with icon and label."""

    def __init__(self, icon_name, text, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setText(f"  {text}")
        self.setIcon(IconProvider.get_icon(icon_name, 20, Theme.TEXT_SECONDARY))
        self.setIconSize(QSize(20, 20))
        self.setCheckable(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(Theme.SIDEBAR_ITEM_HEIGHT)
        self.setStyleSheet(f"""
            SidebarButton {{
                background-color: transparent;
                color: {Theme.TEXT_SECONDARY};
                border: none;
                border-radius: {Theme.RADIUS_SM}px;
                padding: 0px 16px;
                text-align: left;
                font-size: {Theme.FONT_SIZE_MD}pt;
                font-weight: 500;
            }}
            SidebarButton:hover {{
                background-color: {Theme.BG_HOVER};
                color: {Theme.TEXT_PRIMARY};
            }}
            SidebarButton:checked {{
                background-color: {Theme.ACCENT_GLOW};
                color: {Theme.ACCENT_SECONDARY};
                font-weight: 600;
            }}
        """)


class ChatBubble(QWidget):
    """A single chat message bubble."""

    def __init__(self, role, content, timestamp=None, parent=None):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now().strftime("%H:%M")
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 4)
        layout.setSpacing(12)

        # Avatar
        avatar = QLabel()
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self.role == "user":
            avatar.setPixmap(IconProvider.get_pixmap("user", 32, Theme.ACCENT_SECONDARY))
            avatar.setStyleSheet(f"""
                QLabel {{
                    background-color: {Theme.ACCENT_GLOW};
                    border-radius: 16px;
                    border: 2px solid {Theme.ACCENT_PRIMARY};
                }}
            """)
        elif self.role == "assistant":
            avatar.setPixmap(IconProvider.get_pixmap("bot", 32, Theme.ACCENT_PRIMARY))
            avatar.setStyleSheet(f"""
                QLabel {{
                    background-color: {Theme.BG_ELEVATED};
                    border-radius: 16px;
                    border: 2px solid {Theme.BORDER_DEFAULT};
                }}
            """)
        else:  # system
            avatar.setPixmap(IconProvider.get_pixmap("info", 32, Theme.TEXT_TERTIARY))
            avatar.setStyleSheet(f"""
                QLabel {{
                    background-color: {Theme.BG_TERTIARY};
                    border-radius: 16px;
                }}
            """)

        # Content area
        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Role label + timestamp
        header = QHBoxLayout()
        role_label = QLabel("You" if self.role == "user" else "VEXIS" if self.role == "assistant" else "System")
        role_label.setStyleSheet(f"""
            color: {Theme.TEXT_SECONDARY if self.role == 'user' else Theme.ACCENT_SECONDARY};
            font-size: {Theme.FONT_SIZE_XS}pt;
            font-weight: 600;
        """)
        header.addWidget(role_label)
        header.addStretch()
        ts_label = QLabel(self.timestamp)
        ts_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: {Theme.FONT_SIZE_XS}pt;")
        header.addWidget(ts_label)
        content_layout.addLayout(header)

        # Message content
        content_label = QLabel(self.content)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        content_label.setOpenExternalLinks(True)
        content_label.setMaximumWidth(680)

        if self.role == "user":
            content_label.setStyleSheet(f"""
                QLabel {{
                    background-color: {Theme.ACCENT_PRIMARY};
                    color: {Theme.TEXT_ON_ACCENT};
                    border-radius: {Theme.CHAT_BUBBLE_RADIUS}px;
                    border-top-right-radius: 4px;
                    padding: 12px 16px;
                    font-size: {Theme.FONT_SIZE_MD}pt;
                    line-height: 1.5;
                }}
            """)
            layout.addStretch()
            layout.addWidget(content_label)
            layout.addWidget(avatar)
        elif self.role == "assistant":
            content_label.setStyleSheet(f"""
                QLabel {{
                    background-color: {Theme.BG_ELEVATED};
                    color: {Theme.TEXT_PRIMARY};
                    border-radius: {Theme.CHAT_BUBBLE_RADIUS}px;
                    border-top-left-radius: 4px;
                    padding: 12px 16px;
                    font-size: {Theme.FONT_SIZE_MD}pt;
                    line-height: 1.5;
                    border: 1px solid {Theme.BORDER_DEFAULT};
                }}
            """)
            layout.addWidget(avatar)
            layout.addWidget(content_label)
            layout.addStretch()
        else:
            content_label.setStyleSheet(f"""
                QLabel {{
                    background-color: {Theme.BG_TERTIARY};
                    color: {Theme.TEXT_SECONDARY};
                    border-radius: {Theme.RADIUS_SM}px;
                    padding: 8px 14px;
                    font-size: {Theme.FONT_SIZE_SM}pt;
                    font-style: italic;
                }}
            """)
            layout.addWidget(avatar)
            layout.addWidget(content_label)
            layout.addStretch()


class ThinkingIndicator(QWidget):
    """Animated thinking indicator shown while agent is processing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._dot_count = 0

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)

        avatar = QLabel()
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(IconProvider.get_pixmap("bot", 32, Theme.ACCENT_PRIMARY))
        avatar.setStyleSheet(f"""
            QLabel {{
                background-color: {Theme.BG_ELEVATED};
                border-radius: 16px;
                border: 2px solid {Theme.BORDER_DEFAULT};
            }}
        """)

        self.label = QLabel("Thinking")
        self.label.setStyleSheet(f"""
            QLabel {{
                background-color: {Theme.BG_ELEVATED};
                color: {Theme.TEXT_SECONDARY};
                border-radius: {Theme.CHAT_BUBBLE_RADIUS}px;
                border-top-left-radius: 4px;
                padding: 12px 16px;
                font-size: {Theme.FONT_SIZE_MD}pt;
                font-style: italic;
                border: 1px solid {Theme.BORDER_DEFAULT};
            }}
        """)

        layout.addWidget(avatar)
        layout.addWidget(self.label)
        layout.addStretch()

    def start(self):
        self._timer.start(400)
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _animate(self):
        self._dot_count = (self._dot_count + 1) % 4
        self.label.setText("Thinking" + "." * self._dot_count)


class StatusBadge(QWidget):
    """Small status indicator badge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = "idle"
        self.setFixedSize(10, 10)
        self._update_style()

    def set_status(self, status):
        self._status = status
        self._update_style()
        self.update()

    def _update_style(self):
        colors = {
            "idle": Theme.TEXT_TERTIARY,
            "running": Theme.SUCCESS,
            "error": Theme.ERROR,
            "warning": Theme.WARNING,
        }
        self._color = colors.get(self._status, Theme.TEXT_TERTIARY)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(self._color)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 10, 10)


# ── Page Widgets ─────────────────────────────────────────────────

class ChatPage(QWidget):
    """Main chat interface page."""

    send_message = pyqtSignal(str)

    def __init__(self, signal_bridge, parent=None):
        super().__init__(parent)
        self.signal_bridge = signal_bridge
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Chat scroll area ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setSpacing(4)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.addStretch()

        self.scroll.setWidget(self.chat_container)
        layout.addWidget(self.scroll, 1)

        # ── Thinking indicator ──
        self.thinking = ThinkingIndicator()
        self.thinking.hide()
        layout.addWidget(self.thinking)

        # ── Input area ──
        input_frame = QFrame()
        input_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border-top: 1px solid {Theme.BORDER_DEFAULT};
                padding: 0px;
            }}
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(16, 12, 16, 12)
        input_layout.setSpacing(10)

        # Input field
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type your instruction... (⌘+Enter to send)")
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_LG}px;
                padding: 14px 18px;
                font-size: {Theme.FONT_SIZE_MD}pt;
                selection-background-color: {Theme.ACCENT_PRIMARY};
            }}
            QLineEdit:focus {{
                border-color: {Theme.ACCENT_PRIMARY};
                background-color: {Theme.BG_PRIMARY};
            }}
            QLineEdit::placeholder {{
                color: {Theme.TEXT_PLACEHOLDER};
            }}
        """)
        self.input_field.setMinimumHeight(52)
        self.input_field.returnPressed.connect(self._on_send)
        input_layout.addWidget(self.input_field, 1)

        # Send button
        self.send_btn = QPushButton()
        self.send_btn.setIcon(IconProvider.get_icon("send", 20, Theme.TEXT_ON_ACCENT))
        self.send_btn.setIconSize(QSize(20, 20))
        self.send_btn.setFixedSize(52, 52)
        self.send_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                border: none;
                border-radius: 26px;
            }}
            QPushButton:hover {{
                background-color: {Theme.ACCENT_TERTIARY};
            }}
            QPushButton:pressed {{
                background-color: {Theme.ACCENT_SECONDARY};
            }}
            QPushButton:disabled {{
                background-color: {Theme.BG_ACTIVE};
            }}
        """)
        self.send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(self.send_btn)

        # Stop button (hidden by default)
        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(IconProvider.get_icon("stop", 18, Theme.TEXT_ON_ACCENT))
        self.stop_btn.setIconSize(QSize(18, 18))
        self.stop_btn.setFixedSize(52, 52)
        self.stop_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ERROR};
                border: none;
                border-radius: 26px;
            }}
            QPushButton:hover {{
                background-color: {Theme.ERROR};
            }}
        """)
        self.stop_btn.hide()
        input_layout.addWidget(self.stop_btn)

        layout.addWidget(input_frame)

    def _connect_signals(self):
        self.signal_bridge.message_received.connect(self._on_message)
        self.signal_bridge.thinking_started.connect(self._on_thinking_started)
        self.signal_bridge.thinking_stopped.connect(self._on_thinking_stopped)

    def _on_send(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self._add_bubble("user", text)
        self.send_message.emit(text)

    def _add_bubble(self, role, content):
        bubble = ChatBubble(role, content)
        # Insert before the stretch
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        # Auto-scroll
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        )

    def _on_message(self, role, content):
        self._add_bubble(role, content)

    def _on_thinking_started(self):
        self.thinking.start()
        self.send_btn.hide()
        self.stop_btn.show()

    def _on_thinking_stopped(self):
        self.thinking.stop()
        self.send_btn.show()
        self.stop_btn.hide()

    def add_system_message(self, text):
        self._add_bubble("system", text)

    def set_running(self, running):
        self.send_btn.setEnabled(not running)
        if running:
            self.send_btn.hide()
            self.stop_btn.show()
        else:
            self.send_btn.show()
            self.stop_btn.hide()


class TerminalPage(QWidget):
    """Terminal output page showing agent execution logs."""

    def __init__(self, signal_bridge, parent=None):
        super().__init__(parent)
        self.signal_bridge = signal_bridge
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QFrame()
        toolbar.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border-bottom: 1px solid {Theme.BORDER_DEFAULT};
            }}
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel("Terminal Output")
        title.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-weight: 600; font-size: {Theme.FONT_SIZE_SM}pt;")
        toolbar_layout.addWidget(title)
        toolbar_layout.addStretch()

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {Theme.TEXT_SECONDARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_SM}px;
                padding: 4px 12px;
                font-size: {Theme.FONT_SIZE_SM}pt;
            }}
            QPushButton:hover {{
                background-color: {Theme.BG_HOVER};
                color: {Theme.TEXT_PRIMARY};
            }}
        """)
        self.clear_btn.clicked.connect(self.clear_output)
        toolbar_layout.addWidget(self.clear_btn)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.copy_btn.setStyleSheet(self.clear_btn.styleSheet())
        self.copy_btn.clicked.connect(self.copy_output)
        toolbar_layout.addWidget(self.copy_btn)

        layout.addWidget(toolbar)

        # Terminal text area
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.terminal.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {Theme.BG_PRIMARY};
                color: {Theme.TEXT_PRIMARY};
                border: none;
                padding: 12px 16px;
                font-family: {Theme.FONT_MONO}, {", ".join(f'"{f}"' for f in Theme.FONT_MONO_FALLBACK)};
                font-size: {Theme.FONT_SIZE_SM}pt;
                line-height: 1.6;
                selection-background-color: {Theme.ACCENT_PRIMARY};
                selection-color: {Theme.TEXT_ON_ACCENT};
            }}
        """)
        layout.addWidget(self.terminal, 1)

    def _connect_signals(self):
        self.signal_bridge.terminal_output.connect(self.append_output)

    def append_output(self, text):
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)
        self.terminal.insertPlainText(text)
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)

    def clear_output(self):
        self.terminal.clear()

    def copy_output(self):
        QApplication.clipboard().setText(self.terminal.toPlainText())


class SettingsPage(QWidget):
    """Settings and configuration page."""

    config_changed = pyqtSignal()

    PROVIDERS = {
        "ollama": {"name": "Ollama (Local)", "needs_key": False},
        "google": {"name": "Google Gemini", "needs_key": True, "env_var": "GOOGLE_API_KEY"},
        "openai": {"name": "OpenAI", "needs_key": True, "env_var": "OPENAI_API_KEY"},
        "anthropic": {"name": "Anthropic Claude", "needs_key": True, "env_var": "ANTHROPIC_API_KEY"},
        "groq": {"name": "Groq", "needs_key": True, "env_var": "GROQ_API_KEY"},
        "deepseek": {"name": "DeepSeek", "needs_key": True, "env_var": "DEEPSEEK_API_KEY"},
        "mistral": {"name": "Mistral AI", "needs_key": True, "env_var": "MISTRAL_API_KEY"},
        "xai": {"name": "xAI Grok", "needs_key": True, "env_var": "XAI_API_KEY"},
        "meta": {"name": "Meta", "needs_key": True, "env_var": "META_API_KEY"},
        "cohere": {"name": "Cohere", "needs_key": True, "env_var": "COHERE_API_KEY"},
        "openrouter": {"name": "OpenRouter", "needs_key": True, "env_var": "OPENROUTER_API_KEY"},
        "together": {"name": "Together AI", "needs_key": True, "env_var": "TOGETHER_API_KEY"},
        "minimax": {"name": "MiniMax", "needs_key": True, "env_var": "MINIMAX_API_KEY"},
        "zhipuai": {"name": "Zhipu AI", "needs_key": True, "env_var": "ZHIPUAI_API_KEY"},
        "microsoft": {"name": "Microsoft Azure", "needs_key": True, "env_var": "AZURE_API_KEY"},
        "amazon": {"name": "Amazon Bedrock", "needs_key": True, "env_var": "AWS_ACCESS_KEY_ID"},
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_current_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Scroll area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setMaximumWidth(720)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(32, 24, 32, 24)
        container_layout.setSpacing(24)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        # ── Header ──
        header = QLabel("⚙️  Settings")
        header.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: {Theme.FONT_SIZE_2X}pt; font-weight: 700;")
        container_layout.addWidget(header)

        subtitle = QLabel("Configure your AI provider, model, and execution preferences.")
        subtitle.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: {Theme.FONT_SIZE_MD}pt; margin-bottom: 8px;")
        container_layout.addWidget(subtitle)

        # ── Provider Section ──
        provider_group = self._create_group("AI Provider", "Select and configure your AI provider.")
        provider_layout = QFormLayout()
        provider_layout.setSpacing(12)
        provider_layout.setContentsMargins(0, 8, 0, 0)

        # Provider selector
        provider_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        for key, info in self.PROVIDERS.items():
            self.provider_combo.addItem(f"  {info['name']}", key)
        self.provider_combo.setMinimumHeight(40)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo, 1)
        provider_layout.addRow("Provider:", provider_row)

        # API key input
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your API key...")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setMinimumHeight(40)
        self.api_key_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_SM}px;
                padding: 8px 14px;
                font-size: {Theme.FONT_SIZE_MD}pt;
            }}
            QLineEdit:focus {{
                border-color: {Theme.ACCENT_PRIMARY};
            }}
        """)
        provider_layout.addRow("API Key:", self.api_key_input)

        # Show key toggle
        self.show_key_btn = QPushButton("👁")
        self.show_key_btn.setFixedSize(36, 36)
        self.show_key_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.show_key_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_TERTIARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_SM}px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {Theme.BG_HOVER};
            }}
        """)
        self.show_key_btn.clicked.connect(self._toggle_key_visibility)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_input, 1)
        key_row.addWidget(self.show_key_btn)
        provider_layout.addRow("", key_row)

        provider_group.layout().addLayout(provider_layout)
        container_layout.addWidget(provider_group)

        # ── Model Section ──
        model_group = self._create_group("Model Configuration", "Choose the model to use for AI operations.")
        model_layout = QFormLayout()
        model_layout.setSpacing(12)
        model_layout.setContentsMargins(0, 8, 0, 0)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumHeight(40)
        self._populate_models("ollama")
        model_layout.addRow("Model:", self.model_combo)

        model_group.layout().addLayout(model_layout)
        container_layout.addWidget(model_group)

        # ── Execution Section ──
        exec_group = self._create_group("Execution", "Configure how the agent operates.")
        exec_layout = QFormLayout()
        exec_layout.setSpacing(12)
        exec_layout.setContentsMargins(0, 8, 0, 0)

        self.cmd_timeout = QSpinBox()
        self.cmd_timeout.setRange(30, 7200)
        self.cmd_timeout.setValue(1800)
        self.cmd_timeout.setSuffix(" seconds")
        self.cmd_timeout.setMinimumHeight(36)
        exec_layout.addRow("Command Timeout:", self.cmd_timeout)

        self.task_timeout = QSpinBox()
        self.task_timeout.setRange(60, 28800)
        self.task_timeout.setValue(7200)
        self.task_timeout.setSuffix(" seconds")
        self.task_timeout.setMinimumHeight(36)
        exec_layout.addRow("Task Timeout:", self.task_timeout)

        self.max_iterations = QSpinBox()
        self.max_iterations.setRange(1, 5000)
        self.max_iterations.setValue(500)
        self.max_iterations.setMinimumHeight(36)
        exec_layout.addRow("Max Iterations:", self.max_iterations)

        self.debug_mode = QCheckBox("Enable debug mode")
        exec_layout.addRow("Debug:", self.debug_mode)

        self.self_heal = QCheckBox("Enable self-healing")
        exec_layout.addRow("Self-Heal:", self.self_heal)

        exec_group.layout().addLayout(exec_layout)
        container_layout.addWidget(exec_group)

        # ── Save Button ──
        save_btn = QPushButton("  Save Configuration  ")
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.setMinimumHeight(44)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                color: {Theme.TEXT_ON_ACCENT};
                border: none;
                border-radius: {Theme.RADIUS_SM}px;
                padding: 10px 24px;
                font-size: {Theme.FONT_SIZE_MD}pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Theme.ACCENT_TERTIARY};
            }}
            QPushButton:pressed {{
                background-color: {Theme.ACCENT_SECONDARY};
            }}
        """)
        save_btn.clicked.connect(self._save_config)
        container_layout.addWidget(save_btn)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

    def _create_group(self, title, subtitle=None):
        group = QGroupBox(f"  {title}  ")
        group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {Theme.BG_CARD};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_LG}px;
                margin-top: 14px;
                padding-top: 24px;
                padding-bottom: 16px;
                padding-left: 20px;
                padding-right: 20px;
                color: {Theme.TEXT_PRIMARY};
                font-size: {Theme.FONT_SIZE_LG}pt;
                font-weight: 600;
            }}
            QGroupBox::title {{
                color: {Theme.TEXT_PRIMARY};
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 8px;
            }}
        """)
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: {Theme.FONT_SIZE_SM}pt; font-weight: normal; margin-bottom: 4px;")
            layout.addWidget(sub)
        return group

    def _populate_models(self, provider):
        self.model_combo.clear()
        try:
            from src.ai_agent.utils.unified_model_selector import PROVIDER_MODELS
            provider_info = PROVIDER_MODELS.get(provider, {})
            for m in provider_info.get("models", []):
                self.model_combo.addItem(m["id"])
        except Exception:
            pass
        if self.model_combo.count() == 0:
            self.model_combo.addItem("(no models available — select provider first)")

    def _on_provider_changed(self, index):
        provider = self.provider_combo.itemData(index)
        self._populate_models(provider)
        needs_key = self.PROVIDERS.get(provider, {}).get("needs_key", False)
        self.api_key_input.setEnabled(needs_key)
        if not needs_key:
            self.api_key_input.clear()
            self.api_key_input.setPlaceholderText("No API key required for local models")
        else:
            self.api_key_input.setPlaceholderText("Enter your API key...")

    def _toggle_key_visibility(self):
        if self.api_key_input.echoMode() == QLineEdit.EchoMode.Password:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText("🙈")
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText("👁")

    def _load_current_config(self):
        """Load current config from config.yaml."""
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
        try:
            import yaml
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                if config:
                    provider = config.get("api", {}).get("preferred_provider", "ollama")
                    models = config.get("api", {}).get("models", {})
                    # Set provider
                    idx = self.provider_combo.findData(provider)
                    if idx >= 0:
                        self.provider_combo.setCurrentIndex(idx)
                    # Set model
                    model = models.get(provider, "")
                    if model:
                        self.model_combo.setCurrentText(model)
        except Exception:
            pass

    def _save_config(self):
        """Save configuration to config.yaml."""
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
        provider = self.provider_combo.currentData()
        model = self.model_combo.currentText().strip()
        api_key = self.api_key_input.text().strip()

        try:
            import yaml
            config = {}
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}

            config.setdefault("api", {})
            config["api"]["preferred_provider"] = provider
            config["api"].setdefault("models", {})
            config["api"]["models"][provider] = model

            if api_key and self.PROVIDERS.get(provider, {}).get("needs_key"):
                env_var = self.PROVIDERS[provider].get("env_var", "")
                if env_var:
                    os.environ[env_var] = api_key
                config["api"]["api_keys"] = config["api"].get("api_keys", {})
                config["api"]["api_keys"][provider] = api_key

            config.setdefault("execution", {})
            config["execution"]["command_timeout"] = self.cmd_timeout.value()
            config["execution"]["task_timeout"] = self.task_timeout.value()
            config["execution"]["max_iterations"] = self.max_iterations.value()
            config["execution"]["debug"] = self.debug_mode.isChecked()
            config["execution"]["self_heal"] = self.self_heal.isChecked()

            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            self.config_changed.emit()
            QMessageBox.information(self, "Settings Saved", "Configuration has been saved successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save configuration:\n{e}")


class HistoryPage(QWidget):
    """Conversation history page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QFrame()
        toolbar.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border-bottom: 1px solid {Theme.BORDER_DEFAULT};
            }}
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 10, 16, 10)

        title = QLabel("📋  Conversation History")
        title.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: {Theme.FONT_SIZE_LG}pt; font-weight: 600;")
        toolbar_layout.addWidget(title)
        toolbar_layout.addStretch()

        new_btn = QPushButton("  + New Chat  ")
        new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                color: {Theme.TEXT_ON_ACCENT};
                border: none;
                border-radius: {Theme.RADIUS_SM}px;
                padding: 8px 16px;
                font-size: {Theme.FONT_SIZE_SM}pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Theme.ACCENT_TERTIARY};
            }}
        """)
        toolbar_layout.addWidget(new_btn)

        layout.addWidget(toolbar)

        # History list
        self.history_list = QTableWidget()
        self.history_list.setColumnCount(4)
        self.history_list.setHorizontalHeaderLabels(["Date", "Provider", "Model", "Messages"])
        self.history_list.horizontalHeader().setStretchLastSection(True)
        self.history_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_list.setShowGrid(False)
        self.history_list.verticalHeader().setVisible(False)
        self.history_list.setMinimumHeight(400)
        self.history_list.setStyleSheet(f"""
            QTableWidget {{
                background-color: {Theme.BG_PRIMARY};
                border: none;
                color: {Theme.TEXT_PRIMARY};
                font-size: {Theme.FONT_SIZE_MD}pt;
                gridline-color: transparent;
                selection-background-color: {Theme.ACCENT_GLOW};
                selection-color: {Theme.TEXT_ACCENT};
            }}
            QTableWidget::item {{
                padding: 12px 8px;
                border-bottom: 1px solid {Theme.BORDER_SUBTLE};
            }}
            QTableWidget::item:selected {{
                background-color: {Theme.ACCENT_GLOW};
            }}
            QHeaderView::section {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_SECONDARY};
                padding: 12px 8px;
                border: none;
                border-bottom: 2px solid {Theme.BORDER_DEFAULT};
                font-weight: 600;
                font-size: {Theme.FONT_SIZE_SM}pt;
            }}
        """)

        # Sample data
        sample_data = [
            ["Today, 14:32", "OpenRouter", "owl-alpha", "12 messages"],
            ["Today, 11:15", "Ollama", "llama3.2", "8 messages"],
            ["Yesterday", "Google", "gemini-2.5-pro", "24 messages"],
            ["Yesterday", "OpenAI", "gpt-4o", "6 messages"],
            ["Jun 5", "Groq", "llama-3.3-70b", "15 messages"],
        ]
        self.history_list.setRowCount(len(sample_data))
        for i, row in enumerate(sample_data):
            for j, val in enumerate(row):
                item = QTableWidgetItem(val)
                if j == 0:
                    item.setForeground(QColor(Theme.TEXT_SECONDARY))
                self.history_list.setItem(i, j, item)

        layout.addWidget(self.history_list, 1)


# ── Main Window ──────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Main application window with sidebar navigation."""

    def __init__(self):
        super().__init__()
        self.signal_bridge = SignalBridge()
        self._agent_process = None
        self._agent_thread = None
        self._is_agent_running = False
        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self._apply_greeting()

    def _setup_window(self):
        self.setWindowTitle("VEXIS — AI Agent")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)
        self.setStyleSheet(f"QMainWindow {{ background-color: {Theme.BG_PRIMARY}; }}")

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Sidebar ──
        sidebar = self._create_sidebar()
        layout.addWidget(sidebar)

        # ── Content Stack ──
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"QStackedWidget {{ background-color: {Theme.BG_PRIMARY}; }}")

        self.chat_page = ChatPage(self.signal_bridge)
        self.terminal_page = TerminalPage(self.signal_bridge)
        self.settings_page = SettingsPage()
        self.history_page = HistoryPage()

        self.stack.addWidget(self.chat_page)
        self.stack.addWidget(self.terminal_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.history_page)

        layout.addWidget(self.stack, 1)

    def _create_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(Theme.SIDEBAR_WIDTH)
        sidebar.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SIDEBAR};
                border-right: 1px solid {Theme.BORDER_SUBTLE};
            }}
        """)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Logo area ──
        logo_frame = QFrame()
        logo_frame.setFixedHeight(72)
        logo_frame.setStyleSheet(f"border-bottom: 1px solid {Theme.BORDER_SUBTLE};")
        logo_layout = QHBoxLayout(logo_frame)
        logo_layout.setContentsMargins(20, 0, 20, 0)

        logo_icon = QLabel()
        logo_icon.setPixmap(IconProvider.get_pixmap("app_logo", 32))
        logo_icon.setFixedSize(32, 32)
        logo_layout.addWidget(logo_icon)

        logo_text = QLabel("VEXIS")
        logo_text.setStyleSheet(f"""
            color: {Theme.TEXT_PRIMARY};
            font-size: {Theme.FONT_SIZE_XL}pt;
            font-weight: 700;
            letter-spacing: 1px;
        """)
        logo_layout.addWidget(logo_text)

        # Status badge
        self.status_badge = StatusBadge()
        logo_layout.addWidget(self.status_badge)
        logo_layout.addStretch()

        layout.addWidget(logo_frame)

        # ── New Chat button ──
        new_chat_btn = QPushButton("  +  New Chat")
        new_chat_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        new_chat_btn.setFixedHeight(44)
        new_chat_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                color: {Theme.TEXT_ON_ACCENT};
                border: none;
                border-radius: {Theme.RADIUS_SM}px;
                margin: 12px 12px 4px 12px;
                text-align: left;
                font-size: {Theme.FONT_SIZE_MD}pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Theme.ACCENT_TERTIARY};
            }}
        """)
        new_chat_btn.clicked.connect(self._new_chat)
        layout.addWidget(new_chat_btn)

        # ── Navigation ──
        nav_label = QLabel("NAVIGATION")
        nav_label.setStyleSheet(f"""
            color: {Theme.TEXT_TERTIARY};
            font-size: {Theme.FONT_SIZE_XS}pt;
            font-weight: 600;
            letter-spacing: 1.5px;
            padding: 16px 20px 8px 20px;
        """)
        layout.addWidget(nav_label)

        self.nav_buttons = []
        nav_items = [
            ("chat", "Chat", 0),
            ("terminal", "Terminal", 1),
            ("history", "History", 2),
            ("settings", "Settings", 3),
        ]

        for icon_name, text, index in nav_items:
            btn = SidebarButton(icon_name, text)
            btn.clicked.connect(lambda checked, i=index: self._navigate(i))
            layout.addWidget(btn)
            self.nav_buttons.append(btn)

        # Select first by default
        self.nav_buttons[0].setChecked(True)

        layout.addStretch()

        # ── Bottom section ──
        bottom = QFrame()
        bottom.setStyleSheet(f"border-top: 1px solid {Theme.BORDER_SUBTLE};")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 12, 16, 12)

        version_label = QLabel("v3.0.0")
        version_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: {Theme.FONT_SIZE_XS}pt;")
        bottom_layout.addWidget(version_label)
        bottom_layout.addStretch()

        # Theme toggle (decorative for now)
        theme_btn = QPushButton("🌙")
        theme_btn.setFixedSize(32, 32)
        theme_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        theme_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_TERTIARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_SM}px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {Theme.BG_HOVER};
            }}
        """)
        bottom_layout.addWidget(theme_btn)

        layout.addWidget(bottom)

        return sidebar

    def _connect_signals(self):
        self.chat_page.send_message.connect(self._on_send_message)
        self.signal_bridge.agent_started.connect(self._on_agent_started)
        self.signal_bridge.agent_stopped.connect(self._on_agent_stopped)
        self.signal_bridge.error_occurred.connect(self._on_error)

    def _navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    def _new_chat(self):
        # Clear chat and go to chat page
        layout = self.chat_page.chat_layout
        while layout.count() > 1:  # keep stretch
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._navigate(0)
        self._apply_greeting()

    def _apply_greeting(self):
        greeting = (
            "Welcome to **VEXIS 3.0** — Your Autonomous AI Agent.\n\n"
            "I can help you with terminal automation, file management, "
            "code analysis, and much more. Just type your instruction below "
            "and I'll get to work.\n\n"
            "💡 **Tip:** Configure your AI provider in Settings to get started."
        )
        self.signal_bridge.message_received.emit("assistant", greeting)

    def _on_send_message(self, text):
        if self._is_agent_running:
            return
        self._start_agent(text)

    def _start_agent(self, instruction):
        """Start the agent with the given instruction."""
        self._is_agent_running = True
        self.status_badge.set_status("running")
        self.signal_bridge.thinking_started.emit()
        self.signal_bridge.terminal_output.emit(f"\n{'='*60}\n")
        self.signal_bridge.terminal_output.emit(f"[{datetime.now().strftime('%H:%M:%S')}] Starting agent...\n")
        self.signal_bridge.terminal_output.emit(f"Instruction: {instruction}\n")
        self.signal_bridge.terminal_output.emit(f"{'='*60}\n\n")

        # Run agent in a background thread
        self._agent_thread = AgentThread(instruction, self.signal_bridge)
        self._agent_thread.finished.connect(self._on_agent_finished)
        self._agent_thread.start()

    def _on_agent_finished(self):
        self._is_agent_running = False
        self.status_badge.set_status("idle")
        self.signal_bridge.thinking_stopped.emit()
        self.signal_bridge.terminal_output.emit(f"\n[{datetime.now().strftime('%H:%M:%S')}] Agent finished.\n")

    def _on_agent_started(self):
        self._is_agent_running = True
        self.status_badge.set_status("running")

    def _on_agent_stopped(self):
        self._is_agent_running = False
        self.status_badge.set_status("idle")

    def _on_error(self, error_msg):
        self.signal_bridge.message_received.emit("system", f"⚠️ Error: {error_msg}")
        self.status_badge.set_status("error")

    def closeEvent(self, event):
        """Clean up on window close."""
        if self._agent_process:
            try:
                self._agent_process.terminate()
            except Exception:
                pass
        event.accept()


# ── Agent Thread ─────────────────────────────────────────────────

class AgentThread(QThread):
    """Background thread for running the agent."""

    def __init__(self, instruction, signal_bridge, parent=None):
        super().__init__(parent)
        self.instruction = instruction
        self.signal_bridge = signal_bridge

    def run(self):
        try:
            project_root = Path(__file__).resolve().parents[1]
            run_script = project_root / "run.py"

            if not run_script.exists():
                self.signal_bridge.error_occurred.emit("run.py not found in project root.")
                return

            # Build command
            cmd = [
                sys.executable, str(run_script),
                self.instruction,
                "--no-prompt",
            ]

            env = os.environ.copy()

            self.signal_bridge.terminal_output.emit(f"$ {' '.join(cmd)}\n\n")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(project_root),
                env=env,
                bufsize=1,
            )

            # Read output line by line
            for line in process.stdout:
                self.signal_bridge.terminal_output.emit(line)

                # Parse output for chat messages
                stripped = line.strip()
                if stripped.startswith("VEXIS:") or stripped.startswith("Assistant:"):
                    content = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
                    self.signal_bridge.message_received.emit("assistant", content)
                elif stripped.startswith("Error:") or stripped.startswith("❌"):
                    self.signal_bridge.message_received.emit("system", stripped)

            process.wait()

            if process.returncode != 0:
                self.signal_bridge.terminal_output.emit(
                    f"\n[Exit code: {process.returncode}]\n"
                )

        except FileNotFoundError:
            self.signal_bridge.error_occurred.emit(
                f"Python executable not found: {sys.executable}"
            )
        except Exception as e:
            self.signal_bridge.error_occurred.emit(str(e))
