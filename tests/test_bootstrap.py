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
from pathlib import Path

import pytest

# Ensure src/ is on sys.path
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_SRC_PATH = str(_PROJECT_ROOT / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)


# ---------------------------------------------------------------------------
# Re-implementations of run.py bootstrap helpers (copied verbatim logic)
# These mirror the functions in run.py so we can unit-test them in isolation.
# ---------------------------------------------------------------------------

def _version_tuple(value):
    """Parse a dotted version string into a comparable tuple of ints.
    Mirror of run.py's _version_tuple().
    """
    raw = str(value).split("+")[0]
    raw = raw.split("-")[0]
    parts = []
    for chunk in raw.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts) if parts else (0,)


def _is_in_venv():
    """Mirror of run.py's _is_in_venv()."""
    return (
        hasattr(sys, 'real_prefix')
        or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
        or os.getenv('VIRTUAL_ENV') is not None
    )


def _get_venv_python():
    """Mirror of run.py's _get_venv_python()."""
    project_root = _PROJECT_ROOT
    venv_path = project_root / "venv"
    if not venv_path.exists():
        return None
    if platform.system() == "Windows":
        python_exe = venv_path / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = venv_path / "Scripts" / "pythonw.exe"
    else:
        python_exe = venv_path / "bin" / "python"
        if not python_exe.exists():
            python_exe = venv_path / "bin" / "python3"
    if not python_exe.exists():
        return None
    try:
        resolved = python_exe.resolve()
        if resolved.exists():
            return str(resolved)
    except (OSError, ValueError):
        pass
    return str(python_exe)


MIN_PYTHON = (3, 8)

CORE_DEPENDENCIES = {
    "structlog": ("structlog", "23.0.0"),
    "rich": ("rich", "13.0.0"),
    "yaml": ("PyYAML", "6.0.0"),
    "requests": ("requests", "2.31.0"),
    "pluggy": ("pluggy", "1.0.0"),
    "psutil": ("psutil", "5.9.0"),
    "ollama": ("ollama", "0.1.0"),
    "openai": ("openai", "1.0.0"),
}


def _inspect_venv_deps(venv_python):
    """Mirror of run.py's _inspect_venv_deps()."""
    spec_json = json.dumps(CORE_DEPENDENCIES)
    check_script = (
        "import importlib.util, json, sys\n"
        "try:\n"
        "    from importlib.metadata import version as _v, PackageNotFoundError\n"
        "except Exception:\n"
        "    _v = None\n"
        "    class PackageNotFoundError(Exception):\n"
        "        pass\n"
        "def _vt(s):\n"
        "    raw = str(s).split('+')[0]\n"
        "    raw = raw.split('-')[0]\n"
        "    out=[]\n"
        "    for c in raw.split('.'):\n"
        "        n=''\n"
        "        for ch in c:\n"
        "            if ch.isdigit(): n+=ch\n"
        "            else: break\n"
        "        if n=='': break\n"
        "        out.append(int(n))\n"
        "    return tuple(out) if out else (0,)\n"
        f"spec = json.loads('''{spec_json}''')\n"
        "missing=[]; outdated=[]\n"
        "for mod,(pkg,minv) in spec.items():\n"
        "    if importlib.util.find_spec(mod) is None:\n"
        "        missing.append(pkg); continue\n"
        "    if minv and _v is not None:\n"
        "        try:\n"
        "            cur=_v(pkg)\n"
        "            if _vt(cur) < _vt(minv): outdated.append(pkg)\n"
        "        except PackageNotFoundError:\n"
        "            pass\n"
        "        except Exception:\n"
        "            pass\n"
        "print(json.dumps({'missing': missing, 'outdated': outdated}))\n"
    )
    try:
        r = subprocess.run([venv_python, "-c", check_script],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, list({p for p, _ in CORE_DEPENDENCIES.values()}), []
        data = json.loads(r.stdout.strip().splitlines()[-1])
        missing = data.get("missing", [])
        outdated = data.get("outdated", [])
        return (not missing and not outdated), missing, outdated
    except Exception:
        return False, list({p for p, _ in CORE_DEPENDENCIES.values()}), []


def _check_venv_deps(venv_python):
    """Mirror of run.py's _check_venv_deps()."""
    ok, _missing, _outdated = _inspect_venv_deps(venv_python)
    return ok


def _venv_python_is_healthy(venv_python):
    """Mirror of run.py's _venv_python_is_healthy()."""
    try:
        if not Path(venv_python).exists():
            return False
    except OSError:
        return False
    try:
        ver = subprocess.run([venv_python, "-c",
                              "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=15)
        if ver.returncode != 0:
            return False
        ver_str = ver.stdout.strip()
        if "." not in ver_str:
            return False
        try:
            major, minor = (int(x) for x in ver_str.split(".")[:2])
            if (major, minor) < MIN_PYTHON:
                return False
        except Exception:
            pass
        pip = subprocess.run([venv_python, "-m", "pip", "--version"],
                             capture_output=True, text=True, timeout=15)
        return pip.returncode == 0
    except Exception:
        return False


def _resolve_venv_python():
    """Mirror of run.py's _resolve_venv_python()."""
    project_root = _PROJECT_ROOT
    venv_path = project_root / "venv"
    if platform.system() == "Windows":
        candidates = [venv_path / "Scripts" / "python.exe", venv_path / "Scripts" / "pythonw.exe"]
    else:
        candidates = [venv_path / "bin" / "python", venv_path / "bin" / "python3"]
    for p in candidates:
        try:
            if not p.exists():
                continue
        except (OSError, ValueError):
            continue
        # Use the symlink path directly, NOT the resolved path.
        # The venv's bin/python knows it's in a venv and will use the venv's
        # site-packages. Resolving to the system python breaks this mechanism.
        try:
            r = subprocess.run([str(p), "-m", "pip", "--version"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                deps_ok = _check_venv_deps(str(p))
                return str(p), False, deps_ok
        except Exception:
            pass
        return str(p), True, False
    return None, False, False


# ===========================================================================
# 1. Version parsing tests
# ===========================================================================

class TestVersionTuple:
    """Test _version_tuple handles edge cases."""

    def test_simple_version(self):
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_two_part_version(self):
        assert _version_tuple("3.14") == (3, 14)

    def test_single_part_version(self):
        assert _version_tuple("42") == (42,)

    def test_dev_version(self):
        assert _version_tuple("1.0.0.dev0") == (1, 0, 0)

    def test_dev_version_with_number(self):
        assert _version_tuple("2.3.4.dev42") == (2, 3, 4)

    def test_local_version(self):
        assert _version_tuple("1.0.0+git.abc123") == (1, 0, 0)

    def test_local_and_dev(self):
        assert _version_tuple("1.0.0.dev0+local") == (1, 0, 0)

    def test_post_release(self):
        assert _version_tuple("2.0.0.post1") == (2, 0, 0)

    def test_rc_version(self):
        assert _version_tuple("1.2.3rc1") == (1, 2, 3)

    def test_alpha_version(self):
        assert _version_tuple("0.1.0a3") == (0, 1, 0)

    def test_beta_version(self):
        assert _version_tuple("3.0.0b10") == (3, 0, 0)

    def test_empty_string(self):
        assert _version_tuple("") == (0,)

    def test_only_local(self):
        assert _version_tuple("+local") == (0,)

    def test_comparison_works(self):
        assert _version_tuple("1.0.0") < _version_tuple("2.0.0")
        assert _version_tuple("1.9.0") < _version_tuple("1.10.0")
        assert _version_tuple("2.0.0") > _version_tuple("1.99.99")

    def test_comparison_with_dev(self):
        assert _version_tuple("1.0.0.dev0") == _version_tuple("1.0.0")
        assert _version_tuple("0.9.0") < _version_tuple("1.0.0")

    def test_numeric_input(self):
        assert _version_tuple(3) == (3,)


# ===========================================================================
# 2. Venv detection tests
# ===========================================================================

class TestVenvDetection:
    """Test venv detection functions."""

    def test_is_in_venv_returns_bool(self):
        result = _is_in_venv()
        assert isinstance(result, bool)

    def test_get_venv_python_returns_str_or_none(self):
        result = _get_venv_python()
        assert result is None or isinstance(result, str)

    def test_resolve_venv_python_returns_tuple(self):
        result = _resolve_venv_python()
        assert isinstance(result, tuple)
        assert len(result) == 3
        venv_python, needs_install, deps_ok = result
        assert venv_python is None or isinstance(venv_python, str)
        assert isinstance(needs_install, bool)
        assert isinstance(deps_ok, bool)

    def test_venv_python_is_healthy_with_current(self):
        result = _venv_python_is_healthy(sys.executable)
        assert result is True

    def test_venv_python_is_healthy_with_nonexistent(self):
        result = _venv_python_is_healthy("/nonexistent/path/to/python")
        assert result is False

    def test_venv_python_is_healthy_with_empty_string(self):
        result = _venv_python_is_healthy("")
        assert result is False


# ===========================================================================
# 3. Dependency inspection tests
# ===========================================================================

class TestDependencyInspection:
    """Test _inspect_venv_deps against the current environment."""

    def test_inspect_current_interpreter(self):
        ok, missing, outdated = _inspect_venv_deps(sys.executable)
        assert isinstance(ok, bool)
        assert isinstance(missing, list)
        assert isinstance(outdated, list)

    def test_inspect_returns_ok_for_healthy_env(self):
        ok, missing, outdated = _inspect_venv_deps(sys.executable)
        if not ok:
            pytest.fail(
                f"Dependency inspection failed.\n"
                f"Missing: {missing}\n"
                f"Outdated: {outdated}"
            )

    def test_check_venv_deps_returns_bool(self):
        result = _check_venv_deps(sys.executable)
        assert isinstance(result, bool)

    def test_inspect_with_nonexistent_python(self):
        ok, missing, outdated = _inspect_venv_deps("/nonexistent/python")
        assert ok is False
        assert len(missing) > 0

    def test_inspect_missing_is_subset_of_core(self):
        _ok, missing, _outdated = _inspect_venv_deps(sys.executable)
        core_pkgs = {pkg for pkg, _ in CORE_DEPENDENCIES.values()}
        for pkg in missing:
            assert pkg in core_pkgs, f"Unexpected missing package: {pkg}"

    def test_inspect_outdated_is_subset_of_core(self):
        _ok, _missing, outdated = _inspect_venv_deps(sys.executable)
        core_pkgs = {pkg for pkg, _ in CORE_DEPENDENCIES.values()}
        for pkg in outdated:
            assert pkg in core_pkgs, f"Unexpected outdated package: {pkg}"


# ===========================================================================
# 4. Full import chain tests
# ===========================================================================

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
        mod = importlib.import_module(module_name)
        assert mod is not None

    def test_ai_agent_submodules(self):
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
        import ai_agent
        assert hasattr(ai_agent, "__version__")
        assert ai_agent.__version__


# ===========================================================================
# 5. Config loading tests
# ===========================================================================

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
        assert "api" in raw
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
        from ai_agent.utils.config import ConfigManager
        config_path = _PROJECT_ROOT / "config.yaml"
        mgr = ConfigManager(str(config_path))
        config = mgr.load_config()
        assert config is not None
        assert hasattr(config, "api")
        assert hasattr(config, "security")
        assert hasattr(config, "execution")

    def test_config_manager_get(self):
        from ai_agent.utils.config import ConfigManager
        config_path = _PROJECT_ROOT / "config.yaml"
        mgr = ConfigManager(str(config_path))
        mgr.load_config()
        provider = mgr.get("api.preferred_provider")
        assert provider is not None or provider == ""


# ===========================================================================
# 6. Platform detection tests
# ===========================================================================

class TestPlatformDetection:
    """Verify the platform detector works."""

    def test_platform_detector_import(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        assert detector is not None

    def test_detect_system(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        info = detector.detect_system()
        assert info is not None
        assert info.os_name is not None
        assert info.os_name != "unknown"
        assert info.architecture is not None
        assert info.python_version is not None

    def test_detect_os_returns_tuple(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        os_name, os_version = detector._detect_os()
        assert isinstance(os_name, str)
        assert isinstance(os_version, str)
        assert len(os_name) > 0

    def test_detect_architecture(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        arch = detector._detect_architecture()
        assert isinstance(arch, str)
        assert len(arch) > 0

    def test_detect_platform(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        plat = detector._detect_platform()
        assert isinstance(plat, str)
        assert plat == sys.platform.lower()

    def test_detect_python_version(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        ver = detector._detect_python_version()
        assert isinstance(ver, str)
        parts = ver.split(".")
        assert len(parts) == 3

    def test_detect_container(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        is_container = detector._detect_container()
        assert isinstance(is_container, bool)

    def test_detect_headless(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        is_headless = detector._detect_headless()
        assert isinstance(is_headless, bool)

    def test_get_platform_specific_config(self):
        from ai_agent.platform_abstraction.platform_detector import PlatformDetector
        detector = PlatformDetector()
        config = detector.get_platform_specific_config()
        assert isinstance(config, dict)
        assert "os_name" in config
        assert "platform" in config
        assert "architecture" in config

    def test_system_info_dataclass(self):
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
