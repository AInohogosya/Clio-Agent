"""The sub-agent coordination layer.

External agent programs — Foundry and APEX, living beside this checkout — are
wired in here as sub-agents of this agent. Each keeps its own identity, its
own state machine and its own interface; what it gives up is its own private
line to a model vendor.

Three pieces make that work:

* `base`      — the normalized result contract every sub-agent reports in.
* `bridge`    — the model bridge. Sub-agent model calls are translated onto
                this agent's gateway, so `models.yaml`, the spend guard,
                retries, failover and the base model govern them exactly as
                they govern the Self. Where a sub-agent cannot call in-process,
                the bridge points its OpenAI-compatible client at the gateway's
                own OpenAI-compatible shim (`/v1/chat/completions`).
* one adapter per program — which knows that program's real interface
  (Foundry's Orchestrator, APEX's ControlPlane) and drives it, in-process
  when it can be imported safely, as a child process when it cannot.
"""

from __future__ import annotations

from typing import Any

from ethos.config import EthosConfig
from ethos.observability.logger import get_logger

logger = get_logger("ethos.subagents")


class SubagentRegistry:
    """The sub-agents this deployment has, built from `subagents:` config."""

    def __init__(self, config: EthosConfig, gateway: Any = None) -> None:
        self.config = config
        self._agents: dict[str, Any] = {}
        self._unavailable: dict[str, str] = {}
        self._load(config, gateway)

    def _load(self, config: EthosConfig, gateway: Any) -> None:
        # Imported here, not at module import: an adapter module touches the
        # sub-agent program's own source tree, and one broken program must not
        # stop the others — or the agent itself — from starting.
        from ethos.subagents.apex import ApexSubAgent
        from ethos.subagents.foundry import FoundrySubAgent

        for cls in (FoundrySubAgent, ApexSubAgent):
            settings = config.subagents.agent(cls.name)
            if settings is None or not settings.enabled:
                continue
            try:
                self._agents[cls.name] = cls(config, settings, gateway=gateway)
            except Exception as exc:
                logger.warning("subagent.unavailable", agent=cls.name, error=str(exc))
                self._unavailable[cls.name] = str(exc)

    def get(self, name: str) -> Any | None:
        return self._agents.get(name)

    def names(self) -> list[str]:
        return sorted(self._agents)

    def has(self, name: str) -> bool:
        return name in self._agents

    def describe(self) -> list[dict[str, Any]]:
        """One row per sub-agent, as the tool catalog and prompts show them."""
        rows: list[dict[str, Any]] = []
        for name in sorted(self._agents):
            agent = self._agents[name]
            rows.append({
                "name": name,
                "description": agent.description,
                "mode": agent.settings.mode,
                "root": str(agent.root),
                "input": agent.input_contract,
            })
        for name, reason in sorted(self._unavailable.items()):
            rows.append({"name": name, "description": "", "mode": "unavailable",
                         "root": "", "input": reason})
        return rows

    async def run(self, name: str, task: str, **opts: Any) -> Any:
        agent = self._agents.get(name)
        if agent is None:
            reason = self._unavailable.get(name, "no such sub-agent")
            return {"agent": name, "status": "unavailable", "error": reason}
        return await agent.run(task, **opts)

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, agent in self._agents.items():
            try:
                out[name] = await agent.health()
            except Exception as exc:
                out[name] = {"available": False, "error": str(exc)}
        for name, reason in self._unavailable.items():
            out[name] = {"available": False, "error": reason}
        return out


__all__ = ["SubagentRegistry"]
