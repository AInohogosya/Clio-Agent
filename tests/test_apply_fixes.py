"""
Tests for apply_fixes module.
Covers _is_token_configured and _patch function.
"""
import tempfile
from pathlib import Path
from clio_agent_2.apply_fixes import _patch


# The _is_token_configured function is embedded in a string in apply_fixes.py
# We'll define it here for testing
def _is_token_configured(token):
    """Return True only if `token` is a real, usable token."""
    if not token or not str(token).strip():
        return False
    stripped = str(token).strip()
    lowered = stripped.lower()
    if lowered.startswith("your_") or "placeholder" in lowered:
        return False
    if "<" in stripped or ">" in stripped:
        return False
    return True


class TestIsTokenConfigured:
    """Tests for _is_token_configured"""

    def test_real_token(self):
        assert _is_token_configured("sk-abc123def456") is True

    def test_google_token(self):
        assert _is_token_configured("AIzaSyRealKey123") is True

    def test_telegram_token(self):
        assert _is_token_configured("123456:ABC-DEF123") is True

    def test_none_token(self):
        assert _is_token_configured(None) is False

    def test_empty_string(self):
        assert _is_token_configured("") is False

    def test_whitespace_only(self):
        assert _is_token_configured("   ") is False

    def test_placeholder_your_prefix(self):
        assert _is_token_configured("your_telegram_bot_token_here") is False

    def test_placeholder_with_dash(self):
        assert _is_token_configured("your-token-here") is False

    def test_placeholder_keyword(self):
        assert _is_token_configured("placeholder_key") is False
        assert _is_token_configured("example_key") is False

    def test_placeholder_xxxx(self):
        assert _is_token_configured("xxxx-1234-5678") is False

    def test_angle_brackets(self):
        assert _is_token_configured("<api-key-here>") is False

    def test_placeholder_sk_your(self):
        assert _is_token_configured("sk-your-key-here") is False

    def test_real_key_with_spaces(self):
        assert _is_token_configured("  sk-realkey123  ") is True

    def test_real_key_with_angles_in_middle(self):
        # Edge case: key with angle brackets somewhere shouldn't be falsely positive
        assert _is_token_configured("ab<cd") is False


class TestPatchFunction:
    """Tests for _patch function"""

    def test_patch_replaces_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.txt"
            test_file.write_text("old content\n")

            _patch(test_file, [("old content", "new content")], "test")

            content = test_file.read_text()
            assert content == "new content\n"

    def test_patch_missing_pattern_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.txt"
            test_file.write_text("content\n")

            # Should warn but not crash
            _patch(test_file, [("nonexistent", "replacement")], "test")

            content = test_file.read_text()
            assert "nonexistent" not in content
            assert "content" in content

    def test_patch_multiple_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.txt"
            test_file.write_text("line1\nline2\nline3\n")

            _patch(test_file, [
                ("line1", "replaced1"),
                ("line3", "replaced3"),
            ], "test")

            content = test_file.read_text()
            assert "replaced1" in content
            assert "replaced3" in content
            assert "line2" in content

    def test_patch_no_changes_when_text_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.txt"
            original = "original content\n"
            test_file.write_text(original)

            # Empty replacements list - no changes
            _patch(test_file, [], "test")

            assert test_file.read_text() == original

    def test_patch_multiple_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.txt"
            test_file.write_text("foo foo foo\n")

            _patch(test_file, [("foo", "bar")], "test")

            assert test_file.read_text() == "bar bar bar\n"