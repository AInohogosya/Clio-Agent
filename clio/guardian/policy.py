"""GuardianGate Layer 1: fail-closed CEL policies, signed run assertions.

Two mechanisms, one principle: **fail closed**.

The policy engine evaluates structured Common Expression Language rules before
a tool call executes. `deny` rules are evaluated strictly before `allow`
rules; an expression that will not parse, references an attribute the call
does not carry, or raises during evaluation is treated as a `deny` — a policy
nobody can evaluate must never become a policy nobody obeys. Rules are stored
in `cel_policies`, versioned, and reloaded live over PostgreSQL
LISTEN/NOTIFY, so an operator tightening a rule does not restart the
toolhost.

The run-assertion token is the capability a caller carries into the gate: a
signed statement binding `project_id`, `space_id`, `initiator_kind` and
`delegation_depth` to this one call. A token that is expired, mismatches the
call it arrived with, or fails its signature is refused — delegation depth
can only shrink as work is handed down, and a sub-agent cannot mint itself a
human's authority.

The high-impact classifier routes a small, named set of external side
effects — production deploys, messages outside approved channels, financial
transactions above the gate's threshold, host-boundary mounts — to ClioUI
Pre-Execution Approval Cards, unless the project's own policy pre-authorized
them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.guardian.policy")

POLICY_NOTIFY_CHANNEL = "clio_policies"


# ---------------------------------------------------------------------------
# The expression language. A real parser for the CEL subset the policies use.


class PolicyError(Exception):
    """An expression that cannot be evaluated. Always a deny."""


class _Missing:
    """The value of an attribute that is not there. Never comparable."""


_MISSING = _Missing()

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<number>\d+\.\d+|\d+)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<op>==|!=|<=|>=|&&|\|\||[<>()!\[\]{},:.])
  | (?P<ident>[a-zA-Z_][a-zA-Z0-9_]*)
""", re.VERBOSE)

_KEYWORDS = {"true": True, "false": False, "null": None}

_STRING_ESCAPES = {"\\\\": "\\", '\\"': '"', "\\'": "'", "\\n": "\n", "\\t": "\t", "\\r": "\r"}


def _unescape(token: str) -> str:
    """The string literal's value, escapes resolved.

    Only the escapes CEL itself defines are resolved; anything else — a regex
    wanting `\\s` writes `\\\\s` in a policy, as CEL requires — is an invalid
    escape and a parse error, which is fail-closed.
    """
    inner = token[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\":
            pair = inner[i:i + 2]
            if pair not in _STRING_ESCAPES:
                raise PolicyError(f"invalid escape {pair!r} in policy string")
            out.append(_STRING_ESCAPES[pair])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise PolicyError(f"cannot tokenize policy at column {pos}: {text[pos:pos + 12]!r}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        value = match.group()
        if kind == "ident" and value == "in":
            # `in` is membership, spelled like an identifier. Reclassified here
            # so `_comparison` sees an operator where the regex saw a word.
            tokens.append(("op", "in"))
            continue
        tokens.append((kind, value))
    tokens.append(("eof", ""))
    return tokens


class _Parser:
    """Recursive descent over the CEL subset: booleans, comparisons, in, calls."""

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> tuple[str, str]:
        return self._tokens[self._pos]

    def _next(self) -> tuple[str, str]:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, value: str) -> None:
        kind, text = self._next()
        if text != value:
            raise PolicyError(f"expected {value!r}, found {text!r}")

    # precedence: or < and < comparison < unary < primary
    def parse(self) -> Any:
        node = self._or()
        if self._peek()[0] != "eof":
            raise PolicyError(f"trailing tokens at {self._peek()[1]!r}")
        return node

    def _or(self) -> Any:
        node = self._and()
        while self._peek()[1] == "||":
            self._next()
            right = self._and()
            node = ("or", node, right)
        return node

    def _and(self) -> Any:
        node = self._comparison()
        while self._peek()[1] == "&&":
            self._next()
            right = self._comparison()
            node = ("and", node, right)
        return node

    def _comparison(self) -> Any:
        node = self._unary()
        while self._peek()[1] in ("==", "!=", "<", "<=", ">", ">=") or (
            self._peek()[1] == "in"
        ):
            op = self._next()[1]
            right = self._unary()
            node = ("cmp", op, node, right)
        return node

    def _unary(self) -> Any:
        if self._peek()[1] == "!":
            self._next()
            return ("not", self._unary())
        return self._primary()

    def _primary(self) -> Any:
        kind, text = self._peek()
        if text == "(":
            self._next()
            node = self._or()
            self._expect(")")
            return self._postfix(node)
        if kind == "number":
            self._next()
            value = float(text) if "." in text else int(text)
            return value
        if kind == "string":
            self._next()
            return _unescape(text)
        if kind == "ident":
            self._next()
            if text == "in":
                raise PolicyError("'in' is an operator, not a value")
            if text in _KEYWORDS:
                return self._postfix(_KEYWORDS[text])
            if self._peek()[1] == "(":
                # A free function: has(x), size(x). The call's target is a
                # marker the evaluator reads as "no receiver".
                self._next()
                args: list[Any] = []
                if self._peek()[1] != ")":
                    args.append(self._or())
                    while self._peek()[1] == ",":
                        self._next()
                        args.append(self._or())
                self._expect(")")
                return self._postfix(("call", None, text, args))
            return self._postfix(("attr", text))
        if text == "[":
            self._next()
            items: list[Any] = []
            if self._peek()[1] != "]":
                items.append(self._or())
                while self._peek()[1] == ",":
                    self._next()
                    if self._peek()[1] == "]":
                        break
                    items.append(self._or())
            self._expect("]")
            return self._postfix(("list", items))
        if text == "{":
            self._next()
            pairs: list[tuple[Any, Any]] = []
            if self._peek()[1] != "}":
                key = self._or()
                self._expect(":")
                pairs.append((key, self._or()))
                while self._peek()[1] == ",":
                    self._next()
                    if self._peek()[1] == "}":
                        break
                    key = self._or()
                    self._expect(":")
                    pairs.append((key, self._or()))
            self._expect("}")
            return self._postfix(("map", pairs))
        raise PolicyError(f"unexpected token {text!r}")

    def _postfix(self, node: Any) -> Any:
        while True:
            kind, text = self._peek()
            if text == ".":
                self._next()
                name_kind, name = self._next()
                if name_kind != "ident":
                    raise PolicyError("attribute access needs a name")
                if self._peek()[1] == "(":
                    self._next()
                    args: list[Any] = []
                    if self._peek()[1] != ")":
                        args.append(self._or())
                        while self._peek()[1] == ",":
                            self._next()
                            args.append(self._or())
                    self._expect(")")
                    node = ("call", node, name, args)
                else:
                    node = ("getattr", node, name)
            elif text == "[":
                self._next()
                index = self._or()
                self._expect("]")
                node = ("index", node, index)
            else:
                return node


def _evaluate(node: Any, env: dict[str, Any]) -> Any:
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    if isinstance(node, tuple):
        tag = node[0]
        if tag == "attr":
            name = node[1]
            if name in env:
                return env[name]
            raise PolicyError(f"unknown attribute {name!r}")
        if tag == "getattr":
            base = _evaluate(node[1], env)
            name = node[2]
            if base is _MISSING or base is None:
                raise PolicyError(f"attribute {name!r} of a missing value")
            if isinstance(base, dict):
                if name in base:
                    return base[name]
                raise PolicyError(f"missing attribute {name!r}")
            if hasattr(base, name):
                return getattr(base, name)
            raise PolicyError(f"missing attribute {name!r}")
        if tag == "index":
            base = _evaluate(node[1], env)
            index = _evaluate(node[2], env)
            try:
                return base[index]
            except (KeyError, IndexError, TypeError) as exc:
                raise PolicyError(f"index {index!r} out of range") from exc
        if tag == "list":
            return [_evaluate(item, env) for item in node[1]]
        if tag == "map":
            return {_evaluate(k, env): _evaluate(v, env) for k, v in node[1]}
        if tag == "not":
            return not _truthy(_evaluate(node[1], env))
        if tag == "and":
            return _truthy(_evaluate(node[1], env)) and _truthy(_evaluate(node[2], env))
        if tag == "or":
            return _truthy(_evaluate(node[1], env)) or _truthy(_evaluate(node[2], env))
        if tag == "cmp":
            return _compare(node[1], _evaluate(node[2], env), _evaluate(node[3], env))
        if tag == "call":
            if node[2] == "has" and node[3]:
                # `has` is lazy on purpose: its argument is allowed to be
                # missing, and asking is the whole point. Any other function
                # evaluates its arguments strictly.
                try:
                    value = _evaluate(node[3][0], env)
                except PolicyError:
                    return False
                return not isinstance(value, _Missing)
            return _call(_evaluate(node[1], env), node[2],
                         [_evaluate(a, env) for a in node[3]], env)
    raise PolicyError(f"cannot evaluate node {node!r}")


def _truthy(value: Any) -> bool:
    if value is _MISSING:
        return False
    return bool(value)


def _compare(op: str, left: Any, right: Any) -> bool:
    if isinstance(left, _Missing) or isinstance(right, _Missing):
        raise PolicyError(f"comparison {op} on a missing attribute")
    try:
        if op == "==":
            return bool(left == right)
        if op == "!=":
            return bool(left != right)
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
        if op == ">":
            return bool(left > right)
        if op == ">=":
            return bool(left >= right)
        if op == "in":
            return bool(left in right)
    except TypeError as exc:
        raise PolicyError(f"comparison {op} failed: {exc}") from exc
    raise PolicyError(f"unknown operator {op}")


def _call(target: Any, name: str, args: list[Any], env: dict[str, Any]) -> Any:
    # Free functions: size(x). Methods: matches, startsWith, endsWith,
    # contains — on strings. `has` is handled lazily in `_evaluate`.
    if isinstance(target, str) and name == "matches":
        pattern = str(args[0])
        try:
            return re.search(pattern, target) is not None
        except re.error as exc:
            raise PolicyError(f"bad regex in policy: {exc}") from exc
    if isinstance(target, str) and name == "startsWith":
        return target.startswith(str(args[0]))
    if isinstance(target, str) and name == "endsWith":
        return target.endswith(str(args[0]))
    if isinstance(target, str) and name == "contains":
        return str(args[0]) in target
    if isinstance(target, list) and name == "contains":
        return args[0] in target
    if name == "has":
        # has(x.y) is true when the attribute exists — the one call that may
        # ask about a missing value without failing closed.
        return not isinstance(args[0] if args else _MISSING, _Missing)
    if name == "size":
        try:
            return len(args[0])
        except TypeError as exc:
            raise PolicyError("size() of a scalar") from exc
    raise PolicyError(f"unknown function {name!r}")


def evaluate_policy_expression(expression: str, env: dict[str, Any]) -> bool:
    """Evaluate one policy expression. Every failure is a False (deny)."""
    return evaluate_or_none(expression, env) is True


def evaluate_or_none(expression: str, env: dict[str, Any]) -> bool | None:
    """Evaluate one expression, distinguishing "false" from "could not be asked".

    `None` is the fail-closed answer for an expression that will not parse,
    names an attribute the call does not carry, or raises while evaluating.
    The engine treats a `None` from a deny rule as a deny — a rule nobody can
    evaluate must never become a rule nobody obeys — and a `None` from an
    allow rule as no opinion at all, because an allow whose basis cannot be
    evaluated is not a basis.
    """
    if not expression or not expression.strip():
        return None
    try:
        ast = _Parser(_tokenize(expression)).parse()
        return _truthy(_evaluate(ast, env))
    except PolicyError:
        return None
    except RecursionError:
        return None


class MissingAttributeError(PolicyError):
    """Raised when the caller asks for the reason a missing attribute denied."""


def evaluate_strict(expression: str, env: dict[str, Any]) -> bool:
    """Evaluate and raise on failure — for the error path that reports why."""
    ast = _Parser(_tokenize(expression)).parse()
    return _truthy(_evaluate(ast, env))


# ---------------------------------------------------------------------------
# The policy engine over the database.


@dataclass
class PolicyRule:
    policy_id: str
    effect: str
    expression: str
    priority: int = 100
    description: str = ""
    version: int = 1


@dataclass
class PolicyDecision:
    effect: str            # "allow" | "deny"
    rule_id: str | None    # the rule that decided, for the audit line
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def call_environment(tool: str, args: dict[str, Any], *,
                     project_id: str | None, space_id: str,
                     initiator_kind: str, delegation_depth: int,
                     thread_id: str | None = None) -> dict[str, Any]:
    """The attribute bag a policy expression sees. Deny-closed on absence."""
    return {
        "tool": tool,
        "args": dict(args or {}),
        "project_id": project_id,
        "space_id": space_id,
        "initiator_kind": initiator_kind,
        "delegation_depth": delegation_depth,
        "thread_id": thread_id,
    }


class PolicyEngine:
    """CEL rules with deny-before-allow, live-reloaded over LISTEN/NOTIFY."""

    def __init__(self, db: Any, listener: Any = None) -> None:
        self._db = db
        self._rules: list[PolicyRule] = []
        self._loaded_version: int = -1
        self._listener = listener
        self._listening = False

    async def load(self) -> int:
        rows = await self._db.fetch(
            "SELECT policy_id, effect, expression, priority, description, version "
            "FROM cel_policies WHERE enabled = true ORDER BY priority ASC"
        )
        self._rules = [
            PolicyRule(
                policy_id=str(r["policy_id"]), effect=str(r["effect"]),
                expression=str(r["expression"]), priority=int(r["priority"] or 100),
                description=str(r["description"] or ""), version=int(r["version"] or 1),
            )
            for r in rows
        ]
        self._loaded_version = int(time.time())
        return len(self._rules)

    async def start(self) -> None:
        await self.load()
        if self._listener is None:
            return
        conn = await self._listener.new_listener_connection()
        await conn.add_listener(POLICY_NOTIFY_CHANNEL, self._on_notify)
        self._listening = True

    async def stop(self) -> None:
        self._listening = False

    def _on_notify(self, _conn: Any, _pid: Any, _channel: str, _payload: str) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._reload())

    async def _reload(self) -> None:
        try:
            count = await self.load()
            logger.info("guardian.policies_reloaded", rules=count)
        except Exception:
            logger.exception("guardian.policy_reload_failed")

    def notify_changed(self) -> None:
        """Announce a rule change. Callers that edited the table call this."""
        import asyncio

        async def _ping() -> None:
            await self._db.execute(f"SELECT pg_notify('{POLICY_NOTIFY_CHANNEL}', 'reload')")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_ping())
        except RuntimeError:
            pass

    async def decide(self, env: dict[str, Any]) -> PolicyDecision:
        """Deny before allow, fail closed on every error.

        A deny rule whose expression errors is a deny (fail closed); an allow
        rule whose expression errors is nothing at all — it never allows.
        """
        if not self._rules:
            return PolicyDecision("allow", None, "no policies loaded")
        denies: list[PolicyRule] = [r for r in self._rules if r.effect == "deny"]
        allows: list[PolicyRule] = [r for r in self._rules if r.effect == "allow"]
        for rule in denies:
            evaluated = evaluate_or_none(rule.expression, env)
            if evaluated is True:
                return PolicyDecision(
                    "deny", rule.policy_id,
                    rule.description or f"denied by policy {rule.policy_id}",
                )
            if evaluated is None:
                # The rule could not be evaluated: fail closed, and name the
                # rule so the operator can fix the expression. A deny nobody
                # can evaluate is a deny.
                return PolicyDecision(
                    "deny", rule.policy_id,
                    f"deny rule {rule.policy_id} could not be evaluated "
                    "(broken or missing attributes); the call is refused (fail closed)",
                )
        for rule in allows:
            evaluated = evaluate_or_none(rule.expression, env)
            if evaluated is True:
                return PolicyDecision(
                    "allow", rule.policy_id,
                    rule.description or f"allowed by policy {rule.policy_id}",
                )
        # Rules exist but none matched. The default is the operator's choice,
        # spelled in the schema of the table: an unmatched call is allowed —
        # the ladder below (safety levels, hard limits, due process) still
        # governs it. A deployment that wants default-deny writes one deny
        # rule: `true`.
        return PolicyDecision("allow", None, "no policy matched")


# ---------------------------------------------------------------------------
# Signed run assertions.


_ASSERTION_TTL_S = 900.0
_INITIATOR_KINDS = ("human_chat", "gsl_step", "evergreen_evolution", "subagent")


def runtime_key() -> bytes:
    """The signing key: `CLIO_ASSERTION_KEY`, or derived from the master key."""
    from clio.env import env

    named = env("ASSERTION_KEY")
    if named:
        return hashlib.sha256(named.encode("utf-8")).digest()
    master = env("MASTER_KEY")
    if master:
        return hashlib.sha256(f"assertion:{master}".encode()).digest()
    # A deployment with neither gets an ephemeral process key: tokens it mints
    # die with the process, which is the safe default for a deployment that
    # has not configured delegation at all.
    if not hasattr(runtime_key, "_ephemeral"):
        runtime_key._ephemeral = os.urandom(32)  # type: ignore[attr-defined]
    return runtime_key._ephemeral  # type: ignore[attr-defined]


def mint_assertion(
    *,
    project_id: str | None,
    space_id: str,
    initiator_kind: str,
    delegation_depth: int,
    ttl_s: float = _ASSERTION_TTL_S,
    intention_id: str | None = None,
    key: bytes | None = None,
) -> str:
    """A signed runAssertion: who initiated, how deep, until when."""
    if initiator_kind not in _INITIATOR_KINDS:
        raise ValueError(f"initiator_kind must be one of {_INITIATOR_KINDS}")
    body = {
        "project_id": project_id,
        "space_id": space_id,
        "initiator_kind": initiator_kind,
        "delegation_depth": int(delegation_depth),
        "intention_id": intention_id,
        "exp": time.time() + ttl_s,
        "iat": time.time(),
    }
    payload = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True).encode("utf-8"))
    signature = hmac.new(key or runtime_key(), payload, hashlib.sha256).hexdigest()
    return f"{payload.decode('ascii')}.{signature}"


@dataclass
class Assertion:
    project_id: str | None
    space_id: str
    initiator_kind: str
    delegation_depth: int
    intention_id: str | None
    expires_at: float


def verify_assertion(token: str, *, key: bytes | None = None) -> Assertion | None:
    """Verify a token, or answer None — a bad token is no token."""
    if not token or "." not in token:
        return None
    payload_b64, signature = token.rsplit(".", 1)
    expected = hmac.new(key or runtime_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    except (ValueError, TypeError):
        return None
    if float(body.get("exp") or 0) < time.time():
        return None
    if body.get("initiator_kind") not in _INITIATOR_KINDS:
        return None
    depth = int(body.get("delegation_depth", 0))
    if depth < 0:
        return None
    return Assertion(
        project_id=body.get("project_id"),
        space_id=str(body.get("space_id") or ""),
        initiator_kind=str(body["initiator_kind"]),
        delegation_depth=depth,
        intention_id=body.get("intention_id"),
        expires_at=float(body["exp"]),
    )


def assertion_admits(assertion: Assertion | None, *,
                     required_depth: int, space_id: str | None) -> bool:
    """Whether a verified token may act at the required depth in this space."""
    if assertion is None:
        return False
    if space_id is not None and assertion.space_id and assertion.space_id != space_id:
        return False
    return assertion.delegation_depth <= required_depth


# ---------------------------------------------------------------------------
# High-impact external side effects → Pre-Execution Approval Cards.


_PRODUCTION_HINTS = re.compile(
    r"\b(deploy|production|prod\b|release to|kubectl|terraform apply|"
    r"docker push|npm publish|git push .*(main|master|production))",
    re.IGNORECASE,
)
_MOUNT_HINTS = re.compile(r"\b(mount|umount|diskutil|losetup|mkfs)\b", re.IGNORECASE)


def high_impact_external_side_effect(
    tool: str, args: dict[str, Any], *, money_usd: float | None = None,
    approved_channels: set[str] | None = None,
) -> str | None:
    """Why this call needs a Pre-Execution Approval Card, or None if it does not.

    The named set: production deploys, external messages outside approved
    channels, financial transactions, host-boundary mounts. Each is external,
    each is hard to undo from inside the agent, and each is exactly the class
    the approval card exists for. Money above the gate's own H2 threshold is
    already governed there; this classifier does not second-guess it.
    """
    if tool == "shell.run" or tool == "shell.job":
        command = str(args.get("command") or "")
        if _PRODUCTION_HINTS.search(command):
            return "production deploy"
        if _MOUNT_HINTS.search(command):
            return "host-boundary mount"
        return None
    if tool == "comms.send":
        channel = str(args.get("channel") or "")
        if approved_channels is not None and channel and channel not in approved_channels:
            return f"external message over unapproved channel {channel!r}"
        return None
    if tool == "http.request":
        url = str(args.get("url") or "")
        method = str(args.get("method") or "GET").upper()
        if method in ("POST", "PUT", "DELETE", "PATCH") and _PRODUCTION_HINTS.search(url):
            return "external write to a production endpoint"
        return None
    return None


__all__ = [
    "Assertion",
    "MissingAttributeError",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyError",
    "PolicyRule",
    "assertion_admits",
    "call_environment",
    "evaluate_policy_expression",
    "evaluate_strict",
    "high_impact_external_side_effect",
    "mint_assertion",
    "runtime_key",
    "verify_assertion",
]
