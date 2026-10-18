"""A credential in a log file is a credential in a file anybody can read.

The Telegram token reached ninety-nine megabytes of log because of two gaps that
had to be closed together, and either one alone would have left it there:

1. The redactor is a *structlog* processor, and `httpx` does not log through
   structlog. It logs through the standard library, at INFO, and it logs the full
   request URL — which for Telegram embeds the token in the path.
2. No pattern matched a token even once it reached the redactor, because a token
   is `<digits>:<secret>` and it arrives inside a URL with no `token=` in front
   of it for any of the existing rules to key off.

So these are mostly tests about the *route* a string takes to the log file, not
about the regular expression.
"""

from __future__ import annotations

import io
import logging

import pytest

from ethos.observability.logger import REDACTED, RedactingFilter, redact_value

#: Shape only, and deliberately not the live token. A real one in a test file is a
#: real one in a git history, in every clone, and in every CI cache — which is the
#: entire problem this file exists to describe.
TOKEN = "1234567890:AAFakeTokenForTestsOnly_0123456789abc"
OTHER_SECRETS = (
    "sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "xoxb-1234567890-abcdefghij",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AIzaSyAbcdefghijklmnopqrstuvwxyz0123456",
)


def test_a_token_in_a_url_is_redacted():
    """The shape that actually leaked: the path of an httpx request log line."""
    line = f'HTTP Request: POST https://api.telegram.org/bot{TOKEN}/sendMessage "HTTP/1.1 200 OK"'

    out = redact_value(line)

    assert TOKEN not in out
    assert REDACTED in out
    # The parts that make the line worth reading survive. A redaction that
    # removed the method or the endpoint would fix the leak by making the log
    # useless, which is the same as not logging.
    assert "sendMessage" in out and "POST" in out


def test_a_token_is_matched_immediately_after_the_word_bot():
    """Anchoring, pinned, because getting it wrong is silent.

    `bot8711664835:…` has no word boundary between the `t` and the `8`, so a
    pattern that starts with `\\b` or guards itself with a negative lookbehind
    declines to match the only context this ever occurs in — and still passes a
    test written against a token standing on its own.
    """
    for prefix in ("bot", "/bot", "bot:", " ", ""):
        assert TOKEN not in redact_value(f"{prefix}{TOKEN}/getUpdates"), prefix


@pytest.mark.parametrize("secret", OTHER_SECRETS)
def test_the_patterns_that_existed_still_hold(secret: str):
    """Guard against the new rule displacing the old ones.

    They are ordered, and an ordering change is the kind of edit that fixes the
    case in front of it and quietly un-fixes one behind it.
    """
    assert secret not in redact_value(f"Authorization: Bearer {secret} / key={secret}")


def test_a_harmless_colon_is_not_mistaken_for_a_token():
    """The rule is unanchored, so its width is what keeps it from eating prose."""
    text = "listening on 127.0.0.1:8710 with 3 adapters and 12 retries, ratio 16:9"

    assert redact_value(text) == text


def test_a_stdlib_record_is_redacted_even_though_it_never_touches_structlog():
    """The gap that let the token through for a hundred and twenty-eight thousand lines."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("httpx.test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(f"HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getUpdates")
    logger.info("POST https://api.telegram.org/bot%s/sendMessage", TOKEN)
    logger.info("endpoint %(url)s", {"url": f"https://api.telegram.org/bot{TOKEN}/x"})

    out = buf.getvalue()
    assert TOKEN not in out
    assert "getUpdates" in out and "sendMessage" in out, "the useful half of the line is kept"


def test_the_filter_leaves_a_records_shape_alone():
    """A record is rendered with `%`, and the args have to survive that.

    Redacting a tuple into a list is harmless for `%s` and wrong for everything
    else, and the failure mode is a log line missing a field rather than an error.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("httpx.shape")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    record = logging.LogRecord(
        "httpx.shape", logging.INFO, __file__, 1,
        "%(a)s then %(b)s", {"a": "first", "b": f"bot{TOKEN}"}, None,
    )
    handler.handle(record)

    out = buf.getvalue()
    assert "first then" in out, "mapping-style args still render"
    assert TOKEN not in out

def test_a_url_object_is_redacted_because_httpx_does_not_log_a_string():
    """The line that actually leaked, in the shape httpx actually emits it.

    `httpx` logs `'HTTP Request: %s %s "%s %d %s"'` and passes the URL as an
    `httpx.URL` object. A redactor that only understands strings handles the status
    code beside it and walks straight past the token. Stringifying every argument
    instead would raise `TypeError` on the `%d` — and a logging filter that raises
    takes down the request it was annotating — so the argument is rendered,
    compared, and only replaced when it really did change.
    """
    import httpx

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("httpx.url")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        'HTTP Request: %s %s "%s %d %s"',
        "POST",
        httpx.URL(f"https://api.telegram.org/bot{TOKEN}/sendMessage"),
        "HTTP/1.1",
        200,
        "OK",
    )

    out = buf.getvalue()
    assert TOKEN not in out, "the token survived as a URL object"
    assert "200" in out and "HTTP/1.1" in out and "OK" in out, "the `%d` and `%s` still render"
    assert "sendMessage" in out


def test_a_number_argument_is_left_as_a_number():
    """The other half of the same trade: nothing is stringified for nothing.

    If every argument were coerced to text, `%d` would raise. So an argument with
    no secret in it has to come back as the object it was.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("httpx.number")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("HTTP Request: %s %s \"HTTP/%s %d %s\"", "GET", "https://example.test/x",
                "1.1", 502, "Bad Gateway")

    out = buf.getvalue()
    assert "502" in out and "Bad Gateway" in out
