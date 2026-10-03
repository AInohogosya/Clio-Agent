from __future__ import annotations

import logging
import re
from typing import Any

import structlog

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)['\"]?\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"\{\{secret:[A-Za-z0-9_\-\./]+?\}\}"),
]

REDACTED = "[REDACTED]"


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and any(s in k.lower() for s in ("password", "secret", "api_key", "token")):
                out[k] = REDACTED
            else:
                out[k] = redact_value(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


def redact_secrets(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Before the redactor, so a traceback — whose exception messages can
            # carry a URL with a key in it — is redacted like any other value.
            # Without this processor every `logger.exception` in the agent
            # printed `exc_info: true` and dropped the traceback entirely, so
            # the one line that exists to explain a failed cycle explained
            # nothing, and a silence in the logs stayed as unanswerable as the
            # silences in the transcript.
            structlog.processors.format_exc_info,
            redact_secrets,
            structlog.processors.StackInfoRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    cleaned = redact_value(dict(kwargs))
    structlog.contextvars.bind_contextvars(**cleaned)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
