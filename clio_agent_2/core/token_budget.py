"""
Token-budget estimation for Clio-Agent-2.

The previous context manager trimmed history with a rough ``max_chars``
heuristic (~1 token per 4 characters). That drifted badly across models and
often either starved the context window or overflowed it. This module provides
a *token-accurate* estimate using ``tiktoken`` (already a dependency), with a
safe character-based fallback when tiktoken is unavailable or the model name is
unknown.
"""


# Rough but safe fallback: ~1 token per 4 characters for English-ish text.
_FALLBACK_CHARS_PER_TOKEN = 4

# Model name -> tiktoken encoding. We deliberately do NOT call
# ``tiktoken.encoding_for_model`` for arbitrary vendor names (it can raise and
# even hit the network); we map to a stable public encoding instead.
_ENCODING_FOR_MODEL = {
    # OpenAI
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
    # Anthropic / Google / others -> use the closest general-purpose encoding.
    "claude": "cl100k_base",
    "gemini": "cl100k_base",
    "grok": "cl100k_base",
    "deepseek": "cl100k_base",
}

# Cache encoding objects so we don't re-instantiate them on every call.
_ENCODING_CACHE: dict = {}


def _resolve_encoding(model: str = "gpt-4"):
    """Return a tiktoken Encoding for *model*, or ``None`` if unavailable."""
    if model in _ENCODING_CACHE:
        return _ENCODING_CACHE[model]

    enc = None
    try:
        import tiktoken  # local import keeps the dependency optional

        name = _ENCODING_FOR_MODEL.get(model)
        if name is None:
            # Infer from a common prefix (e.g. "gpt-4o-2024-..." -> "gpt-4o").
            for key, enc_name in _ENCODING_FOR_MODEL.items():
                if model.startswith(key):
                    name = enc_name
                    break
        enc = tiktoken.get_encoding(name) if name else tiktoken.get_encoding("cl100k_base")
    except Exception:
        enc = None

    _ENCODING_CACHE[model] = enc
    return enc


def estimate_tokens(text: str, model: str = "gpt-4") -> int:
    """Estimate the number of tokens in *text* for the given model.

    Uses tiktoken when available, otherwise falls back to a character heuristic.
    Always returns a positive integer for non-empty input.
    """
    if not text:
        return 0

    enc = _resolve_encoding(model)
    if enc is None:
        return max(1, len(text) // _FALLBACK_CHARS_PER_TOKEN)

    try:
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // _FALLBACK_CHARS_PER_TOKEN)


def truncate_to_tokens(text: str, max_tokens: int, model: str = "gpt-4") -> str:
    """Truncate *text* so it fits within *max_tokens* tokens (best effort)."""
    if max_tokens <= 0 or not text:
        return ""
    enc = _resolve_encoding(model)
    if enc is None:
        # Fallback: crude character slice.
        return text[: max_tokens * _FALLBACK_CHARS_PER_TOKEN]

    try:
        ids = enc.encode(text)
        if len(ids) <= max_tokens:
            return text
        return enc.decode(ids[:max_tokens])
    except Exception:
        return text[: max_tokens * _FALLBACK_CHARS_PER_TOKEN]
