"""One place for the "the model answered, but in prose around a JSON object" problem.

Every caller in this system asks a model for JSON and receives text — a preamble,
a code fence, an apology, the object, sometimes a trailing remark. Slicing the
first `{` to the last `}` and parsing that is the shared answer, and it lives
here so the eight places that used to carry their own copy cannot drift apart
again: a parser that grows a repair for one provider's habit should grow it for
all of them.
"""
from __future__ import annotations

import json
import re
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def parse_json_block(text: str) -> dict[str, Any]:
    """The last JSON object in `text`, or `{}` when there is not one.

    Empty for empty, `{}` for unparseable, and `{}` for a value that parsed but
    is not an object — every caller reads the result with `.get(...)`, so a
    stray list or number from the model must not reach them as anything else.
    A trailing comma, the one syntax slip models actually make, is repaired
    before giving up.
    """
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    block = text[start:end + 1]
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        try:
            data = json.loads(_TRAILING_COMMA_RE.sub(r"\1", block))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}
