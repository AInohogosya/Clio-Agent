"""
Executor: The execution layer of the autonomous agent.

Responsibilities:
  - Execute a list of AgentActions (read, write, edit, bash, etc.)
  - Handle parallel execution
  - Record results in the execution log
  - Send messages via Telegram/Discord

This module knows NOTHING about thinking or loop control. It only executes.
"""

import json
import time
import subprocess
import concurrent.futures
from typing import List, Optional, Tuple
from pathlib import Path

from .agent_schema import AgentAction, ActionType, AgentPlan
from .terminal_history import get_terminal_history
from ..tools.base import _get_or_create_loop_tool_registry
from ..utils.logger import get_logger


class Executor:
    """Executes: runs actions and records results."""

    def __init__(self, telegram_bot=None, discord_bot=None,
                 command_timeout: int = 1800):
        self.telegram_bot = telegram_bot
        self.discord_bot = discord_bot
        self.command_timeout = command_timeout
        self.logger = get_logger("executor")
        self._term_log = None  # set externally if needed

    def execute_plan(
        self,
        plan: AgentPlan,
        execution_log: List[str],
        telegram_mode: bool = False,
        discord_mode: bool = False,
        telegram_user_id: Optional[int] = None,
    ) -> Tuple[bool, bool]:
        """Execute all actions in a plan.

        Returns (should_sleep, should_exit).
        """
        should_sleep = False
        should_exit = False

        for action in plan.actions:
            # Handle control flow actions first
            if action.type == ActionType.SLEEP:
                should_sleep = True
                self._log(execution_log, "[sleep requested]")
                break
            if action.type == ActionType.EXIT:
                should_exit = True
                self._log(execution_log, "[exit requested]")
                break
            if action.type == ActionType.THINKING:
                thinking_text = action.args.get("text", "")
                if thinking_text:
                    self._log(execution_log, f"[thinking] {thinking_text}")
                continue
            if action.type == ActionType.TELEGRAM:
                msg = action.args.get("message", "")
                self._send_telegram(msg, telegram_mode, telegram_user_id)
                self._log(execution_log, f"[telegram sent] {msg[:100]}")
                continue
            if action.type == ActionType.DISCORD:
                msg = action.args.get("message", "")
                self._send_discord(msg, discord_mode, telegram_user_id)
                self._log(execution_log, f"[discord sent] {msg[:100]}")
                continue
            if action.type == ActionType.PARALLEL:
                parallel_actions = action.args.get("actions", [])
                self._execute_parallel(parallel_actions, execution_log)
                continue

            # Execute a single tool action
            self._execute_single(action, execution_log)

        return should_sleep, should_exit

    def _execute_single(self, action: AgentAction, execution_log: List[str]):
        """Execute a single action via the tool registry."""
        tool_name = action.type
        args = action.args

        # Map action type to tool name and build input
        tool_input = self._build_tool_input(tool_name, args)
        if tool_input is None:
            self._log(execution_log, f"[error] unsupported action type: {tool_name}")
            return

        self._log(execution_log, f"[{tool_name}] {args}")

        try:
            registry = _get_or_create_loop_tool_registry()
            result = registry.execute(tool_name, tool_input)
            if result.success:
                snippet = (result.output or "").strip()[:500]
                self._log(execution_log, f"  ok: {snippet}")
            else:
                err = result.error.message if result.error else "unknown error"
                self._log(execution_log, f"  FAIL: {err}")
        except Exception as e:
            self._log(execution_log, f"  ERROR: {e}")
            self.logger.error(f"Action execution error: {e}")

    def _execute_parallel(self, actions_data: list, execution_log: List[str]):
        """Execute a list of actions in parallel using a thread pool."""
        self._log(execution_log, f"[parallel batch] {len(actions_data)} actions")

        parsed_actions = []
        for action_data in actions_data:
            if isinstance(action_data, dict):
                parsed_actions.append(AgentAction(
                    type=action_data.get("type", "command"),
                    args=action_data.get("args", {}),
                ))

        if not parsed_actions:
            return

        def _run_action(action):
            self._execute_single(action, execution_log)

        # Use ThreadPoolExecutor for parallel I/O-bound tool execution
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(parsed_actions), 8),
        ) as executor:
            futures = [executor.submit(_run_action, a) for a in parsed_actions]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self._log(execution_log, f"  PARALLEL ERROR: {e}")

    def _build_tool_input(self, tool_name: str, args: dict):
        """Convert action args to the appropriate ToolInput."""
        from ..tools.bash import BashInput
        from ..tools.file_read import FileReadInput
        from ..tools.file_write import FileWriteInput
        from ..tools.file_edit import FileEditInput
        from ..tools.glob import GlobInput
        from ..tools.grep import GrepInput

        if tool_name == ActionType.BASH:
            return BashInput(
                command=args.get("command", ""),
                timeout=float(args.get("timeout", 60)),
            )
        elif tool_name == ActionType.READ:
            return FileReadInput(
                file_path=args.get("path", args.get("file_path", "")),
                start_line=args.get("start_line"),
                end_line=args.get("end_line"),
            )
        elif tool_name == ActionType.WRITE:
            return FileWriteInput(
                file_path=args.get("path", args.get("file_path", "")),
                content=args.get("content", ""),
            )
        elif tool_name == ActionType.EDIT:
            return FileEditInput(
                file_path=args.get("path", args.get("file_path", "")),
                old_string=args.get("old_string", args.get("old", "")),
                new_string=args.get("new_string", args.get("new", "")),
            )
        elif tool_name == ActionType.GLOB:
            return GlobInput(
                pattern=args.get("pattern", ""),
                path=args.get("path", "."),
            )
        elif tool_name == ActionType.GREP:
            return GrepInput(
                pattern=args.get("pattern", ""),
                path=args.get("path", "."),
            )
        elif tool_name == ActionType.COMMAND:
            # Fallback: wrap in bash tool
            return BashInput(
                command=args.get("command", args.get("shell", "")),
                timeout=float(args.get("timeout", self.command_timeout)),
            )
        return None

    def _send_telegram(self, message: str, telegram_mode: bool, telegram_user_id: Optional[int] = None):
        """Send a message via Telegram.
        
        Args:
            message: The message to send
            telegram_mode: Whether Telegram mode is enabled
            telegram_user_id: Optional user ID for targeted messages
        """
        if telegram_mode and self.telegram_bot:
            try:
                # queue_message accepts user_id and resolves to channel internally
                user_id = telegram_user_id if telegram_user_id else 0
                self.telegram_bot.queue_message(user_id, message)
            except Exception as e:
                self.logger.error(f"Telegram send failed: {e}")

    def _send_discord(self, message: str, discord_mode: bool, telegram_user_id: Optional[int] = None):
        """Send a message via Discord.
        
        Args:
            message: The message to send
            discord_mode: Whether Discord mode is enabled
            telegram_user_id: User ID for targeted messages (used as user_id for Discord routing)
        """
        if discord_mode and self.discord_bot:
            try:
                # queue_message accepts user_id and resolves to channel internally
                user_id = telegram_user_id if telegram_user_id else 0
                self.discord_bot.queue_message(user_id, message)
            except Exception as e:
                self.logger.error(f"Discord send failed: {e}")

    def _log(self, execution_log: List[str], entry: str):
        """Append timestamped entry to execution log."""
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        execution_log.append(f"[{timestamp}] {entry}")
