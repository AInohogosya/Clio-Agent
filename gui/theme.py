"""
Clio-Agent-1 GUI — Premium Dark Theme
Flawless design system with no compromises.
"""

from PyQt6.QtGui import QColor, QFont, QPalette, QIcon
from PyQt6.QtCore import Qt


class Theme:
    """Centralized design tokens for the entire application."""

    # ── Core Palette ──────────────────────────────────────────────
    BG_PRIMARY       = "#0a0a0f"
    BG_SECONDARY     = "#0f1019"
    BG_TERTIARY      = "#141520"
    BG_ELEVATED      = "#1a1b2e"
    BG_HOVER         = "#1e2033"
    BG_ACTIVE        = "#232540"
    BG_INPUT         = "#0d0e17"
    BG_SIDEBAR       = "#08090d"
    BG_CARD          = "#11121c"
    BG_OVERLAY       = "rgba(0, 0, 0, 0.7)"

    # ── Accent Colors ─────────────────────────────────────────────
    ACCENT_PRIMARY   = "#6c5ce7"
    ACCENT_SECONDARY = "#a29bfe"
    ACCENT_TERTIARY  = "#7c6ff7"
    ACCENT_GLOW      = "rgba(108, 92, 231, 0.15)"
    ACCENT_GLOW_STRONG = "rgba(108, 92, 231, 0.25)"
    ACCENT_DIM       = "rgba(108, 92, 231, 0.08)"

    # ── Semantic Colors ───────────────────────────────────────────
    SUCCESS          = "#00b894"
    SUCCESS_DIM      = "rgba(0, 184, 148, 0.12)"
    WARNING          = "#fdcb6e"
    WARNING_DIM      = "rgba(253, 203, 110, 0.12)"
    ERROR            = "#ff6b6b"
    ERROR_DIM        = "rgba(255, 107, 107, 0.12)"
    INFO             = "#74b9ff"
    INFO_DIM         = "rgba(116, 185, 255, 0.12)"

    # ── Text Colors ───────────────────────────────────────────────
    TEXT_PRIMARY     = "#e8e8f0"
    TEXT_SECONDARY   = "#9090a8"
    TEXT_TERTIARY    = "#5a5a70"
    TEXT_PLACEHOLDER = "#3a3a50"
    TEXT_DISABLED    = "#2a2a3a"
    TEXT_ACCENT      = "#a29bfe"
    TEXT_ON_ACCENT   = "#ffffff"

    # ── Border Colors ─────────────────────────────────────────────
    BORDER_DEFAULT   = "#1e1e2e"
    BORDER_HOVER     = "#2a2a40"
    BORDER_FOCUS     = "#6c5ce7"
    BORDER_SUBTLE    = "#16161f"

    # ── Typography ────────────────────────────────────────────────
    FONT_FAMILY      = "SF Pro Display"
    FONT_FAMILY_FALLBACK = ["Inter", "Segoe UI", "Helvetica Neue", "Arial"]
    FONT_MONO        = "SF Mono"
    FONT_MONO_FALLBACK = ["JetBrains Mono", "Fira Code", "Consolas", "Menlo", "Monaco"]

    FONT_SIZE_XS     = 10
    FONT_SIZE_SM     = 11
    FONT_SIZE_MD     = 13
    FONT_SIZE_LG     = 15
    FONT_SIZE_XL     = 18
    FONT_SIZE_2X     = 24
    FONT_SIZE_3X     = 32
    FONT_SIZE_4X     = 40

    # ── Spacing ───────────────────────────────────────────────────
    SPACE_2   = 2
    SPACE_4   = 4
    SPACE_6   = 6
    SPACE_8   = 8
    SPACE_12  = 12
    SPACE_16  = 16
    SPACE_20  = 20
    SPACE_24  = 24
    SPACE_32  = 32
    SPACE_40  = 40
    SPACE_48  = 48

    # ── Radii ─────────────────────────────────────────────────────
    RADIUS_XS  = 3
    RADIUS_SM  = 6
    RADIUS_MD  = 8
    RADIUS_LG  = 12
    RADIUS_XL  = 16
    RADIUS_2X  = 20
    RADIUS_FULL = 9999

    # ── Shadows ──────────────────────────────────────────────────
    SHADOW_SM  = "0 2px 8px rgba(0,0,0,0.3)"
    SHADOW_MD  = "0 4px 16px rgba(0,0,0,0.4)"
    SHADOW_LG  = "0 8px 32px rgba(0,0,0,0.5)"
    SHADOW_XL  = "0 16px 48px rgba(0,0,0,0.6)"
    SHADOW_GLOW = "0 0 20px rgba(108,92,231,0.15)"

    # ── Transitions ──────────────────────────────────────────────
    TRANS_FAST    = "100ms ease"
    TRANS_NORMAL  = "200ms ease"
    TRANS_SLOW    = "300ms cubic-bezier(0.4, 0, 0.2, 1)"
    TRANS_BOUNCE  = "400ms cubic-bezier(0.34, 1.56, 0.64, 1)"

    # ── Sidebar ───────────────────────────────────────────────────
    SIDEBAR_WIDTH         = 260
    SIDEBAR_ITEM_HEIGHT   = 40
    SIDEBAR_ICON_SIZE     = 20

    # ── Chat ──────────────────────────────────────────────────────
    CHAT_BUBBLE_RADIUS    = 14
    CHAT_MAX_WIDTH_PCT    = 72
    CHAT_INPUT_MIN_HEIGHT = 52
    CHAT_INPUT_MAX_HEIGHT = 160

    # ── Scrollbar ─────────────────────────────────────────────────
    SCROLLBAR_WIDTH       = 6
    SCROLLBAR_RADIUS      = 3

    @classmethod
    def font(cls, size=FONT_SIZE_MD, weight=QFont.Weight.Normal, mono=False):
        """Create a QFont with the theme's font family."""
        families = [cls.FONT_MONO] + cls.FONT_MONO_FALLBACK if mono else [cls.FONT_FAMILY] + cls.FONT_FAMILY_FALLBACK
        font = QFont()
        font.setFamilies(families)
        font.setPointSize(size)
        font.setWeight(weight)
        return font

    @classmethod
    def color(cls, name):
        """Get a QColor by theme token name."""
        return QColor(getattr(cls, name))

    @classmethod
    def stylesheet(cls):
        """Return the global application stylesheet."""
        return f"""
        /* ── Global ─────────────────────────────────────────── */
        QMainWindow, QDialog {{
            background-color: {cls.BG_PRIMARY};
            color: {cls.TEXT_PRIMARY};
        }}

        QWidget {{
            background-color: transparent;
            color: {cls.TEXT_PRIMARY};
            font-family: "{cls.FONT_FAMILY}", {", ".join(f'"{f}"' for f in cls.FONT_FAMILY_FALLBACK)};
            font-size: {cls.FONT_SIZE_MD}pt;
        }}

        /* ── Scrollbars ─────────────────────────────────────── */
        QScrollBar:vertical {{
            background: transparent;
            width: {cls.SCROLLBAR_WIDTH}px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {cls.BG_ACTIVE};
            border-radius: {cls.SCROLLBAR_RADIUS}px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {cls.BORDER_HOVER};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}

        QScrollBar:horizontal {{
            background: transparent;
            height: {cls.SCROLLBAR_WIDTH}px;
            margin: 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: {cls.BG_ACTIVE};
            border-radius: {cls.SCROLLBAR_RADIUS}px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {cls.BORDER_HOVER};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}

        /* ── Tooltips ───────────────────────────────────────── */
        QToolTip {{
            background-color: {cls.BG_ELEVATED};
            color: {cls.TEXT_PRIMARY};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_SM}px;
            padding: 6px 10px;
            font-size: {cls.FONT_SIZE_SM}pt;
        }}

        /* ── Separators ─────────────────────────────────────── */
        QFrame[frameShape="4"] {{  /* HLine */
            color: {cls.BORDER_SUBTLE};
            background-color: {cls.BORDER_SUBTLE};
            max-height: 1px;
        }}
        QFrame[frameShape="5"] {{  /* VLine */
            color: {cls.BORDER_SUBTLE};
            background-color: {cls.BORDER_SUBTLE};
            max-width: 1px;
        }}

        /* ── Menu ───────────────────────────────────────────── */
        QMenu {{
            background-color: {cls.BG_ELEVATED};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_MD}px;
            padding: 6px;
        }}
        QMenu::item {{
            color: {cls.TEXT_PRIMARY};
            padding: 8px 24px 8px 16px;
            border-radius: {cls.RADIUS_SM}px;
        }}
        QMenu::item:selected {{
            background-color: {cls.ACCENT_GLOW};
            color: {cls.TEXT_ACCENT};
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {cls.BORDER_SUBTLE};
            margin: 4px 8px;
        }}

        /* ── ComboBox ───────────────────────────────────────── */
        QComboBox {{
            background-color: {cls.BG_INPUT};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_SM}px;
            padding: 8px 12px;
            color: {cls.TEXT_PRIMARY};
            min-height: 20px;
        }}
        QComboBox:hover {{
            border-color: {cls.BORDER_HOVER};
        }}
        QComboBox:focus {{
            border-color: {cls.ACCENT_PRIMARY};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 30px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {cls.BG_ELEVATED};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_SM}px;
            selection-background-color: {cls.ACCENT_GLOW};
            selection-color: {cls.TEXT_ACCENT};
        }}

        /* ── SpinBox ────────────────────────────────────────── */
        QSpinBox, QDoubleSpinBox {{
            background-color: {cls.BG_INPUT};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_SM}px;
            padding: 6px 10px;
            color: {cls.TEXT_PRIMARY};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {cls.ACCENT_PRIMARY};
        }}

        /* ── CheckBox ───────────────────────────────────────── */
        QCheckBox {{
            color: {cls.TEXT_PRIMARY};
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: {cls.RADIUS_XS}px;
            border: 2px solid {cls.BORDER_HOVER};
            background-color: {cls.BG_INPUT};
        }}
        QCheckBox::indicator:checked {{
            background-color: {cls.ACCENT_PRIMARY};
            border-color: {cls.ACCENT_PRIMARY};
        }}
        QCheckBox::indicator:hover {{
            border-color: {cls.ACCENT_PRIMARY};
        }}

        /* ── RadioButton ────────────────────────────────────── */
        QRadioButton {{
            color: {cls.TEXT_PRIMARY};
            spacing: 8px;
        }}
        QRadioButton::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 9px;
            border: 2px solid {cls.BORDER_HOVER};
            background-color: {cls.BG_INPUT};
        }}
        QRadioButton::indicator:checked {{
            background-color: {cls.ACCENT_PRIMARY};
            border-color: {cls.ACCENT_PRIMARY};
        }}

        /* ── Slider ─────────────────────────────────────────── */
        QSlider::groove:horizontal {{
            height: 4px;
            background: {cls.BG_ACTIVE};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -6px 0;
            background: {cls.ACCENT_PRIMARY};
            border-radius: 8px;
        }}
        QSlider::sub-page:horizontal {{
            background: {cls.ACCENT_PRIMARY};
            border-radius: 2px;
        }}

        /* ── ProgressBar ────────────────────────────────────── */
        QProgressBar {{
            background-color: {cls.BG_ACTIVE};
            border-radius: {cls.RADIUS_SM}px;
            text-align: center;
            color: {cls.TEXT_SECONDARY};
            font-size: {cls.FONT_SIZE_XS}pt;
        }}
        QProgressBar::chunk {{
            background-color: {cls.ACCENT_PRIMARY};
            border-radius: {cls.RADIUS_SM}px;
        }}

        /* ── TabWidget ──────────────────────────────────────── */
        QTabWidget::pane {{
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_MD}px;
            background-color: {cls.BG_SECONDARY};
        }}
        QTabBar::tab {{
            background-color: {cls.BG_TERTIARY};
            color: {cls.TEXT_SECONDARY};
            padding: 10px 20px;
            border-top-left-radius: {cls.RADIUS_SM}px;
            border-top-right-radius: {cls.RADIUS_SM}px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background-color: {cls.BG_SECONDARY};
            color: {cls.TEXT_PRIMARY};
        }}
        QTabBar::tab:hover:!selected {{
            background-color: {cls.BG_HOVER};
            color: {cls.TEXT_PRIMARY};
        }}

        /* ── GroupBox ───────────────────────────────────────── */
        QGroupBox {{
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_MD}px;
            margin-top: 12px;
            padding-top: 20px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            color: {cls.TEXT_SECONDARY};
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
        }}

        /* ── Table ──────────────────────────────────────────── */
        QTableWidget, QTableView {{
            background-color: {cls.BG_SECONDARY};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_MD}px;
            gridline-color: {cls.BORDER_SUBTLE};
            color: {cls.TEXT_PRIMARY};
        }}
        QTableWidget::item:selected, QTableView::item:selected {{
            background-color: {cls.ACCENT_GLOW};
        }}
        QHeaderView::section {{
            background-color: {cls.BG_TERTIARY};
            color: {cls.TEXT_SECONDARY};
            padding: 8px;
            border: none;
            border-bottom: 1px solid {cls.BORDER_DEFAULT};
        }}

        /* ── ListWidget ─────────────────────────────────────── */
        QListWidget {{
            background-color: {cls.BG_SECONDARY};
            border: 1px solid {cls.BORDER_DEFAULT};
            border-radius: {cls.RADIUS_MD}px;
            outline: none;
        }}
        QListWidget::item {{
            padding: 10px 14px;
            border-radius: {cls.RADIUS_SM}px;
            color: {cls.TEXT_PRIMARY};
        }}
        QListWidget::item:selected {{
            background-color: {cls.ACCENT_GLOW};
            color: {cls.TEXT_ACCENT};
        }}
        QListWidget::item:hover:!selected {{
            background-color: {cls.BG_HOVER};
        }}
        """
