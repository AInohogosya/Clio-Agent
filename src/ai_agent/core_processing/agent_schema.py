"""
Structured output schema for the autonomous agent.

Instead of parsing free-form text with regex, we ask the LLM to output
JSON that conforms to a strict schema. This eliminates the entire class
of parsing errors and makes the agent fundamentally more reliable.

OpenRouter supports response_format={type: "json_object"} which forces
the model to output valid JSON. We combine this with a JSON Schema via
the response_format.schema field (supported by OpenAI-compatible endpoints).
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ActionType(str, Enum):
    """All possible action types the agent can emit."""
    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    GLOB = "glob"
    GREP = "grep"
    BASH = "bash"
    COMMAND = "command"
    THINKING = "thinking"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLEEP = "sleep"
    EXIT = "exit"
    PARALLEL = "parallel"


@dataclass
class AgentAction:
    """A single action emitted by the agent."""
    type: str  # ActionType value
    args: dict = field(default_factory=dict)
    # For PARALLEL type, args contains {"actions": [AgentAction, ...]}


@dataclass
class AgentPlan:
    """The complete output from the planner — a structured plan."""
    thinking: Optional[str] = None  # Optional internal reasoning
    actions: List[AgentAction] = field(default_factory=list)

    def has_real_work(self) -> bool:
        """Check if this plan contains any meaningful actions."""
        real_types = {
            ActionType.READ, ActionType.WRITE, ActionType.EDIT,
            ActionType.GLOB, ActionType.GREP, ActionType.BASH,
            ActionType.COMMAND, ActionType.TELEGRAM, ActionType.DISCORD,
        }
        return any(a.type in real_types for a in self.actions)

    def get_action_signatures(self) -> List[str]:
        """Get normalized signatures for loop detection."""
        sigs = []
        for a in self.actions:
            if a.type == ActionType.THINKING:
                sigs.append("thinking")
            elif a.type in (ActionType.TELEGRAM, ActionType.DISCORD):
                sigs.append(a.type)
            else:
                # Hash the args for dedup
                import hashlib, json
                arg_hash = hashlib.md5(
                    json.dumps(a.args, sort_keys=True).encode()
                ).hexdigest()[:8]
                sigs.append(f"{a.type}:{arg_hash}")
        sigs.sort()
        return sigs


# ── JSON Schema for OpenRouter response_format ──
# This schema is sent to the LLM API to enforce structured output.
AGENT_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "thinking": {
            "type": "string",
            "description": "Brief internal reasoning (1-2 sentences max). Optional."
        },
        "actions": {
            "type": "array",
            "description": "List of actions to execute. Must contain at least one action.",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "read", "write", "edit", "glob", "grep",
                            "bash", "command", "thinking", "telegram",
                            "discord", "sleep", "exit", "parallel"
                        ]
                    },
                    "args": {
                        "type": "object",
                        "description": "Action-specific arguments"
                    }
                },
                "required": ["type"],
                "additionalProperties": False
            }
        }
    },
    "required": ["actions"],
    "additionalProperties": False
}


def parse_plan_from_json(raw: str) -> AgentPlan:
    """Parse a JSON string into an AgentPlan.

    This replaces the entire regex-based _parse_model_commands().
    """
    import json
    # Strip markdown code blocks if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```") and not in_block:
                in_block = True
                continue
            if line.strip() == "```" and in_block:
                break
            if in_block:
                inner_lines.append(line)
        text = "\n".join(inner_lines).strip()

    data = json.loads(text)
    plan = AgentPlan()
    plan.thinking = data.get("thinking")
    for action_data in data.get("actions", []):
        action = AgentAction(
            type=action_data["type"],
            args=action_data.get("args", {})
        )
        plan.actions.append(action)
    return plan
