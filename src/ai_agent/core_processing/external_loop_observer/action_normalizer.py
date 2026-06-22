"""Action Normalizer for External Loop Observer.

Converts raw agent action tuples into normalized, comparable signatures.
This is the foundation of deterministic loop detection.

Design principle: normalization strips away the agent's *intent* (which
the LLM controls) and preserves the *structure* (which reveals loops).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class NormalizedAction:
    """A single normalized action that can be compared for equality."""
    tool: str
    target_hash: str
    operation: str
    parameter_fingerprint: str
    raw_summary: str = ""

    def signature(self) -> str:
        return f"{self.tool}:{self.operation}:{self.target_hash}:{self.parameter_fingerprint}"

    def __str__(self) -> str:
        return self.raw_summary or self.signature()


@dataclass
class NormalizedIteration:
    """All normalized actions from a single agent iteration."""
    iteration_number: int
    actions: Tuple[NormalizedAction, ...] = ()
    timestamp: float = 0.0
    output_digest: str = ""

    def signature(self) -> str:
        parts = sorted(a.signature() for a in self.actions)
        combined = "|".join(parts)
        return hashlib.md5(combined.encode()).hexdigest()[:16]


class ActionNormalizer:
    """Converts raw command tuples into NormalizedAction instances."""

    _INTERNAL_TOOLS = {"thinking"}
    _PATH_IN_COMMAND = re.compile(
        r'(?:^|\s)(?:cd|ls|cat|grep|find|rm|cp|mv|touch|mkdir|chmod|chown|'
        r'vim|nano|code|open)\s+([^\s;|&]+)'
    )
    _FILE_IN_REDIRECT = re.compile(r'[>]\s*([^\s;|&]+)')

    def normalize_iteration(
        self,
        iteration_number: int,
        commands: List[Tuple[str, str]],
        output_text: str = "",
        timestamp: float = 0.0,
    ) -> NormalizedIteration:
        """Normalize all commands from one iteration."""
        actions: List[NormalizedAction] = []
        for cmd_type, arg in commands:
            action = self._normalize_single(cmd_type, arg)
            if action is not None:
                actions.append(action)

        output_digest = ""
        if output_text:
            output_digest = hashlib.md5(output_text.encode()).hexdigest()[:12]

        return NormalizedIteration(
            iteration_number=iteration_number,
            actions=tuple(actions),
            timestamp=timestamp,
            output_digest=output_digest,
        )

    def _normalize_single(self, cmd_type: str, arg: str) -> Optional[NormalizedAction]:
        if cmd_type == "tool_call":
            return self._normalize_tool_call(arg)
        if cmd_type == "command":
            return self._normalize_shell_command(arg)
        if cmd_type == "thinking":
            return None
        if cmd_type in ("telegram", "discord"):
            return self._normalize_messaging(cmd_type, arg)
        if cmd_type == "sleep":
            return NormalizedAction("lifecycle", "sleep", "sleep", "0", "sleep")
        if cmd_type == "exit":
            return NormalizedAction("lifecycle", "exit", "exit", "0", "exit")
        h = hashlib.md5(arg.encode()).hexdigest()[:8]
        return NormalizedAction(cmd_type, h, "unknown", "0", f"{cmd_type}(...)")

    def _normalize_tool_call(self, arg: str) -> Optional[NormalizedAction]:
        try:
            data = json.loads(arg)
        except (json.JSONDecodeError, TypeError):
            h = hashlib.md5(arg.encode()).hexdigest()[:8]
            return NormalizedAction("unknown", h, "tool_call", "0", "tool_call(?)")

        tool = data.get("__tool__", "unknown")
        if tool in self._INTERNAL_TOOLS:
            return None

        target, operation, params = self._extract_tool_info(tool, data)
        target_hash = hashlib.md5(target.encode()).hexdigest()[:8]
        param_fp = self._fingerprint_params(params)

        return NormalizedAction(
            tool=tool, target_hash=target_hash, operation=operation,
            parameter_fingerprint=param_fp, raw_summary=f"{tool}({target})",
        )

    def _extract_tool_info(self, tool, data):
        if tool == "read":
            return data.get("path", ""), "read", {}
        if tool == "write":
            return data.get("path", ""), "write", {}
        if tool == "edit":
            return data.get("path", ""), "edit", {}
        if tool == "glob":
            return data.get("pattern", ""), "search", {}
        if tool == "grep":
            pat = data.get("pattern", "")
            path = data.get("path", ".")
            return f"{pat}@{path}", "search", {}
        if tool == "bash":
            return data.get("command", ""), "exec", {}
        if tool == "sub_agent":
            task = data.get("task", "")
            agent = data.get("agent_type", "")
            return f"{agent}:{task}", "delegate", {}
        all_vals = sorted(str(v) for v in data.values() if v != tool)
        return "|".join(all_vals), "generic", {}

    def _normalize_shell_command(self, arg: str) -> NormalizedAction:
        target = ""
        m = self._PATH_IN_COMMAND.search(arg)
        if m:
            target = m.group(1)
        else:
            m = self._FILE_IN_REDIRECT.search(arg)
            if m:
                target = m.group(1)
        if not target:
            target = arg[:80]
        target_hash = hashlib.md5(target.encode()).hexdigest()[:8]
        param_fp = hashlib.md5(arg.encode()).hexdigest()[:8]
        return NormalizedAction(
            "shell", target_hash, "exec", param_fp, f"shell({arg[:60]})",
        )

    def _normalize_messaging(self, cmd_type: str, arg: str) -> NormalizedAction:
        content_hash = hashlib.md5(arg.encode()).hexdigest()[:8]
        return NormalizedAction(
            cmd_type, content_hash, "message", "0", f"{cmd_type}(len={len(arg)})",
        )

    @staticmethod
    def _fingerprint_params(params: Dict[str, str]) -> str:
        if not params:
            return "0"
        serialized = json.dumps(params, sort_keys=True)
        return hashlib.md5(serialized.encode()).hexdigest()[:8]
