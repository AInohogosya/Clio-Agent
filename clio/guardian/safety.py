from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clio.env import env

DEFAULT_SAFETY_LEVEL = 3
SAFETY_LEVELS = (1, 2, 3, 4)
SUBAGENT_ROLES = ("foundry", "apex", "axiom")

DIALOGUE_TOOLS = frozenset({
    "memory.record",
    "memory.search",
    "memory.remember_fact",
    "memory.open_question",
    "memory.random",
    "history.browse",
    "intent.list",
    "intent.get",
})

DIALOGUE_ALLOW = "allow"
DIALOGUE_REJECT = "reject"
DIALOGUE_REQUIRE_APPROVAL = "require_approval"


def parse_safety_level(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in SAFETY_LEVELS else None
    text = str(value).strip()
    if text in {"1", "2", "3", "4"}:
        return int(text)
    return None


def effective_level(config: Any = None, control: Any = None) -> int:
    if control is not None:
        level = parse_safety_level(getattr(control, "safety_level", None))
        if level is not None:
            return level
    env_level = parse_safety_level(env("SAFETY_LEVEL"))
    if env_level is not None:
        return env_level
    if config is not None:
        perms = getattr(config, "permissions", None)
        level = parse_safety_level(getattr(perms, "safety_level", None))
        if level is not None:
            return level
    return DEFAULT_SAFETY_LEVEL


def is_dialogue_tool(tool: str) -> bool:
    return tool in DIALOGUE_TOOLS


def is_subagent_tool(tool: str) -> bool:
    return tool.startswith("subagent.")


def subagent_name(tool: str) -> str | None:
    if not is_subagent_tool(tool):
        return None
    name = tool.removeprefix("subagent.")
    if name in SUBAGENT_ROLES:
        return name
    return None


def _shell_text(tool: str, args: dict[str, Any]) -> str:
    if tool in ("shell.run", "shell.job") and isinstance(args.get("command"), str):
        return str(args["command"])
    if tool == "code.exec" and isinstance(args.get("code"), str):
        return str(args["code"])
    if tool == "tools.run":
        name = args.get("name") or args.get("tool") or ""
        return str(name)
    return ""


_NETWORK_CODE_HINTS = (
    "http://", "https://", "urllib", "requests.", "httpx", "socket.",
    "ftplib", "smtplib", "websocket", "curl", "wget", "fetch(",
    "ssh ", "scp ", "rsync", "nc ", "ncat",
)


def is_network_command(tool: str, args: dict[str, Any]) -> bool:
    from clio.guardian.classifier import classify_shell, classify_tool

    if tool == "http.request":
        return True
    if tool == "browser.visit":
        return True
    if tool in ("shell.run", "shell.job"):
        command = str(args.get("command") or "")
        if str(args.get("action") or "start") not in ("start",) and tool == "shell.job":
            return False
        if not command:
            return True
        return "network" in classify_shell(command).categories
    effects = classify_tool(tool, args)
    if "network" in effects.categories:
        return True
    if tool == "code.exec":
        code = str(args.get("code") or "")
        lowered = code.lower()
        return any(hint in lowered for hint in _NETWORK_CODE_HINTS)
    return False


@dataclass(frozen=True)
class SafetyDecision:
    action: str
    reason: str
    level: int
    is_dialogue: bool
    is_network: bool
    is_subagent: bool


def _targets_apex(tool: str, args: dict[str, Any]) -> bool:
    if tool == "subagent.apex":
        return True
    if tool == "thread.spawn" and str(args.get("role") or "") == "apex":
        return True
    return False


_HOST_TOOLS = ("http.request", "browser.visit")


def request_host(tool: str, args: dict[str, Any]) -> str | None:
    """The host an outbound request names, if the tool names exactly one.

    Only `http.request` and `browser.visit` carry a URL the gate can read
    before the fact. A shell command that reaches the network names nothing
    of the sort -- `curl` can be told one thing and do another -- so it has
    no host here and never qualifies for an allowlist waiver.
    """
    if tool not in _HOST_TOOLS:
        return None
    url = str(args.get("url") or args.get("href") or "").strip()
    if not url:
        return None
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return f"{parsed.scheme}://{parsed.hostname.lower()}"


def host_allowlisted(tool: str, args: dict[str, Any], allowed_hosts: list[str] | None) -> bool:
    """Whether the request targets only a host the owner pre-approved.

    The allowlist is the level-3 escape hatch that is decided on purpose:
    the owner names the research hosts once (docs, package registries), and
    a step that reads them does not wait five hours for a human to click.
    Matching is prefix-on-origin -- `https://github.com` covers
    `https://github.com/org/repo` and nothing else; `http://` never matches
    an `https://` entry. An empty list allows nothing, the same way an empty
    channel allowlist admits nobody.
    """
    if not allowed_hosts:
        return False
    host = request_host(tool, args)
    if host is None:
        return False
    for entry in allowed_hosts:
        prefix = str(entry or "").strip().rstrip("/").lower()
        if not prefix:
            continue
        if "://" not in prefix:
            prefix = f"https://{prefix}"
        if host == prefix or host.startswith(prefix + "/"):
            return True
    return False


def decide(
    level: int,
    tool: str,
    args: dict[str, Any],
    *,
    inside_subagent: str | None = None,
) -> SafetyDecision:
    dialogue = is_dialogue_tool(tool)
    network = is_network_command(tool, args)
    subagent = is_subagent_tool(tool)
    if level == 1:
        if dialogue:
            return SafetyDecision(DIALOGUE_ALLOW, "dialogue tool", level, True, network, subagent)
        return SafetyDecision(
            DIALOGUE_REJECT,
            "safety level 1 is dialogue-only: no commands and no sub-agents are provided",
            level, False, network, subagent,
        )
    if level == 4:
        return SafetyDecision(DIALOGUE_ALLOW, "safety level 4: no approval required", level, dialogue, network, subagent)
    if dialogue:
        return SafetyDecision(DIALOGUE_ALLOW, "dialogue tool", level, True, network, subagent)
    if inside_subagent is not None:
        if inside_subagent == "apex" or _targets_apex(tool, args):
            return SafetyDecision(
                DIALOGUE_REQUIRE_APPROVAL,
                f"safety level {level}: inside sub-agent {inside_subagent!r} approval is required (APEX only)",
                level, False, network, subagent,
            )
        return SafetyDecision(DIALOGUE_ALLOW, f"inside sub-agent {inside_subagent!r}: no approval until level 4", level, False, network, subagent)
    if level == 2:
        return SafetyDecision(
            DIALOGUE_REQUIRE_APPROVAL,
            "safety level 2: all commands require manual approval",
            level, False, network, subagent,
        )
    if level == 3:
        if network or _targets_apex(tool, args):
            return SafetyDecision(
                DIALOGUE_REQUIRE_APPROVAL,
                "safety level 3: internet-connected commands (and APEX) require manual approval",
                level, False, network, subagent,
            )
        return SafetyDecision(DIALOGUE_ALLOW, "safety level 3: non-internet command", level, False, network, subagent)
    return SafetyDecision(DIALOGUE_ALLOW, "unknown level, treated as level 4", level, dialogue, network, subagent)


def usable_tool_names(tool_names: list[str], level: int) -> list[str]:
    if level != 1:
        return list(tool_names)
    return [name for name in tool_names if is_dialogue_tool(name)]


def describe_level(level: int) -> str:
    descriptions = {
        1: "dialogue-only: no commands, no sub-agents",
        2: "all commands require manual approval",
        3: "internet-connected commands require manual approval, others auto-run",
        4: "all commands and sub-agents run without approval",
    }
    return descriptions.get(level, "unknown level")
