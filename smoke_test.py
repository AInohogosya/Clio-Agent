#!/usr/bin/env python3
"""
Standalone smoke test for Clio Agent — no external dependencies required.
Uses only stdlib + yaml (for config checks). Prints a clear PASS/FAIL summary.

Usage: python3 smoke_test.py
"""

import importlib
import os
import platform
import sys
from pathlib import Path

# Counters
passed = 0
failed = 0
skipped = 0


def check(label, condition, detail=""):
    global passed, failed, skipped
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def skip(label, detail=""):
    global skipped
    skipped += 1
    msg = f"  SKIP  {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def section(name):
    print(f"\n{'─' * 50}")
    print(f"  {name}")
    print(f"{'─' * 50}")


# ── 1. Python version ────────────────────────────────────────────────────────
section("Python Version")

py_version = sys.version_info[:2]
check(
    f"Python >= 3.8 (running {py_version[0]}.{py_version[1]})",
    py_version >= (3, 8),
    f"Need 3.8+, got {py_version[0]}.{py_version[1]}",
)

# ── 2. Core module imports ───────────────────────────────────────────────────
section("Core Module Imports")

CORE_MODULES = [
    "yaml",
    "requests",
    "structlog",
    "rich",
    "psutil",
    "pluggy",
    "ollama",
    "openai",
]

for mod_name in CORE_MODULES:
    try:
        mod = importlib.import_module(mod_name)
        version = getattr(mod, "__version__", "installed")
        check(f"import {mod_name} ({version})", True)
    except ImportError:
        check(f"import {mod_name}", False, "Module not found")

# ai_agent
try:
    import ai_agent

    ver = getattr(ai_agent, "__version__", "unknown")
    check(f"import ai_agent ({ver})", True)
except ImportError:
    check("import ai_agent", False, "Module not found")

# ── 3. Config loading ────────────────────────────────────────────────────────
section("Config Loading")

project_root = Path(__file__).parent.resolve()
config_path = project_root / "config.yaml"

check("config.yaml exists", config_path.exists())

if config_path.exists():
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        check("config.yaml parses as YAML", isinstance(raw, dict))

        if isinstance(raw, dict):
            check("config has 'api' section", "api" in raw)
            if "api" in raw:
                api = raw["api"]
                check("api.preferred_provider exists", "preferred_provider" in api)
                check("api.api_keys exists", "api_keys" in api)
                check("api.models exists", "models" in api)
            check("config has 'security' section", "security" in raw)
            check("config has 'execution' section", "execution" in raw)
            check("config has 'logging' section", "logging" in raw)
    except ImportError:
        skip("config.yaml parsing", "yaml not installed")
    except Exception as e:
        check("config.yaml parsing", False, str(e))
else:
    skip("config.yaml checks", "file not found")

# ── 4. Version parsing (inline test) ─────────────────────────────────────────
section("Version Parsing")


def _version_tuple(value):
    """Mirror of run.py's _version_tuple for standalone testing."""
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


VERSION_CASES = [
    ("1.2.3", (1, 2, 3)),
    ("3.14", (3, 14)),
    ("42", (42,)),
    ("1.0.0.dev0", (1, 0, 0)),
    ("2.3.4.dev42", (2, 3, 4)),
    ("1.0.0+git.abc123", (1, 0, 0)),
    ("1.0.0.dev0+local", (1, 0, 0)),
    ("2.0.0.post1", (2, 0, 0)),
    ("1.2.3rc1", (1, 2, 3)),
    ("0.1.0a3", (0, 1, 0)),
    ("3.0.0b10", (3, 0, 0)),
    ("", (0,)),
]

all_version_ok = True
for input_val, expected in VERSION_CASES:
    result = _version_tuple(input_val)
    ok = result == expected
    if not ok:
        all_version_ok = False
        check(f"version('{input_val}') == {expected}", False, f"got {result}")

if all_version_ok:
    check(f"All {len(VERSION_CASES)} version parsing cases", True)

# Comparison test
check(
    "version comparison: 1.0.0 < 2.0.0",
    _version_tuple("1.0.0") < _version_tuple("2.0.0"),
)
check(
    "version comparison: 1.9.0 < 1.10.0",
    _version_tuple("1.9.0") < _version_tuple("1.10.0"),
)

# ── 5. Venv detection ────────────────────────────────────────────────────────
section("Virtual Environment")

in_venv = (
    hasattr(sys, "real_prefix")
    or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    or os.getenv("VIRTUAL_ENV") is not None
)
# Informational only — not a hard requirement (Homebrew, pyenv, conda are fine)
if in_venv:
    check("Running in virtual environment", True)
else:
    skip("Running in virtual environment", "using system/Homebrew Python is OK")

venv_path = project_root / "venv"
check("venv directory exists", venv_path.exists() and venv_path.is_dir())

# ── 6. Platform detection ────────────────────────────────────────────────────
section("Platform Detection")

check(f"OS detected: {platform.system()}", bool(platform.system()))
check(f"Architecture: {platform.machine()}", bool(platform.machine()))
check(f"Platform: {sys.platform}", bool(sys.platform))
check(
    f"Python version string: {sys.version.split()[0]}",
    bool(sys.version.split()[0]),
)

# ── 7. Project structure ─────────────────────────────────────────────────────
section("Project Structure")

REQUIRED_DIRS = ["src", "src/ai_agent", "tests"]
for d in REQUIRED_DIRS:
    p = project_root / d
    check(f"Directory '{d}' exists", p.exists() and p.is_dir())

REQUIRED_FILES = ["run.py", "config.yaml", "pyproject.toml"]
for f in REQUIRED_FILES:
    p = project_root / f
    check(f"File '{f}' exists", p.exists() and p.is_file())

# ── Summary ──────────────────────────────────────────────────────────────────
total = passed + failed + skipped
print(f"\n{'═' * 50}")
print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped ({total} total)")
print(f"{'═' * 50}")

if failed == 0:
    print("  ✅ ALL CHECKS PASSED")
    sys.exit(0)
else:
    print(f"  ❌ {failed} CHECK(S) FAILED")
    sys.exit(1)
