"""
Token-budget estimation for Clio-Agent-2.

The previous context manager trimmed history with a rough ``max_chars``
heuristic (~1 token per 4 characters). That drifted badly across models and
often either starved the context window or overflowed it. This module provides
a *token-accurate* estimate using ``tiktoken`` (already a dependency), with a
safe character-based fallback when tiktoken is unavailable or the model name is
unknown.
"""

import unicodedata

# Rough but safe fallback: ~1 token per 4 characters for English-ish text.
_FALLBACK_CHARS_PER_TOKEN = 4

# CJK and other wide scripts use far more tokens per character (typically 1-3
# tokens per char, compared to ~0.25 for Latin text). Using the naive
# len//4 heuristic for CJK text underestimates the token count by 4-12x,
# causing context-window overflow. We detect the dominant script family and
# adjust accordingly.
_CJK_BLOCK_START = 0x4E00
_CJK_BLOCK_END = 0x9FFF
_CJK_EXT_A_START = 0x3400
_CJK_EXT_A_END = 0x4DBF
_HIRA_START = 0x3040
_HIRA_END = 0x309F
_KATA_START = 0x30A0
_KATA_END = 0x30FF
_HANGUL_START = 0xAC00
_HANGUL_END = 0xD7AF


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    if _CJK_BLOCK_START <= cp <= _CJK_BLOCK_END:
        return True
    if _CJK_EXT_A_START <= cp <= _CJK_EXT_A_END:
        return True
    if _HIRA_START <= cp <= _HIRA_END:
        return True
    if _KATA_START <= cp <= _KATA_END:
        return True
    if _HANGUL_START <= cp <= _HANGUL_END:
        return True
    return False


def _char_token_weight(text: str) -> float:
    """Return the average *characters-per-token* multiplier for *text*.

    English-heavy text gets the standard 4.0; CJK-heavy text gets a lower
    divisor (as low as 1.0), producing a higher token count to prevent
    context overflow.
    """
    if not text:
        return _FALLBACK_CHARS_PER_TOKEN
    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    ratio = cjk_count / len(text)
    if ratio > 0.5:
        return 1.5
    if ratio > 0.25:
        return 2.5
    if ratio > 0.1:
        return 3.0
    return _FALLBACK_CHARS_PER_TOKEN

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

    if model is None:
        model = "gpt-4"

    enc = None
    try:
        import tiktoken

        name = _ENCODING_FOR_MODEL.get(model)
        if name is None:
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
        cpt = _char_token_weight(text)
        return max(1, int(len(text) / cpt))

    try:
        return len(enc.encode(text))
    except Exception:
        cpt = _char_token_weight(text)
        return max(1, int(len(text) / cpt))


def truncate_to_tokens(text: str, max_tokens: int, model: str = "gpt-4") -> str:
    """Truncate *text* so it fits within *max_tokens* tokens (best effort)."""
    if max_tokens <= 0 or not text:
        return ""
    enc = _resolve_encoding(model)
    if enc is None:
        # Fallback: character slice with CJK-aware weight.
        cpt = _char_token_weight(text)
        return text[: int(max_tokens * cpt)]

    try:
        ids = enc.encode(text)
        if len(ids) <= max_tokens:
            return text
        return enc.decode(ids[:max_tokens])
    except Exception:
        cpt = _char_token_weight(text)
        return text[: int(max_tokens * cpt)]
