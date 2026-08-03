"""
Tests for the Config YAML helper functions (_dump_yaml_value, _dump_yaml_scalar).
"""
from clio_agent_2.config.settings import _dump_yaml_value, _dump_yaml_scalar


class TestDumpYamlScalar:
    """Tests for _dump_yaml_scalar"""

    def test_none_value(self):
        assert _dump_yaml_scalar(None) == "null"

    def test_true_value(self):
        assert _dump_yaml_scalar(True) == "true"

    def test_false_value(self):
        assert _dump_yaml_scalar(False) == "false"

    def test_int_value(self):
        assert _dump_yaml_scalar(42) == "42"

    def test_float_value(self):
        assert _dump_yaml_scalar(3.14) == "3.14"

    def test_simple_string(self):
        assert _dump_yaml_scalar("hello") == "hello"

    def test_empty_string(self):
        assert _dump_yaml_scalar("") == '""'

    def test_string_that_looks_like_bool(self):
        assert _dump_yaml_scalar("true") == '"true"'
        assert _dump_yaml_scalar("false") == '"false"'

    def test_string_with_colon(self):
        result = _dump_yaml_scalar("value: with colon")
        assert "colon" in result

    def test_string_with_special_prefix(self):
        result = _dump_yaml_scalar("# comment")
        assert '"' in result

    def test_string_with_hash(self):
        result = _dump_yaml_scalar("value # comment")
        assert 'value # comment' in result


class TestDumpYamlValue:
    """Tests for _dump_yaml_value"""

    def test_dict_empty(self):
        assert _dump_yaml_value({}) == "{}"

    def test_list_empty(self):
        assert _dump_yaml_value([]) == "[]"

    def test_simple_dict(self):
        result = _dump_yaml_value({"key": "value"})
        assert "key: value" in result

    def test_nested_dict(self):
        result = _dump_yaml_value({"outer": {"inner": "value"}})
        assert "outer:" in result
        assert "inner: value" in result

    def test_simple_list(self):
        result = _dump_yaml_value(["item1", "item2"])
        assert "- item1" in result
        assert "- item2" in result

    def test_nested_list_in_dict(self):
        result = _dump_yaml_value({"items": ["a", "b"]})
        assert "items:" in result
        assert "- a" in result
        assert "- b" in result

    def test_dict_with_list_value(self):
        result = _dump_yaml_value({"key": ["v1", "v2"]})
        assert "key:" in result
        assert "- v1" in result

    def test_indentation(self):
        result = _dump_yaml_value({"outer": {"inner": "value"}}, indent=1)
        assert "outer:" in result
        assert "  inner: value" in result  # 2 spaces for indent=1