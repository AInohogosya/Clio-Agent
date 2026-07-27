"""
Voice Conversation Page for GUI

Adds voice conversation capabilities to the main window.
"""

import logging
from typing import Optional
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QScrollArea, QSizePolicy, QProgressBar,
    QComboBox, QSlider, QCheckBox, QGroupBox, QFormLayout,
    QMessageBox, QApplication, QLineEdit
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QThread, pyqtSlot, QSize
)
from PyQt6.QtGui import QFont, QColor, QPixmap, QIcon, QPalette

from gui.theme import Theme
from gui.resources import IconProvider

logger = logging.getLogger(__name__)


class VoiceControlPanel(QWidget):
    """Voice conversation control panel."""
    
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    settings_changed = pyqtSignal(dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running = False
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Status indicator
        self.status_frame = QFrame()
        self.status_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_ELEVATED};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_LG}px;
                padding: 16px;
            }}
        """)
        status_layout = QVBoxLayout(self.status_frame)
        
        self.status_label = QLabel("Voice Conversation")
        self.status_label.setStyleSheet(f"""
            color: {Theme.TEXT_PRIMARY};
            font-size: {Theme.FONT_SIZE_XL}pt;
            font-weight: 600;
        """)
        status_layout.addWidget(self.status_label)
        
        self.state_label = QLabel("Stopped")
        self.state_label.setStyleSheet(f"""
            color: {Theme.TEXT_SECONDARY};
            font-size: {Theme.FONT_SIZE_MD}pt;
        """)
        status_layout.addWidget(self.state_label)
        
        # Volume indicator
        self.volume_bar = QProgressBar()
        self.volume_bar.setRange(0, 100)
        self.volume_bar.setValue(0)
        self.volume_bar.setTextVisible(False)
        self.volume_bar.setFixedHeight(8)
        self.volume_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Theme.BG_TERTIARY};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {Theme.ACCENT_PRIMARY};
                border-radius: 4px;
            }}
        """)
        status_layout.addWidget(self.volume_bar)
        
        layout.addWidget(self.status_frame)
        
        # Control buttons
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(12)
        
        self.start_btn = QPushButton("  Start Voice Chat")
        self.start_btn.setIcon(IconProvider.get_icon("mic", 20, Theme.TEXT_ON_ACCENT))
        self.start_btn.setIconSize(QSize(20, 20))
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setFixedHeight(48)
        self.start_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.SUCCESS};
                color: {Theme.TEXT_ON_ACCENT};
                border: none;
                border-radius: {Theme.RADIUS_MD}px;
                padding: 0 24px;
                font-size: {Theme.FONT_SIZE_MD}pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: {Theme.BG_ACTIVE};
                color: {Theme.TEXT_TERTIARY};
            }}
        """)
        self.start_btn.clicked.connect(self._on_start)
        controls_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("  Stop")
        self.stop_btn.setIcon(IconProvider.get_icon("stop", 20, Theme.TEXT_ON_ACCENT))
        self.stop_btn.setIconSize(QSize(20, 20))
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setFixedHeight(48)
        self.stop_btn.hide()
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ERROR};
                color: {Theme.TEXT_ON_ACCENT};
                border: none;
                border-radius: {Theme.RADIUS_MD}px;
                padding: 0 24px;
                font-size: {Theme.FONT_SIZE_MD}pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
        """)
        self.stop_btn.clicked.connect(self._on_stop)
        controls_layout.addWidget(self.stop_btn)
        
        layout.addLayout(controls_layout)
        
        # Settings
        settings_group = QGroupBox("  Settings  ")
        settings_group.setStyleSheet(f"""
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
        settings_layout = QFormLayout(settings_group)
        settings_layout.setSpacing(12)
        
        # STT Provider
        self.stt_combo = QComboBox()
        self.stt_combo.addItems(["Local (faster-whisper)", "OpenAI Whisper", "Groq Whisper"])
        self.stt_combo.setMinimumHeight(36)
        self.stt_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {Theme.BG_INPUT};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_SM}px;
                padding: 6px 12px;
                font-size: {Theme.FONT_SIZE_MD}pt;
            }}
            QComboBox:focus {{
                border-color: {Theme.ACCENT_PRIMARY};
            }}
        """)
        settings_layout.addRow("Speech Recognition:", self.stt_combo)
        
        # TTS Provider
        self.tts_combo = QComboBox()
        self.tts_combo.addItems(["Local (Piper)", "OpenAI TTS", "ElevenLabs", "Kokoro"])
        self.tts_combo.setMinimumHeight(36)
        self.tts_combo.setStyleSheet(self.stt_combo.styleSheet())
        settings_layout.addRow("Text-to-Speech:", self.tts_combo)
        
        # Voice selection
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(["Default", "Alloy", "Echo", "Fable", "Onyx", "Nova", "Shimmer"])
        self.voice_combo.setMinimumHeight(36)
        self.voice_combo.setStyleSheet(self.stt_combo.styleSheet())
        settings_layout.addRow("Voice:", self.voice_combo)
        
        # Camera
        self.camera_check = QCheckBox("Capture camera on speech end")
        self.camera_check.setChecked(True)
        self.camera_check.setStyleSheet(f"""
            QCheckBox {{
                color: {Theme.TEXT_PRIMARY};
                font-size: {Theme.FONT_SIZE_MD}pt;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {Theme.BORDER_DEFAULT};
                border-radius: 4px;
                background-color: {Theme.BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Theme.ACCENT_PRIMARY};
                border-color: {Theme.ACCENT_PRIMARY};
            }}
        """)
        settings_layout.addRow("", self.camera_check)
        
        # VAD Sensitivity
        vad_layout = QHBoxLayout()
        self.vad_slider = QSlider(Qt.Orientation.Horizontal)
        self.vad_slider.setRange(1, 100)
        self.vad_slider.setValue(30)
        self.vad_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {Theme.BG_TERTIARY};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {Theme.ACCENT_PRIMARY};
                width: 18px;
                height: 18px;
                border-radius: 9px;
                margin: -6px 0;
            }}
        """)
        self.vad_label = QLabel("30%")
        self.vad_label.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: {Theme.FONT_SIZE_SM}pt; min-width: 40px;")
        self.vad_slider.valueChanged.connect(lambda v: self.vad_label.setText(f"{v}%"))
        vad_layout.addWidget(self.vad_slider)
        vad_layout.addWidget(self.vad_label)
        settings_layout.addRow("VAD Sensitivity:", vad_layout)
        
        layout.addWidget(settings_group)
        layout.addStretch()
        
        # Connect settings changes
        self.stt_combo.currentTextChanged.connect(self._emit_settings)
        self.tts_combo.currentTextChanged.connect(self._emit_settings)
        self.voice_combo.currentTextChanged.connect(self._emit_settings)
        self.camera_check.toggled.connect(self._emit_settings)
        self.vad_slider.valueChanged.connect(self._emit_settings)
    
    def _on_start(self):
        self.start_requested.emit()
    
    def _on_stop(self):
        self.stop_requested.emit()
    
    def _emit_settings(self):
        settings = {
            "stt_provider": self.stt_combo.currentText(),
            "tts_provider": self.tts_combo.currentText(),
            "voice": self.voice_combo.currentText(),
            "camera_enabled": self.camera_check.isChecked(),
            "vad_sensitivity": self.vad_slider.value() / 100.0
        }
        self.settings_changed.emit(settings)
    
    def set_running(self, running: bool):
        """Update running state."""
        self._is_running = running
        self.start_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.state_label.setText("Listening..." if running else "Stopped")
        
        if running:
            self.status_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {Theme.SUCCESS}20;
                    border: 1px solid {Theme.SUCCESS};
                    border-radius: {Theme.RADIUS_LG}px;
                    padding: 16px;
                }}
            """)
        else:
            self.status_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {Theme.BG_ELEVATED};
                    border: 1px solid {Theme.BORDER_DEFAULT};
                    border-radius: {Theme.RADIUS_LG}px;
                    padding: 16px;
                }}
            """)
    
    def set_state(self, state: str):
        """Update state label."""
        self.state_label.setText(state)
    
    def set_volume(self, volume: float):
        """Update volume indicator (0.0 - 1.0)."""
        self.volume_bar.setValue(int(volume * 100))
    
    def get_settings(self) -> dict:
        """Get current settings."""
        return {
            "stt_provider": self.stt_combo.currentText(),
            "tts_provider": self.tts_combo.currentText(),
            "voice": self.voice_combo.currentText(),
            "camera_enabled": self.camera_check.isChecked(),
            "vad_sensitivity": self.vad_slider.value() / 100.0
        }


class VoiceChatPage(QWidget):
    """Voice conversation chat page."""
    
    # Signals to communicate with voice backend
    voice_start = pyqtSignal(dict)  # settings
    voice_stop = pyqtSignal()
    voice_text_message = pyqtSignal(str)
    
    def __init__(self, signal_bridge, parent=None):
        super().__init__(parent)
        self.signal_bridge = signal_bridge  # Main window's SignalBridge
        self._voice_running = False
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Left panel - Chat
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        
        # Chat header
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border-bottom: 1px solid {Theme.BORDER_DEFAULT};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        
        title = QLabel("🎙️  Voice Conversation")
        title.setStyleSheet(f"""
            color: {Theme.TEXT_PRIMARY};
            font-size: {Theme.FONT_SIZE_XL}pt;
            font-weight: 600;
        """)
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        self.status_badge = QLabel("● Stopped")
        self.status_badge.setStyleSheet(f"""
            color: {Theme.TEXT_TERTIARY};
            font-size: {Theme.FONT_SIZE_SM}pt;
            padding: 4px 12px;
            background-color: {Theme.BG_TERTIARY};
            border-radius: 12px;
        """)
        header_layout.addWidget(self.status_badge)
        
        chat_layout.addWidget(header)
        
        # Chat area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setSpacing(8)
        self.chat_layout.setContentsMargins(20, 20, 20, 20)
        self.chat_layout.addStretch()
        
        self.scroll.setWidget(self.chat_container)
        chat_layout.addWidget(self.scroll, 1)
        
        # Input area (for text fallback)
        input_frame = QFrame()
        input_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border-top: 1px solid {Theme.BORDER_DEFAULT};
            }}
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(16, 12, 16, 12)
        input_layout.setSpacing(10)
        
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Type a message... (or use voice)")
        self.text_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.BORDER_DEFAULT};
                border-radius: {Theme.RADIUS_LG}px;
                padding: 12px 16px;
                font-size: {Theme.FONT_SIZE_MD}pt;
            }}
            QLineEdit:focus {{
                border-color: {Theme.ACCENT_PRIMARY};
            }}
        """)
        self.text_input.returnPressed.connect(self._send_text)
        input_layout.addWidget(self.text_input, 1)
        
        self.send_btn = QPushButton()
        self.send_btn.setIcon(IconProvider.get_icon("send", 20, Theme.TEXT_ON_ACCENT))
        self.send_btn.setIconSize(QSize(20, 20))
        self.send_btn.setFixedSize(44, 44)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY};
                border: none;
                border-radius: 22px;
            }}
            QPushButton:hover {{
                background-color: {Theme.ACCENT_TERTIARY};
            }}
        """)
        self.send_btn.clicked.connect(self._send_text)
        input_layout.addWidget(self.send_btn)
        
        chat_layout.addWidget(input_frame)
        
        layout.addWidget(chat_widget, 2)
        
        # Right panel - Controls
        self.control_panel = VoiceControlPanel()
        self.control_panel.setFixedWidth(360)
        self.control_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {Theme.BG_SIDEBAR};
                border-left: 1px solid {Theme.BORDER_SUBTLE};
            }}
        """)
        layout.addWidget(self.control_panel)
        
        # Welcome message
        self._add_welcome_message()
    
    def _connect_signals(self):
        self.control_panel.start_requested.connect(self._on_start_voice)
        self.control_panel.stop_requested.connect(self._on_stop_voice)
        self.control_panel.settings_changed.connect(self._on_settings_changed)
    
    def _add_welcome_message(self):
        welcome = (
            "Welcome to **Voice Conversation**! 🎙️\n\n"
            "Click **Start Voice Chat** to begin a hands-free conversation.\n\n"
            "Features:\n"
            "• Voice activity detection (auto-stops when you finish speaking)\n"
            "• Camera capture for multimodal context\n"
            "• Local or cloud STT/TTS options\n"
            "• Tool calling during conversation (end voice, spawn agents, etc.)\n\n"
            "💡 **Tip:** Say \"end voice conversation\" to stop, or use the stop button."
        )
        self._add_bubble("assistant", welcome)
    
    def _add_bubble(self, role: str, content: str, image: QPixmap = None):
        """Add a chat bubble."""
        from gui.main_window import ChatBubble
        bubble = ChatBubble(role, content)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)
    
    def _scroll_to_bottom(self):
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        )
    
    def _send_text(self):
        text = self.text_input.text().strip()
        if not text:
            return
        self.text_input.clear()
        self._add_bubble("user", text)
        # Emit to voice backend
        self.voice_text_message.emit(text)
    
    def _on_start_voice(self):
        logger.info("Starting voice conversation")
        settings = self.control_panel.get_settings()
        self.voice_start.emit(settings)
        self.control_panel.set_running(True)
        self._voice_running = True
    
    def _on_stop_voice(self):
        logger.info("Stopping voice conversation")
        self.voice_stop.emit()
        self.control_panel.set_running(False)
        self._voice_running = False
    
    def _on_settings_changed(self, settings: dict):
        logger.info(f"Voice settings changed: {settings}")
    
    # Public methods for voice backend to call
    def on_transcription(self, text: str):
        """Called when user speech is transcribed."""
        self._add_bubble("user", f"🎤 {text}")
    
    def on_response(self, text: str):
        """Called when AI responds."""
        self._add_bubble("assistant", text)
    
    def on_error(self, error: str):
        """Called when an error occurs."""
        self._add_bubble("system", f"⚠️ Error: {error}")
    
    def on_state_change(self, state: str):
        """Called when voice state changes."""
        self.control_panel.set_state(state)
        self.status_badge.setText(f"● {state}")
        
        colors = {
            "idle": Theme.TEXT_TERTIARY,
            "listening": Theme.ACCENT_PRIMARY,
            "processing": Theme.WARNING,
            "speaking": Theme.SUCCESS,
            "stopped": Theme.TEXT_TERTIARY,
            "error": Theme.ERROR
        }
        color = colors.get(state.lower(), Theme.TEXT_PRIMARY)
        self.status_badge.setStyleSheet(f"""
            color: {color};
            font-size: {Theme.FONT_SIZE_SM}pt;
            padding: 4px 12px;
            background-color: {color}20;
            border-radius: 12px;
        """)
    
    def on_volume(self, volume: float):
        """Called with microphone volume level."""
        self.control_panel.set_volume(volume)


def add_voice_page(main_window):
    """Add voice conversation page to main window."""
    # Create voice page with main window's signal bridge
    voice_page = VoiceChatPage(main_window.signal_bridge)
    index = main_window.stack.addWidget(voice_page)
    
    # Add nav button
    from gui.main_window import SidebarButton
    voice_btn = SidebarButton("mic", "Voice")
    voice_btn.clicked.connect(lambda: main_window._navigate(index))
    main_window.nav_buttons.append(voice_btn)
    
    # Insert before settings (which is now at index 4)
    # Find sidebar layout
    sidebar = main_window.findChild(QWidget, "sidebar")
    if sidebar:
        layout = sidebar.layout()
        if layout:
            # Insert before the last item (settings is at index 4 in nav_items)
            # We want voice at index 1
            layout.insertWidget(3, voice_btn)  # After "New Chat" button area
    
    return voice_page, index