"""The sub-agent contract: what delegating to one means, and what comes back.

A sub-agent is an agent program with its own identity, its own state machine
and its own notion of done. The one thing it shares with this agent is who
pays for its thinking and under what rules — the gateway. Everything else is
normalized at this boundary: `SubAgentResult` is the shape a sub-agent's
outcome takes inside this agent, whatever shape the program itself reports.
"""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ethos.config import EthosConfig, SubagentConfig
from ethos.observability.logger import get_logger

logger = get_logger("ethos.subagents")

# The normalized status vocabulary. Every adapter maps its program's own
# terminal states onto exactly one of these; nothing else crosses the boundary.
STATUS_DONE = "done"                    # the sub-agent completed and verified it
STATUS_BLOCKED = "blocked"              # safely stopped, needs a human decision
STATUS_OUTCOME_UNKNOWN = "unknown"      # may or may not have happened — never retried blind
STATUS_FAILED = "failed"                # the sub-agent itself failed
STATUS_TIMEOUT = "timeout"
STATUS_UNAVAILABLE = "unavailable"      # the program could not be reached at all


@dataclass
class SubAgentResult:
    """One delegation's outcome, in this agent's vocabulary."""

    agent: str
    status: str
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_s: float = 0.0
    mode: str = ""       # "inprocess" | "subprocess"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SubAgent(ABC):
    """One external agent program, as this agent sees it."""

    name: str = "abstract"
    # The directory name the program ships in when config names none. Not
    # derived from `name`: the sibling directories are `Foundry/` and `APEX/`,
    # and no case rule generates the second from the first.
    dirname: str = ""
    description: str = ""
    # What a caller is expected to hand `run()` — shown in the tool catalog.
    input_contract: str = "task: str"

    def __init__(
        self,
        config: EthosConfig,
        settings: SubagentConfig,
        gateway: Any = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.gateway = gateway

    # ---- program location -----------------------------------------------

    @classmethod
    def default_root(cls) -> Path:
        """The program's home when config names none: the sibling directory
        beside this checkout (the layout this repository ships in)."""
        return Path(__file__).resolve().parents[2] / (cls.dirname or cls.name.capitalize())

    @property
    def root(self) -> Path:
        raw = self.settings.root or str(self.default_root())
        return Path(raw).expanduser().resolve()

    def repo_root(self, workdir: str | None = None) -> Path:
        """The directory the sub-agent should work on. Foundry's target
        repository, or a thread's isolated worktree — never this checkout
        by accident."""
        raw = self.settings.repo_root or workdir
        return Path(raw).expanduser().resolve() if raw else self.root

    # ---- the gateway, as sub-agents reach it -----------------------------

    def gateway_url(self) -> str:
        """This agent's gateway over HTTP, as a child process reaches it."""
        raw = self.config.subagents.gateway_url or os.environ.get("ETHOS_GATEWAY_URL", "")
        if raw:
            return raw.rstrip("/")
        return f"http://127.0.0.1:{self.config.gateway.port}"

    def model_name(self) -> str:
        """The model name sub-agents send on the wire: this agent's base
        model unless the deployment pinned one for delegation."""
        if self.config.subagents.model_name:
            return self.config.subagents.model_name
        if self.config.models.base is not None:
            return self.config.models.base.model
        return self.config.self_name.lower().replace(" ", "-")

    def tier(self):
        from ethos.schemas.models import Tier

        try:
            return Tier(self.config.subagents.tier)
        except ValueError:
            return Tier.T2

    def purpose(self, step: str = "run") -> str:
        return f"subagent.{self.name}.{step}"

    # ---- running ---------------------------------------------------------

    @abstractmethod
    async def run(self, task: str, *, workdir: str | None = None, **opts: Any) -> SubAgentResult:
        """Delegate one task to the sub-agent and wait for its outcome."""

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Whether the program is reachable, and how."""

    # ---- shared mechanics -------------------------------------------------

    def child_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment a child process of this sub-agent runs under.

        The bridge variables are the point: they point the child's
        OpenAI-compatible client at this agent's gateway, so the child's model
        calls are routed, budgeted and failed over here rather than at a
        vendor it was handed directly.
        """
        env = dict(os.environ)
        env.update(self.settings.env)
        if extra:
            env.update(extra)
        return env

    async def run_command(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> tuple[int, str, str]:
        """One child process, with a wall-clock ceiling and output capture."""
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        logger.info(
            "subagent.command",
            agent=self.name,
            argv=argv[:2],
            exit=proc.returncode,
            seconds=round(time.monotonic() - started, 1),
        )
        return proc.returncode or 0, out.decode("utf-8", errors="replace"), ""

    def timed(self, result: SubAgentResult, started: float) -> SubAgentResult:
        result.duration_s = round(time.monotonic() - started, 1)
        return result

    def timeout_s(self, override: float | None = None) -> float:
        return float(override if override is not None else self.settings.timeout_s)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a child's stdout, or `None` — child CLIs print
    banners, warnings and logs around the JSON they were asked for."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    import json

    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "SubAgent",
    "SubAgentResult",
    "extract_json_object",
    "STATUS_DONE",
    "STATUS_BLOCKED",
    "STATUS_OUTCOME_UNKNOWN",
    "STATUS_FAILED",
    "STATUS_TIMEOUT",
    "STATUS_UNAVAILABLE",
]
