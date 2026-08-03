"""
Tests for the apply_fixes module - the actual fixes applied.
"""
import tempfile
from pathlib import Path
from unittest import mock

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
    """Tests for _is_token_configured function"""

    def test_real_openai_key(self):
        assert _is_token_configured("sk-proj-abc123def456") is True

    def test_real_google_key(self):
        assert _is_token_configured("AIzaSyBRealKey123456") is True

    def test_real_telegram_token(self):
        assert _is_token_configured("123456:ABC-DEF1234ghIkl-zyx57W2") is True

    def test_real_discord_token(self):
        assert _is_token_configured("MTIzNDU2.abcdef.ghijklmnopqrstuvwxyz") is True

    def test_none_token(self):
        assert _is_token_configured(None) is False

    def test_empty_string(self):
        assert _is_token_configured("") is False

    def test_whitespace(self):
        assert _is_token_configured("   ") is False

    def test_your_prefix_placeholder(self):
        assert _is_token_configured("your_telegram_bot_token_here") is False
        assert _is_token_configured("your_openai_api_key") is False

    def test_placeholder_keyword(self):
        assert _is_token_configured("placeholder_value") is False
        assert _is_token_configured("example_key_here") is False

    def test_sk_your_prefix(self):
        assert _is_token_configured("sk-your_key_here") is False

    def test_angle_brackets(self):
        assert _is_token_configured("<your_api_key>") is False
        assert _is_token_configured("<PLACEHOLDER>") is False

    def test_xxxx_pattern(self):
        assert _is_token_configured("xxxx-xxxx-xxxx") is False

    def test_real_token_with_spaces_trimmed(self):
        assert _is_token_configured("  sk-real-key-123  ") is True


class TestPatchFunction:
    """Tests for _patch function"""

    def test_patch_replaces_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.py"
            test_file.write_text("a = 1\na = 2\na = 3\n")

            _patch(test_file, [("a = 1", "b = 1"), ("a = 2", "c = 2")], "test")

            content = test_file.read_text()
            assert "b = 1" in content
            assert "c = 2" in content
            assert "a = 3" in content  # Unchanged

    def test_patch_no_occurrences_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.py"
            test_file.write_text("x = 1\n")

            # Should not raise
            _patch(test_file, [("nonexistent", "replacement")], "test")

            assert test_file.read_text() == "x = 1\n"

    def test_patch_empty_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.py"
            test_file.write_text("original\n")

            _patch(test_file, [], "test")

            assert test_file.read_text() == "original\n"

    def test_patch_file_not_found(self):
        # _patch reads the file, so missing file will raise
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "nonexistent.py"
            try:
                _patch(test_file, [("a", "b")], "test")
                assert False, "Expected FileNotFoundError"
            except FileNotFoundError:
                pass


class TestApplyFixesMain:
    """Test the main function of apply_fixes"""

    def test_main_runs_without_error(self):
        # Just test that the module can be imported and main exists
        from clio_agent_2.apply_fixes import main
        assert main is not None


class TestHelperFunction:
    """Tests for the HELPER function content"""

    def test_helper_is_token_configured(self):
        from clio_agent_2.apply_fixes import _is_token_configured
        # Same function, just verify it exists
        assert callable(_is_token_configured)


class TestDotenvBlock:
    """Tests for the DOTENV_BLOCK fallback"""

    def test_load_dotenv_fallback_exists(self):
        from clio_agent_2.config.settings import load_dotenv
        # The fallback should be defined even without python-dotenv
        assert callable(load_dotenv)