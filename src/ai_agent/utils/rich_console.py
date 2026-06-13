"""
Rich Console — Centralized beautiful CLI output for Clio Agent.

Features:
  • Themed Console with gradient panels, tables, and tree views
  • ShimmerLoader — animated gradient loading bar while LLM is thinking
  • StreamingPrinter — smooth character-by-character output animation
  • Styled log sink that replaces the old _TerminalLogSink ANSI codes
  • Helper functions for common patterns (status boxes, step indicators, etc.)

All Rich output goes to stderr so stdout stays clean for command piping.
"""

from __future__ import annotations

import sys
import time
import threading
from typing import Optional, Sequence

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.columns import Columns
from rich.live import Live
from rich.spinner import Spinner
from rich.bar import Bar
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.tree import Tree
from rich.markup import escape
from rich.style import Style
from rich.color import Color

# ── Singleton console (stderr, force terminal colors) ──────────────────────

_console: Optional[Console] = None


def get_console() -> Console:
    global _console
    if _console is None:
        _console = Console(
            file=sys.stderr,
            force_terminal=True,
            force_interactive=True,
            color_system="truecolor",
            highlight=True,
            soft_wrap=True,
        )
    return _console


# ── Theme palette ──────────────────────────────────────────────────────────

class Theme:
    """Design tokens — mirrors gui/theme.py palette for CLI consistency."""

    # Accent
    ACCENT = "#6c5ce7"
    ACCENT_LIGHT = "#a29bfe"
    ACCENT_DIM = "rgba(108, 92, 231, 0.15)"

    # Semantic
    SUCCESS = "#00b894"
    WARNING = "#fdcb6e"
    ERROR = "#ff6b6b"
    INFO = "#74b9ff"

    # Text
    TEXT_PRIMARY = "#e8e8f0"
    TEXT_SECONDARY = "#9090a8"
    TEXT_TERTIARY = "#5a5a70"

    # Background
    BG_PRIMARY = "#0a0a0f"
    BG_ELEVATED = "#1a1b2e"
    BG_CARD = "#11121c"

    # Border
    BORDER = "#1e1e2e"
    BORDER_SUBTLE = "#16161f"

    # Shimmer gradient stops (for the loading animation)
    SHIMMER_COLORS = [
        "#6c5ce7", "#7c6ff7", "#8c82ff", "#a29bfe",
        "#8c82ff", "#7c6ff7", "#6c5ce7", "#5c4bd7",
        "#6c5ce7",
    ]


# ── ShimmerLoader ──────────────────────────────────────────────────────────

class ShimmerLoader:
    """
    Animated shimmer / gradient loading bar shown while the LLM is thinking.

    Usage:
        loader = ShimmerLoader("Thinking")
        loader.start()
        # ... call LLM ...
        loader.stop()

    The loader renders as a flowing gradient bar with a spinner and label.
    It runs in a background thread and is safe to start/stop from the main
    thread.
    """

    def __init__(self, label: str = "Thinking", console: Optional[Console] = None):
        self._label = label
        self._console = console or get_console()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_time: float = 0
        self._live: Optional[Live] = None

    # -- public API --

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._live:
            self._live.stop()
        if self._thread:
            self._thread.join(timeout=2)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- internal --

    def _run(self) -> None:
        colors = Theme.SHIMMER_COLORS
        idx = 0
        self._live = Live(
            self._render(colors[idx % len(colors)], 0),
            console=self._console,
            refresh_per_second=20,
            transient=True,
        )
        self._live.start()
        while self._running:
            elapsed = time.monotonic() - self._start_time
            color = colors[idx % len(colors)]
            if self._live:
                self._live.update(self._render(color, elapsed))
            idx += 1
            time.sleep(0.05)

    def _render(self, color: str, elapsed: float) -> RenderableType:
        # Build a shimmer bar using block characters
        bar_width = 32
        blocks = []
        for i in range(bar_width):
            c_idx = (int(elapsed * 8) + i) % len(colors)
            bc = colors[c_idx]
            blocks.append(f"[{bc}]█[/]")
        bar_text = Text.from_markup("".join(blocks))
        label = Text(f"  {self._label}  ", style=f"bold {Theme.ACCENT}")
        elapsed_text = Text(f"  {elapsed:.1f}s", style=Theme.TEXT_TERTIARY)
        return Group(bar_text, label + elapsed_text)


# ── StreamingPrinter ──────────────────────────────────────────────────────

class StreamingPrinter:
    """
    Print text with a smooth typing-style animation.

    Usage:
        printer = StreamingPrinter()
        printer.stream("Hello, world! ")
        printer.stream("This appears character by character.")
        printer.flush()  # ensure everything is printed

    Set speed=0 for instant output (useful for non-interactive mode).
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        speed: float = 0.02,
        style: str = "",
    ):
        self._console = console or get_console()
        self._speed = speed
        self._style = style
        self._buffer: list[str] = []

    def stream(self, text: str) -> None:
        """Print text character by character."""
        if self._speed <= 0:
            self._console.print(text, style=self._style, end="")
            return
        for ch in text:
            self._console.print(ch, style=self._style, end="")
            time.sleep(self._speed)

    def stream_line(self, text: str = "") -> None:
        """Stream text followed by a newline."""
        self.stream(text + "\n")

    def flush(self) -> None:
        """Flush any buffered content (no-op for now, reserved)."""
        pass


# ── Styled log sink (replaces _TerminalLogSink) ───────────────────────────

class StyledLogSink:
    """
    Beautiful real-time terminal log using Rich.

    Every method renders a distinct visual style so the user can instantly
    distinguish phases, thoughts, commands, results, and errors.
    """

    def __init__(self, enabled: bool = True, console: Optional[Console] = None):
        self._enabled = enabled
        self._console = console or get_console()

    def _ts(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime())

    # ── Phase banner ───────────────────────────────────────────────────

    def phase(self, iteration: int, phase_name: str) -> None:
        if not self._enabled:
            return
        self._console.print()
        self._console.print(
            Rule(
                title=f"[bold {Theme.ACCENT}]Iteration {iteration} — {phase_name}[/]",
                style=Theme.ACCENT,
                characters="━",
            )
        )

    # ── Thinking / internal monologue ──────────────────────────────────

    def thinking(self, text: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("💭 ", style="bold")
        t.append(text, style=Theme.ACCENT_LIGHT)
        self._console.print(t)

    # ── Shell command ──────────────────────────────────────────────────

    def command(self, cmd: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("❯ ", style=f"bold {Theme.WARNING}")
        t.append(cmd, style="bold white")
        self._console.print(t)

    # ── Command result ─────────────────────────────────────────────────

    def command_result(self, return_code: int, stdout: str, stderr: str) -> None:
        if not self._enabled:
            return
        ok = return_code == 0
        color = Theme.SUCCESS if ok else Theme.ERROR
        icon = "✓" if ok else "✗"
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append(f"{icon} ", style=f"bold {color}")
        t.append(f"exit={return_code}", style=color)
        self._console.print(t)
        if stdout.strip():
            for line in stdout.strip().splitlines():
                self._console.print(
                    Text(f"    │ {line}", style=Theme.TEXT_TERTIARY)
                )
        if stderr.strip():
            for line in stderr.strip().splitlines():
                self._console.print(Text(f"    │ {line}", style=Theme.ERROR))

    # ── Model request / response ───────────────────────────────────────

    def model_request(self, iteration: int, model: str, provider: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("🤖 ", style="bold")
        t.append("→ ", style=Theme.ACCENT)
        t.append(f"{provider}/{model}", style=f"bold {Theme.ACCENT}")
        self._console.print(t)

    def model_response(self, iteration: int, output_length: int, latency: float = 0) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("🤖 ", style="bold")
        t.append("← ", style=Theme.SUCCESS)
        t.append(f"{output_length} chars", style=Theme.SUCCESS)
        if latency > 0:
            t.append(f"  ({latency:.1f}s)", style=Theme.TEXT_TERTIARY)
        self._console.print(t)

    # ── Telegram / Discord message ─────────────────────────────────────

    def telegram(self, content: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("📨 ", style="bold")
        t.append(content[:150], style=Theme.INFO)
        self._console.print(t)

    # ── Error / warning / info ─────────────────────────────────────────

    def error(self, text: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("❌ ", style="bold")
        t.append(text, style=f"bold {Theme.ERROR}")
        self._console.print(t)

    def warning(self, text: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("⚠️  ", style="bold")
        t.append(text, style=f"bold {Theme.WARNING}")
        self._console.print(t)

    def info(self, text: str) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("ℹ️  ", style="bold")
        t.append(text, style=Theme.INFO)
        self._console.print(t)

    # ── Status / completion ────────────────────────────────────────────

    def task_done(self, success: bool, iterations: int, duration: float) -> None:
        if not self._enabled:
            return
        color = Theme.SUCCESS if success else Theme.ERROR
        icon = "✅" if success else "❌"
        status = "completed" if success else "failed"
        self._console.print()
        self._console.print(
            Rule(
                title=f"[bold {icon} {color}] Task {status} in {iterations} iteration(s), {duration:.1f}s[/]",
                style=color,
            )
        )

    def cancelled(self) -> None:
        if not self._enabled:
            return
        self._console.print()
        self._console.print(
            Rule("[bold ⚠️  Cancelled by user request[/]", style=Theme.WARNING)
        )

    # ── Parallel batch ─────────────────────────────────────────────────

    def parallel_start(self, count: int) -> None:
        if not self._enabled:
            return
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append("⇅ ", style=f"bold {Theme.WARNING}")
        t.append(f"parallel batch: {count} tasks", style=f"bold {Theme.WARNING}")
        self._console.print(t)

    def parallel_result(self, result) -> None:
        if not self._enabled:
            return
        ok = getattr(result, "all_succeeded", False)
        color = Theme.SUCCESS if ok else Theme.ERROR
        icon = "✓" if ok else "✗"
        fail = getattr(result, "fail_count", 0)
        t = Text()
        t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
        t.append(f"{icon} ", style=f"bold {color}")
        t.append(
            f"parallel done: {result.success_count} ok, {fail} failed, "
            f"{result.total_duration_ms:.0f}ms",
            style=color,
        )
        self._console.print(t)

    # ── Separator / context ────────────────────────────────────────────

    def separator(self) -> None:
        if not self._enabled:
            return
        self._console.print(Rule(style=Theme.BORDER_SUBTLE))

    def context(self, text: str) -> None:
        if not self._enabled:
            return
        for line in text.strip().splitlines():
            t = Text()
            t.append(f"[{self._ts()}]  ", style=Theme.TEXT_TERTIARY)
            t.append("📂 ", style="bold")
            t.append(line, style=Theme.WARNING)
            self._console.print(t)


# ── Helper: status panel ──────────────────────────────────────────────────

def status_panel(
    title: str,
    rows: Sequence[tuple[str, str]],
    *,
    border_color: str = Theme.ACCENT,
    title_color: str = "bold white",
) -> Panel:
    """Build a compact key-value status panel."""
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("key", style=Theme.TEXT_SECONDARY, min_width=18)
    table.add_column("value", style="bold white")
    for key, value in rows:
        table.add_row(key, value)
    return Panel(
        table,
        title=f"[{title_color}]{title}[/]",
        border_style=border_color,
        padding=(1, 2),
    )


# ── Helper: step indicator ────────────────────────────────────────────────

def step(current: int, total: int, label: str = "") -> Text:
    """Render a step indicator like [●───○───○] Step label."""
    parts: list[str] = []
    for i in range(total):
        if i < current:
            parts.append(f"[bold {Theme.SUCCESS}]●[/]")
        elif i == current:
            parts.append(f"[bold {Theme.ACCENT}]●[/]")
        else:
            parts.append(f"[dim {Theme.TEXT_TERTIARY}]○[/]")
        if i < total - 1:
            parts.append(f"[dim {Theme.BORDER}]───[/]")
    t = Text.from_markup("".join(parts))
    if label:
        t.append(f"  {label}", style="bold white")
    return t


# ── Helper: gradient text ─────────────────────────────────────────────────

def gradient_text(text: str, colors: Sequence[str] | None = None) -> Text:
    """Render text with a smooth color gradient across characters."""
    if colors is None:
        colors = [Theme.ACCENT, Theme.ACCENT_LIGHT, Theme.INFO, Theme.ACCENT]
    result = Text()
    for i, ch in enumerate(text):
        c = colors[i % len(colors)]
        result.append(ch, style=c)
    return result


# ── Helper: banner ────────────────────────────────────────────────────────

def banner(text: str, *, style: str = f"bold {Theme.ACCENT}") -> Panel:
    """Render a centered banner panel."""
    return Panel(
        Align.center(Text(text, style=style)),
        border_style=Theme.ACCENT,
        padding=(1, 4),
    )
