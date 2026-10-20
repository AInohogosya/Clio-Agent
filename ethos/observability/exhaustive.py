"""Exhaustive capture of what the agent does, one JSON line at a time.

`logger.py` answers the operator's question — what happened, at the level a
console is worth reading. This module answers the archivist's question: every
fact this program can know about an action, kept whether or not anybody is
watching a terminal. It is deliberately independent of `ETHOS_LOG_LEVEL`:
granularity here is not a runtime tuning knob, it is the point.

Three kinds of record land here:

  user.action      everything a person did to the agent — a message sent on a
                   channel, a control verb pressed, a secret set, a channel door
                   opened or closed — with what changed, when, and by whom.
  model.request    every call to the model gateway, verbatim: the full message
                   list, every sampling parameter, the tier, purpose, budget
                   bucket, and the moment the ask was made.
  model.response   the answer to one of those asks, verbatim: which model
                   produced it, the moment it came back (to the second; the
                   minute is carried alongside), the full text, the reasoning
                   traces the provider exposed, token counts, cost, latency.
  model.failure    an ask that did not produce an answer, and why.

Two honest limits, written here because nowhere else would say them:

  * A hosted model's *internal* chain of thought is not ours to read. What a
    provider sends back — Anthropic thinking blocks, the `reasoning_content`
    OpenRouter and DeepSeek spellings, Google's thought parts — is captured
    verbatim under `cot`. When a provider exposes nothing, the record says so
    under `cot_exposed: false` with the reasoning token count beside it, rather
    than leaving an absent field to be mistaken for a model that did not think.

  * Secrets stay redacted. An exhaustive log that carries a live credential is
    not a more complete log, it is an incident waiting in a file — the exact
    failure `logger.py`'s redactor exists for, and this sink applies the same
    redaction to every value it writes.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ethos.observability.logger import redact_value

_default_dir = Path(
    os.environ.get("ETHOS_EXHAUSTIVE_LOG_DIR")
    or Path.home() / ".ethos" / "data" / "logs"
)
_enabled = os.environ.get("ETHOS_EXHAUSTIVE_LOG", "1") not in ("0", "false", "no")

_lock = threading.Lock()


def _stamp(now: datetime) -> tuple[str, str]:
    """(ISO timestamp, minute) — the minute carried beside the second, since
    'when, down to the minute' is the question asked of every record here."""
    iso = now.astimezone(UTC).isoformat(timespec="seconds")
    return iso, iso[:16]


def record(kind: str, **payload: Any) -> dict[str, Any] | None:
    """Append one redacted JSON line. Never raises: a logging sink that takes
    the agent down mid-action is a worse failure than a lost record."""
    if not _enabled:
        return None
    try:
        now = datetime.now(UTC)
        ts, minute = _stamp(now)
        entry = {
            "ts": ts,
            "minute": minute,
            "kind": kind,
            "pid": os.getpid(),
            "process": Path(sys.argv[0]).name if sys.argv else "",
            **{k: redact_value(v) for k, v in payload.items()},
        }
        line = json.dumps(entry, sort_keys=True, default=str, ensure_ascii=False)
        _default_dir.mkdir(parents=True, exist_ok=True)
        path = _default_dir / f"exhaustive-{now.strftime('%Y%m%d')}.jsonl"
        with _lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        return entry
    except Exception:
        return None


# ---- emitters: one per kind, so a call site reads as the fact it records ----


def log_user_action(
    actor: str,
    action: str,
    *,
    channel: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """A person did something to the agent: what, when, and how."""
    record("user.action", actor=actor, action=action, channel=channel,
           detail=detail or {})


def log_settings_change(
    actor: str, what: str, *, before: Any = None, after: Any = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """A setting moved: which one, from what, to what, by whom, when."""
    record("settings.change", actor=actor, what=what, before=before,
           after=after, detail=detail or {})


def _messages_dump(request: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in getattr(request, "messages", []) or []:
        entry: dict[str, Any] = {
            "role": getattr(getattr(m, "role", None), "value", str(getattr(m, "role", ""))),
            "content": getattr(m, "content", None),
        }
        thinking = getattr(m, "thinking", None) or []
        if thinking:
            entry["thinking"] = [
                {"text": tb.text, "signature": tb.signature} for tb in thinking
            ]
        tool_calls = getattr(m, "tool_calls", None) or []
        if tool_calls:
            entry["tool_calls"] = [
                {"name": getattr(tc, "name", ""), "arguments": getattr(tc, "arguments", "")}
                for tc in tool_calls
            ]
        out.append(entry)
    return out


def log_model_request(
    request: Any, *, request_id: str, model: str, provider: str,
) -> str:
    """One ask to the model gateway, verbatim, with the moment it was made."""
    record(
        "model.request",
        request_id=request_id,
        purpose=getattr(request, "purpose", ""),
        tier=str(getattr(getattr(request, "tier", None), "value", "")),
        model=model,
        provider=provider,
        messages=_messages_dump(request),
        params={
            "max_tokens": getattr(request, "max_tokens", None),
            "temperature": getattr(request, "temperature", None),
            "thinking_budget": getattr(request, "thinking_budget", None),
            "reasoning_budget": getattr(request, "reasoning_budget", None),
            "cache_prefix_blocks": getattr(request, "cache_prefix_blocks", None),
            "provider_hint": getattr(request, "provider_hint", None),
            "model_hint": getattr(request, "model_hint", None),
            "thread_id": getattr(request, "thread_id", None),
            "intention_id": getattr(request, "intention_id", None),
            "budget_bucket": getattr(request, "budget_bucket", None),
            "consequence_class": getattr(request, "consequence_class", None),
            "task_class": getattr(request, "task_class", None),
            "idempotency_key": getattr(request, "idempotency_key", None),
            "tool_count": len(getattr(request, "tools", None) or []),
            "tool_names": [getattr(t, "name", "") for t in (getattr(request, "tools", None) or [])],
            "metadata": getattr(request, "metadata", None) or {},
        },
    )
    return request_id


def log_model_response(
    request: Any, response: Any, *, request_id: str, requested_at: datetime,
) -> None:
    """The answer, verbatim: model, moment, text, exposed reasoning, cost."""
    cot_blocks = getattr(response, "thinking_blocks", None) or []
    cot = [
        {"text": tb.text, "signature": tb.signature, "raw": tb.raw}
        for tb in cot_blocks
    ]
    usage = getattr(response, "usage", None)
    record(
        "model.response",
        request_id=request_id,
        purpose=getattr(request, "purpose", ""),
        tier=str(getattr(getattr(request, "tier", None), "value", "")),
        requested_at=requested_at.astimezone(UTC).isoformat(timespec="seconds"),
        model=getattr(response, "model", ""),
        provider=getattr(response, "provider", ""),
        latency_ms=getattr(response, "latency_ms", 0),
        text=getattr(response, "text", None),
        tool_calls=[
            {"name": getattr(tc, "name", ""), "arguments": getattr(tc, "arguments", "")}
            for tc in (getattr(response, "tool_calls", None) or [])
        ],
        # The reasoning the provider actually exposed, verbatim. An empty list
        # with reasoning_tokens > 0 means the model thought and the provider
        # kept it — the record says which of the two happened, so a silent
        # field is never read as "the model did not think".
        cot=cot,
        cot_exposed=bool(cot),
        cot_note=(
            "provider exposed reasoning traces (verbatim above)"
            if cot
            else "provider did not expose chain-of-thought for this response; "
                 "internal reasoning is not retrievable beyond these token counts"
        ),
        # Token counts as name/value pairs, not a dict: the redactor redacts
        # by key name as well as by value, and a key like `input_tokens` is
        # not a credential no matter how it is spelled.
        usage=[
            ["input_tokens", getattr(usage, "input_tokens", 0)],
            ["cached_input_tokens", getattr(usage, "cached_input_tokens", 0)],
            ["cache_write_tokens", getattr(usage, "cache_write_tokens", 0)],
            ["output_tokens", getattr(usage, "output_tokens", 0)],
            ["reasoning_tokens", getattr(usage, "reasoning_tokens", 0)],
            ["total_tokens", getattr(usage, "total_tokens", 0)],
        ] if usage is not None else [],
        finish_reason=getattr(response, "finish_reason", None),
        cost_usd=getattr(response, "cost_usd", 0.0),
        billed_cost_usd=getattr(response, "billed_cost_usd", None),
        cache_read=getattr(response, "cache_read", False),
        cache_creation=getattr(response, "cache_creation", False),
    )


def log_model_failure(
    request: Any, *, request_id: str, requested_at: datetime,
    model: str, provider: str, error: str, attempt: int | None = None,
) -> None:
    """An ask that did not produce an answer, and why."""
    record(
        "model.failure",
        request_id=request_id,
        purpose=getattr(request, "purpose", ""),
        tier=str(getattr(getattr(request, "tier", None), "value", "")),
        requested_at=requested_at.astimezone(UTC).isoformat(timespec="seconds"),
        model=model,
        provider=provider,
        attempt=attempt,
        error=str(error)[:2000],
    )
