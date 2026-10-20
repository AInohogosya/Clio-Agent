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
    # Before the generic `token=` rule, which would otherwise match only the tail
    # of one and leave the bot id standing. A Telegram token is a bot id, a colon,
    # and a secret, and it appears in *URLs* — `api.telegram.org/bot<token>/…` —
    # which is why none of the rules above caught it and why a hundred megabytes
    # of request logs held a working credential in plain text.
    #
    # Deliberately unanchored at the front. The real context is `bot8711664835:…`,
    # where the token abuts the `t` of `bot`, so a leading `\b` fails (no boundary
    # between two word characters) and a `(?<![A-Za-z0-9])` guard fails too — both
    # decline to match the only shape this ever takes. Thirty characters of
    # base64url after a colon is narrow enough to be worth the lack of an anchor.
    re.compile(r"\d{6,12}:[A-Za-z0-9_\-]{30,}"),
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


class RedactingFilter(logging.Filter):
    """Applies the same redaction to records that never went through structlog.

    The redactor above is a structlog processor, and structlog only ever sees what
    structlog logged. `httpx` does not use structlog: it logs through the standard
    library at INFO, and it logs the *full request URL*, which for Telegram is
    `https://api.telegram.org/bot<token>/sendMessage`. So the agent's own messages
    were redacted and the library's — thousands of them an hour, the ones carrying
    the credential — were not.

    A filter on the logger is not enough, and the distinction is the whole reason
    this class exists rather than a `logging.Filter` hung off `httpx`: a logger's
    own filters run only for records logged *to that logger*. `httpx` logs to
    `httpx`, and the record travels to the root **handlers** from there. The
    handlers are the one place every stdlib record passes through, so that is where
    this goes.
    """

    @staticmethod
    def _redact_arg(value: Any) -> Any:
        """Redact one logging argument, changing its type only if it held a secret.

        `httpx` logs `'HTTP Request: %s %s "%s %d %s"'` and passes the URL as an
        `httpx.URL` *object*, not as text. A redactor that only understands strings
        therefore misses the single argument that matters — the one carrying the
        token — while cheerfully handling the status code beside it.

        Stringifying every argument is not the fix either: that would turn the
        `%d` in the same format string into a `TypeError`, and a logging filter
        that raises is a logging filter that has taken down the request it was
        annotating. So the argument is rendered, redacted, and compared with what
        it rendered as. Unchanged means the original object goes back untouched
        and every other `%`-conversion in the line still works; changed means a
        secret was found and the text replaces it.
        """
        if isinstance(value, str):
            return redact_value(value)
        try:
            text = str(value)
        except Exception:
            return value
        redacted = redact_value(text)
        return redacted if redacted != text else value

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_value(record.msg)
        # The args keep their own type. `redact_value` turns a tuple into a list,
        # which `%` formatting tolerates, but a record that was built to be
        # rendered one way and is rendered another is the kind of change that
        # shows up as a missing field in somebody's log rather than as an error.
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: self._redact_arg(v) for k, v in record.args.items()}
        return True


def _install_redacting_handlers() -> None:
    """Put the filter on every handler the root logger has, idempotently."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


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
    # After `basicConfig`, because it is what creates the handlers to attach to.
    _install_redacting_handlers()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    cleaned = redact_value(dict(kwargs))
    structlog.contextvars.bind_contextvars(**cleaned)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
