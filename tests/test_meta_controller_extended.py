"""
Tests for the meta_controller additional edge cases.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.meta_controller import (
    RepetitionDetector,
    META_SYSTEM_PROMPT,
    extract_action_block,
    _coding_agent_prompt,
    _build_context_blob,
    run_meta,
)


def _run(coro):
    return asyncio.run(coro)


class TestRepetitionDetectorAdditional:
    """Additional tests for RepetitionDetector"""

    def test_signature_with_none_args(self):
        detector = RepetitionDetector()
        sig = detector.record("tool", None, True)
        assert isinstance(sig, str)

    def test_signature_with_empty_dict(self):
        detector = RepetitionDetector()
        sig1 = detector.record("tool", {}, True)
        sig2 = detector.record("tool", {}, True)
        assert sig1 == sig2

    def test_is_stuck_with_exact_threshold(self):
        detector = RepetitionDetector(threshold=3)
        for _ in range(3):
            detector.record("tool", {"a": 1}, True)
        assert detector.is_stuck() is True

    def test_is_stuck_with_more_than_threshold(self):
        detector = RepetitionDetector(threshold=3)
        for _ in range(5):
            detector.record("tool", {"a": 1}, True)
        assert detector.is_stuck() is True

    def test_window_sliding(self):
        detector = RepetitionDetector(window=3, threshold=2)
        # First two
        detector.record("tool", {"i": 1}, True)
        detector.record("tool", {"i": 1}, True)
        assert detector.is_stuck() is True

        # Add third different
        detector.record("tool", {"i": 2}, True)
        assert detector.is_stuck() is False  # Now only 1 of the last 3 matches

        # Add another matching
        detector.record("tool", {"i": 2}, True)
        assert detector.is_stuck() is True  # Last 3: i=1, i=2, i=2 -> 2 matches

    def test_reset_works(self):
        detector = RepetitionDetector()
        detector.record("tool", {"a": 1}, True)
        detector.record("tool", {"a": 1}, True)
        assert detector.is_stuck() is True

        detector.reset()
        assert detector.is_stuck() is False
        assert len(detector._history) == 0


class TestExtractActionBlockAdditional:
    """Additional tests for extract_action_block"""

    def test_fenced_block_with_language_specifier(self):
        text = '''```python
ACTION
MODE: build
TOPIC: test
REASON: testing
```'''
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: build" in result

    def test_fenced_block_case_insensitive(self):
        text = '''```ACTION
MODE: research
TOPIC: test
REASON: testing
```'''
        result = extract_action_block(text)
        assert "ACTION" in result

    def test_unfenced_block_stops_at_blank_line(self):
        text = """ACTION
MODE: bug_hunt
TOPIC: fix leak
REASON: memory growing

Some other text here."""
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: bug_hunt" in result
        assert "Some other text" not in result

    def test_unfenced_block_stops_at_second_action(self):
        text = """ACTION
MODE: build
TOPIC: first
REASON: first reason
ACTION
MODE: research
TOPIC: second
REASON: second reason"""
        result = extract_action_block(text)
        assert "first" in result
        assert "second" not in result

    def test_fenced_block_preferred_over_unfenced(self):
        text = """ACTION
MODE: unfenced
TOPIC: unfenced
REASON: first

```
ACTION
MODE: fenced
TOPIC: fenced
REASON: second
```"""
        result = extract_action_block(text)
        assert "fenced" in result
        assert "unfenced" not in result

    def test_no_action_in_fenced_falls_back(self):
        text = """```
not an action block
```

ACTION
MODE: unfenced
TOPIC: real
REASON: this one"""
        result = extract_action_block(text)
        assert "unfenced" in result
        assert "real" in result

    def test_whitespace_handling(self):
        text = """   ACTION
  MODE: build
  TOPIC: test
  REASON: reason  """
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: build" in result


class TestCodingAgentPrompt:
    """Tests for _coding_agent_prompt"""

    def test_contains_file_reference(self):
        prompt = _coding_agent_prompt("error detail")
        assert "meta_controller.py" in prompt

    def test_contains_error_detail(self):
        prompt = _coding_agent_prompt("specific error message")
        assert "specific error message" in prompt

    def test_contains_fix_instructions(self):
        prompt = _coding_agent_prompt("error")
        assert "fix" in prompt.lower()


class TestBuildContextBlobAdditional:
    """Additional tests for _build_context_blob"""

    def test_many_entries(self):
        entries = [f"entry{i}" for i in range(100)]
        recs = [f"rec{i}" for i in range(50)]
        blob = _build_context_blob(entries, recs)
        assert "1. entry0" in blob
        assert "100. entry99" in blob
        assert "1. rec0" in blob
        assert "50. rec49" in blob

    def test_unicode_entries(self):
        blob = _build_context_blob(["日本語", "中文"], ["рекомендация"])
        assert "日本語" in blob
        assert "中文" in blob
        assert "рекомендация" in blob

    def test_mixed_type_entries(self):
        class FakeObj:
            def __str__(self):
                return "FakeObject"

        blob = _build_context_blob([FakeObj(), "string", 42], [])
        assert "FakeObject" in blob
        assert "string" in blob
        assert "42" in blob


class TestRunMetaAdditional:
    """Additional tests for run_meta"""

    def test_run_meta_converts_non_string_response(self):
        async def fake_chat(messages):
            return 123  # Non-string response

        llm_router = mock.MagicMock()
        llm_router.chat = fake_chat

        result = _run(run_meta(llm_router, [], []))
        assert "ACTION" in result  # Should still extract action from stringified response

    def test_run_meta_llm_router_chat_exception(self):
        async def failing_chat(messages):
            raise ConnectionError("Network down")

        llm_router = mock.MagicMock()
        llm_router.chat = failing_chat

        try:
            _run(run_meta(llm_router, [], []))
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "llm_router.chat failed" in str(e)
            assert "Network down" in str(e)

    def test_run_meta_empty_response(self):
        async def fake_chat(messages):
            return ""

        llm_router = mock.MagicMock()
        llm_router.chat = fake_chat

        try:
            _run(run_meta(llm_router, [], []))
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "No parseable ACTION block" in str(e)

    def test_run_meta_only_action_keyword(self):
        async def fake_chat(messages):
            return "ACTION"

        llm_router = mock.MagicMock()
        llm_router.chat = fake_chat

        try:
            _run(run_meta(llm_router, [], []))
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass  # Missing MODE: should fail