"""
Tests for the meta_controller module.
Covers RepetitionDetector, extract_action_block, _coding_agent_prompt, run_meta, _build_context_blob.
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


class TestRepetitionDetector:
    """Tests for RepetitionDetector"""

    def test_defaults(self):
        detector = RepetitionDetector()
        assert detector.window == 6
        assert detector.threshold == 4
        assert len(detector._history) == 0

    def test_custom_params(self):
        detector = RepetitionDetector(window=10, threshold=5)
        assert detector.window == 10
        assert detector.threshold == 5

    def test_invalid_window(self):
        try:
            RepetitionDetector(window=0)
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_invalid_threshold(self):
        try:
            RepetitionDetector(threshold=0)
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_record_returns_signature(self):
        detector = RepetitionDetector()
        sig = detector.record("read_file", {"filepath": "test.txt"}, True)
        assert isinstance(sig, str)
        assert len(sig) == 16
        assert len(detector._history) == 1

    def test_record_same_signature_multiple_times(self):
        detector = RepetitionDetector(window=10, threshold=4)
        sig = detector.record("read_file", {"filepath": "test.txt"}, True)

        for _ in range(3):
            detector.record("read_file", {"filepath": "test.txt"}, True)

        assert detector.is_stuck() is True

    def test_not_stuck_below_threshold(self):
        detector = RepetitionDetector(threshold=4)
        for _ in range(3):
            detector.record("read_file", {"filepath": "test.txt"}, True)

        assert detector.is_stuck() is False

    def test_not_stuck_different_signatures(self):
        detector = RepetitionDetector(threshold=2)
        detector.record("read_file", {"filepath": "a.txt"}, True)
        detector.record("read_file", {"filepath": "b.txt"}, True)

        assert detector.is_stuck() is False

    def test_window_trims_history(self):
        detector = RepetitionDetector(window=3)
        for i in range(5):
            detector.record("tool", {"i": i}, True)

        assert len(detector._history) == 3

    def test_reset_clears_history(self):
        detector = RepetitionDetector()
        detector.record("read_file", {"filepath": "a.txt"}, True)
        detector.record("read_file", {"filepath": "a.txt"}, True)

        detector.reset()
        assert len(detector._history) == 0
        assert detector.is_stuck() is False

    def test_signature_with_list_args(self):
        """The old code crashed on unhashable list args; ensure it works now"""
        detector = RepetitionDetector()
        sig = detector.record("tool", {"items": [1, 2, 3]}, True)
        assert isinstance(sig, str)

    def test_signature_with_dict_args(self):
        detector = RepetitionDetector()
        sig = detector.record("tool", {"nested": {"a": 1}}, True)
        assert isinstance(sig, str)

    def test_signature_with_mixed_type_args(self):
        """Old code crashed when int and str values couldn't be sorted"""
        detector = RepetitionDetector()
        sig = detector.record("tool", {"a": 1, "b": "str", "c": True}, True)
        assert isinstance(sig, str)

    def test_signature_deterministic(self):
        """Same inputs should produce the same signature"""
        sig1 = RepetitionDetector._signature("tool", {"a": 1, "b": 2}, True)
        sig2 = RepetitionDetector._signature("tool", {"b": 2, "a": 1}, True)
        assert sig1 == sig2

    def test_signature_differs_on_tool(self):
        sig1 = RepetitionDetector._signature("tool_a", {"a": 1}, True)
        sig2 = RepetitionDetector._signature("tool_b", {"a": 1}, True)
        assert sig1 != sig2

    def test_signature_differs_on_args(self):
        sig1 = RepetitionDetector._signature("tool", {"a": 1}, True)
        sig2 = RepetitionDetector._signature("tool", {"a": 2}, True)
        assert sig1 != sig2

    def test_signature_differs_on_result(self):
        sig1 = RepetitionDetector._signature("tool", {"a": 1}, True)
        sig2 = RepetitionDetector._signature("tool", {"a": 1}, False)
        assert sig1 != sig2


class TestExtractActionBlock:
    """Tests for extract_action_block"""

    def test_extract_from_fenced_block(self):
        text = """
Here is my analysis:

```
ACTION
MODE: build
TOPIC: Fix bug in parser
REASON: The parser crashes on empty input
```

That's what I'll do.
"""
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: build" in result
        assert "TOPIC: Fix bug in parser" in result
        assert "REASON:" in result

    def test_extract_from_fenced_with_language(self):
        text = "Here is what I'll do:\n```python\nACTION\nMODE: research\nTOPIC: investigate\nREASON: unknown\n```\n"
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: research" in result

    def test_extract_from_unfenced(self):
        text = """
ACTION
MODE: bug_hunt
TOPIC: Fix memory leak
REASON: Memory usage grows over time

Some additional context here.
"""
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: bug_hunt" in result

    def test_extract_none_text(self):
        assert extract_action_block(None) == ""

    def test_extract_empty_text(self):
        assert extract_action_block("") == ""

    def test_extract_no_action_block(self):
        text = "Just some regular text without any action block."
        assert extract_action_block(text) == ""

    def test_extract_preserves_content(self):
        text = "```\nACTION\nMODE: build\nTOPIC: Add tests\nREASON: Need coverage\n```"
        result = extract_action_block(text)
        assert result == "ACTION\nMODE: build\nTOPIC: Add tests\nREASON: Need coverage"

    def test_extract_multiple_blocks_returns_first_with_action(self):
        text = """
```plain
Not an action
```

```
ACTION
MODE: build
TOPIC: Fix
REASON: test
```
"""
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: build" in result

    def test_extract_with_crlf_line_endings(self):
        text = "Here is an action\r\n\r\n```\r\nACTION\r\nMODE: build\r\nTOPIC: test\r\nREASON: because\r\n```\r\n"
        result = extract_action_block(text)
        assert "ACTION" in result
        assert "MODE: build" in result


class TestCodingAgentPrompt:
    """Tests for _coding_agent_prompt"""

    def test_includes_failure_detail(self):
        prompt = _coding_agent_prompt("Something went wrong")
        assert "meta_controller.py" in prompt
        assert "Something went wrong" in prompt

    def test_mentions_fix_instructions(self):
        prompt = _coding_agent_prompt("error")
        assert "fix it" in prompt.lower() or "fix" in prompt.lower()


class TestBuildContextBlob:
    """Tests for _build_context_blob"""

    def test_with_entries_and_recommendations(self):
        blob = _build_context_blob(["entry1", "entry2"], ["rec1"])
        assert "Recent entries" in blob
        assert "1. entry1" in blob
        assert "2. entry2" in blob
        assert "Recent recommendations" in blob
        assert "1. rec1" in blob

    def test_empty_entries(self):
        blob = _build_context_blob([], [])
        assert "Recent entries" in blob
        assert "(none)" in blob
        assert "Recent recommendations" in blob

    def test_with_object_entries(self):
        class FakeEntry:
            def __str__(self):
                return "FakeEntry content"

        blob = _build_context_blob([FakeEntry()], [])
        assert "FakeEntry content" in blob


class TestRunMeta:
    """Tests for run_meta"""

    def test_run_meta_returns_action_block(self):
        async def fake_chat(messages):
            return "```\nACTION\nMODE: build\nTOPIC: test\nREASON: testing\n```"

        llm_router = mock.MagicMock()
        llm_router.chat = fake_chat

        result = _run(run_meta(llm_router, ["entry1"], ["rec1"]))
        assert "ACTION" in result
        assert "MODE: build" in result

    def test_run_meta_no_action_block_raises(self):
        async def fake_chat(messages):
            return "No action here"

        llm_router = mock.MagicMock()
        llm_router.chat = fake_chat

        import pytest
        with pytest.raises(RuntimeError) as exc_info:
            _run(run_meta(llm_router, [], []))
        assert "No parseable ACTION block" in str(exc_info.value)

    def test_run_meta_chat_exception_raises(self):
        llm_router = mock.MagicMock()

        async def failing_chat(messages):
            raise ConnectionError("Network error")

        llm_router.chat = failing_chat

        import pytest
        with pytest.raises(RuntimeError) as exc_info:
            _run(run_meta(llm_router, [], []))
        assert "llm_router.chat failed" in str(exc_info.value)
        assert "Network error" in str(exc_info.value)

    def test_run_meta_passes_system_prompt(self):
        captured = []

        async def fake_chat(messages):
            captured.extend(messages)
            return "```\nACTION\nMODE: research\nTOPIC: test\nREASON: why\n```"

        llm_router = mock.MagicMock()
        llm_router.chat = fake_chat

        _run(run_meta(llm_router, ["entry1"], ["rec1"]))

        assert len(captured) == 2
        assert captured[0]["role"] == "system"
        assert META_SYSTEM_PROMPT in captured[0]["content"]
        assert captured[1]["role"] == "user"


class TestMetaSystemPrompt:
    """Tests for META_SYSTEM_PROMPT"""

    def test_prompt_contains_modes(self):
        assert "bug_hunt" in META_SYSTEM_PROMPT
        assert "build" in META_SYSTEM_PROMPT
        assert "research" in META_SYSTEM_PROMPT

    def test_prompt_has_action_format(self):
        assert "ACTION" in META_SYSTEM_PROMPT
        assert "MODE:" in META_SYSTEM_PROMPT
        assert "TOPIC:" in META_SYSTEM_PROMPT
        assert "REASON:" in META_SYSTEM_PROMPT