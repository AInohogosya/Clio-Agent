"""
Autonomous Loop Application Entry Point for VEXIS-CLI AI Agent System.
Implements the autonomous think-execute loop architecture.

The agent is fully self-directed. It starts thinking the moment it is
launched — with or without an instruction — and keeps thinking until the
user interrupts or the process is killed.

There is no concept of task completion. An initial instruction is purely
optional seed text; the agent decides what to do on its own.
"""

import sys
import time
import signal
from typing import Optional, Dict, Any
from pathlib import Path

from ..core_processing.autonomous_loop_engine import AutonomousLoopEngine, LoopPhase
from ..utils.exceptions import AIAgentException
from ..utils.logger import get_logger, setup_logging
from ..utils.config import load_config


class AutonomousAIAgent:
    """Autonomous Loop AI Agent implementing the think-execute cycle.

    The agent works continuously.  It does not wait for instructions and
    it does not stop after completing a task — it keeps going.
    """

    def __init__(self, provider: str = None, model: str = None,
                 config_path: Optional[str] = None, telegram_bot=None, discord_bot=None):
        self.config = (
            load_config(config_path, force_reload=bool(config_path))
            if config_path else load_config()
        )
        self.logger = get_logger("autonomous_app")

        engine_config = self._build_engine_config()
        self.engine = AutonomousLoopEngine(
            provider=provider,
            model=model,
            config=engine_config,
            telegram_bot=telegram_bot,
            discord_bot=discord_bot,
        )

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.logger.info("Autonomous Loop AI Agent initialized")

    def _build_engine_config(self) -> Dict[str, Any]:
        """Build engine config from config.yaml."""
        execution = getattr(self.config, "execution", None)
        engine_cfg = getattr(self.config, "engine", None)
        api_cfg = getattr(self.config, "api", None)

        return {
            "command_timeout": getattr(execution, "command_timeout", 1800),
            "task_timeout": getattr(execution, "task_timeout", 7200),
            "api_keys": getattr(api_cfg, "api_keys", {}) or {},
            "openrouter_api_key": getattr(api_cfg, "openrouter_api_key", None) or "",
        }

    def _apply_runtime_options(self, options: Dict[str, Any]) -> None:
        for name in ("command_timeout", "task_timeout"):
            if name in options and options[name] is not None:
                setattr(self.engine, name, options[name])

    def run(self, instruction: Optional[str], options: Dict[str, Any],
            conversation_history=None, cancel_event=None) -> int:
        """Run the agent in perpetual autonomous mode.

        Whether or not an *instruction* is provided, the agent enters its
        infinite think-execute loop immediately and never exits on its own.

        The distinction between "with instruction" and "without instruction"
        is gone — an instruction is simply the initial seed thought.  The
        agent will think and act regardless.
        """
        try:
            self.logger.info(
                "Starting Autonomous Loop — perpetual mode",
                instruction=instruction or "(none)",
                options=options,
            )

            if options.get("verbose"):
                setup_logging(level="DEBUG")
            elif options.get("log_file"):
                setup_logging(file_path=options["log_file"])

            self._apply_runtime_options(options)

            execute_kwargs = {
                "conversation_history": conversation_history,
                "telegram_mode": True,
            }
            if cancel_event is not None:
                execute_kwargs["cancel_event"] = cancel_event

            ctx = self.engine.execute_instruction(instruction or "", **execute_kwargs)

            # If we get here, the loop exited via sleep or error — never
            # via "completion".
            if getattr(ctx, "cancelled", False):
                return 2
            if ctx.error:
                return 1
            return 0

        except AIAgentException as e:
            self.logger.error(f"Autonomous Agent error: {e}")
            print(f"Error: {e}", file=sys.stderr)
            return 3
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            print(f"Unexpected error: {e}", file=sys.stderr)
            return 4

    def run_autonomous_boot(self, options: Dict[str, Any],
                            conversation_history=None,
                            telegram_bot=None,
                            discord_bot=None,
                            initial_instruction: str = None) -> int:
        """Run the agent in perpetual autonomous mode.

        The agent boots, observes its environment, picks work to do,
        executes it, and then immediately picks the next thing to work on.
        This continues indefinitely — there is no "task completion" exit,
        only cancellation or process termination.

        Args:
            options:                Runtime options (debug, command_timeout, …)
            conversation_history:   Optional ConversationHistory for Telegram.
            telegram_bot:           Optional TelegramBotManager.
            initial_instruction:    Optional first seed.  When *None*
                                    the agent boots fully self-directed.

        Returns:
            Exit code (only reached on cancellation or exception).
        """
        try:
            self.logger.info("Starting perpetual autonomous mode — no task boundaries")

            if options.get("verbose"):
                setup_logging(level="DEBUG")
            elif options.get("log_file"):
                setup_logging(file_path=options["log_file"])

            self._apply_runtime_options(options)

            _discord_mode = discord_bot is not None
            _telegram_mode = telegram_bot is not None or (not _discord_mode)
            execute_kwargs = {
                "conversation_history": conversation_history,
                "telegram_mode": _telegram_mode,
                "discord_mode": _discord_mode,
                "telegram_user_id": getattr(
                    discord_bot if _discord_mode else telegram_bot, "_boot_user_id", None
                ) if (telegram_bot or discord_bot) else None,
            }

            ctx = self.engine.execute_instruction(
                initial_instruction or "", **execute_kwargs
            )

            # If we get here, sleep or cancellation ended the loop.
            if getattr(ctx, "cancelled", False):
                return 2
            if ctx.error:
                return 1
            return 0

        except AIAgentException as e:
            self.logger.error(f"Autonomous boot error: {e}")
            print(f"Error: {e}", file=sys.stderr)
            return 3
        except Exception as e:
            self.logger.error(f"Unexpected autonomous boot error: {e}")
            print(f"Unexpected error: {e}", file=sys.stderr)
            return 4

    def _signal_handler(self, signum, frame) -> None:
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        try:
            if hasattr(self, "engine") and self.engine:
                ctx = getattr(self.engine, "_current_context", None)
                if ctx is not None:
                    try:
                        _project_root = Path(__file__).resolve().parents[3]
                        self.engine._handle_exit(
                            ctx, fast=True, project_root=_project_root
                        )
                    except Exception as save_err:
                        self.logger.error(f"Failed to save exit state on signal: {save_err}")
                self.engine.request_cancel()
        except Exception as e:
            self.logger.error(f"Error during cancellation: {e}")
        # Also stop the Telegram bot if it is running
        if hasattr(self, "engine") and self.engine and self.engine.telegram_bot:
            try:
                self.engine.telegram_bot.stop_bot()
            except Exception:
                pass
        sys.exit(0)

    def shutdown(self) -> None:
        self.logger.info("Shutting down Autonomous Loop AI Agent...")


def main():
    """CLI entry point (retained for backward compatibility)."""
    import argparse
    parser = argparse.ArgumentParser(
        description="VEXIS-CLI Autonomous Loop AI Agent",
    )
    parser.add_argument("instruction", type=str, nargs="?", default="",
                        help="Initial thought seed (optional — agent works without it)")
    parser.add_argument("--config", type=str, help="Path to config file")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--log-file", type=str)
    parser.add_argument("--command-timeout", type=int, default=1800)
    parser.add_argument("--task-timeout", type=int, default=7200)

    args = parser.parse_args()
    agent = AutonomousAIAgent(config_path=args.config)
    options = {
        "verbose": args.verbose,
        "quiet": args.quiet,
        "log_file": args.log_file,
        "command_timeout": args.command_timeout,
        "task_timeout": args.task_timeout,
    }
    sys.exit(agent.run(args.instruction or None, options))


if __name__ == "__main__":
    main()
