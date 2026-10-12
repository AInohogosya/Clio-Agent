"""Small pure helpers that more than one module was carrying its own copy of."""
from __future__ import annotations

from typing import Any


def clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def clip_value(value: Any, lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
    """`clip`, for a value the model may have handed over as "0.7", 2, or junk."""
    try:
        return clip(float(value), lo, hi)
    except (TypeError, ValueError):
        return default


def clip_text(text: Any, limit: int) -> str:
    """Trim to `limit` characters on one whole-ellipsis, never mid-grapheme."""
    text = str(text)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def one_line(text: Any) -> str:
    """Collapse every run of whitespace, so two renderings can be compared."""
    return " ".join(str(text).split())
