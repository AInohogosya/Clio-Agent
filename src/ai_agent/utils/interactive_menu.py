#!/usr/bin/env python3
"""
Interactive menu system with arrow key navigation and colored output.
Now uses Rich for beautiful panels, tables, and styled output.

On Windows (where tty/termios are unavailable), falls back to simple
numbered input.
"""

import sys
from typing import List, Tuple, Optional

# tty/termios are Unix-only; guard the import
try:
    import tty
    import termios
    _HAS_TTY = True
except ImportError:
    _HAS_TTY = False

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.table import Table
from rich.rule import Rule

from .rich_console import Theme, get_console, status_panel, step


class Colors:
    """ANSI color codes for terminal output (kept for backward compat)"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'
    BG_BRIGHT_BLACK = '\033[100m'
    BG_BRIGHT_BLUE = '\033[104m'


class MenuItem:
    """Represents a single menu item"""
    def __init__(self, title: str, description: str = "", value: str = None, icon: str = ""):
        self.title = title
        self.description = description
        self.value = value if value is not None else title
        self.icon = icon


class InteractiveMenu:
    """Interactive menu with arrow key navigation using Rich for smooth display"""

    def __init__(self, title: str, subtitle: str = ""):
        self.title = title
        self.subtitle = subtitle
        self.items: List[MenuItem] = []
        self.current_selection = 0
        self.show_current = False
        self.current_value = None
        self.console = get_console()
        self.live = None
        self._should_exit = False

    def add_item(self, title: str, description: str = "", value: str = None, icon: str = ""):
        """Add a menu item"""
        self.items.append(MenuItem(title, description, value, icon))

    def set_current_selection(self, value: str):
        """Set the current/preferred value"""
        self.current_value = value
        self.show_current = True
        for i, item in enumerate(self.items):
            if item.value == value:
                self.current_selection = i
                break

    def _get_key(self) -> str:
        """Get a single keypress with improved arrow key handling.

        Falls back to line-based input on platforms without tty/termios.
        """
        if not _HAS_TTY:
            return self._get_key_simple()

        import select
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)

            if ch == '\x1b':  # ESC
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    ch += sys.stdin.read(2)
                    if ch == '\x1b[A':
                        return 'UP'
                    elif ch == '\x1b[B':
                        return 'DOWN'
                    elif ch == '\x1b[C':
                        return 'RIGHT'
                    elif ch == '\x1b[D':
                        return 'LEFT'
                    else:
                        return ch
                else:
                    return 'ESC'
            elif ch in ['\r', '\n']:
                return '\r'
            elif ch.lower() in ['q', 'Q']:
                return 'q'
            elif ch.isdigit():
                return ch
            else:
                return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _get_key_simple(self) -> str:
        """Simple line-based key input for platforms without tty/termios."""
        try:
            line = input().strip()
            if not line:
                return '\r'
            if line.lower() == 'q':
                return 'q'
            if line.isdigit():
                return line
            return 'UNKNOWN'
        except (EOFError, KeyboardInterrupt):
            return 'q'

    def _render_menu(self):
        """Render the menu using Rich components"""
        # Build menu content
        content_lines = []

        if self.subtitle:
            content_lines.append(Text(self.subtitle, style=Theme.TEXT_SECONDARY))
            content_lines.append(Text(""))

        # Show current preference if available
        if self.show_current and self.current_value:
            for item in self.items:
                if item.value == self.current_value:
                    t = Text()
                    t.append("Current: ", style=Theme.TEXT_SECONDARY)
                    t.append(item.icon + " ", style=Theme.SUCCESS)
                    t.append(item.title, style=f"bold {Theme.SUCCESS}")
                    content_lines.append(t)
                    break
            content_lines.append(Text(""))

        # Menu items
        for i, item in enumerate(self.items):
            if i == self.current_selection:
                t = Text()
                t.append("  ▶ ", style=f"bold {Theme.ACCENT}")
                t.append(item.icon + " ", style="bold white")
                t.append(item.title, style="bold white")
                content_lines.append(t)
                if item.description:
                    dt = Text()
                    dt.append("     ", style=Theme.TEXT_SECONDARY)
                    dt.append(item.description, style=Theme.TEXT_SECONDARY)
                    content_lines.append(dt)
            else:
                t = Text()
                t.append("    ", style=Theme.TEXT_TERTIARY)
                t.append(item.icon + " ", style=Theme.TEXT_SECONDARY)
                t.append(item.title, style="white")
                content_lines.append(t)
                if item.description:
                    dt = Text()
                    dt.append("     ", style=Theme.TEXT_TERTIARY)
                    dt.append(item.description, style=Theme.TEXT_TERTIARY)
                    content_lines.append(dt)
            content_lines.append(Text(""))

        # Instructions
        content_lines.append(Text(""))
        inst = [
            ("↑/↓", "Navigate"),
            ("Enter", "Select"),
            ("q/Ctrl+C", "Cancel"),
        ]
        for key, action in inst:
            t = Text()
            t.append("  " + key + "  ", style=f"bold {Theme.ACCENT}")
            t.append(action, style=Theme.TEXT_SECONDARY)
            content_lines.append(t)

        full_content = Text("\n").join(content_lines)

        panel = Panel(
            full_content,
            title=f"[bold {Theme.ACCENT}]{self.title}[/]",
            border_style=Theme.ACCENT,
            padding=(1, 2),
        )
        return panel

    def _print_menu_simple(self):
        """Print the menu using Rich console"""
        self.console.print()
        self.console.print(self._render_menu())

    def show(self) -> Optional[str]:
        """Display the interactive menu and return selected value.

        On platforms with tty/termios, uses arrow-key navigation.
        On other platforms (e.g. Windows), falls back to numbered input.
        """
        if not self.items:
            return None

        if not _HAS_TTY:
            return self._show_simple()

        # ANSI clear-screen for Unix terminals
        print("\033[2J\033[H", end="")

        while not self._should_exit:
            print("\033[H\033[J", end="")
            self._print_menu_simple()

            try:
                key = self._get_key()

                if key == 'UP':
                    self.current_selection = (self.current_selection - 1) % len(self.items)
                elif key == 'DOWN':
                    self.current_selection = (self.current_selection + 1) % len(self.items)
                elif key in ['\r', '\n']:
                    selected_item = self.items[self.current_selection]
                    self._should_exit = True
                    print("\033[H\033[J", end="")
                    self.console.print(
                        f"[bold {Theme.SUCCESS}]✓ Selected: {selected_item.icon} {selected_item.title}[/]"
                    )
                    return selected_item.value
                elif key.lower() == 'q' or key == 'ESC':
                    self._should_exit = True
                    print("\033[H\033[J", end="")
                    self.console.print(f"[bold {Theme.WARNING}]Operation cancelled[/]")
                    return None
                elif key == '\x03':
                    self._should_exit = True
                    print("\033[H\033[J", end="")
                    self.console.print(f"[bold {Theme.WARNING}]Operation cancelled[/]")
                    return None

            except KeyboardInterrupt:
                self._should_exit = True
                print("\033[H\033[J", end="")
                self.console.print(f"[bold {Theme.WARNING}]Operation cancelled[/]")
                return None
            except Exception as e:
                self._should_exit = True
                print("\033[H\033[J", end="")
                self.console.print(f"[bold {Theme.ERROR}]Error reading input: {e}[/]")
                return None

        return None

    def _show_simple(self) -> Optional[str]:
        """Simple numbered menu for platforms without tty/termios."""
        self.console.print()
        self.console.print(f"[bold]{self.title}[/]")
        if self.subtitle:
            self.console.print(f"[dim]{self.subtitle}[/]")
        self.console.print()
        for i, item in enumerate(self.items, 1):
            self.console.print(f"  [bold]{i}.[/] {item.icon} {item.title}")
            if item.description:
                self.console.print(f"      [dim]{item.description}[/]")
        self.console.print()
        self.console.print("[dim]Enter number to select, or 'q' to cancel[/]")
        try:
            response = input("> ").strip()
            if response.lower() == 'q':
                return None
            idx = int(response) - 1
            if 0 <= idx < len(self.items):
                return self.items[idx].value
            return None
        except (ValueError, EOFError, KeyboardInterrupt):
            return None


# ── Styled message helpers ──────────────────────────────────────────────

def confirm_dialog(message: str, default: bool = False) -> bool:
    """Show a confirmation dialog with Rich styling."""
    console = get_console()
    console.print()
    console.print(f"[bold {Theme.WARNING}]{message}[/]")
    hint = "[Y/n]" if default else "[y/N]"
    default_label = " (default: Yes)" if default else " (default: No)"
    console.print(f"[{Theme.INFO}]{hint}{default_label}[/]")
    try:
        response = input().strip().lower()
        if not response:
            return default
        return response.startswith('y')
    except KeyboardInterrupt:
        console.print(f"[bold {Theme.WARNING}]Operation cancelled[/]")
        return False


def info_message(message: str, color: str = Theme.ACCENT):
    """Display an info message with Rich styling."""
    console = get_console()
    console.print()
    console.print(f"[bold {color}]ℹ️  {message}[/]")


def success_message(message: str):
    """Display a success message with Rich styling."""
    console = get_console()
    console.print(f"[bold {Theme.SUCCESS}]✓ {message}[/]")


def error_message(message: str):
    """Display an error message with Rich styling."""
    console = get_console()
    console.print(f"[bold {Theme.ERROR}]✗ {message}[/]")


def warning_message(message: str):
    """Display a warning message with Rich styling."""
    console = get_console()
    console.print(f"[bold {Theme.WARNING}]⚠ {message}[/]")
