"""
Tests for ToolResult class.
"""
from clio_agent_2.tools.tool_registry import ToolResult


class TestToolResult:
    """Tests for the ToolResult class"""

    def test_success_result(self):
        result = ToolResult(success=True, output="Some output")
        assert result.success is True
        assert result.output == "Some output"
        assert result.error is None

    def test_failure_result(self):
        result = ToolResult(success=False, output="", error="Something went wrong")
        assert result.success is False
        assert result.error == "Something went wrong"

    def test_result_with_output_and_error(self):
        result = ToolResult(success=False, output="partial output", error="error")
        assert result.success is False
        assert result.output == "partial output"
        assert result.error == "error"

    def test_to_dict_success(self):
        result = ToolResult(success=True, output="data", error=None)
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "data"
        assert d["error"] is None

    def test_to_dict_failure(self):
        result = ToolResult(success=False, output="", error="error msg")
        d = result.to_dict()
        assert d["success"] is False
        assert d["output"] == ""
        assert d["error"] == "error msg"

    def test_to_dict_with_both_output_and_error(self):
        result = ToolResult(success=False, output="partial", error="err")
        d = result.to_dict()
        assert d["success"] is False
        assert d["output"] == "partial"
        assert d["error"] == "err"

    def test_result_with_empty_error(self):
        result = ToolResult(success=True, output="ok", error="")
        assert result.error == ""

    def test_result_with_multiline_output(self):
        output = "Line 1\nLine 2\nLine 3"
        result = ToolResult(success=True, output=output)
        assert result.output == output
        d = result.to_dict()
        assert d["output"] == output


class TestToolResultConstruction:
    """Tests for various ToolResult construction patterns"""

    def test_all_positional_args(self):
        result = ToolResult(True, "output", "error")
        assert result.success is True
        assert result.output == "output"
        assert result.error == "error"

    def test_posargs_without_error(self):
        result = ToolResult(True, "output")
        assert result.success is True
        assert result.output == "output"
        assert result.error is None

    def test_error_only_posargs(self):
        result = ToolResult(False, "", "Error occurred")
        assert result.success is False
        assert result.output == ""
        assert result.error == "Error occurred"