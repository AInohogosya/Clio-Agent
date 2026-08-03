"""
Tests for ClioAgent._parse_tool_calls and _extract_json_objects.
Extended test coverage for JSON parsing edge cases.
"""
import asyncio
import json
from unittest import mock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import ToolResult


def _run(coro):
    return asyncio.run(coro)


def _make_agent():
    agent = mock.MagicMock(spec=ClioAgent)
    agent._extract_json_objects = ClioAgent._extract_json_objects
    agent._is_valid_tool_call = ClioAgent._is_valid_tool_call
    agent._parse_tool_calls = ClioAgent._parse_tool_calls.__get__(agent, ClioAgent)
    return agent


class TestExtractJsonObjects:
    """Tests for _extract_json_objects static method"""

    def test_simple_object(self):
        agent = _make_agent()
        result = agent._extract_json_objects('{"key": "value"}')
        assert len(result) == 1
        assert result[0] == '{"key": "value"}'

    def test_multiple_objects(self):
        agent = _make_agent()
        text = '{"a": 1}\n{"b": 2}'
        result = agent._extract_json_objects(text)
        assert len(result) == 2
        assert '{"a": 1}' in result
        assert '{"b": 2}' in result

    def test_nested_objects(self):
        agent = _make_agent()
        text = '{"outer": {"inner": "value"}}'
        result = agent._extract_json_objects(text)
        assert len(result) == 1
        assert result[0] == '{"outer": {"inner": "value"}}'

    def test_objects_in_prose(self):
        agent = _make_agent()
        text = 'Some text before {"key": "value"} and text after'
        result = agent._extract_json_objects(text)
        assert len(result) == 1
        assert result[0] == '{"key": "value"}'

    def test_empty_string(self):
        agent = _make_agent()
        result = agent._extract_json_objects("")
        assert result == []

    def test_no_json_objects(self):
        agent = _make_agent()
        result = agent._extract_json_objects("This text has no JSON objects")
        assert result == []

    def test_braces_in_strings(self):
        agent = _make_agent()
        text = '{"path": "file}name.txt"}'
        result = agent._extract_json_objects(text)
        assert len(result) == 1

    def test_escaped_quotes_in_string(self):
        agent = _make_agent()
        text = '{"message": "say \\"hello\\""}'
        result = agent._extract_json_objects(text)
        assert len(result) == 1
        parsed = json.loads(result[0])
        assert parsed["message"] == 'say "hello"'

    def test_unterminated_string(self):
        """Unterminated strings should not cause infinite loop"""
        agent = _make_agent()
        text = '{"key": "value'
        result = agent._extract_json_objects(text)
        assert result == []

    def test_object_after_partial_brace(self):
        agent = _make_agent()
        text = 'notjson{"valid": "object"}'
        result = agent._extract_json_objects(text)
        assert len(result) == 1


class TestIsValidToolCall:
    """Tests for _is_valid_tool_call static method"""

    def test_valid_tool_call(self):
        agent = _make_agent()
        obj = {"tool": "read_file", "arguments": {"filepath": "test.txt"}}
        assert agent._is_valid_tool_call(obj) is True

    def test_valid_tool_call_empty_args(self):
        agent = _make_agent()
        obj = {"tool": "say", "arguments": {}}
        assert agent._is_valid_tool_call(obj) is True

    def test_invalid_missing_tool(self):
        agent = _make_agent()
        obj = {"arguments": {"filepath": "test.txt"}}
        assert agent._is_valid_tool_call(obj) is False

    def test_invalid_missing_arguments(self):
        agent = _make_agent()
        obj = {"tool": "read_file"}
        assert agent._is_valid_tool_call(obj) is False

    def test_invalid_arguments_not_dict(self):
        agent = _make_agent()
        obj = {"tool": "read_file", "arguments": "not a dict"}
        assert agent._is_valid_tool_call(obj) is False

    def test_invalid_not_dict(self):
        agent = _make_agent()
        assert agent._is_valid_tool_call("string") is False
        assert agent._is_valid_tool_call(123) is False
        assert agent._is_valid_tool_call(None) is False
        assert agent._is_valid_tool_call([]) is False


class TestParseToolCallsExtended:
    """Extended tests for _parse_tool_calls"""

    def test_single_tool_call(self):
        agent = _make_agent()
        result = agent._parse_tool_calls('{"tool": "read_file", "arguments": {"filepath": "/tmp/x.txt"}}')
        assert len(result) == 1

    def test_array_of_tool_calls(self):
        agent = _make_agent()
        result = agent._parse_tool_calls(
            '[{"tool": "read_file", "arguments": {"a": "1"}}, {"tool": "write_file", "arguments": {"b": "2"}}]'
        )
        assert len(result) == 2

    def test_multiple_separate_objects_in_prose(self):
        agent = _make_agent()
        result = agent._parse_tool_calls(
            "I'll do two things:\n"
            '{"tool": "read_file", "arguments": {"filepath": "/tmp/a.txt"}}\n'
            "and then:\n"
            '{"tool": "list_directory", "arguments": {"directory": "/tmp"}}'
        )
        assert len(result) == 2

    def test_no_tool_call_returns_empty(self):
        agent = _make_agent()
        result = agent._parse_tool_calls("Just a normal answer")
        assert result == []

    def test_empty_response(self):
        agent = _make_agent()
        result = agent._parse_tool_calls("")
        assert result == []

    def test_whitespace_response(self):
        agent = _make_agent()
        result = agent._parse_tool_calls("   \n\t  ")
        assert result == []

    def test_none_response(self):
        agent = _make_agent()
        result = agent._parse_tool_calls(None)
        assert result == []

    def test_array_with_invalid_element(self):
        agent = _make_agent()
        result = agent._parse_tool_calls(
            '[{"tool": "read_file", "arguments": {"filepath": "a"}}, {"invalid": "no_tool"}]'
        )
        assert len(result) == 1

    def test_mixed_prose_and_array(self):
        """If the whole response parses as an array, use that"""
        agent = _make_agent()
        result = agent._parse_tool_calls(
            '[{"tool": "read_file", "arguments": {"a": "1"}}]\nextra text'
        )
        assert len(result) == 1

    def test_json_array_with_invalid_objects(self):
        """Array containing non-tool-call objects should be filtered"""
        agent = _make_agent()
        result = agent._parse_tool_calls(
            '[{"tool": "read_file", "arguments": {}}, "not a dict", {"no_tool": "test"}, 42]'
        )
        assert len(result) == 1

    def test_braces_inside_string_values(self):
        agent = _make_agent()
        result = agent._parse_tool_calls('{"tool": "read_file", "arguments": {"filepath": "a{b}c.txt"}}')
        assert len(result) == 1
        assert result[0]["arguments"]["filepath"] == "a{b}c.txt"

    def test_empty_arguments_dict(self):
        agent = _make_agent()
        result = agent._parse_tool_calls('{"tool": "say", "arguments": {}}')
        assert len(result) == 1
        assert result[0]["tool"] == "say"

    def test_json_parse_failure_falls_through(self):
        """If json.loads fails, should fall back to _extract_json_objects"""
        agent = _make_agent()
        result = agent._parse_tool_calls('Some text {"tool": "say", "arguments": {"message": "hi"}} more text')
        assert len(result) == 1