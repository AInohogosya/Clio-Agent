from __future__ import annotations

import ast
import io
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from ethos.observability.logger import get_logger

logger = get_logger("ethos.toolhost.kernels")

MAX_OUTPUT_CHARS = 60_000
EXEC_TIMEOUT_S = 60


class KernelTimeout(RuntimeError):
    pass


class _ResultCapture:
    def __init__(self) -> None:
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.value: Any = None


class KernelSession:
    """In-memory persistent Python kernel: state survives across code.exec calls."""

    def __init__(self, session_id: str, namespace: dict[str, Any] | None = None):
        self.id = session_id
        self.namespace: dict[str, Any] = namespace if namespace is not None else {"__name__": "__ethos_kernel__"}
        self.lock = threading.Lock()

    def exec(self, code: str, timeout_s: float = EXEC_TIMEOUT_S) -> dict[str, Any]:
        result = _ResultCapture()
        error = None
        with self.lock:
            try:
                with redirect_stdout(result.stdout), redirect_stderr(result.stderr):
                    tree = ast.parse(code, filename=f"<kernel-{self.id}>")
                    if tree.body and isinstance(tree.body[-1], ast.Expr):
                        *init, last = tree.body
                        if init:
                            module = ast.Module(body=init, type_ignores=[])
                            exec(compile(module, f"<kernel-{self.id}>", "exec"), self.namespace)
                        result.value = eval(
                            compile(ast.Expression(last.value), f"<kernel-{self.id}>", "eval"),
                            self.namespace,
                        )
                    else:
                        compiled = compile(code, f"<kernel-{self.id}>", "exec")
                        exec(compiled, self.namespace)
                        underscore = self.namespace.get("_", None)
                        if underscore is not None:
                            result.value = underscore
            except BaseException:
                error = traceback.format_exc(limit=8)
        return {
            "stdout": result.stdout.getvalue()[:MAX_OUTPUT_CHARS],
            "stderr": result.stderr.getvalue()[:MAX_OUTPUT_CHARS],
            "value": _safe_repr(result.value),
            "error": error,
            "ok": error is None,
        }


def _safe_repr(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return repr(value)[:MAX_OUTPUT_CHARS]
    except Exception:
        return "<unrepresentable>"


class KernelManager:
    def __init__(self, idle_ttl_s: float = 3600.0):
        self.sessions: dict[str, KernelSession] = {}
        self.idle_ttl_s = idle_ttl_s

    def get_or_create(self, session_id: str) -> KernelSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = KernelSession(session_id)
        return self.sessions[session_id]

    def reset(self, session_id: str) -> KernelSession:
        self.sessions[session_id] = KernelSession(session_id)
        return self.sessions[session_id]

    def close(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def list(self) -> list[dict[str, Any]]:
        return [{"id": s.id, "symbols": len(s.namespace)} for s in self.sessions.values()]
