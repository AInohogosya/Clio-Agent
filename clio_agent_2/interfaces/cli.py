"""
Refined CLI Interface for Clio-Agent-2.
Full-screen TUI with a messaging-app-like interface.
"""

import asyncio
import logging
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent import MESSAGE_PROCESS_TIMEOUT, ClioAgent
from core.llm_router import BUILTIN_PROVIDER_INFO, LLMSettingsLockedError
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    Float,
    FloatContainer,
    HSplit,
    ScrollOffsets,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, Dialog, Label, RadioList, TextArea
from rich.console import Console as RichConsole
from rich.markdown import Markdown as RichMarkdown

logger = logging.getLogger(__name__)


def _render_markdown(md: str, width: int = 0) -> str:
    """Render markdown to an ANSI-formatted string."""
    buf = StringIO()
    c = RichConsole(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width or None,
    )
    c.print(RichMarkdown(md))
    return buf.getvalue()


class ChatMessage:
    __slots__ = ("role", "content", "time")

    def __init__(self, role: str, content: str, time: str = "") -> None:
        self.role = role
        self.content = content
        self.time = time or datetime.now().strftime("%H:%M")


class CLIInterface:
    def __init__(self, agent: ClioAgent) -> None:
        self.agent = agent
        self.messages: List[ChatMessage] = []
        self.running = True
        self._processing = False

        self.input_buffer = Buffer(
            multiline=False,
            accept_handler=self._handle_submit,
        )

        self._msgs_win: Optional[Window] = None
        self._input_win: Optional[Window] = None
        self._root: Optional[FloatContainer] = None
        self._status_text = "Ready"

        self.app = self._build_app()

    # ------------------------------------------------------------------
    # App construction
    # ------------------------------------------------------------------

    def _build_app(self) -> Application:
        kb = KeyBindings()

        @kb.add("c-c")
        def _exit(event):
            event.app.exit()

        @kb.add("c-d")
        def _exit_cd(event):
            buf = event.app.current_buffer
            if buf is not None and not buf.text:
                event.app.exit()

        self._msgs_win = Window(
            content=FormattedTextControl(self._render_messages),
            wrap_lines=True,
            style="class:messages",
            always_hide_cursor=True,
            scroll_offsets=ScrollOffsets(bottom=10),
        )

        self._input_win = Window(
            content=BufferControl(
                buffer=self.input_buffer,
                focusable=True,
            ),
            height=1,
            style="class:input-field",
        )

        sep = Window(
            content=FormattedTextControl(self._rule),
            height=1,
            style="class:separator",
            always_hide_cursor=True,
        )

        header = Window(
            content=FormattedTextControl(self._render_header),
            height=1,
            style="class:header",
            always_hide_cursor=True,
        )

        status = Window(
            content=FormattedTextControl(self._render_status),
            height=1,
            style="class:status",
            always_hide_cursor=True,
        )

        body = HSplit([
            header,
            sep,
            self._msgs_win,
            sep,
            VSplit([
                Window(
                    content=FormattedTextControl([("class:prompt-char", " ❯ ")]),
                    width=3,
                    style="class:prompt-char",
                    always_hide_cursor=True,
                ),
                self._input_win,
            ]),
            status,
        ])

        self._root = FloatContainer(content=body, floats=[])

        return Application(
            layout=Layout(self._root, focused_element=self._input_win),
            key_bindings=kb,
            style=self._build_style(),
            full_screen=True,
            mouse_support=True,
        )

    @staticmethod
    def _build_style() -> Style:
        return Style.from_dict({
            "header":      "bg:#1a1a2e #e0e0e0 bold",
            "separator":   "#4a4a6a",
            "messages":    "bg:#0a0a1a",
            "user-label":  "bg:#2d4a7a #ffffff bold",
            "user-text":   "#c8d6e5",
            "agent-label": "bg:#1a5a3a #ffffff bold",
            "agent-text":  "#e0e0e0",
            "system-text": "bg:#3a2a0a #ffcc66",
            "error-text":  "bg:#3a0a0a #ff6666",
            "processing":  "#ffaa00 italic",
            "prompt-char": "bg:#1a1a2e #00ff88 bold",
            "input-field": "bg:#1a1a2e #ffffff",
            "status":      "bg:#0d0d1a #888888",
        })

    # ------------------------------------------------------------------
    # Dynamic text providers
    # ------------------------------------------------------------------

    def _tw(self) -> int:
        try:
            return get_app().output.get_size().columns
        except Exception:
            return 80

    def _rule(self) -> FormattedText:
        return [("class:separator", "━" * self._tw())]

    def _render_header(self) -> FormattedText:
        name = self.agent.name
        router = self.agent.llm_router
        prov = getattr(router, "default_provider", "?")
        model = getattr(router, "current_model", "?")
        lock = "🔒" if getattr(router, "llm_settings_locked", True) else "🔓"
        w = self._tw()
        left = f"  {name}  "
        right = f"  {lock}  {prov} / {model}  "
        pad = max(1, w - len(left) - len(right))
        return [("class:header", left + " " * pad + right)]

    def _render_messages(self) -> FormattedText:
        parts: list = []
        for msg in self.messages:
            role = msg.role
            content = msg.content
            t = msg.time

            if role == "user":
                parts.append(("class:user-label", f"  You  {t}\n"))
                parts.append(("class:user-text", f"  {content}\n\n"))
            elif role == "agent":
                parts.append(("class:agent-label", f"  {self.agent.name}  {t}\n"))
                rendered = _render_markdown(content, self._tw())
                parts.append(("class:agent-text", f"  {rendered}\n\n"))
            elif role == "system":
                parts.append(("class:system-text", f"  {content}\n\n"))
            elif role == "error":
                parts.append(("class:error-text", f"  {content}\n\n"))

        if self._processing:
            parts.append(("class:processing", "  ● Processing...\n"))

        if not self.messages and not self._processing:
            parts.append((
                "class:system-text",
                "  Welcome to Clio-Agent-2!\n  Type /help for available commands.\n",
            ))

        return parts

    def _render_status(self) -> FormattedText:
        router = self.agent.llm_router
        prov = getattr(router, "default_provider", "?")
        model = getattr(router, "current_model", "?")
        return [("class:status", f"  ● {prov}/{model}  |  {self._status_text}  ")]

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    async def _handle_submit(self, buffer: Buffer) -> bool:
        text = buffer.text.strip()
        if not text or self._processing:
            return False

        buffer.text = ""
        self.messages.append(ChatMessage("user", text))
        self._processing = True
        self._status_text = "Processing..."
        self.app.invalidate()
        await self._scroll_bottom()

        try:
            if text.startswith("/"):
                await self._handle_command(text)
            else:
                await self._handle_message(text)
        except Exception as exc:
            logger.exception("CLI error")
            self.messages.append(ChatMessage("error", f"⚠️ {exc}"))
        finally:
            self._processing = False
            self._status_text = "Ready"
            self.app.invalidate()
            await self._scroll_bottom()

        return True

    async def _scroll_bottom(self) -> None:
        if self._msgs_win is not None:
            self._msgs_win.vertical_scroll = 10 ** 9
        self.app.invalidate()
        await asyncio.sleep(0)

    async def _handle_message(self, text: str) -> None:
        try:
            response = await asyncio.wait_for(
                self.agent.process_message(text),
                timeout=MESSAGE_PROCESS_TIMEOUT,
            )
            if response:
                self.messages.append(ChatMessage("agent", response))
        except asyncio.TimeoutError:
            self.messages.append(ChatMessage(
                "error",
                "⏰ Response timed out. Please try again later.",
            ))

    async def send_to_agent(self, message: str) -> None:
        if message.startswith("[Autonomous Thought]"):
            return
        self.messages.append(ChatMessage("agent", message))
        self.app.invalidate()
        await self._scroll_bottom()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _handle_command(self, command: str) -> None:
        parts = command[1:].strip().split()
        cmd = parts[0].lower() if parts else ""
        args = parts[1:] if len(parts) > 1 else []

        handlers = {
            "help":        self._cmd_help,
            "settings":    self._cmd_settings,
            "llm_default": lambda: self._cmd_llm_default(args),
            "model":       lambda: self._cmd_llm_default(args),
            "llm_providers": self._cmd_llm_providers,
            "llm_models":  self._cmd_llm_models,
            "llm_unlock":  self._cmd_llm_unlock,
            "llm_lock":    self._cmd_llm_lock,
            "reconfigure": self._cmd_reconfigure,
            "configure":   self._cmd_configure,
            "setup":       self._cmd_configure,
            "api_keys":    self._cmd_api_keys,
            "clear":       self._cmd_clear,
        }

        handler = handlers.get(cmd)
        if handler is not None:
            await handler()
            return

        if cmd in ("exit", "quit"):
            self.running = False
            self.app.exit()
            return

        result = await self.agent.execute_command(cmd, args)
        if result == "__EXIT__":
            self.running = False
            self.app.exit()
        elif result:
            self.messages.append(ChatMessage("system", result))

    async def _cmd_help(self) -> None:
        self.messages.append(ChatMessage("system", (
            "Available commands:\n"
            "  /help          Show this help message\n"
            "  /settings      View current settings\n"
            "  /reconfigure   Interactive reconfiguration (via dialogs)\n"
            "  /configure     Full configuration screen (API keys, tokens, model)\n"
            "  /llm_default   Set default LLM (interactive selection)\n"
            "  /llm_providers List available LLM providers\n"
            "  /llm_models    List available models\n"
            "  /llm_unlock    Unlock LLM settings\n"
            "  /llm_lock      Lock LLM settings\n"
            "  /api_keys      Check API key status\n"
            "  /clear         Clear the chat\n"
            "  /exit          Exit the CLI"
        )))

    async def _cmd_settings(self) -> None:
        cfg = self.agent.config.to_dict()
        lines = "\n".join(f"  {k}: {v}" for k, v in cfg.items())
        self.messages.append(ChatMessage("system", f"Settings:\n{lines}"))

    async def _cmd_llm_unlock(self) -> None:
        self.agent.llm_router.unlock_llm_settings()
        self.messages.append(ChatMessage("system", "🔓 LLM settings unlocked."))

    async def _cmd_llm_lock(self) -> None:
        self.agent.llm_router.lock_llm_settings()
        self.messages.append(ChatMessage("system", "🔒 LLM settings locked."))

    async def _cmd_llm_providers(self) -> None:
        avail = self.agent.llm_router.get_available_providers()
        text = "\n".join(f"  • {p}" for p in avail) if avail else "  (none)"
        self.messages.append(ChatMessage("system", f"Available providers:\n{text}"))

    async def _cmd_llm_models(self) -> None:
        try:
            models_by_provider = await self.agent.llm_router.list_all_models()
        except Exception:
            models_by_provider = {}

        lines: list = []
        for prov, models in models_by_provider.items():
            lines.append(f"  {prov}:")
            for m in (models or [])[:20]:
                lines.append(f"    • {m}")
            if models and len(models) > 20:
                lines.append(f"    ... ({len(models) - 20} more)")
        if not lines:
            lines.append("  (no models returned)")

        self.messages.append(ChatMessage("system", "Available models:\n" + "\n".join(lines)))

    async def _cmd_api_keys(self) -> None:
        status = self.agent.config.validate_api_keys()
        lines = []
        for prov, ok in sorted(status.items()):
            m = "✅" if ok else "❌"
            lines.append(f"  {m} {prov}")
        self.messages.append(ChatMessage("system", "API keys:\n" + "\n".join(lines)))

    async def _cmd_clear(self) -> None:
        self.messages.clear()

    async def _cmd_configure(self) -> None:
        self.app.exit(result="__CONFIGURE__")

    async def _cmd_reconfigure(self) -> None:
        current_provider = getattr(self.agent.llm_router, "default_provider", "openai")
        current_model = getattr(self.agent.llm_router, "current_model", "")
        available = self.agent.llm_router.get_available_providers()

        if not available:
            self.messages.append(ChatMessage("error", "No providers configured."))
            return

        values = []
        for pid in available:
            info = BUILTIN_PROVIDER_INFO.get(pid)
            label = info["label"] if info else pid
            values.append((pid, f"{label} ({pid})"))

        p = await self._select_from_list(
            title="Reconfigure – Provider",
            text=f"Current: {current_provider}. Choose a new provider (↑↓ Enter):",
            values=values,
            default=current_provider,
        )
        if p is not None and p != current_provider:
            try:
                self.agent.llm_router.set_llm_provider(p, force=True)
                current_provider = p
            except LLMSettingsLockedError:
                self.messages.append(ChatMessage(
                    "error",
                    "🔒 LLM settings locked. Use /llm_unlock first.",
                ))

        info = BUILTIN_PROVIDER_INFO.get(current_provider)
        suggested = info["default_model"] if info else ""
        m = await self._input_dialog(
            title="Reconfigure – Model",
            text=f"Current: {current_model}. Enter model name:",
            default=current_model or suggested,
        )
        if m:
            try:
                self.agent.llm_router.set_llm_model(m, force=True)
            except LLMSettingsLockedError:
                pass

        val = await self._select_from_list(
            title="Reconfigure – Autonomous Mode",
            text="Enable autonomous thinking?",
            values=[("true", "✅ Enabled"), ("false", "❌ Disabled")],
            default="true" if self.agent.autonomous_mode else "false",
        )
        if val is not None:
            self.agent.autonomous_mode = val == "true"
            if self.agent.autonomous_mode:
                await self.agent.start_autonomous_loop()
            else:
                await self.agent.stop_autonomous_loop()

        self.agent.persist_settings()
        self.messages.append(ChatMessage(
            "system",
            "✅ Reconfiguration complete. Settings saved.",
        ))

    # ------------------------------------------------------------------
    # Interactive dialogs
    # ------------------------------------------------------------------

    async def _select_from_list(
        self,
        title: str,
        text: str,
        values: Sequence[Tuple[str, AnyFormattedText]],
        default: Optional[str] = None,
    ) -> Optional[str]:
        radiolist = RadioList(values=values, default=default)
        done = asyncio.Event()
        result: list = [None]

        def ok() -> None:
            result[0] = radiolist.current_value
            done.set()

        def cancel() -> None:
            done.set()

        dialog = Dialog(
            title=title,
            body=HSplit([Label(text=text), Window(height=1), radiolist]),
            buttons=[Button("OK", handler=ok), Button("Cancel", handler=cancel)],
            width=min(60, self._tw()),
            modal=True,
        )

        float_ = Float(content=dialog)
        self._root.floats.append(float_)
        self.app.layout.focus(radiolist)
        self.app.invalidate()

        await done.wait()

        self._root.floats.remove(float_)
        self.app.layout.focus(self._input_win)
        self.app.invalidate()

        return result[0]

    async def _input_dialog(
        self,
        title: str,
        text: str,
        default: str = "",
    ) -> Optional[str]:
        inp = TextArea(text=default, height=1, multiline=False)
        done = asyncio.Event()
        result: list = [None]

        def ok() -> None:
            result[0] = inp.text.strip()
            done.set()

        def cancel() -> None:
            done.set()

        dialog = Dialog(
            title=title,
            body=HSplit([Label(text=text), Window(height=1), inp]),
            buttons=[Button("OK", handler=ok), Button("Cancel", handler=cancel)],
            width=min(50, self._tw()),
            modal=True,
        )

        float_ = Float(content=dialog)
        self._root.floats.append(float_)
        self.app.layout.focus(inp)
        self.app.invalidate()

        await done.wait()

        self._root.floats.remove(float_)
        self.app.layout.focus(self._input_win)
        self.app.invalidate()

        return result[0]

    async def _cmd_llm_default(self, args: list) -> None:
        try:
            available = self.agent.llm_router.get_available_providers()
            if not available:
                self.messages.append(ChatMessage(
                    "error",
                    "No LLM providers configured. Run /configure first.",
                ))
                return

            values = []
            for pid in available:
                info = BUILTIN_PROVIDER_INFO.get(pid)
                label = info["label"] if info else pid
                values.append((pid, f"{label} ({pid})"))

            selected = await self._select_from_list(
                title="Select LLM Provider",
                text="Choose a provider (↑↓ Enter):",
                values=values,
                default=self.agent.llm_router.default_provider,
            )
            if selected is None:
                return

            try:
                self.agent.llm_router.set_llm_provider(selected, force=True)
            except LLMSettingsLockedError:
                self.messages.append(ChatMessage(
                    "error",
                    "🔒 LLM settings locked. Use /llm_unlock first.",
                ))
                return

            if args:
                model = " ".join(args)
                try:
                    self.agent.llm_router.set_llm_model(model, force=True)
                except LLMSettingsLockedError:
                    pass
                self.agent.persist_settings()
                self.messages.append(ChatMessage(
                    "system",
                    f"✅ Provider: {selected}  Model: {model}",
                ))
                return

            info = BUILTIN_PROVIDER_INFO.get(selected)
            suggested = info["default_model"] if info else ""

            model = await self._input_dialog(
                title="Set Model",
                text=f"Enter model name for {selected}:",
                default=suggested,
            )
            if model:
                try:
                    self.agent.llm_router.set_llm_model(model, force=True)
                except LLMSettingsLockedError:
                    pass
                self.agent.persist_settings()
                self.messages.append(ChatMessage(
                    "system",
                    f"✅ Provider: {selected}  Model: {model}",
                ))
            else:
                self.agent.persist_settings()
                self.messages.append(ChatMessage(
                    "system",
                    f"✅ Provider set to: {selected}",
                ))

        except Exception as exc:
            self.messages.append(ChatMessage("error", str(exc)))

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.agent.register_response_callback(self.send_to_agent)
        await self.agent.initialize()
        await self.agent.ensure_autonomous_loop()

        while self.running:
            try:
                result = await self.app.run_async()
            except (EOFError, KeyboardInterrupt):
                break

            if result == "__CONFIGURE__":
                await self._run_configure_async()
                await self._reload_agent_config()
                self.messages.append(ChatMessage(
                    "system",
                    "✅ Configuration applied. Continuing...",
                ))
                self.app = self._build_app()
            else:
                break

        await self.agent.stop_autonomous_loop()

    async def _run_configure_async(self) -> None:
        from config.setup_env import interactive_setup
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, interactive_setup, True)
        except (EOFError, KeyboardInterrupt):
            pass

    async def _reload_agent_config(self) -> None:
        try:
            self.agent.config.reload()
            self.agent.llm_router.register_providers(self.agent.config)

            provider = (self.agent.config.default_llm_provider or "openai").strip().lower()
            model = (self.agent.config.current_model or "").strip()
            available = self.agent.llm_router.get_available_providers()

            if provider in available:
                try:
                    self.agent.llm_router.set_llm_provider(provider, force=True)
                except Exception:
                    pass
            if model:
                try:
                    self.agent.llm_router.set_llm_model(model, force=True)
                except Exception:
                    pass

            self.agent.persist_settings()
        except Exception as exc:
            logger.exception("Config reload failed")
            self.messages.append(ChatMessage("error", f"Config reload: {exc}"))
