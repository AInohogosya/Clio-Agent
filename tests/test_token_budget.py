"""
Tests for the token_budget module.
Covers estimate_tokens, truncate_to_tokens, _resolve_encoding.
"""
from clio_agent_2.core.token_budget import (
    estimate_tokens,
    truncate_to_tokens,
    _resolve_encoding,
    _ENCODING_FOR_MODEL,
    _FALLBACK_CHARS_PER_TOKEN,
)


class TestEstimateTokens:
    """Tests for estimate_tokens"""

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_none_raises(self):
        result = estimate_tokens(None)
        assert result == 0

    def test_short_text_openai(self):
        result = estimate_tokens("Hello world", "gpt-4")
        assert result > 0
        assert result < 20  # "Hello world" should be ~2-3 tokens

    def test_long_text_openai(self):
        text = "This is a longer piece of text that should contain many tokens. " * 100
        result = estimate_tokens(text, "gpt-4")
        assert result > 500

    def test_gpt4o_encoding(self):
        text = "Hello world, this is a test"
        result = estimate_tokens(text, "gpt-4o")
        assert result > 0

    def test_gpt4o_mini_encoding(self):
        text = "Hello world, this is a test"
        result = estimate_tokens(text, "gpt-4o-mini")
        assert result > 0

    def test_fallback_encoding(self):
        """When tiktoken is unavailable, should fall back to char-based estimate"""
        result = estimate_tokens("Hello world", "unknown-model-xyz")
        # Fallback: len(text) // 4
        expected = max(1, len("Hello world") // _FALLBACK_CHARS_PER_TOKEN)
        assert result == expected

    def test_fallback_non_empty_minimum_one(self):
        """Non-empty text should return at least 1 token"""
        result = estimate_tokens("x", "unknown-model")
        assert result >= 1

    def test_encoding_cache(self):
        """_resolve_encoding should cache results"""
        result1 = _resolve_encoding("gpt-4")
        result2 = _resolve_encoding("gpt-4")
        assert result1 is result2  # Same cached object

    def test_encoding_prefix_match(self):
        """Model names that start with a known prefix should use matching encoding"""
        enc = _resolve_encoding("gpt-4o-2024-08-06")
        assert enc is not None

    def test_claude_model(self):
        result = estimate_tokens("Hello world", "claude-3-5-sonnet-20240620")
        assert result > 0


class TestTruncateToTokens:
    """Tests for truncate_to_tokens"""

    def test_empty_text(self):
        assert truncate_to_tokens("", 100) == ""

    def test_zero_max_tokens(self):
        assert truncate_to_tokens("Hello world", 0) == ""

    def test_negative_max_tokens(self):
        assert truncate_to_tokens("Hello world", -1) == ""

    def test_short_text_not_truncated(self):
        text = "Short text"
        result = truncate_to_tokens(text, 1000)
        assert result == text

    def test_long_text_truncated(self):
        text = "Hello world " * 1000
        result = truncate_to_tokens(text, 10)
        assert len(result) < len(text)

    def test_fallback_truncation(self):
        """Fallback path: char-based truncation"""
        text = "Hello world " * 1000
        from unittest import mock
        with mock.patch("clio_agent_2.core.token_budget._resolve_encoding", return_value=None):
            result = truncate_to_tokens(text, 10, model="unavailable")
            assert len(result) <= 10 * _FALLBACK_CHARS_PER_TOKEN

    def test_truncation_preserves_content(self):
        """Truncated text should start with the original prefix"""
        text = "The beginning stays the same and then there's a lot more text" * 10
        result = truncate_to_tokens(text, 20)
        assert result.startswith("The beginning stays the same")


class TestEncodingResolution:
    """Tests for _resolve_encoding"""

    def test_known_model(self):
        enc = _resolve_encoding("gpt-4")
        assert enc is not None

    def test_unknown_model_still_returns_fallback(self):
        enc = _resolve_encoding("unknown-model-12345")
        # Even unknown models fall back to cl100k_base rather than None
        assert enc is not None

    def test_none_model(self):
        enc = _resolve_encoding(None)
        assert enc is not None  # None is now treated as gpt-4

    def test_anthropic_prefix(self):
        enc = _resolve_encoding("claude-3-opus-20240229")
        assert enc is not None

    def test_gemini_prefix(self):
        enc = _resolve_encoding("gemini-1.5-pro")
        assert enc is not None

    def test_grok_prefix(self):
        enc = _resolve_encoding("grok-2-latest")
        assert enc is not None

    def test_deepseek_prefix(self):
        enc = _resolve_encoding("deepseek-chat")
        assert enc is not None


class TestEncodingModelMap:
    """Tests for _ENCODING_FOR_MODEL constant"""

    def test_openai_models_in_map(self):
        assert "gpt-3.5-turbo" in _ENCODING_FOR_MODEL
        assert "gpt-4" in _ENCODING_FOR_MODEL
        assert "gpt-4o" in _ENCODING_FOR_MODEL
        assert "gpt-4o-mini" in _ENCODING_FOR_MODEL

    def test_openai_models_encoding(self):
        assert _ENCODING_FOR_MODEL["gpt-4"] == "cl100k_base"
        assert _ENCODING_FOR_MODEL["gpt-4o"] == "o200k_base"
        assert _ENCODING_FOR_MODEL["gpt-4o-mini"] == "o200k_base"

    def test_anthropic_in_map(self):
        assert "claude" in _ENCODING_FOR_MODEL

    def test_google_in_map(self):
        assert "gemini" in _ENCODING_FOR_MODEL

    def test_grok_in_map(self):
        assert "grok" in _ENCODING_FOR_MODEL

    def test_deepseek_in_map(self):
        assert "deepseek" in _ENCODING_FOR_MODEL