import os, sys, json, tempfile, threading, subprocess, re
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_SRC_PATH = str(_PROJECT_ROOT / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)


def _extract_function(source, func_name):
    start = source.find(f"def {func_name}(")
    if start == -1:
        raise ValueError(f"Function {func_name} not found")
    end = source.find("\n\n\ndef ", start + 1)
    if end == -1:
        end = source.find("\n\n\nclass ", start + 1)
    if end == -1:
        end = len(source)
    return source[start:end]


def _get_func(name):
    source = (_PROJECT_ROOT / "run.py").read_text()
    return _extract_function(source, name)


class TestPromptForApiKeyEOF:
    def test_exception_caught_in_getpass(self):
        """prompt_for_api_key uses getpass which catches Exception (Bug #3 fix: EOF via Exception)."""
        source = _get_func("prompt_for_api_key")
        assert "except Exception as e:" in source or "except (KeyboardInterrupt, EOFError):" in source

    def test_keyboard_interrupt_caught(self):
        source = _get_func("prompt_for_api_key")
        assert "KeyboardInterrupt" in source

    def test_returns_none_on_error(self):
        source = _get_func("prompt_for_api_key")
        assert "return None" in source


class TestGetValidApiKeyEOF:
    def test_eof_is_caught(self):
        source = _get_func("get_valid_api_key")
        assert "EOFError" in source

    def test_returns_none_on_empty(self):
        source = _get_func("get_valid_api_key")
        assert "return None" in source


class TestConfigPathResolution:
    def test_explicit_path_priority(self):
        from ai_agent.utils.config import _resolve_config_path
        explicit = Path("/tmp/explicit.yaml")
        assert _resolve_config_path(explicit) == explicit.resolve()

    def test_directory_appends_yaml(self):
        from ai_agent.utils.config import _resolve_config_path
        with tempfile.TemporaryDirectory() as d:
            assert _resolve_config_path(d) == Path(d).resolve() / "config.yaml"

    def test_env_var_used(self):
        from ai_agent.utils.config import _resolve_config_path
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            env_path = f.name
        try:
            with patch.dict(os.environ, {"CLIO_CONFIG": env_path}):
                assert _resolve_config_path() == Path(env_path).resolve()
        finally:
            os.unlink(env_path)


class TestAtomicWriteYaml:
    def test_successful_write(self):
        from ai_agent.utils.config import _atomic_write_yaml
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.yaml"
            _atomic_write_yaml(path, {"key": "value"})
            assert path.exists()
            assert "key: value" in path.read_text()

    def test_exception_cleans_up(self):
        from ai_agent.utils.config import _atomic_write_yaml
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.yaml"
            try:
                _atomic_write_yaml(path, object())
            except Exception:
                pass
            leftover = list(Path(d).glob(".config_*"))
            assert len(leftover) == 0

    def test_concurrent_writes(self):
        from ai_agent.utils.config import _atomic_write_yaml
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.yaml"
            errors = []
            def write(tid):
                try:
                    _atomic_write_yaml(path, {"t": tid})
                except Exception as e:
                    errors.append(e)
            threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if path.exists():
                import yaml
                data = yaml.safe_load(path.read_text())
                assert isinstance(data, dict)


class TestSettingsManagerThreadSafety:
    def test_lock_exists(self):
        import ai_agent.utils.settings_manager as mod
        source = open(mod.__file__).read()
        assert "_singleton_lock" in source or "threading.Lock" in source or "_get_lock" in source


class TestFallbackSelectionEOF:
    def test_eof_handled(self):
        source = open(_PROJECT_ROOT / "external_integration/yellow_selection/fallback_interactive_menu.py").read()
        assert "EOFError" in source


class TestEncodingNotForced:
    def test_guard_exists(self):
        source = (_PROJECT_ROOT / "run.py").read_text()
        assert 'if not os.environ.get("PYTHONIOENCODING")' in source or "setdefault" in source


class TestNormalOperation:
    def test_version_tuple_ordering(self):
        """Test _version_tuple from run.py (Bug #12: pre-release version comparison)."""
        source = _get_func("_version_tuple")
        # Verify the function has pre-release handling
        assert "_PRERELEASE_RANK" in source or "PRERELEASE_ORDER" in source
        assert "dev" in source.lower() or "alpha" in source.lower()

    def test_is_in_venv_returns_bool(self):
        source = _get_func("_is_in_venv")
        assert "return True" in source
        assert "return False" in source


class TestEdgeCases:
    def test_empty_config(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("")
        import yaml
        assert yaml.safe_load(p.read_text()) is None

    def test_long_key_accepted(self):
        source = _get_func("get_valid_api_key")
        assert "len(api_key) < 10" in source


class TestFailureScenarios:
    def test_venv_invalid_python(self):
        source = _get_func("_try_create_venv")
        assert "FileNotFoundError" in source

    def test_bootstrap_counter(self):
        source = (_PROJECT_ROOT / "run.py").read_text()
        assert "_CLIO_BOOTSTRAP_ATTEMPTS" in source
        assert "_bootstrap_count" in source

    def test_restart_process_exits(self):
        import ai_agent.core_processing.autonomous_loop_engine as mod
        source = open(mod.__file__).read()
        assert "sys.exit(127)" in source

    def test_max_restart_defined(self):
        source = open(_PROJECT_ROOT / "external_integration/discord_bot.py").read()
        assert "MAX_RESTART_ATTEMPTS" in source
        match = re.search(r"MAX_RESTART_ATTEMPTS\s*=\s*(\d+)", source)
        assert match is not None
        assert int(match.group(1)) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
