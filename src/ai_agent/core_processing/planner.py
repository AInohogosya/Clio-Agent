"""
Planner: The thinking layer of the autonomous agent.

Responsibilities:
  - Build the system prompt (behavioral rules)
  - Build the user prompt (current context + execution log)
  - Call the LLM with structured output (JSON mode)
  - Parse the JSON response into an AgentPlan

This module knows NOTHING about execution. It only thinks.
"""

import json
import time
from typing import Optional
from pathlib import Path

from .agent_schema import AgentPlan, parse_plan_from_json, AGENT_PLAN_SCHEMA
from ..external_integration.model_runner import ModelRunner, TaskType, ModelRequest
from .context_manager import (
    context_files_exist,
    get_context_for_prompt,
    display_context_in_terminal,
)
from ..utils.logger import get_logger


class Planner:
    """Thinks: given context, produces a structured plan of actions."""

    # How many recent log lines to include in the prompt
    MAX_LOG_LINES = 80
    # When to warn about log size
    NOTIFICATION_THRESHOLD = 100

    def __init__(self, model_runner: ModelRunner):
        self.model_runner = model_runner
        self.logger = get_logger("planner")

    def build_system_prompt(self, telegram_mode: bool = False,
                            discord_mode: bool = False) -> str:
        """Build the system prompt — all behavioral rules go here.

        This is sent once per API call as the system message.
        It contains ALL rules about output format, behavior, and anti-loops.
        """
        base = (
            "# Clio Agent 1 — Autonomous AI Agent\n\n"
            "## OUTPUT FORMAT (CRITICAL)\n"
            "You MUST respond with valid JSON matching this schema:\n"
            "```json\n"
            '{\n'
            '  "thinking": "(optional) brief 1-2 sentence reasoning",\n'
            '  "actions": [\n'
            '    {"type": "read", "args": {"path": "/file/path"}},\n'
            '    {"type": "bash", "args": {"command": "ls -la"}}\n'
            '  ]\n'
            '}\n'
            "```\n\n"
            "## ⚡ SUB-AGENT DELEGATION (HIGHEST PRIORITY)\n"
            "You have specialized sub-agents. DELEGATE tasks to them — do NOT do the work yourself.\n\n"
            "### CODING AGENT (MOST IMPORTANT):\n"
            "For ANY coding task — writing code, editing code, debugging, refactoring,\n"
            "writing tests, implementing features — you MUST spawn the Coding Agent:\n"
            '  sub_agent(action="spawn", agent_type="coding", task="<detailed description>")\n'
            "The Coding Agent is FAR more capable than you at all software development.\n"
            "NEVER write code yourself. ALWAYS delegate to the Coding Agent.\n\n"
            "### OTHER SUB-AGENTS:\n"
            "- Research (codebase exploration, analysis): sub_agent(action=\"spawn\", agent_type=\"research\", task=\"...\")\n"
            "- Code Review (quality, security audit): sub_agent(action=\"spawn\", agent_type=\"review\", task=\"...\")\n"
            "- Architecture (system design, ADRs):   sub_agent(action=\"spawn\", agent_type=\"architect\", task=\"...\")\n"
            "- Coding (ALL code write/edit/debug):   sub_agent(action=\"spawn\", agent_type=\"coding\", task=\"...\") ← ALWAYS\n\n"
            "RULES:\n"
            "1. Output ONLY the JSON object. No explanations outside JSON.\n"
            "2. `actions` must have at least 1 item. Never empty.\n"
            "3. DELEGATE coding tasks to the Coding Agent — never write code yourself.\n"
            "4. Prefer direct tool calls (read/write/edit/glob/grep/bash) over command() for non-coding tasks.\n"
            "5. Batch independent reads/searches into one response.\n"
            "6. VARY your actions — don\'t repeat the same command.\n"
            "7. If stuck, try a completely different approach.\n"
            "8. Execute `sleep` when the log grows past 100 lines.\n"
            "9. thinking() is invisible to the user.\n"
        )

        if telegram_mode:
            base += (
                "\n## TELEGRAM MODE\n"
                "- telegram() is the ONLY way to reach the user.\n"
                "- Reply to user messages as your first action.\n"
                "- Send progress updates every 5-10 iterations.\n"
            )
        if discord_mode:
            base += (
                "\n## DISCORD MODE\n"
                "- discord() is the ONLY way to reach the user.\n"
                "- Reply to user messages as your first action.\n"
                "- Send progress updates every 5-10 iterations.\n"
            )

        return base

    def build_user_prompt(
        self,
        goal: str,
        execution_log: str,
        os_info: str,
        iteration: int,
        saved_context: str = "",
        conversation_history: str = "",
        telegram_mode: bool = False,
        discord_mode: bool = False,
        log_line_count: int = 0,
        loop_warning: str = "",
    ) -> str:
        """Build the user prompt — dynamic context only.

        This contains ONLY the current situation, not behavioral rules.
        """
        parts = []

        # Mode banner
        if discord_mode:
            parts.append("💬 DISCORD MODE")
        elif telegram_mode:
            parts.append("📱 TELEGRAM MODE")
        else:
            parts.append("🖥 LOCAL MODE")

        # Task
        parts.append(f"\n## TASK\n{goal or '(self-directed — explore and do useful work)'}")

        # Resume context
        if saved_context:
            parts.append(f"\n## PREVIOUS SESSION\n{saved_context}")

        # Conversation history
        if conversation_history:
            parts.append(f"\n## CONVERSATION\n{conversation_history}")

        # Sleep warning
        if log_line_count >= self.NOTIFICATION_THRESHOLD:
            parts.append(
                f"\n⚠️ SLEEP NOW: log has {log_line_count} lines. "
                'Include {"type": "sleep"} in your actions.'
            )
        elif log_line_count >= self.NOTIFICATION_THRESHOLD * 4 // 5:
            parts.append(
                f"\n⚡ SLEEP SOON: log at {log_line_count}/{self.NOTIFICATION_THRESHOLD} lines."
            )

        # Loop warning
        if loop_warning:
            parts.append(f"\n{loop_warning}")

        # Execution log
        parts.append(f"\n## EXECUTION LOG (last {self.MAX_LOG_LINES} lines)\n{execution_log}")

        return "\n".join(parts)

    def think(
        self,
        goal: str,
        execution_log: str,
        os_info: str,
        iteration: int,
        saved_context: str = "",
        conversation_history: str = "",
        telegram_mode: bool = False,
        discord_mode: bool = False,
        log_line_count: int = 0,
        loop_warning: str = "",
    ) -> Optional[AgentPlan]:
        """Send context to LLM and get a structured plan.

        Returns an AgentPlan on success, None on failure.
        """
        system_prompt = self.build_system_prompt(telegram_mode, discord_mode)
        user_prompt = self.build_user_prompt(
            goal=goal,
            execution_log=execution_log,
            os_info=os_info,
            iteration=iteration,
            saved_context=saved_context,
            conversation_history=conversation_history,
            telegram_mode=telegram_mode,
            discord_mode=discord_mode,
            log_line_count=log_line_count,
            loop_warning=loop_warning,
        )

        request = ModelRequest(
            task_type=TaskType.AUTONOMOUS_LOOP,
            prompt=user_prompt,
            context={
                "goal": goal,
                "os_info": os_info,
                "telegram_mode": telegram_mode,
            },
            max_tokens=4096,
            temperature=0.6,
            system_instruction=system_prompt,
        )

        # Enable JSON mode via response_format
        request.response_format = {
            "type": "json_object",
            "schema": AGENT_PLAN_SCHEMA,
        }

        self.logger.info("Planner: sending request", iteration=iteration)
        response = self.model_runner.run_model(request)

        if not response.success or not response.content.strip():
            self.logger.warning("Planner: empty or failed response", iteration=iteration)
            return None

        try:
            plan = parse_plan_from_json(response.content)
            self.logger.info(
                "Planner: got plan",
                iteration=iteration,
                action_count=len(plan.actions),
                has_thinking=plan.thinking is not None,
            )
            return plan
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.error("Planner: failed to parse JSON response", error=str(e))
            self.logger.debug("Raw response: " + response.content[:500])
            return None
