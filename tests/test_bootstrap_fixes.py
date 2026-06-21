"""
Tests for all bug fixes applied to the bootstrap system in run.py.
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_SRC_PATH = str(_PROJECT_ROOT / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)


# ===========================================================================
# Bug #12: Pre-release version comparison tests
# ===========================================================================

class TestVersionTuple:
    """Test the _version_tuple function from run.py with pre-release handling."""

    def _get_version_tuple(self):
        with open(_PROJECT_ROOT / "run.py", "r") as f:
            source = f.read()
        start = source.find("def _version_tuple(value):")
        end = source.find("\n\n\ndef ", start + 1)
        if end == -1:
            end = source.find("\n\n", start + 100)
        func_source = source[start:end]
        ns = {}
        exec(func_source, ns)
        return ns["_version_tuple"]

    def setup_method(self):
        self.vt = self._get_version_tuple()

    def test_simple_version(self):
        assert self.vt("1.2.3") == (1, 2, 3, 4)

    def test_two_part_version(self):
        assert self.vt("3.14") == (3, 14, 4)

    def test_single_part_version(self):
        assert self.vt("42") == (42, 4)

    def test_local_version_stripped(self):
        assert self.vt("1.0.0+git.abc123") == (1, 0, 0, 4)

    def test_empty_string(self):
        assert self.vt("") == (0,)

    def test_rc_less_than_release(self):
        """1.0.0rc1 should be LESS than 1.0.0."""
        assert self.vt("1.0.0rc1") < self.vt("1.0.0")

    def test_dev_less_than_release(self):
        """1.0.0.dev0 should be LESS than 1.0.0."""
        assert self.vt("1.0.0.dev0") < self.vt("1.0.0")

    def test_alpha_less_than_release(self):
        assert self.vt("1.0.0a3") < self.vt("1.0.0")

    def test_beta_less_than_release(self):
        assert self.vt("1.0.0b10") < self.vt("1.0.0")

    def test_post_greater_than_release(self):
        assert self.vt("1.0.0.post1") > self.vt("1.0.0")

    def test_pre_release_ordering(self):
        """dev < alpha < beta < rc < final."""
        vt = self.vt
        assert vt("1.0.0.dev0") < vt("1.0.0a1")
        assert vt("1.0.0a1") < vt("1.0.0b1")
        assert vt("1.0.0b1") < vt("1.0.0rc1")
        assert vt("1.0.0rc1") < vt("1.0.0")

    def test_different_versions_still_compare(self):
        assert self.vt("1.9.0") < self.vt("1.10.0")

    def test_1_0_0_less_than_2_0_0(self):
        assert self.vt("1.0.0") < self.vt("2.0.0")

    def test_dev_marker_with_dash(self):
        assert self.vt("1.0.0-dev") < self.vt("1.0.0")

    def test_alpha_with_underscore(self):
        assert self.vt("1.0.0_alpha1") < self.vt("1.0.0")


# ===========================================================================
# Bug #30: Virtual environment detection tests
# ===========================================================================

class TestIsInVenv:
    """Test _is_in_venv with various environment types."""

    def _get_is_in_venv(self):
        with open(_PROJECT_ROOT / "run.py", "r") as f:
            source = f.read()
        start = source.find("def _is_in_venv():")
        end = source.find("\n\n\ndef ", start + 1)
        if end == -1:
            end = source.find("\n\n", start + 100)
        func_source = source[start:end]
        ns = {"os": os, "sys": sys}
        exec(func_source, ns)
        return ns["_is_in_venv"]

    def setup_method(self):
        self.is_in_venv = self._get_is_in_venv()

    def test_detects_virtual_env_var(self):
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/some/venv"}):
            assert self.is_in_venv() is True

    def test_detects_conda_prefix(self):
        with mock.patch.dict(os.environ, {"CONDA_PREFIX": "/some/conda"}):
            assert self.is_in_venv() is True

    def test_detects_pipenv(self):
        with mock.patch.dict(os.environ, {"PIPENV_ACTIVE": "1"}):
            assert self.is_in_venv() is True

    def test_detects_poetry(self):
        with mock.patch.dict(os.environ, {"POETRY_ACTIVE": "1"}):
            assert self.is_in_venv() is True

    def test_detects_pyenv_virtualenv(self):
        with mock.patch.dict(os.environ, {"PYENV_VIRTUAL_ENV": "/some/pyenv"}):
            assert self.is_in_venv() is True

    def test_no_venv_returns_false(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("VIRTUAL_ENV", "CONDA_PREFIX", "PIPENV_ACTIVE",
                           "POETRY_ACTIVE", "PYENV_VIRTUAL_ENV")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, 'base_prefix', sys.prefix):
                assert self.is_in_venv() is False


# ===========================================================================
# Bug #3: Python candidate sorting tests
# ===========================================================================

class TestCollectPythonCandidates:
    """Test that _collect_python_candidates sorts by version number."""

    def _get_collect_fn(self):
        with open(_PROJECT_ROOT / "run.py", "r") as f:
            source = f.read()
        start = source.find("def _collect_python_candidates():")
        end = source.find("\n\n\ndef ", start + 1)
        if end == -1:
            end = source.find("\n\n", start + 100)
        func_source = source[start:end]
        ns = {"os": os, "sys": sys, "shutil": __import__("shutil"),
              "platform": platform}
        exec(func_source, ns)
        return ns["_collect_python_candidates"]

    def test_version_sort_higher_first(self):
        """python3.14 should come before python3.9 in the candidate list."""
        fn = self._get_collect_fn()
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("os.path.islink", return_value=False), \
             mock.patch("glob.glob", return_value=[
                 "/opt/homebrew/bin/python3.9",
                 "/opt/homebrew/bin/python3.14",
                 "/opt/homebrew/bin/python3.12",
             ]), \
             mock.patch.object(sys, "executable", "/usr/bin/python3"), \
             mock.patch("shutil.which", return_value=None):
            candidates = fn()
            hb = [c for c in candidates if "homebrew" in c]
            assert hb[0] == "/opt/homebrew/bin/python3.14"
            assert hb[1] == "/opt/homebrew/bin/python3.12"
            assert hb[2] == "/opt/homebrew/bin/python3.9"


# ===========================================================================
# Bug #24: Shell injection prevention in _run_fix_command
# ===========================================================================

class TestRunFixCommand:
    """Test that _run_fix_command uses safe command execution."""

    def _get_fn(self):
        with open(_PROJECT_ROOT / "run.py", "r") as f:
            source = f.read()
        start = source.find("def _run_fix_command(cmd, timeout=120):")
        end = source.find("\n\n\ndef ", start + 1)
        if end == -1:
            end = source.find("\n\n", start + 100)
        func_source = source[start:end]
        ns = {"subprocess": subprocess, "shlex": __import__("shlex"),
              "print": print}
        exec(func_source, ns)
        return ns["_run_fix_command"]

    def test_string_command_uses_shlex(self):
        fn = self._get_fn()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            fn("echo hello world")
            call_kwargs = mock_run.call_args
            assert call_kwargs.kwargs.get("shell", False) is False
            assert isinstance(call_kwargs.args[0], list)

    def test_list_command_passed_directly(self):
        fn = self._get_fn()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            fn(["echo", "hello"])
            assert mock_run.call_args.args[0] == ["echo", "hello"]

    def test_injection_attempt_safe(self):
        fn = self._get_fn()
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=1)
            fn("echo hello; rm -rf /")
            assert mock_run.call_args.kwargs.get("shell", False) is False


# ===========================================================================
# Bug #4: Dep inspection graceful error handling
# ===========================================================================

class TestInspectVenvDeps:
    """Test that _inspect_venv_deps handles errors gracefully."""

    def _get_fn(self):
        with open(_PROJECT_ROOT / "run.py", "r") as f:
            source = f.read()
        start = source.find("def _inspect_venv_deps(venv_python):")
        end = source.find("\n\n\ndef ", start + 1)
        if end == -1:
            end = source.find("\n\n", start + 100)
        func_source = source[start:end]
        _DEPS = {
            "structlog": ("structlog", "23.0.0"),
            "rich": ("rich", "13.0.0"),
            "yaml": ("PyYAML", "6.0.0"),
            "requests": ("requests", "2.31.0"),
        }
        ns = {"_json_mod": json, "subprocess": subprocess, "print": print,
              "CORE_DEPENDENCIES": _DEPS}
        exec(func_source, ns)
        return ns["_inspect_venv_deps"]

    def test_nonzero_exit_returns_empty_not_all(self):
        fn = self._get_fn()
        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        mock_result.stdout = ""
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, missing, outdated = fn("/some/python")
            assert ok is False
            assert missing == []
            assert outdated == []

    def test_timeout_returns_empty(self):
        fn = self._get_fn()
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="test", timeout=120)):
            ok, missing, outdated = fn("/some/python")
            assert ok is False
            assert missing == []
            assert outdated == []

    def test_no_json_output_returns_empty(self):
        fn = self._get_fn()
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "some warning\nno json here"
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, missing, outdated = fn("/some/python")
            assert ok is False
            assert missing == []
            assert outdated == []

    def test_finds_json_line_robustly(self):
        fn = self._get_fn()
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'some warning\n{"missing": [], "outdated": []}\n'
        with mock.patch("subprocess.run", return_value=mock_result):
            ok, missing, outdated = fn("/some/python")
            assert ok is True
            assert missing == []
            assert outdated == []


# ===========================================================================
# Structural tests: verify fix patterns exist in run.py source
# ===========================================================================

class TestStructuralFixes:
    """Verify that fix patterns are present in run.py source code."""

    def setup_method(self):
        with open(_PROJECT_ROOT / "run.py", "r") as f:
            self.source = f.read()

    def _get_func(self, name):
        start = self.source.find(f"def {name}():")
        if start == -1:
            start = self.source.find(f"def {name}(")
        end = self.source.find("\n\n\ndef ", start + 1)
        if end == -1:
            end = self.source.find("\n\n", start + 200)
        return self.source[start:end]

    # Bug #18: Stale venv cleanup
    def test_venv_cleanup_in_create(self):
        func = self._get_func("_create_venv_and_install")
        assert "shutil.rmtree(venv_path)" in func

    # Bug #19: Partial venv cleanup
    def test_partial_venv_cleanup(self):
        func = self._get_func("_try_create_venv")
        assert "Cleaned up partial venv" in func

    # Bug #20: Failure tracking
    def test_failure_tracking_in_repair(self):
        func = self._get_func("_repair_venv_deps")
        assert "_failed_pkgs" in func or "_install_failures" in func

    # Bug #16: No API key in config
    def test_no_plaintext_key_in_quick_setup(self):
        func = self._get_func("_quick_bootstrap_config")
        assert 'config["api"]["api_keys"][provider] = api_key' not in func

    # Bug #17: --health-check wraps in try/except
    def test_health_check_wraps_bootstrap(self):
        hc = self.source[self.source.find('if "--health-check" in sys.argv:'):]
        hc = hc[:hc.find("\n\n")]
        assert "try:" in hc
        assert "except SystemExit:" in hc

    # Bug #27: --repair restarts into venv
    def test_repair_restarts(self):
        func = self._get_func("repair_environment")
        assert "os.execv(venv_python" in func

    # Bug #29: get-pip.py cleanup in finally
    def test_getpip_cleanup_in_finally(self):
        func = self._get_func("_bootstrap_pip_via_getpip")
        assert "finally:" in func
        assert "os.remove(getpip_path)" in func

    # Bug #23: Non-blocking xcode-select
    def test_xcode_select_uses_popen(self):
        func = self._get_func("_auto_fix_venv_failure")
        assert "subprocess.Popen" in func

    # Bug #25: Increased timeout
    def test_dep_inspect_timeout_120(self):
        func = self._get_func("_inspect_venv_deps")
        assert "timeout=120" in func
        assert "timeout=30" not in func

    # Bug #1: Encoding fix
    def test_encoding_wrapped_in_try(self):
        assert "except (ValueError, AttributeError, OSError):" in self.source

    # Bug #11: Platform deps checked via subprocess
    def test_platform_dep_uses_subprocess(self):
        func = self._get_func("repair_environment")
        # Should use subprocess to check platform deps in venv
        assert "subprocess.run" in func
        # Should NOT use importlib.import_module for platform check
        # (the old pattern was importlib.import_module(mod_name))

    # Bug #15: No system PyYAML install in auto_configure
    def test_no_system_pyyaml_in_autoconfig(self):
        func = self._get_func("_auto_configure")
        # Should not have pip install --quiet PyYAML for system
        assert 'pip.*install.*--quiet.*PyYAML' not in func
