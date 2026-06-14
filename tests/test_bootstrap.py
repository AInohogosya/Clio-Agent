"""
Smoke tests for the Clio Agent bootstrap system.

Verifies that the environment is correctly set up after bootstrap:
  - Version parsing handles edge cases
  - Venv detection logic works
  - Dependency inspection identifies missing/outdated packages
  - Core modules are importable
  - Config loading works
  - Platform detection works
"""

import importlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure src/ is on sys.path so we can import run.py helpers
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_SRC_PATH = str(_PROJECT_ROOT / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)


# ---------------------------------------------------------------------------
# 1. Version parsing
# ---------------------------------------------------------------------------

class TestVersionTuple:
    """Test the _version_tuple helper from run.py edge cases."""

    @pytest.fixture(autouse=True)
    def _import_version_tuple(self):
        # run.py is not a module, so we exec just the function
        run_py = _PROJECT_ROOT / "run.py"
        src = run_py.read_text(encoding="utf-8")
        # Extract the function body by finding the def
        ns = {}
        # We only need _version_tuple and CORE_DEPENDENCIES — exec the whole
        # file would trigger _auto_bootstrap_venv(), so we parse just the func.
        # Instead, re-implement the exact logic here for a pure unit test,
        # then verify it matches the real one by importing via exec.
        exec(src.split("def _auto_bootstrap_venv")[0], ns)
        self._vt = ns["_version_tuple"]

    def test_simple_version(self):
        assert self._vt("1.2.3") == (1, 2, 3)

    def test_two_part_version(self):
        assert self._vt("3.14") == (3, 14)

    def test_single_part_version(self):
        assert self._vt("42") == (42,)

    def test_dev_version(self):
        assert self._vt("1.0.0.dev0") == (1, 0, 0)

    def test_dev_version_with_number(self):
        assert self._vt("2.3.4.dev42") == (2, 3, 4)

    def test_local_version(self):
        assert self._vt("1.0.0+git.abc123") == (1, 0, 0)

    def test_local_and_dev(self):
        assert self._vt("1.0.0.dev0+local") == (1, 0, 0)

    def test_post_release(self):
        assert self._vt("2.0.0.post1") == (2, 0, 0)

    def test_rc_version(self):
        assert self._vt("1.2.3rc1") == (1, 2, 3)

    def test_alpha_version(self):
        assert self._vt("0.1.0a3") == (0, 1, 0)

    def test_beta_version(self):
        assert self._vt("3.0.0b10") == (3, 0, 0)

    def test_empty_string(self):
        assert self._vt("") == (0,)

    def test_only_local(self):
        # "local" has no numeric prefix → falls through to (0,)
        assert self._vt("+local") == (0,)

    def test_comparison_works(self):
        """Verify that tuple comparison gives correct ordering."""
        assert self._vt("1.0.0") < self._vt("2.0.0")
        assert self._vt("1.9.0") < self._vt("1.10.0")
        assert self._vt("2.0.0") > self._vt("1.99.99")

    def test_comparison_with_dev(self):
        """dev versions should compare correctly against releases."""
        assert self._vt("1.0.0.dev0") == self._vt("1.0.0")
        assert self._vt("0.9.0") < self._vt("1.0.0")

    def test_numeric_input(self):
        """Non-string input (e.g. int) should be handled via str()."""
        assert self._vt(3) == (3,)


# ---------------------------------------------------------------------------
# 2. Venv detection logic
# ---------------------------------------------------------------------------

class TestVenvDetection:
    """Test the venv detection functions from run.py."""

    @pytest.fixture(autouse=True)
    def _import_helpers(self):
        run_py = _PROJECT_ROOT / "run.py"
        src = run_py.read_text(encoding="utf-8")
        ns = {}
        exec(src.split("def _auto_bootstrap_venv")[0], ns)
        self._is_in_venv = ns["_is_in_venv"]
        self._get_venv_python = ns["_get_venv_python"]
        self._resolve_venv_python = ns["_resolve_venv_python"]
        self._venv_python_is_healthy = ns["_venv_python_is_healthy"]

    def test_is_in_venv_returns_bool(self):
        result = self._is_in_venv()
        assert isinstance(result, bool)

    def test_get_venv_python_no_venv_dir(self):
        """When no venv directory exists, should return None."""
        # This project may or may not have a venv dir; test the function
        # returns either a string or None
        result = self._get_venv_python()
        assert result is None or isinstance(result, str)

    def test_resolve_venv_python_returns_tuple(self):
        """_resolve_venv_python should return a 3-tuple."""
        result = self._resolve_venv_python()
        assert isinstance(result, tuple)
        assert len(result) == 3
        venv_python, needs_install, deps_ok = result
        assert venv_python is None or isinstance(venv_python, str)
        assert isinstance(needs_install, bool)
        assert isinstance(deps_ok, bool)

    def test_venv_python_is_healthy_with_current(self):
        """The current interpreter should pass the health check."""
        result = self._venv_python_is_healthy(sys.executable)
        assert result is True

    def test_venv_python_is_healthy_with_nonexistent(self):
        """A non-existent path should return False."""
        result = self._venv_python_is_healthy("/nonexistent/path/to/python")
        assert result is False

    def test_venv_python_is_healthy_with_empty_string(self):
        """An empty string should return False."""
        result = self._venv_python_is_healthy("")
        assert result is False


# ---------------------------------------------------------------------------
# 3. Dependency inspection
# ---------------------------------------------------------------------------

class TestDependencyInspection:
    """Test _inspect_venv_deps against the current environment."""

    @pytest.fixture(autouse=True)
    def _import_helpers(self):
        run_py = _PROJECT_ROOT / "run.py"
        src = run_py.read_text(encoding="utf-8")
        ns = {}
        exec(src.split("def _auto_bootstrap_venv")[0], ns)
        self._inspect_venv_deps = ns["_inspect_venv_deps"]
        self._check_venv_deps = ns["_check_venv_deps"]
        self.CORE_DEPENDENCIES = ns["CORE_DEPENDENCIES"]

    def test_inspect_current_interpreter(self):
        """Running inspection against the current interpreter should work."""
        ok, missing, outdated = self._inspect_venv_deps(sys.executable)
        assert isinstance(ok, bool)
        assert isinstance(missing, list)
        assert isinstance(outdated, list)

    def test_inspect_returns_ok_for_healthy_env(self):
        """In a properly bootstrapped env, all core deps should be present."""
        ok, missing, outdated = self._inspect_venv_deps(sys.executable)
        # We expect the current env to be healthy
        if not ok:
            pytest.fail(
                f"Dependency inspection failed.\n"
                f"Missing: {missing}\n"
                f"Outdated: {outdated}"
            )

    def test_check_venv_deps_returns_bool(self):
        result = self._check_venv_deps(sys.executable)
        assert isinstance(result, bool)

    def test_inspect_with_nonexistent_python(self):
        """A non-existent python should return all deps as missing."""
        ok, missing, outdated = self._inspect_venv_deps("/nonexistent/python")
        assert ok is False
        assert len(missing) > 0

    def test_inspect_missing_is_subset_of_core(self):
        """Missing packages should be a subset of CORE_DEPENDENCIES values."""
        _ok, missing, _outdated = self._inspect_venv_deps(sys.executable)
        core_pkgs = {pkg for pkg, _ in self.CORE_DEPENDENCIES.values()}
        for pkg in missing:
            assert pkg in core_pkgs, f"Unexpected missing package: {pkg}"

    def test_inspect_outdated_is_subset_of_core(self):
        """Outdated packages should be a subset of CORE_DEPENDENCIES values."""
        _ok, _missing, outdated = self._inspect_venv_deps(sys.executable)
        core_pkgs = {pkg for pkg, _ in self.CORE_DEPENDENCIES.values()}
        for pkg in outdated:
            assert pkg in core_pkgs, f"Unexpected outdated package: {pkg}"


# ---------------------------------------------------------------------------
# 4. Full import chain
# ---------------------------------------------------------------------------

class TestImportChain:
    """Verify all core modules can be imported."""

    @pytest.mark.parametrize(
        "module_name",
        [
            "ai_agent",
            "yaml",
            "requests",
            "structlog",
            "rich",
            "psutil",
            "pluggy",
            "ollama",
            "openai",
        ],
    )
    def test_import(self, module_name):
        """Each core module should be importable without errors."""
        mod = importlib.import_module(module_name)
        assert mod is not None

    def test_ai_agent_submodules(self):
        """Key ai_agent submodules should be importable."""
        submodules = [
            "ai_agent.core_processing.autonomous_loop_engine",
            "ai_agent.platform_abstraction.platform_detector",
            "ai_agent.utils.config",
            "ai_agent.utils.dependency_checker",
            "ai_agent.utils.exceptions",
            "ai_agent.utils.logger",
        ]
        for name in submodules:
            mod = importlib.import_module(name)
            assert mod is not None

    def test_ai_agent_version(self):
        """ai_agent should expose a version."""
        import ai_agent
        assert hasattr(ai_agent, "__version__")
        assert ai_agent.__version__


# ---------------------------------------------------------------------------
# 5. Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    """Verify config.yaml can be loaded and has required fields."""

    def test_config_file_exists(self):
        config_path = _PROJECT_ROOT / "config.yaml"
        assert config_path.exists(), "config.yaml should exist in project root"

    def test_config_loads_with_yaml(self):
        import yaml

        config_path = _PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        assert isinstance(raw, dict)

    def test_config_has_api_section(self):
        import yaml

        config_path = _PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        assert "api" in raw, "config.yaml should have an 'api' section"
        assert isinstance(raw["api"], dict)

    def test_config_api_has_required_fields(self):
        import yaml

        config_path = _PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        api = raw["api"]
        assert "preferred_provider" in api
        assert "api_keys" in api
        assert "models" in api

    def test_config_has_security_section(self):
        import yaml

        config_path = _PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        assert "security" in raw

    def test_config_has_execution_section(self):
        import yaml

        config_path = _PROJECT_ROOT / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        assert "execution" in raw

    def test_config_manager_loads(self):
        """ConfigManager should load config without errors."""
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.utils.config import ConfigManager

        config_path = _PROJECT_ROOT / "config.yaml"
        mgr = ConfigManager(str(config_path))
        config = mgr.load_config()
        assert config is not None
        assert hasattr(config, "api")
        assert hasattr(config, "security")
        assert hasattr(config, "execution")

    def test_config_manager_get(self):
        """ConfigManager.get() should work with dot notation."""
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.utils.config import ConfigManager

        config_path = _PROJECT_ROOT / "config.yaml"
        mgr = ConfigManager(str(config_path))
        mgr.load_config()
        # Should return a value (even if empty string)
        provider = mgr.get("api.preferred_provider")
        assert provider is not None or provider == ""


# ---------------------------------------------------------------------------
# 6. Platform detection
# ---------------------------------------------------------------------------

class TestPlatformDetection:
    """Verify the platform detector works."""

    def test_platform_detector_import(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        assert detector is not None

    def test_detect_system(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        info = detector.detect_system()
        assert info is not None
        assert info.os_name is not None
        assert info.os_name != "unknown"
        assert info.architecture is not None
        assert info.python_version is not None

    def test_detect_os_returns_tuple(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        os_name, os_version = detector._detect_os()
        assert isinstance(os_name, str)
        assert isinstance(os_version, str)
        assert len(os_name) > 0

    def test_detect_architecture(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        arch = detector._detect_architecture()
        assert isinstance(arch, str)
        assert len(arch) > 0

    def test_detect_platform(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        plat = detector._detect_platform()
        assert isinstance(plat, str)
        assert plat == sys.platform.lower()

    def test_detect_python_version(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        ver = detector._detect_python_version()
        assert isinstance(ver, str)
        parts = ver.split(".")
        assert len(parts) == 3  # major.minor.micro

    def test_detect_container(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        is_container = detector._detect_container()
        assert isinstance(is_container, bool)

    def test_detect_headless(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        is_headless = detector._detect_headless()
        assert isinstance(is_headless, bool)

    def test_get_platform_specific_config(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector

        detector = PlatformDetector()
        config = detector.get_platform_specific_config()
        assert isinstance(config, dict)
        assert "os_name" in config
        assert "platform" in config
        assert "architecture" in config

    def test_system_info_dataclass(self):
        sys.path.insert(0, str(_PROJECT_ROOT / "src"))
        from ai_agent.platform_abstraction.platform_detector import SystemInfo

        info = SystemInfo(
            os_name="test",
            os_version="1.0",
            architecture="x86_64",
            platform="linux",
            python_version="3.12.0",
        )
        assert info.os_name == "test"
        assert info.is_headless is False
        assert info.is_container is False
