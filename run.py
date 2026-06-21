#!/usr/bin/env python3
"""
Clio Agent - Full-featured entry point.
Install the package with: pip install -e .
Then invoke globally with:   Clio-Agent
Or with the lowercase alias:  clio-agent

This is the complete bootstrap entry point that handles:
  - Virtual environment creation and management
  - Dependency installation
  - Provider/model selection
  - Telegram/Discord bot mode
  - Signal handling and context survival
"""

import sys
import os
import signal
import subprocess

# Fix encoding issues on macOS / environments with surrogateescape
try:
    if (sys.stdout.encoding
            and sys.stdout.encoding.lower().startswith("utf")
            and sys.stdout.errors == "surrogateescape"):
        sys.stdout.reconfigure(errors="surrogatepass")
except (ValueError, AttributeError, OSError):
    pass
try:
    if (sys.stderr.encoding
            and sys.stderr.encoding.lower().startswith("utf")
            and sys.stderr.errors == "surrogateescape"):
        sys.stderr.reconfigure(errors="surrogatepass")
except (ValueError, AttributeError, OSError):
    pass

# Windows consoles may use cp1252 or cp65001; force UTF-8 where possible
# Use the compat module for platform detection (avoids import cycle).
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
# Only set default IO encoding if not already set by the system (avoids overriding
# system locale settings like LANG=C which may be intentional)
if not os.environ.get("PYTHONIOENCODING"):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import shutil
import json as _json_mod
import time
from pathlib import Path
from typing import Optional

# Import platform compat (safe after sys.path is set up below)
def _is_windows() -> bool:
    """Check if running on Windows (early import, before compat module)."""
    return sys.platform == "win32"

# Ensure src directory is in sys.path so ai_agent can be imported
_project_root_for_path = Path(__file__).parent.resolve()
_src_path = str(_project_root_for_path / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

# ── Ensure external_integration/ is accessible as ai_agent.external_integration ──
# The codebase uses relative imports like `from ..external_integration import ...`
# inside src/ai_agent/, but external_integration/ lives at the project root.
# We create a symlink (or copy on restricted platforms) so that the package
# structure is correct at import time, BEFORE any ai_agent modules are loaded.
_ext_src = _project_root_for_path / "src" / "ai_agent" / "external_integration"
_ext_target = _project_root_for_path / "external_integration"
if _ext_target.is_dir():
    # FIX #3: Handle symlink/copy carefully to avoid stale copies and
    # avoid __init__.py creating a conflicting copy on top of our symlink.
    if _ext_src.exists() or _ext_src.is_symlink():
        # Already set up — ensure it's valid
        if _ext_src.is_symlink():
            try:
                if not _ext_src.resolve().exists():
                    # Broken symlink — recreate
                    _ext_src.unlink()
                    _ext_src.symlink_to(os.path.relpath(str(_ext_target), str(_ext_src.parent)))
            except OSError:
                pass
        # If it's a directory (from a previous copy), leave it — __init__.py won't overwrite
    else:
        try:
            _ext_src.symlink_to(os.path.relpath(str(_ext_target), str(_ext_src.parent)))
        except OSError:
            # Symlinks unavailable (e.g. Windows without dev mode) — fall back to
            # copying the directory tree. Mark it so __init__.py knows not to overwrite.
            try:
                import shutil as _shutil
                _shutil.copytree(str(_ext_target), str(_ext_src))
            except Exception:
                pass  # Will be handled by __init__.py's fallback


def _is_in_venv():
    """Detect whether we are running inside a virtual environment.

    Covers standardvenv, venv, virtualenv, conda, pipenv, poetry,
    and pyenv-virtualenv environments.
    """
    # Standard venv / virtualenv detection
    if hasattr(sys, 'real_prefix'):
        return True
    if hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix:
        return True
    # Virtual environment indicator
    if os.getenv('VIRTUAL_ENV') is not None:
        return True
    # Conda environment detection
    if os.getenv('CONDA_PREFIX') is not None:
        return True
    # pipenv environment detection
    if os.getenv('PIPENV_ACTIVE') is not None:
        return True
    # poetry environment detection
    if os.getenv('POETRY_ACTIVE') is not None:
        return True
    # pyenv-virtualenv detection
    if os.getenv('PYENV_VIRTUAL_ENV') is not None:
        return True
    return False


def _get_venv_python():
    project_root = Path(__file__).parent
    venv_path = project_root / "venv"
    if not venv_path.exists():
        return None
    if _is_windows():
        python_exe = venv_path / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = venv_path / "Scripts" / "pythonw.exe"
    else:
        python_exe = venv_path / "bin" / "python"
        if not python_exe.exists():
            python_exe = venv_path / "bin" / "python3"
    if not python_exe.exists():
        return None
    # Return the symlink path directly, NOT the resolved path.
    # The venv's bin/python knows it's in a venv and will use the venv's
    # site-packages. Resolving to the system python breaks this mechanism.
    return str(python_exe)


# ── Core dependency contract used by the bootstrap ──────────────────────────
# Maps the importable module name -> (pip package name, minimum version).
# A minimum version of None means "any installed version is acceptable".
# This is the single source of truth for the self-healing bootstrap: it is
# used to detect missing packages (partial installs) AND outdated packages
# (wrong-version installs), so only the components that actually need work
# are touched.
CORE_DEPENDENCIES = {
    "structlog": ("structlog", "23.0.0"),
    "rich": ("rich", "13.0.0"),
    "yaml": ("PyYAML", "6.0.0"),
    "requests": ("requests", "2.31.0"),
    "pluggy": ("pluggy", "1.0.0"),
    "psutil": ("psutil", "5.9.0"),
    "ollama": ("ollama", "0.1.0"),
    "openai": ("openai", "1.0.0"),
    "PIL": ("Pillow", "10.0.0"),
    "numpy": ("numpy", "1.24.0"),
    "groq": ("groq", "0.5.0"),
    "anthropic": ("anthropic", "0.25.0"),
    "google.genai": ("google-genai", "0.3.0"),
    "mistralai": ("mistralai", "0.1.0"),
    "cohere": ("cohere", "5.0.0"),
    "telegram": ("python-telegram-bot", "21.0.0"),
    "discord": ("discord.py", "2.3.0"),
    "boto3": ("boto3", "1.34.0"),
}

# Platform specific dependencies handled separately
PLATFORM_DEPENDENCIES = {
    "darwin": {
        "objc": ("pyobjc-framework-Cocoa", "9.0.0")
    },
    "win32": {
        "win32api": ("pywin32", "306.0.0")
    },
    "linux": {
        "Xlib": ("python-xlib", "0.33.0")
    }
}

# Minimum Python version this project supports (keep in sync with pyproject).
MIN_PYTHON = (3, 8)


def _version_tuple(value):
    """Parse a dotted version string into a comparable tuple of ints.

    Non-numeric suffixes (e.g. '1.2.3rc1', '2.0.0.post1') are handled so that
    pre-release and dev versions are considered LESS than the release version.
    This ensures '1.0.0rc1' < '1.0.0' and '1.0.0.dev0' < '1.0.0'.

    Also strips local version identifiers (e.g. '1.0.0+git.abc123').
    """
    import re
    raw = str(value).split("+")[0]  # Strip local version identifier (PEP 440)
    # Detect pre-release/dev markers: rc, beta, alpha, dev, post, a, b
    # PEP 440 ordering: dev < alpha < beta < rc < (final) < post
    # Rank: higher = more mature (post > final > rc > beta > alpha > dev)
    _PRERELEASE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2,
                        "rc": 3, "c": 3, "post": 5, "r": 5, "rev": 5}
    _FINAL_RANK = 4  # final release sits between rc and post
    _rank = None  # None means "no pre-release marker found yet"

    # Extract the numeric parts and detect pre-release suffix
    parts = []
    for chunk in raw.split("."):
        num_str = ""
        suffix_start = len(chunk)
        for i, ch in enumerate(chunk):
            if ch.isdigit():
                num_str += ch
            else:
                suffix_start = i
                break
        if num_str == "":
            # Handle chunks that are entirely a pre-release marker
            # e.g., "1.0.0.dev0" splits into ["1","0","0","dev0"]
            _chk = chunk.lower().strip().lstrip("-_")
            if _chk:
                for _mark, _r in _PRERELEASE_RANK.items():
                    if _chk.startswith(_mark):
                        _rank = _r
                        break
            break
        parts.append(int(num_str))
        # Check if the remainder of this chunk is a pre-release marker
        remainder = chunk[suffix_start:].lower().strip()
        if remainder:
            remainder = remainder.lstrip("-_")
            for _mark, _r in _PRERELEASE_RANK.items():
                if remainder.startswith(_mark):
                    _rank = _r
                    break
            break  # Stop at first non-numeric chunk

    if not parts:
        return (0,)
    # Use _FINAL_RANK (4) if no pre-release marker was found
    return tuple(parts) + (_rank if _rank is not None else _FINAL_RANK,)


def _inspect_venv_deps(venv_python):
    """Inspect the venv and report which core deps are missing or outdated.

    Returns (ok, missing, outdated):
      ok       -> True when every core dependency is present and new enough.
      missing  -> list of pip package names that are not importable at all.
      outdated -> list of pip package names installed below the minimum version.
    """
    spec_json = _json_mod.dumps(CORE_DEPENDENCIES)
    # Subprocess script with pre-release handling, increased timeout,
    # robust JSON parsing, and graceful error reporting.
    check_script = (
        "import importlib.util, json, sys\n"
        "try:\n"
        "    from importlib.metadata import version as _v, PackageNotFoundError\n"
        "except Exception:\n"
        "    _v = None\n"
        "    class PackageNotFoundError(Exception):\n"
        "        pass\n"
        "_PRERELEASE_ORDER = {'dev': 0, 'a': 1, 'alpha': 1, 'b': 2, 'beta': 2,\n"
        "                     'rc': 3, 'c': 3, 'post': 5, 'r': 5, 'rev': 5}\n"
        "def _vt(s):\n"
        "    raw = str(s).split('+')[0]\n"
        "    _pr = 4\n"
        "    parts = []\n"
        "    for chunk in raw.split('.'):\n"
        "        ns = ''\n"
        "        ss = len(chunk)\n"
        "        for i, ch in enumerate(chunk):\n"
        "            if ch.isdigit(): ns += ch\n"
        "            else: ss = i; break\n"
        "        if not ns: break\n"
        "        parts.append(int(ns))\n"
        "        rem = chunk[ss:].lower().strip().lstrip('-_')\n"
        "        if rem:\n"
        "            for m, r in _PRERELEASE_ORDER.items():\n"
        "                if rem.startswith(m): _pr = min(_pr, r); break\n"
        "            break\n"
        "    return tuple(parts) + (_pr,) if parts else (0, 4)\n"
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
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            _err_msg = (r.stderr or r.stdout or "unknown error").strip()[:300]
            print(f"  [dep-check] WARNING: venv dep probe exited {r.returncode}: {_err_msg}")
            return False, [], []
        _json_line = None
        for _line in reversed(r.stdout.strip().splitlines()):
            _line_s = _line.strip()
            if _line_s.startswith("{") and _line_s.endswith("}"):
                _json_line = _line_s
                break
        if _json_line is None:
            print(f"  [dep-check] WARNING: No JSON output from dep probe")
            return False, [], []
        data = _json_mod.loads(_json_line)
        missing = data.get("missing", [])
        outdated = data.get("outdated", [])
        return (not missing and not outdated), missing, outdated
    except subprocess.TimeoutExpired:
        print(f"  [dep-check] WARNING: venv dep probe timed out after 120s")
        return False, [], []
    except Exception as _e:
        print(f"  [dep-check] WARNING: venv dep probe failed: {_e}")
        return False, [], []
def _check_venv_deps(venv_python):
    """Return True if all core dependencies are present and new enough."""
    ok, _missing, _outdated = _inspect_venv_deps(venv_python)
    return ok


def _venv_python_is_healthy(venv_python):
    """Return True if the venv interpreter actually runs and has a working pip.

    Detects the common 'stale venv' failure where the base Python that created
    the venv was upgraded or removed (very common with Homebrew/pyenv), leaving
    a venv whose python symlink is dead. Such a venv must be rebuilt.

    Also handles cases where the venv python exists but is a symlink to
    a now-missing interpreter (broken symlink returns FileNotFoundError).
    """
    from pathlib import Path as _P
    # First check: does the file actually exist (not a broken symlink)?
    try:
        if not _P(venv_python).exists():
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
                print("Virtual environment uses Python %d.%d "
                      "(< %d.%d required)." % (major, minor, MIN_PYTHON[0], MIN_PYTHON[1]))
                return False
        except Exception:
            pass
        pip = subprocess.run([venv_python, "-m", "pip", "--version"],
                             capture_output=True, text=True, timeout=15)
        return pip.returncode == 0
    except Exception:
        return False


def _resolve_venv_python():
    """Return (venv_python_path, needs_install, deps_ok).

    needs_install is True when the venv exists but pip is missing/broken.
    deps_ok is True only when every core dependency is present and new enough.
    """
    from pathlib import Path as _P
    project_root = Path(__file__).parent.resolve()
    venv_path = project_root / "venv"
    if _is_windows():
        candidates = [venv_path / "Scripts" / "python.exe", venv_path / "Scripts" / "pythonw.exe"]
    else:
        candidates = [venv_path / "bin" / "python", venv_path / "bin" / "python3"]
    # Collect any existing candidates (even broken ones) for fallback
    existing = []
    for p in candidates:
        try:
            if p.exists():
                existing.append(p)
        except (OSError, ValueError):
            pass
    # Try each candidate for a healthy venv python with working pip
    for p in existing:
        try:
            r = subprocess.run([str(p), "-m", "pip", "--version"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                deps_ok = _check_venv_deps(str(p))
                return str(p), False, deps_ok
        except Exception:
            pass
    # No healthy candidate — return first existing one as broken
    if existing:
        return str(existing[0]), True, False
    return None, False, False


def _pip_install(venv_python, args, timeout=600, retries=3):
    """Run `pip install <args>` in the venv with retry logic for network failures.

    Retries on common transient errors: timeouts, SSL errors, connection drops.
    Returns (ok, stderr).
    """
    _TRANSIENT_ERRS = ("connectionerror", "timeout", "sslerror",
                       "temporaryfailure", "retriableerror",
                       "network", "proxyerror", "reset by peer",
                       "connection reset", "broken pipe")
    for attempt in range(1, retries + 1):
        try:
            r = subprocess.run([venv_python, "-m", "pip", "install", *args],
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return True, ""
            err_lower = (r.stderr or "").lower()
            is_transient = any(e in err_lower for e in _TRANSIENT_ERRS)
            if is_transient and attempt < retries:
                delay = 2 * attempt
                print("  pip attempt %d/%d failed (transient); retrying in %ds ..." % (attempt, retries, delay))
                print("    Error: %s" % (r.stderr or "").strip()[:200])
                time.sleep(delay)
                continue
            return False, (r.stderr or "").strip()
        except subprocess.TimeoutExpired:
            if attempt < retries:
                delay = 2 * attempt
                print("  pip attempt %d/%d timed out; retrying in %ds ..." % (attempt, retries, delay))
                time.sleep(delay)
                continue
            return False, "pip timed out after %ds" % timeout
        except Exception as e:
            if attempt < retries:
                delay = 2 * attempt
                print("  pip attempt %d/%d errored (%s); retrying in %ds ..." % (attempt, retries, e, delay))
                time.sleep(delay)
                continue
            return False, str(e)


def _ensure_pip(venv_python):
    """Make sure pip exists in the venv, bootstrapping it if necessary."""
    try:
        r = subprocess.run([venv_python, "-m", "pip", "--version"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    print("Bootstrapping pip (ensurepip) ...")
    try:
        subprocess.run([venv_python, "-m", "ensurepip", "--upgrade"],
                       capture_output=True, text=True, timeout=120)
    except Exception:
        pass
    try:
        r = subprocess.run([venv_python, "-m", "pip", "--version"],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _repair_venv_deps(venv_python, project_root):
    """Install missing and upgrade outdated core deps with minimal churn.

    Strategy:
      1. Try a single editable install (cheap, resolves the whole graph).
      2. Re-inspect; if anything is still missing or outdated, install/upgrade
         ONLY those specific packages (pinned to their minimum version) so we
         never reinstall things that are already correct.
    Returns True if the venv ends up satisfying the core contract.
    """
    ok, missing, outdated = _inspect_venv_deps(venv_python)
    if ok:
        return True
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
    if outdated:
        print(f"Outdated dependencies (need upgrade): {', '.join(outdated)}")

    # 1. Try the whole project first so version constraints resolve together.
    print("Installing/updating project dependencies ...")
    success, err = _pip_install(venv_python, ["-e", str(project_root)])
    if not success:
        print("Editable install failed: %s" % err[:300])
        print("Falling back to non-editable project install ...")
        success, err = _pip_install(venv_python, [str(project_root)])
        if not success:
            # Platform-specific deps (pyobjc, pywin32, xlib) may fail on wrong OS.
            # Install core deps individually as last resort, skipping failed ones.
            print("Full project install failed: %s" % err[:300])
            print("Falling back to installing core deps individually ...")
            _pip_install(venv_python, ["--upgrade", "pip", "setuptools", "wheel"])
            for _mod, (_pkg, _minv) in CORE_DEPENDENCIES.items():
                _spec = "%s>=%s" % (_pkg, _minv) if _minv else _pkg
                _pip_install(venv_python, [_spec])
            # Try project with --no-deps to get the package registered
            _pip_install(venv_python, ["--no-deps", str(project_root)])

    # 2. Only touch whatever is still wrong → install missing, upgrade outdated.
    ok, missing, outdated = _inspect_venv_deps(venv_python)
    if ok:
        return True
    # BUG FIX #20: Track individual install failures
    _install_failures = []
    for pkg in missing:
        minv = _min_version_for(pkg)
        spec = f"{pkg}>={minv}" if minv else pkg
        print(f"Installing missing package: {spec}")
        _ok, _err = _pip_install(venv_python, [spec], timeout=300)
        if not _ok:
            _install_failures.append((pkg, _err))
            print(f"  WARNING: Failed to install {pkg}: {_err[:200]}")
    for pkg in outdated:
        minv = _min_version_for(pkg)
        spec = f"{pkg}>={minv}" if minv else pkg
        print(f"Upgrading outdated package: {spec}")
        _ok, _err = _pip_install(venv_python, ["--upgrade", spec], timeout=300)
        if not _ok:
            _install_failures.append((pkg, _err))
            print(f"  WARNING: Failed to upgrade {pkg}: {_err[:200]}")
    if _install_failures:
        print(f"\n  WARNING: {len(_install_failures)} package(s) could not be installed:")
        for _pkg, _err in _install_failures:
            print(f"    - {_pkg}: {_err[:100]}")

    return _check_venv_deps(venv_python)


def _min_version_for(pkg):
    """Return the minimum version configured for a pip package name (or None)."""
    for _mod, (name, minv) in CORE_DEPENDENCIES.items():
        if name.lower() == pkg.lower():
            return minv
    return None


def _sudo_works_noninteractive():
    """Return True if sudo -n true succeeds (passwordless sudo available)."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _get_sudo_password():
    """Prompt the user for their sudo password and validate it.

    Returns the password string if valid, or None if the user cancels
    or provides an invalid password after retries.

    The password is only prompted during initial setup or explicit repair.
    """
    import getpass
    # Check if we're on a platform that uses sudo
    if sys.platform == "win32":
        return None

    # First check if passwordless sudo already works
    if _sudo_works_noninteractive():
        return ""

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            prompt = "Enter your system (sudo) password"
            if attempt > 1:
                prompt += f" (attempt {attempt}/{max_attempts})"
            prompt += ": "
            password = getpass.getpass(prompt)
        except (KeyboardInterrupt, EOFError):
            print()
            return None

        if not password:
            print("  No password entered. Skipping system-level fixes.")
            return None

        # Validate the password by running sudo -S true
        try:
            proc = subprocess.run(
                ["sudo", "-S", "true"],
                input=password + "\n",
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return password
            else:
                stderr_out = (proc.stderr or "").strip()
                if "incorrect password" in stderr_out.lower() or "sorry" in stderr_out.lower():
                    print("  Incorrect password.")
                else:
                    print(f"  Password validation failed: {stderr_out[:100]}")
        except subprocess.TimeoutExpired:
            print("  Password validation timed out.")
        except Exception as e:
            print(f"  Error validating password: {e}")

    print("  Too many incorrect attempts. Skipping system-level fixes.")
    return None


def _run_fix_command(cmd, timeout=120, sudo_password=None):
    """Run a fix command and return True if it succeeded.

    Args:
        cmd: Command as list or string.
        timeout: Timeout in seconds.
        sudo_password: If provided, use this password for sudo commands via stdin.
                       Empty string "" means passwordless sudo is available.
                       None means no password available (skip sudo commands).
    """
    import shlex
    try:
        # BUG FIX #24: Always use shell=False with properly split args
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)

        # Determine if this is a sudo command
        use_sudo = len(cmd) > 0 and cmd[0] == "sudo"

        if use_sudo:
            if sudo_password is None:
                # No password available — skip this command
                print(f"    (skipping: sudo requires password)")
                return False
            elif sudo_password == "":
                # Passwordless sudo — use -n flag
                if len(cmd) > 1 and cmd[1] != "-n":
                    cmd = [cmd[0], "-n"] + cmd[1:]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                    shell=False,
                )
            else:
                # We have a password — use -S to read from stdin
                # Replace any -n flags with -S
                if len(cmd) > 1 and cmd[1] == "-n":
                    cmd = [cmd[0], "-S"] + cmd[2:]
                elif len(cmd) > 1 and cmd[1] != "-S":
                    cmd = [cmd[0], "-S"] + cmd[1:]
                result = subprocess.run(
                    cmd, input=sudo_password + "\n",
                    capture_output=True, text=True, timeout=timeout,
                    shell=False,
                )
        else:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                shell=False,
            )

        if result.returncode == 0:
            return True
        err_out = (result.stderr or result.stdout or "").strip()
        if err_out:
            print(f"    Output: {err_out[:200]}")
        return False
    except subprocess.TimeoutExpired:
        print(f"    Timed out after {timeout}s")
        return False
    except Exception as e:
        print(f"    Error: {e}")
        return False


def _auto_fix_venv_failure(err="", sudo_password=None):
    """Attempt to automatically fix the venv creation failure.

    Args:
        err: Error message from the failed venv creation attempt.
        sudo_password: If not None, use this password for sudo commands.
                       Empty string "" means passwordless sudo.
                       None means no password available.

    Returns True if a fix was applied, False otherwise.
    """
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    fixed = False
    can_sudo = sudo_password is not None

    print()
    print("Attempting automatic fix ...")
    if not can_sudo:
        print("  (sudo requires password — will skip system package manager fixes)")
    print()

    if sys.platform == "darwin":
        # Fix 1: xcode-select --install
        print("[auto-fix] Checking Xcode Command Line Tools ...")
        try:
            r = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                print("[auto-fix] Xcode CLI tools not found. Installing (GUI dialog may appear)...")
                # BUG FIX #23: xcode-select --install is blocking; use Popen
                try:
                    subprocess.Popen(["xcode-select", "--install"])
                    fixed = True
                    print("[auto-fix] Xcode CLI tools installation triggered (background).")
                except Exception as _xcode_err:
                    print(f"[auto-fix] Could not trigger Xcode CLI install: {_xcode_err}")
            else:
                print("[auto-fix] Xcode CLI tools already installed.")
        except Exception:
            pass

        # Fix 2: Homebrew
        if not fixed and shutil.which("brew"):
            print(f"[auto-fix] Installing python@{py_ver} via brew ...")
            if _run_fix_command(["brew", "install", f"python@{py_ver}"], timeout=300):
                fixed = True
                print("[auto-fix] Homebrew Python installed.")

        # Fix 3: virtualenv at user level (no sudo)
        if not fixed:
            print("[auto-fix] Installing virtualenv at user level ...")
            if _run_fix_command(
                [sys.executable, "-m", "pip", "install", "--user", "virtualenv"], timeout=120,
            ):
                fixed = True
                print("[auto-fix] virtualenv installed at user level.")

    elif sys.platform.startswith("linux"):
        has_apt = shutil.which("apt") or shutil.which("apt-get")
        has_dnf = shutil.which("dnf")
        has_yum = shutil.which("yum")
        venv_pkg = f"python{py_ver}-venv"

        # Fix 1: apt (with password support)
        if not fixed and has_apt and can_sudo:
            print(f"[auto-fix] Installing {venv_pkg} via apt ...")
            if _run_fix_command(["sudo", "apt", "update"], timeout=120,
                                sudo_password=sudo_password) and \
               _run_fix_command(["sudo", "apt", "install", "-y", venv_pkg], timeout=300,
                                sudo_password=sudo_password):
                fixed = True
                print(f"[auto-fix] {venv_pkg} installed via apt.")

        # Fix 2: dnf (with password support)
        if not fixed and has_dnf and can_sudo:
            print(f"[auto-fix] Installing {venv_pkg} via dnf ...")
            if _run_fix_command(["sudo", "dnf", "install", "-y", venv_pkg], timeout=300,
                                sudo_password=sudo_password):
                fixed = True
                print(f"[auto-fix] {venv_pkg} installed via dnf.")

        # Fix 3: yum (with password support)
        if not fixed and has_yum and can_sudo:
            print(f"[auto-fix] Installing {venv_pkg} via yum ...")
            if _run_fix_command(["sudo", "yum", "install", "-y", venv_pkg], timeout=300,
                                sudo_password=sudo_password):
                fixed = True
                print(f"[auto-fix] {venv_pkg} installed via yum.")

        # Fix 4: ensurepip package (with password support)
        if not fixed and "ensurepip" in err and can_sudo:
            pip_pkg = f"python{py_ver}-pip"
            if has_apt:
                print(f"[auto-fix] Installing {pip_pkg} via apt ...")
                if _run_fix_command(["sudo", "apt", "install", "-y", pip_pkg], timeout=300,
                                    sudo_password=sudo_password):
                    fixed = True
            elif has_dnf:
                print(f"[auto-fix] Installing {pip_pkg} via dnf ...")
                if _run_fix_command(["sudo", "dnf", "install", "-y", pip_pkg], timeout=300,
                                    sudo_password=sudo_password):
                    fixed = True

        # Fix 5: virtualenv at user level (no sudo)
        if not fixed:
            print("[auto-fix] Installing virtualenv at user level ...")
            if _run_fix_command(
                [sys.executable, "-m", "pip", "install", "--user", "virtualenv"], timeout=120,
            ):
                fixed = True
                print("[auto-fix] virtualenv installed at user level.")

    elif _is_windows():
        print("[auto-fix] On Windows, re-run the Python installer and ensure 'venv' is checked.")

    if fixed:
        print("\n[auto-fix] Fix applied! The program will now retry.\n")
    else:
        print("\n[auto-fix] Could not automatically fix the issue.\n")

    return fixed


def _try_create_venv_without_pip(venv_path, python_exe):
    """Create a venv with --without-pip, then bootstrap pip via ensurepip or get-pip.py.

    This handles the case where the system pythonX.Y-venv / ensurepip package is
    missing but the Python itself is functional. Returns (ok, err).
    """
    print(f"  Trying: {python_exe} -m venv --without-pip ...")
    try:
        result = subprocess.run(
            [python_exe, "-m", "venv", "--without-pip", str(venv_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            print(f"    Failed: {err[:200]}")
            return False, err

        # Determine the venv python path
        if sys.platform == "win32":
            venv_python = str(venv_path / "Scripts" / "python.exe")
        else:
            venv_python = str(venv_path / "bin" / "python")

        if not os.path.exists(venv_python):
            return False, "venv python not found after --without-pip creation"

        # Try ensurepip first
        print(f"    Bootstrapping pip via ensurepip ({venv_python}) ...")
        try:
            ep = subprocess.run(
                [venv_python, "-m", "ensurepip", "--upgrade"],
                capture_output=True, text=True, timeout=120,
            )
            if ep.returncode == 0:
                print("    pip bootstrapped via ensurepip.")
                return True, ""
            ensure_err = (ep.stderr or ep.stdout or "").strip()
            print(f"    ensurepip failed: {ensure_err[:200]}")
        except Exception as e:
            print(f"    ensurepip error: {e}")

        # Try get-pip.py as last resort
        return _bootstrap_pip_via_getpip(venv_python, venv_path)

    except FileNotFoundError:
        print(f"    Not found: {python_exe}")
        return False, "not found"
    except Exception as e:
        print(f"    Error: {e}")
        return False, str(e)


def _bootstrap_pip_via_getpip(venv_python, venv_path):
    """Bootstrap pip using get-pip.py as a last resort."""
    import urllib.request
    print("    Trying get-pip.py ...")
    getpip_path = str(venv_path / "get-pip.py")
    try:
        try:
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", getpip_path)
            r = subprocess.run(
                [venv_python, getpip_path, "--no-warn-script-location"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                print("    pip bootstrapped via get-pip.py.")
                return True, ""
            else:
                err = (r.stderr or r.stdout or "").strip()
                print(f"    get-pip.py failed: {err[:200]}")
                return False, err
        except Exception as e:
            print(f"    get-pip.py error: {e}")
            return False, str(e)
    finally:
        # BUG FIX #29: Always clean up get-pip.py
        try:
            os.remove(getpip_path)
        except Exception:
            pass


def _diagnose_venv_failure(err=""):
    """Print platform-specific diagnostic hints when venv creation fails."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    print()
    print("Diagnostic hints for venv creation failure:")
    if sys.platform == "darwin":
        print("  macOS detected. Common fixes:")
        print("    1. Install Xcode CLI tools:  xcode-select --install")
        print(f"    2. If using Homebrew Python:  brew install python@{py_ver}")
        print("    3. If using pyenv:  pyenv install <ver> && pyenv local <ver>")
        print("    4. Try:  python3 -m pip install virtualenv && python3 -m virtualenv venv")
    elif sys.platform.startswith("linux"):
        print("  Linux detected. Common fixes:")
        print(f"    1. sudo apt install python{py_ver}-venv")
        print(f"    2. sudo dnf install python{py_ver}-venv")
    elif _is_windows():
        print("  Windows detected. Common fixes:")
        print("    1. Re-run the Python installer, ensure 'venv' is checked")
        print("    2. Use:  py -m venv venv")
    if "ensurepip" in err:
        print("  'ensurepip' error: install the system venv package for your Python.")
    print()


def _try_create_venv(venv_path, python_exe):
    """Try creating a venv with the given Python executable. Returns (ok, err)."""
    print(f"  Trying: {python_exe} -m venv ...")
    try:
        result = subprocess.run(
            [python_exe, "-m", "venv", str(venv_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return True, ""
        err = (result.stderr or result.stdout or "").strip()
        print(f"    Failed: {err[:200]}")
        # BUG FIX #19: Clean up partial venv on failure
        if venv_path.exists():
            try:
                shutil.rmtree(venv_path)
                print(f"    Cleaned up partial venv")
            except Exception:
                pass
        return False, err
    except FileNotFoundError:
        print(f"    Not found: {python_exe}")
        return False, "not found"
    except Exception as e:
        print(f"    Error: {e}")
        # BUG FIX #19: Clean up partial venv on failure
        if venv_path.exists():
            try:
                shutil.rmtree(venv_path)
                print(f"    Cleaned up partial venv")
            except Exception:
                pass
        return False, str(e)


def _try_create_venv_virtualenv(venv_path):
    """Try creating a venv using the virtualenv package. Returns True on success."""
    for exe in ("virtualenv", "python3", sys.executable):
        print(f"  Trying virtualenv via: {exe}")
        try:
            cmd = [exe, str(venv_path)] if exe == "virtualenv" else [exe, "-m", "virtualenv", str(venv_path)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                return True
            print(f"    Failed: {(result.stderr or result.stdout or '').strip()[:200]}")
        except FileNotFoundError:
            print(f"    Not found: {exe}")
        except Exception as e:
            print(f"    Error: {e}")
    return False


def _collect_python_candidates():
    """Collect a deduplicated list of candidate Python executables.

    FIX #18: Dynamically discover Homebrew Python versions instead of
    hardcoding specific minor versions. Also prefers the current
    interpreter's version first.
    """
    candidates = [sys.executable]

    # Always include generic python3 from PATH
    found = shutil.which("python3")
    if found and found not in candidates:
        candidates.append(found)

    # Dynamically discover Homebrew Python versions
    import glob as _glob
    import re as _re
    def _python_version_key(p):
        """BUG FIX #3: Sort by version number, not lexicographically."""
        m = _re.search(r'python3\.(\d+)', p)
        return int(m.group(1)) if m else 0
    for hb_base in ("/opt/homebrew/bin", "/usr/local/bin"):
        for pattern in (f"{hb_base}/python3.*",):
            for path in sorted(_glob.glob(pattern), key=_python_version_key, reverse=True):
                if os.path.isfile(path) and not os.path.islink(path):
                    if path not in candidates:
                        candidates.append(path)
                elif os.path.islink(path):
                    # Resolve symlink to actual binary
                    real = os.path.realpath(path)
                    if real not in candidates:
                        candidates.append(path)

    # Also try versioned names via shutil.which
    for minor in range(8, 20):
        name = f"python3.{minor}"
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)

    # pyenv shims
    pyenv_root = os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    for shim_name in ("python3", "python"):
        shim = os.path.join(pyenv_root, "shims", shim_name)
        if os.path.exists(shim) and shim not in candidates:
            candidates.append(shim)

    # Conda Python
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        for cp_name in ("python3", "python"):
            cp = os.path.join(conda_prefix, "bin", cp_name)
            if os.path.exists(cp) and cp not in candidates:
                candidates.append(cp)

    return candidates


def _create_venv_and_install(sudo_password=None):
    """Create a fresh venv, install deps, return venv python path.

    Args:
        sudo_password: If not None, use this password for sudo commands during
                       auto-fix. None means no password available.

    Self-healing strategy:
      1. Remove any existing broken venv.
      2. Try creating venv with multiple Python executables.
      3. Try with virtualenv package as a last resort.
      4. Install deps with progressive fallback.
    """
    project_root = Path(__file__).parent.resolve()
    venv_path = project_root / "venv"

    # BUG FIX #18: Always remove existing venv before creating a new one
    if venv_path.exists():
        print(f"  Removing existing venv ...")
        try:
            shutil.rmtree(venv_path)
        except Exception as _rm_err:
            print(f"  WARNING: Could not fully remove venv: {_rm_err}")

    # Phase 1: Try multiple Python executables
    print(f"Creating virtual environment at {venv_path} ...")
    candidates = _collect_python_candidates()
    venv_created = False
    last_err = ""
    for exe in candidates:
        ok, err = _try_create_venv(venv_path, exe)
        if ok:
            venv_created = True
            print(f"  OK: Virtual environment created with {exe}")
            break
        last_err = err

    # Phase 2: Try --without-pip + ensurepip/get-pip.py bootstrap
    if not venv_created:
        print("Standard venv failed. Trying --without-pip + pip bootstrap ...")
        for exe in candidates:
            ok, err = _try_create_venv_without_pip(venv_path, exe)
            if ok:
                venv_created = True
                print(f"  OK: Virtual environment created with {exe} (--without-pip)")
                break
            last_err = err

    # Phase 3: Fallback to virtualenv package
    if not venv_created:
        print("Trying virtualenv package ...")
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "virtualenv"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and _try_create_venv_virtualenv(venv_path):
                venv_created = True
                print("  OK: Virtual environment created with virtualenv")
        except Exception:
            pass

    # Phase 4: Auto-fix and retry
    if not venv_created:
        print(f"\nERROR: Could not create virtual environment after trying all methods.")
        _diagnose_venv_failure(last_err)
        print("  Manual fix: rm -rf venv && python3 -m venv venv")
        if _auto_fix_venv_failure(last_err, sudo_password=sudo_password):
            if venv_path.exists():
                try:
                    shutil.rmtree(venv_path)
                except Exception:
                    pass
            print("Retrying all creation methods after auto-fix ...")
            # Retry Phase 1
            for exe in _collect_python_candidates():
                ok, err2 = _try_create_venv(venv_path, exe)
                if ok:
                    venv_created = True
                    print(f"  OK: Created with {exe} after auto-fix")
                    break
                last_err = err2
            # Retry Phase 2 (--without-pip)
            if not venv_created:
                for exe in _collect_python_candidates():
                    ok, err2 = _try_create_venv_without_pip(venv_path, exe)
                    if ok:
                        venv_created = True
                        print(f"  OK: Created with {exe} (--without-pip) after auto-fix")
                        break
                    last_err = err2
            # Retry Phase 3 (virtualenv)
            if not venv_created:
                try:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--user", "virtualenv"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if _try_create_venv_virtualenv(venv_path):
                        venv_created = True
                        print("  OK: Created with virtualenv after auto-fix")
                except Exception:
                    pass
            if not venv_created:
                print("Auto-fix was applied but venv creation still failed.")
                sys.exit(1)
        else:
            sys.exit(1)

    # Phase 3: Resolve venv Python and install deps
    venv_python, _needs_install, _deps_ok = _resolve_venv_python()
    if not venv_python:
        print("Could not find venv Python after creation")
        sys.exit(1)

    _ensure_pip(venv_python)
    print("Upgrading pip ...")
    _pip_install(venv_python, ["--upgrade", "pip", "setuptools", "wheel"], timeout=300)

    if not _repair_venv_deps(venv_python, project_root):
        print("ERROR: Core dependencies could not be installed.")
        print("       Activate the venv and run: pip install -e .")
        print("       For all optional deps:  pip install -e \".[all]\"")
        sys.exit(1)
    return venv_python


def repair_environment():
    """Comprehensive environment repair: install missing deps, fix platform issues.

    This function can be called independently via --repair to fix the environment
    without starting the agent.

    Prompts for sudo password when triggered via --repair so that system-level
    dependencies can be installed.
    """
    import platform as _plat
    import importlib
    system = _plat.system().lower()
    print("=" * 60)
    print("  Clio Agent - Environment Repair")
    print("=" * 60)
    print()

    # Prompt for sudo password to enable system-level repairs
    sudo_password = _get_sudo_password()
    # Clear password from local scope as soon as possible after use
    _sudo_pwd = sudo_password  # keep reference for passing to sub-functions

    # Step 1: Ensure venv exists
    project_root = Path(__file__).parent.resolve()
    venv_path = project_root / "venv"

    if not venv_path.exists():
        print("[1/4] Virtual environment not found. Creating...")
        venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
        if not venv_python:
            _sudo_pwd = None
            print("ERROR: Failed to create virtual environment.")
            sys.exit(1)
    else:
        print("[1/4] Virtual environment found.")
        venv_python, needs_install, deps_ok = _resolve_venv_python()
        if not venv_python:
            print("  Venv appears broken. Recreating...")
            venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
            if not venv_python:
                _sudo_pwd = None
                print("ERROR: Failed to recreate virtual environment.")
                sys.exit(1)
    
    # Step 2: Ensure pip is working
    print("\n[2/4] Checking pip...")
    if not _ensure_pip(venv_python):
        print("ERROR: pip is not available in the virtual environment.")
        sys.exit(1)
    print("  pip is OK.")
    
    # Step 3: Install/upgrade all core dependencies
    print("\n[3/4] Installing core dependencies...")
    _ensure_pip(venv_python)
    _pip_install(venv_python, ["--upgrade", "pip"], timeout=300)
    
    # Install all core deps
    ok, missing, outdated = _inspect_venv_deps(venv_python)
    if not ok:
        if missing:
            print(f"  Missing: {', '.join(missing)}")
            print("  Installing missing packages...")
            success, err = _pip_install(venv_python, missing, timeout=600)
            if not success:
                print(f"  WARNING: Failed to install some packages: {err[:200]}")
        if outdated:
            print(f"  Outdated: {', '.join(outdated)}")
            print("  Upgrading packages...")
            success, err = _pip_install(venv_python, ["--upgrade"] + outdated, timeout=600)
            if not success:
                print(f"  WARNING: Failed to upgrade some packages: {err[:200]}")
    else:
        print("  All core dependencies are up to date.")
    
    # Install platform-specific dependencies
    print("\n[4/4] Installing platform-specific dependencies...")
    platform_deps = PLATFORM_DEPENDENCIES.get(sys.platform, {})
    if platform_deps:
        for mod_name, (pkg_name, min_ver) in platform_deps.items():
            try:
                # BUG FIX #11: Check platform deps inside the venv
                _r = subprocess.run(
                    [venv_python, "-c",
                     f"try:\n    import {mod_name}\n    print('ok')\nexcept ImportError:\n    print('missing')"],
                    capture_output=True, text=True, timeout=30)
                if _r.stdout.strip() == "ok":
                    print(f"  {pkg_name}: already installed")
            except ImportError:
                print(f"  {pkg_name}: installing...")
                success, err = _pip_install(venv_python, [f"{pkg_name}>={min_ver}"], timeout=300)
                if success:
                    print(f"  {pkg_name}: installed successfully")
                else:
                    print(f"  WARNING: Failed to install {pkg_name}: {err[:100]}")
    else:
        print("  No platform-specific dependencies needed.")
    
    # Final verification
    print("\n" + "=" * 60)
    print("  Final Verification")
    print("=" * 60)
    ok, missing, outdated = _inspect_venv_deps(venv_python)
    if ok:
        print("  ✓ All dependencies are installed and up to date!")
    else:
        if missing:
            print(f"  ✗ Still missing: {', '.join(missing)}")
        if outdated:
            print(f"  ✗ Still outdated: {', '.join(outdated)}")
        print("\n  Try running: pip install -e \".[all]\"")
    
    # Securely clear sudo password from memory after use
    try:
        if '_sudo_pwd' in dir() and _sudo_pwd:
            _sudo_pwd = None
    except Exception:
        pass

    print("\nRepair complete. You can now run: Clio-Agent")
    print()

    # BUG FIX #27: Restart into the venv after repair
    run_py = str(Path(__file__).parent.resolve() / "run.py")
    print(f"Restarting in virtual environment ({venv_python}) ...\n")
    try:
        os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
    except (OSError, Exception) as _exec_err:
        print(f"Warning: could not restart into venv ({_exec_err}). Running directly.")


def _auto_bootstrap_venv():
    """Ensure we run inside the project venv, creating it if necessary.

    Self-healing: this function detects common environment problems and
    automatically fixes them (stale venvs, broken pip, missing deps)
    before restarting into the venv.
    """
    project_root = Path(__file__).parent.resolve()
    run_py = str(project_root / "run.py")

    # Prevent infinite restart loops: cap the number of bootstrap attempts.
    _bootstrap_count = int(os.environ.get("_CLIO_BOOTSTRAP_ATTEMPTS", "0"))

    # On first run, prompt for sudo password to enable system-level repairs.
    # The password is only requested during initial setup, NOT during normal
    # operation or automatic recovery.
    _sudo_pwd = None
    if _bootstrap_count == 0:
        _sudo_pwd = _get_sudo_password()

    if _bootstrap_count >= 3:
        print()
        print("=" * 60)
        print("ERROR: Bootstrap failed after 3 attempts.")
        print()
        _diagnose_venv_failure("")
        print("Quick fix:")
        print("  rm -rf venv && python3 -m venv venv")
        print("  source venv/bin/activate  # or venv\\Scripts\\activate on Windows")
        print("  pip install -e .")
        # Last-ditch auto-fix attempt before giving up
        if _auto_fix_venv_failure("", sudo_password=_sudo_pwd):
            print("Auto-fix applied on final attempt. One more bootstrap try ...")
            # Reset the bootstrap counter for one last attempt
            os.environ["_CLIO_BOOTSTRAP_ATTEMPTS"] = "0"
            # Remove stale venv so creation is clean
            venv_dir = project_root / "venv"
            if venv_dir.exists():
                try:
                    shutil.rmtree(venv_dir)
                except Exception:
                    pass
            # Retry full bootstrap path
            venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
            _sudo_pwd = None  # clear password after use
            if venv_python:
                print(f"Restarting in virtual environment ({venv_python}) ...")
                try:
                    os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
                except (OSError, Exception):
                    print("Warning: restart via execv failed. Running directly.")
                    return
            else:
                print("ERROR: Could not create venv even after auto-fix.")
                sys.exit(1)
        print("=" * 60)
        sys.exit(1)
    os.environ["_CLIO_BOOTSTRAP_ATTEMPTS"] = str(_bootstrap_count + 1)

    venv_python, needs_install, deps_ok = _resolve_venv_python()

    # ── Detect a stale/broken venv (e.g. base Python upgraded or removed) ──
    if venv_python and not _venv_python_is_healthy(venv_python):
        print("Existing virtual environment is unusable; rebuilding ...")
        venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
        _sudo_pwd = None  # clear password after use
        print(f"Restarting in virtual environment ({venv_python}) ...")
        try:
            os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
        except (OSError, Exception):
            print("Warning: restart via execv failed. Running directly.")
            return

    # ── Venv exists but pip is broken → recreate entirely ──
    if venv_python and needs_install:
        print("Virtual environment has broken pip; recreating from scratch ...")
        venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
        _sudo_pwd = None  # clear password after use
        print(f"Restarting in virtual environment ({venv_python}) ...")
        try:
            os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
        except (OSError, Exception):
            print("Warning: restart via execv failed. Running directly.")
            return

    # ── Venv exists with deps → check if we need to restart into it ──
    if venv_python and deps_ok:
        if _is_in_venv():
            return  # already running inside venv with deps
        print(f"Switching to virtual environment ({venv_python}) ...")
        try:
            os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
        except (OSError, Exception) as _exec_err:
            print(f"Warning: could not restart into venv ({_exec_err}). Running directly.")
            return

    # ── Venv exists but deps are missing/outdated → repair in place ──
    if venv_python and not deps_ok:
        print("Virtual environment found but dependencies need attention. Repairing ...")
        _ensure_pip(venv_python)
        _pip_install(venv_python, ["--upgrade", "pip"], timeout=300)
        if not _repair_venv_deps(venv_python, project_root):
            # Last resort: try recreating the entire venv
            print("In-place repair failed. Recreating virtual environment from scratch ...")
            venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
        _sudo_pwd = None  # clear password after use
        print(f"Restarting in virtual environment ({venv_python}) ...")
        try:
            os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
        except (OSError, Exception):
            print("Warning: restart via execv failed. Running directly.")
            return

    # ── No valid venv at all → create one and restart inside it ──
    venv_python = _create_venv_and_install(sudo_password=_sudo_pwd)
    _sudo_pwd = None  # clear password after use
    print(f"Restarting in virtual environment ({venv_python}) ...")
    try:
        os.execv(venv_python, [venv_python, run_py] + sys.argv[1:])
    except (OSError, Exception):
        print("Warning: restart via execv failed. Running directly.")
        return


def _auto_configure():
    """Non-interactive auto-configuration: detect the best available provider
    and write config.yaml without any user interaction.
    Runs BEFORE venv bootstrap, so it uses only stdlib (no project imports).

    Detection priority:
      1. If config.yaml already has a valid provider+model+key, do nothing.
      2. If Ollama is installed and responding, use it (local, no key needed).
      3. Scan environment variables for cloud provider API keys.
      4. If nothing is found, write a minimal config and let the agent
         prompt interactively on next run.
    """
    project_root = Path(__file__).parent.resolve()
    config_path = project_root / "config.yaml"

    # ── Resolve yaml (PyYAML may not be installed yet) ──
    # FIX: Only install via pip if NOT in a venv — if we're in a venv, PyYAML
    # should already be present. Installing to system Python here would be
    # wasted since the subsequent venv bootstrap restarts the process.
    try:
        import yaml as _yaml  # noqa: F401
    except ImportError:
        if _is_in_venv():
            # Already in a venv but PyYAML is missing — this shouldn't happen
            # if the bootstrap worked, but handle gracefully.
            print("WARNING: PyYAML not found in virtual environment.")
            print("Run: pip install -e .  inside the venv to fix.")
            # Continue without writing config; use a dummy
            import types as _types
            _yaml = _types.ModuleType('yaml')
            _yaml.safe_load = lambda f: {}
            _yaml.dump = lambda *a, **kw: None
        else:
            # BUG FIX #15: Don't install PyYAML to system Python - wasteful
            # since the process restarts into venv. Use a minimal YAML parser
            # that handles the config structure we need.
            print("Using minimal YAML parser (PyYAML will be in venv).")
            import types as _types
            import re as _re
            _yaml = _types.ModuleType('yaml')
            def _minimal_safe_load(f):
                """Minimal YAML loader for flat/nested dict config files."""
                if hasattr(f, 'read'):
                    text = f.read()
                else:
                    with open(f, 'r', encoding='utf-8') as fh:
                        text = fh.read()
                # Use a simple approach: try to parse as JSON first (valid YAML)
                try:
                    import json as _json
                    return _json.loads(text)
                except Exception:
                    pass
                # Fallback: parse flat key: value pairs
                result = {}
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    m = _re.match(r'^(\w[\w.]*)\s*:\s*(.*)', line)
                    if m:
                        key = m.group(1)
                        val = m.group(2).strip().strip("'\"")
                        result[key] = val
                return result
            _yaml.safe_load = _minimal_safe_load
            def _minimal_dump(data, f=None, **kw):
                """Minimal YAML dumper for config dicts."""
                lines = []
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict):
                            lines.append(f"{k}:")
                            for k2, v2 in v.items():
                                lines.append(f"  {k2}: {v2}")
                        else:
                            lines.append(f"{k}: {v}")
                _out = "\n".join(lines) + "\n"
                if f:
                    f.write(_out)
                return _out
            _yaml.dump = lambda data, f=None, **kw: (
                f.write(_minimal_dump(data, **kw)) if f else _minimal_dump(data, **kw)
            )

    # ── Load existing config ──
    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as _f:
                _raw = _yaml.safe_load(_f)
            if _raw is not None and isinstance(_raw, dict):
                config = _raw
        except Exception:
            config = {}

    # Guard: ensure api section exists and is a dict (handles malformed config)
    _api_raw = config.get("api")
    if not isinstance(_api_raw, dict):
        config["api"] = {}
    api_cfg = config["api"]

    existing_provider = api_cfg.get("preferred_provider", "")
    existing_keys = api_cfg.get("api_keys") or {}
    if not isinstance(existing_keys, dict):
        existing_keys = {}
    existing_models = api_cfg.get("models") or {}
    if not isinstance(existing_models, dict):
        existing_models = {}

    # ── Check if config is already valid ──
    if existing_provider and isinstance(existing_provider, str):
        _prov_key = existing_keys.get(existing_provider, "")
        _prov_model = existing_models.get(existing_provider, "")
        if _prov_model and (existing_provider == "ollama" or _prov_key):
            print(f"[auto-config] Config already valid: provider={existing_provider}, model={_prov_model}")
            return  # nothing to do

    provider = None
    model = None
    api_key = None

    # ── 1. Try Ollama (local) ──
    import shutil
    ollama_path = shutil.which("ollama")
    if ollama_path:
        print("[auto-config] Ollama detected at:", ollama_path)
        try:
            import subprocess
            _proc = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if _proc.returncode == 0:
                _lines = [
                    l.strip() for l in _proc.stdout.strip().splitlines()
                    if l.strip() and not l.strip().startswith("NAME")
                ]
                if _lines:
                    model = _lines[0].split()[0]
                else:
                    model = ""
                    print("[auto-config] Ollama running but no models pulled. "
                          "Run: ollama pull llama3.2")
                if model:
                    provider = "ollama"
                    print(f"[auto-config] Using Ollama with model: {model}")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as _e:
            print(f"[auto-config] Ollama check failed: {_e}")

    # ── 2. Scan environment variables for cloud providers ──
    if provider is None:
        _CLOUD_PROVIDERS = [
            ("openai",      "gpt-4o",                  "OPENAI_API_KEY",      []),
            ("anthropic",   "claude-sonnet-4-20250514","ANTHROPIC_API_KEY",   []),
            ("google",      "gemini-2.5-flash",        "GOOGLE_API_KEY",      ["GEMINI_API_KEY"]),
            ("groq",        "llama-3.3-70b-versatile",  "GROQ_API_KEY",        []),
            ("deepseek",    "deepseek-chat",           "DEEPSEEK_API_KEY",    []),
            ("mistral",     "mistral-large-latest",     "MISTRAL_API_KEY",     []),
            ("together",    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY", []),
            ("openrouter",  "openrouter/auto",         "OPENROUTER_API_KEY",  []),
            ("cohere",      "command-r-plus",          "COHERE_API_KEY",      []),
            ("xai",         "grok-4.1",                "XAI_API_KEY",         []),
            ("minimax",     "MiniMax-Text-01",         "MINIMAX_API_KEY",     []),
            ("zhipuai",     "glm-5",                   "ZHIPUAI_API_KEY",     []),
        ]
        for _pk, _default_model, _env_var, _alt_env_vars in _CLOUD_PROVIDERS:
            _key = os.getenv(_env_var, "").strip()
            if not _key and _alt_env_vars:
                for _alt_ev in _alt_env_vars:
                    _key = os.getenv(_alt_ev, "").strip()
                    if _key:
                        break
            # Reject whitespace-only or empty keys (defense in depth)
            if _key and _key.strip():
                provider = _pk
                model = existing_models.get(_pk, _default_model)
                api_key = _key
                os.environ[_env_var] = _key
                print(f"[auto-config] Found {_pk} via env var {_env_var}")
                break

    # ── 3. Fallback: write minimal config so agent can prompt later ──
    if provider is None:
        provider = existing_provider or ""
        model = ""
        print("[auto-config] No provider auto-detected. Writing minimal config.")
        print("[auto-config] Run Clio-Agent (without --auto-config) for interactive setup.")
        print("[auto-config] Or set an API key env var (e.g. OPENAI_API_KEY=sk-...)")

    # ── Write config ──
    api_cfg["preferred_provider"] = provider
    if model:
        existing_models[provider] = model
        api_cfg["models"] = existing_models
    # FIX #25: Do NOT write API keys into config.yaml in plaintext.
    # Keys from environment variables are already available at runtime
    # via os.environ. Writing them to disk is a security risk.
    # Users who want persistence should use the settings manager or
    # set environment variables in their shell profile.
    if api_key and provider != "ollama":
        # Ensure the env var is set for the current process
        os.environ[_env_var] = api_key
        print(f"[auto-config] NOTE: API key for {provider} is set in the current")
        print(f"[auto-config]       process environment only. Add it to your shell")
        print(f"[auto-config]       profile (~/.bashrc, ~/.zshrc) to persist it.")

    with open(config_path, "w", encoding="utf-8") as _f:
        _yaml.dump(config, _f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"[auto-config] Config saved to {config_path}")
    print(f"[auto-config]   Provider : {provider or '(none)'}")
    print(f"[auto-config]   Model    : {model or '(none)'}")
    if api_key:
        print(f"[auto-config]   API Key  : *****{api_key[-4:]}")
    print()


def _quick_bootstrap_config():
    """Interactive quick setup: detect Ollama or configure a cloud provider.
    Runs BEFORE venv bootstrap, so it uses only stdlib (no project imports)."""
    project_root = Path(__file__).parent.resolve()
    config_path = project_root / "config.yaml"

    print()
    print("=" * 50)
    print("  Clio Agent - Quick Setup")
    print("=" * 50)
    print()

    # Resolve yaml (PyYAML may not be installed yet)
    try:
        import yaml as _yaml  # noqa: F401
    except ImportError:
        print("Installing PyYAML for config saving...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "PyYAML"],
            capture_output=True, text=True, timeout=120,
        )
        import yaml as _yaml  # noqa: F401

    # ── Ollama detection ──
    ollama_path = shutil.which("ollama")
    if ollama_path:
        print("Ollama detected at:", ollama_path)
        provider = "ollama"
        model = "llama3.2:3b"
        api_key = None
        print(f"  -> Using provider: Ollama, model: {model}")
    else:
        print("Ollama not found.")
        print()
        # Cloud provider list
        _CLOUD_PROVIDERS = [
            ("openai",      "OpenAI",       "gpt-4o",                  "OPENAI_API_KEY",           "https://platform.openai.com/api-keys"),
            ("anthropic",   "Anthropic",     "claude-sonnet-4-20250514","ANTHROPIC_API_KEY",       "https://console.anthropic.com/settings/keys"),
            ("google",      "Google Gemini","gemini-2.5-flash",        "GOOGLE_API_KEY",          "https://aistudio.google.com/app/apikey"),
            ("groq",        "Groq",         "llama-3.3-70b-versatile",  "GROQ_API_KEY",            "https://console.groq.com/keys"),
            ("deepseek",    "DeepSeek",     "deepseek-chat",           "DEEPSEEK_API_KEY",        "https://platform.deepseek.com/api_keys"),
            ("mistral",     "Mistral AI",   "mistral-large-latest",     "MISTRAL_API_KEY",         "https://console.mistral.ai/api-keys"),
            ("together",    "Together AI",  "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY", "https://api.together.ai/settings/api-keys"),
            ("openrouter",  "OpenRouter",   "openrouter/auto",         "OPENROUTER_API_KEY",      "https://openrouter.ai/keys"),
            ("cohere",      "Cohere",       "command-r-plus",          "COHERE_API_KEY",          "https://dashboard.cohere.com/api-keys"),
            ("xai",         "xAI Grok",     "grok-4.1",                "XAI_API_KEY",             "https://x.ai/api"),
            ("minimax",     "MiniMax",      "MiniMax-Text-01",         "MINIMAX_API_KEY",         "https://platform.minimaxi.com/user-center/basic-information/interface-key"),
            ("zhipuai",     "ZhipuAI",      "glm-5",                   "ZHIPUAI_API_KEY",         "https://open.bigmodel.cn/usercenter/apikeys"),
        ]
        print("Available cloud providers:")
        for i, (_pk, name, _mdl, _ek, _url) in enumerate(_CLOUD_PROVIDERS, 1):
            print(f"  {i:2d}. {name}")
        print()

        # Select provider — with retry limit to prevent infinite loop on non-TTY input
        _max_retries = 100
        _retries = 0
        while _retries < _max_retries:
            try:
                choice = input(f"Select a provider (1-{len(_CLOUD_PROVIDERS)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(_CLOUD_PROVIDERS):
                    break
            except (ValueError, EOFError):
                pass
            _retries += 1
            print("Invalid choice, try again.")
        else:
            print("Too many invalid attempts. Exiting quick setup.")
            return

        pk, name, default_model, env_var, key_url = _CLOUD_PROVIDERS[idx]
        provider = pk
        model = default_model

        # Check for existing env var
        _existing_key = os.getenv(env_var, "").strip()
        if _existing_key:
            api_key = _existing_key
            print(f"API key found in environment variable {env_var}.")
        else:
            # Ask for model override
            _new_model = input(f"Model [{default_model}]: ").strip()
            if _new_model:
                model = _new_model
            # Ask for API key
            print(f"Set model to: {model}")
            print(f"Get an API key from: {key_url}")
            _new_key = input(f"Enter your {name} API key: ").strip()
            api_key = _new_key if _new_key else None

    # ── Build and save config ──
    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as _f:
                _raw = _yaml.safe_load(_f)
            if _raw is not None and isinstance(_raw, dict):
                config = _raw
        except Exception:
            config = {}
    if not isinstance(config.get("api"), dict):
        config["api"] = {}
    config["api"]["preferred_provider"] = provider
    if not isinstance(config["api"].get("models"), dict):
        config["api"]["models"] = {}
    config["api"]["models"][provider] = model
    # BUG FIX #16: Do NOT write API key to config.yaml in plaintext.
    if api_key and provider != "ollama":
        _env_map = {
            "google": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "xai": "XAI_API_KEY", "meta": "META_API_KEY",
            "mistral": "MISTRAL_API_KEY", "microsoft": "AZURE_OPENAI_API_KEY",
            "cohere": "COHERE_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
            "together": "TOGETHER_API_KEY", "minimax": "MINIMAX_API_KEY",
            "zhipuai": "ZHIPUAI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
            "amazon": "AWS_ACCESS_KEY_ID",
        }
        _ev = _env_map.get(provider)
        if _ev:
            os.environ[_ev] = api_key

    with open(config_path, "w", encoding="utf-8") as _f:
        _yaml.dump(config, _f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print()
    print(f"Config saved to {config_path}")
    print(f"  Provider : {provider}")
    print(f"  Model    : {model}")
    if api_key:
        print(f"  API Key  : *****{api_key[-4:]}")
    print()
    print("Config saved! Starting setup...")
    print()


# Handle --quick-setup before venv bootstrap (needs no project imports)
if "--quick-setup" in sys.argv:
    sys.argv.remove("--quick-setup")
    _quick_bootstrap_config()

# Handle --auto-config before venv bootstrap (non-interactive auto-detection)
if "--auto-config" in sys.argv:
    sys.argv.remove("--auto-config")
    _auto_configure()

# Handle --help/-h before venv bootstrap (no imports needed)
if "--help" in sys.argv or "-h" in sys.argv:
    print("Clio Agent - AI Agent Runner")
    print()
    print("Usage: python3 run.py [options]")
    print()
    print("Options:")
    print("  --help, -h       Show this help message")
    print("  --auto-config    Auto-detect provider and configure")
    print("  --quick-setup    Interactive provider setup")
    print("  --check, -c      Run environment check")
    print("  --fix            Run environment check with auto-fix")
    print("  --repair         Repair environment: install all missing dependencies")
    print("  --health-check   Run self-diagnostic")
    print("  --debug          Enable debug mode")
    print("  --no-prompt      Use saved provider without prompting")
    print("  --setting        Force provider/model selection")
    print("  --telegram       Run in Telegram bot mode")
    print("  --discord        Run in Discord bot mode")
    print("  --install-global Install Clio-Agent globally")
    print("  --install-sdks   Install AI provider SDKs")
    print("  --sdk-status     Show SDK installation status")
    print()
    sys.exit(0)

# Handle diagnostic/fix commands before venv bootstrap so they work
# even when the venv is broken or missing.
if "--health-check" in sys.argv:
    sys.argv.remove("--health-check")
    try:
        _auto_bootstrap_venv()
    except SystemExit:
        pass
    _run_health_check()
    sys.exit(0)

if "--check" in sys.argv or "-c" in sys.argv:
    sys.argv.remove("--check") if "--check" in sys.argv else sys.argv.remove("-c")
    print("Running environment check (pre-venv) ...")
    try:
        _auto_bootstrap_venv()
    except SystemExit:
        pass
    _run_health_check()
    sys.exit(0)

if "--fix" in sys.argv:
    sys.argv.remove("--fix")
    print("Running environment check with auto-fix ...")
    _auto_bootstrap_venv()
    sys.exit(0)

if "--repair" in sys.argv:
    sys.argv.remove("--repair")
    repair_environment()
    sys.exit(0)

# Auto-bootstrap venv BEFORE any project imports
_auto_bootstrap_venv()

# Rich console for beautiful CLI output (with fallback)
try:
    from ai_agent.utils.rich_console import (
        get_console, Theme, status_panel, gradient_text,
        ShimmerLoader, StreamingPrinter
    )
except ImportError:
    # Minimal fallback when rich_console is not available
    class _FakeConsole:
        def print(self, *args, **kwargs): print(*args)
    def get_console(): return _FakeConsole()
    class Theme:
        ACCENT = TEXT_PRIMARY = TEXT_SECONDARY = TEXT_TERTIARY = INFO = SUCCESS = WARNING = ERROR = BORDER = BORDER_SUBTLE = ""
    def status_panel(*a, **kw): return str(a)
    def gradient_text(t): return t
    class ShimmerLoader:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class StreamingPrinter:
        def __init__(self, *a, **kw): pass
        def print(self, t): print(t)
        def flush(self): pass

# Project root & context directory
_PROJECT_ROOT = Path(__file__).parent.resolve()
_CONTEXT_DIR = _PROJECT_ROOT / ".context"
_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

def _emergency_save_context(signum, frame):
    """Save as much context as possible when interrupted by a signal."""
    import json as _j, time as _t
    try:
        data = {
            "status": f"Interrupted by signal {signum}",
            "goal": "", "user_prompt": "", "iteration_count": 0,
            "compressed_context": f"Process interrupted by signal {signum}.",
            "execution_log": [], "timestamp": _t.time(),
            "telegram_mode": False,
            "auxiliary": {"git_diff": "", "metadata": "", "errors": "", "log_tail_lines": 0},
        }
        _agent = globals().get("_global_agent_instance")
        if _agent is None:
            import inspect as _inspect
            for _frame_info in _inspect.stack():
                _locals = _frame_info[0].f_locals
                if "agent" in _locals and hasattr(_locals["agent"], "engine"):
                    _agent = _locals["agent"]
                    break
                _self = _locals.get("self")
                if _self is not None and hasattr(_self, "engine") and hasattr(_self.engine, "_current_context"):
                    _agent = _self
                    break
        if _agent is not None:
            _engine = getattr(_agent, "engine", None)
            if _engine is not None:
                _ctx = getattr(_engine, "_current_context", None)
                if _ctx is not None:
                    data["goal"] = getattr(_ctx, "current_goal", "") or ""
                    data["user_prompt"] = getattr(_ctx, "user_prompt", "") or ""
                    data["iteration_count"] = getattr(_ctx, "iteration_count", 0) or 0
                    data["telegram_mode"] = getattr(_ctx, "telegram_mode", False) or False
                    _exec_log = getattr(_ctx, "execution_log", None)
                    if _exec_log:
                        data["execution_log"] = _exec_log[-200:]
                    _meta = getattr(_ctx, "metadata", {}) or {}
                    data["auxiliary"]["metadata"] = _j.dumps(_meta, ensure_ascii=False)[:1000]
                    data["restart_provider"] = _meta.get("restart_provider", "")
                    data["restart_model"] = _meta.get("restart_model", "")
                    data["compressed_context"] = (
                        f"Process interrupted by signal {signum}. "
                        f"Last goal: {data['goal']}. "
                        f"Iterations: {data['iteration_count']}."
                    )
        try:
            from ai_agent.core_processing.terminal_history import get_terminal_history as _get_th
            _th = _get_th()
            _entries = getattr(getattr(_th, "terminal_session", None), "entries", [])
            if _entries:
                _log_lines = []
                for _e in _entries[-200:]:
                    _ts = _t.strftime("%H:%M:%S", _t.localtime(_e.timestamp))
                    _content = _e.content[:200] if _e.content else ""
                    _log_lines.append(f"[{_ts}] [{_e.entry_type.value}] {_content}")
                if not data["execution_log"]:
                    data["execution_log"] = _log_lines
                data["auxiliary"]["log_tail_lines"] = len(_log_lines)
        except Exception:
            pass
        try:
            import subprocess as _sp
            _gd = _sp.run(["git", "diff", "--stat"], capture_output=True, text=True, timeout=10)
            if _gd.returncode == 0 and _gd.stdout.strip():
                data["auxiliary"]["git_diff"] = _gd.stdout.strip()[:2000]
        except Exception:
            pass
        _errors = [l for l in data["execution_log"]
                   if any(m in l.lower() for m in ("error", "exception", "failed", "traceback"))]
        if _errors:
            data["auxiliary"]["errors"] = "\n".join(_errors)[:2000]
        with open(_CONTEXT_DIR / "exit_state.json", "w", encoding="utf-8") as f:
            _j.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

try:
    signal.signal(signal.SIGINT, _emergency_save_context)
    signal.signal(signal.SIGTERM, _emergency_save_context)
except Exception:
    pass

# Initialize resilience engine
try:
    from ai_agent.utils.resilience_engine import get_resilience_engine, ResilienceConfig
    _resilience_config = ResilienceConfig(
        max_retries=3, base_delay=2.0, backoff_factor=2.0,
        enable_self_healing=True, telegram_notify_on_error=True,
        telegram_notify_on_recovery=True, install_global_hook=True,
        log_all_errors=True, error_log_path="logs/resilience_errors.jsonl",
    )
    _resilience_engine = get_resilience_engine(_resilience_config)
except Exception:
    pass

VENV_DIR = "venv"
VENV_RESTART_FLAG = "--__venv_restarted__"
USER_RESTART_FLAG = "--__user_restarted__"
RESTART_ENV_PREFIX = "CLIO_RESTART_"
RESTART_MODE_ENV = f"{RESTART_ENV_PREFIX}MODE"
RESTART_PROVIDER_ENV = f"{RESTART_ENV_PREFIX}PROVIDER"
RESTART_MODEL_ENV = f"{RESTART_ENV_PREFIX}MODEL"
RESTART_API_KEY_ENV = f"{RESTART_ENV_PREFIX}API_KEY"

PROVIDER_API_KEY_ENV_VARS = {
    "google": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY", "xai": "XAI_API_KEY", "meta": "META_API_KEY",
    "mistral": "MISTRAL_API_KEY", "microsoft": "AZURE_OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY", "amazon": "AWS_ACCESS_KEY_ID",
    "cohere": "COHERE_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY", "minimax": "MINIMAX_API_KEY",
    "zhipuai": "ZHIPUAI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
}


def _get_api_key_for_provider(provider):
    if not provider or provider == "ollama":
        return None
    try:
        from ai_agent.utils.settings_manager import get_settings_manager
        settings_manager = get_settings_manager()
        try:
            api_key = settings_manager.get_api_key(provider)
            if api_key:
                return api_key
        except Exception:
            pass
        method_name = f"get_{provider}_api_key"
        if hasattr(settings_manager, method_name):
            api_key = getattr(settings_manager, method_name)()
            if api_key:
                return api_key
    except Exception:
        pass
    env_var = PROVIDER_API_KEY_ENV_VARS.get(provider)
    return os.getenv(env_var) if env_var else None


def _restore_restart_settings_from_env():
    provider = os.getenv(RESTART_PROVIDER_ENV)
    model = os.getenv(RESTART_MODEL_ENV)
    api_key = os.getenv(RESTART_API_KEY_ENV)
    if not provider:
        return
    try:
        from ai_agent.utils.settings_manager import get_settings_manager
        settings_manager = get_settings_manager()
        try:
            settings_manager.set_preferred_provider(provider)
        except Exception:
            pass
        if model:
            try:
                settings_manager.set_model(provider, model)
            except Exception:
                method_name = f"set_{provider}_model"
                if hasattr(settings_manager, method_name):
                    getattr(settings_manager, method_name)(model)
        if api_key:
            try:
                settings_manager.set_api_key(provider, api_key)
            except Exception:
                method_name = f"set_{provider}_api_key"
                if hasattr(settings_manager, method_name):
                    getattr(settings_manager, method_name)(api_key)
            env_var = PROVIDER_API_KEY_ENV_VARS.get(provider)
            if env_var:
                os.environ[env_var] = api_key
            if provider == "google":
                os.environ.setdefault("GEMINI_API_KEY", api_key)
    except Exception as e:
        print(f"\u26a0\ufe0f Could not restore restart settings: {e}")


def restart_with_current_settings(selected_mode, selected_provider, selected_model, debug_mode=False, max_iterations=None):
    env = os.environ.copy()
    env[RESTART_MODE_ENV] = selected_mode
    if selected_provider:
        env[RESTART_PROVIDER_ENV] = selected_provider
    else:
        env.pop(RESTART_PROVIDER_ENV, None)
    if selected_model:
        env[RESTART_MODEL_ENV] = selected_model
    else:
        env.pop(RESTART_MODEL_ENV, None)
    api_key = _get_api_key_for_provider(selected_provider)
    if api_key:
        env[RESTART_API_KEY_ENV] = api_key
        api_env_var = PROVIDER_API_KEY_ENV_VARS.get(selected_provider or "")
        if api_env_var:
            env[api_env_var] = api_key
        if selected_provider == "google":
            env.setdefault("GEMINI_API_KEY", api_key)
    else:
        env.pop(RESTART_API_KEY_ENV, None)
    new_args = [sys.executable, str(_PROJECT_ROOT / "run.py"), USER_RESTART_FLAG, "--no-prompt"]
    if debug_mode:
        new_args.append("--debug")
    if max_iterations is not None:
        new_args.extend(["--max-iterations", str(max_iterations)])
    try:
        os.execve(sys.executable, new_args, env)
    except OSError as e:
        print(f"Fatal: restart failed: {e}")
        sys.exit(1)


def is_in_virtual_environment():
    return (
        hasattr(sys, 'real_prefix') or
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) or
        os.getenv('VIRTUAL_ENV') is not None
    )


def get_venv_python_path():
    from pathlib import Path as _P
    project_root = Path(__file__).parent
    venv_path = project_root / VENV_DIR
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
    # Return the symlink path directly, NOT the resolved path.
    # The venv's bin/python knows it's in a venv and will use the venv's
    # site-packages. Resolving to the system python breaks this mechanism.
    return str(python_exe)


def show_help():
    """Display beautiful help page using Rich."""
    from rich.rule import Rule
    from rich.table import Table
    from rich.panel import Panel
    from rich.align import Align
    console = get_console()
    console.print()
    console.print(gradient_text("◆ Clio-Agent AI Agent Runner"))
    console.print()
    console.print(Rule(style=Theme.BORDER_SUBTLE))
    console.print(f"[bold {Theme.ACCENT}]Usage:[/] Clio-Agent [options]")
    console.print()
    console.print(f"[bold {Theme.TEXT_PRIMARY}]The agent is fully autonomous — no instruction needed.[/]")
    console.print(f"[{Theme.TEXT_SECONDARY}]It observes, explores, and acts on its own.[/]")
    console.print()

    # Features table
    table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
        border_style=Theme.BORDER,
    )
    table.add_column("icon", width=4)
    table.add_column("desc", style=Theme.TEXT_SECONDARY)
    features = [
        ("⚙️ ", "Virtual environment creation and management"),
        ("📦", "Dependency installation"),
        ("🤖", "Model selection (16 AI providers with model options)"),
        ("🌐", "Cross-platform compatibility"),
        ("🔄", "Self-bootstrapping"),
        ("🔍", "Environment detection and adaptive execution"),
    ]
    for icon, desc in features:
        table.add_row(icon, desc)
    console.print(Panel(table, title="Auto-Handled", border_style=Theme.ACCENT, padding=(1, 2)))

    # Model options
    console.print()
    console.print(f"[bold {Theme.ACCENT}]Model Options:[/]")
    providers = [
        ("🦊", "Ollama", "Local models (privacy-focused)", "Stable"),
        ("🌐", "Google", "Gemini models (enterprise-grade)", "Stable"),
        ("🤖", "OpenAI", "GPT models (advanced capabilities)", "Beta"),
        ("🧠", "Anthropic", "Claude models (strong reasoning)", "Beta"),
        ("🚀", "xAI", "Grok models (real-time knowledge)", "Beta"),
        ("🦙", "Meta", "Llama models (via Meta API)", "Beta"),
        ("⚡", "Groq", "Fast inference (Llama/Mixtral)", "Beta"),
        ("🔍", "DeepSeek", "Advanced reasoning models", "Beta"),
        ("🤝", "Together AI", "Open-source model hosting", "Beta"),
        ("☁️ ", "Microsoft", "GPT models via Azure", "Beta"),
        ("🌍", "Mistral AI", "Multilingual models", "Beta"),
        ("🏭", "Amazon Bedrock", "Titan/Nova models via AWS", "Beta"),
        ("🏢", "Cohere", "Command models for enterprise", "Beta"),
        ("🚀", "MiniMax", "M2-series models for productivity", "Beta"),
    ]
    prov_table = Table(
        show_header=False, box=None, padding=(0, 1), expand=True,
    )
    prov_table.add_column("icon", width=3)
    prov_table.add_column("name", width=18, style=f"bold {Theme.TEXT_PRIMARY}")
    prov_table.add_column("desc", width=40, style=Theme.TEXT_SECONDARY)
    prov_table.add_column("status", width=8, style=Theme.SUCCESS)
    for icon, name, desc, status in providers:
        prov_table.add_row(icon, name, desc, status)
    console.print(Panel(prov_table, border_style=Theme.BORDER, padding=(1, 2)))

    # Environment commands
    console.print()
    console.print(f"[bold {Theme.ACCENT}]Environment Commands:[/]")
    env_table = Table(show_header=False, box=None, padding=(0, 2))
    env_table.add_column("cmd", width=22, style=f"bold {Theme.INFO}")
    env_table.add_column("desc", style=Theme.TEXT_SECONDARY)
    env_cmds = [
        ("--check, -c", "Run environment check and show recommendations"),
        ("--fix", "Run environment check and auto-fix issues"),
        ("--install-sdks", "Install missing AI provider SDKs"),
        ("--sdk-status", "Show AI provider SDK installation status"),
    ]
    for cmd, desc in env_cmds:
        env_table.add_row(cmd, desc)
    console.print(env_table)

    console.print()
    console.print(f"[bold {Theme.ACCENT}]Options:[/]")
    opt_table = Table(show_header=False, box=None, padding=(0, 2))
    opt_table.add_column("opt", width=22, style=f"bold {Theme.INFO}")
    opt_table.add_column("desc", style=Theme.TEXT_SECONDARY)
    opts = [
        ("--help, -h", "Show this help message"),
        ("--health-check", "Run a self-diagnostic and exit"),
        ("--repair", "Repair environment: install all missing dependencies"),
        ("--debug", "Enable debug mode"),
        ("--no-prompt", "Use saved provider preference without prompting"),
        ("--setting", "Force interactive provider/model selection menu"),
        ("--sleep", "Compress context and restart immediately"),
        ("--self-heal", "Enable enhanced self-healing mode"),
        ("--telegram", "Run in Telegram bot mode"),
        ("--discord", "Run in Discord bot mode"),
        ("--watchdog", "Enable watchdog supervisor (auto-restart on crash)"),
        ("--supervisor", "Enable eternal supervisor (maximum resilience)"),
        ("--install-global", "Install Clio-Agent globally (any directory)"),
        ("--auto-config", "Auto-detect provider and configure non-interactively"),
    ]
    for opt, desc in opts:
        opt_table.add_row(opt, desc)
    console.print(opt_table)

    # Examples
    console.print()
    console.print(f"[bold {Theme.ACCENT}]Examples:[/]")
    examples = [
        ("Clio-Agent --auto-config", "# Auto-configure and start agent"),
        ("Clio-Agent", "# Start autonomous agent"),
        ('Clio-Agent "Take a screenshot"', "# Run a specific task"),
        ("Clio-Agent --check", "# Check environment"),
        ("Clio-Agent --install-sdks", "# Install SDKs"),
        ("Clio-Agent --install-global", "# Install globally (any dir)"),
    ]
    for cmd, comment in examples:
        console.print(f"  [{Theme.ACCENT}]$[/] [bold white]{cmd}[/] [{Theme.TEXT_TERTIARY}]{comment}[/]")

    console.print()
    console.print(Rule(style=Theme.BORDER_SUBTLE))
    console.print(
        f"[{Theme.TEXT_TERTIARY}]v3.0 • pip install -e .  •  Clio-Agent to launch[/]"
    )
    console.print()


def check_ollama_login_with_fallback():
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    from ai_agent.utils.environment_detector import EnvironmentDetector
    detector = EnvironmentDetector()
    ollama_available = detector._detect_ollama_available()
    if not ollama_available:
        error_message("Ollama is not installed or not in PATH")
        print(f"{Colors.BRIGHT_CYAN}Please install Ollama first: https://ollama.com/{Colors.RESET}")
        return False, "not_installed"
    needs_update = detector._detect_needs_ollama_update()
    has_whoami = detector._detect_ollama_has_whoami()
    if needs_update:
        warning_message("Ollama version is outdated (cloud models require 0.17.0+)")
        print(f"{Colors.CYAN}Local models will work, but cloud models require update.{Colors.RESET}")
        return True, "local_only"
    if has_whoami:
        try:
            result = subprocess.run(["ollama", "whoami"], capture_output=True, text=True, timeout=10)
            output_combined = (result.stdout or "") + (result.stderr or "")
            is_signed_in = (result.returncode == 0 and output_combined.strip() and "not signed in" not in output_combined.lower())
            if is_signed_in:
                success_message("Ollama is signed in")
                return True, "full"
            else:
                warning_message("Ollama is available but you are not signed in.")
                print(f"{Colors.CYAN}Cloud models require signin. Local models will work.{Colors.RESET}")
                return True, "needs_signin"
        except Exception:
            return True, "local_only"
    return True, "local_only"


def run_environment_check(fix_mode=False):
    from ai_agent.utils.environment_detector import detect_and_plan
    from ai_agent.utils.interactive_menu import Colors
    env_info, executor = detect_and_plan()
    import json
    from dataclasses import asdict
    report_path = Path("environment_report.json")
    with open(report_path, 'w') as f:
        json.dump(asdict(env_info), f, indent=2)
    print(f"\n\uD83D\uDCC4 Detailed report saved to: {report_path}")
    if fix_mode and executor.execution_plan:
        print(f"\n\uD83D\uDDA5\ufe0f Fix mode enabled - executing {len(executor.execution_plan)} steps")
        executor.execute_plan(interactive=True)
    elif executor.execution_plan:
        print(f"\n\uD83D\uDCA1 Run with --fix to automatically address these issues")
    return env_info, executor


def update_ollama():
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    import tempfile
    print(f"{Colors.CYAN}Updating Ollama...{Colors.RESET}")
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as tmp_script:
            script_path = tmp_script.name
        try:
            download_result = subprocess.run(
                ['curl', '-fsSL', 'https://ollama.com/install.sh'],
                capture_output=True, text=True, timeout=120)
            if download_result.returncode != 0:
                error_message(f"Failed to download Ollama install script: {download_result.stderr}")
                return False
            with open(script_path, 'w') as f:
                f.write(download_result.stdout)
            os.chmod(script_path, 0o755)
            result = subprocess.run(['bash', script_path], capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                success_message("Ollama updated successfully")
                return True
            else:
                error_message(f"Ollama update failed: {result.stderr}")
                return False
        finally:
            try:
                os.unlink(script_path)
            except Exception:
                pass
    except Exception as e:
        error_message(f"Error updating Ollama: {e}")
        return False


def prompt_for_api_key(provider_name, env_var_name, setup_url):
    import getpass
    from ai_agent.utils.interactive_menu import Colors, error_message, warning_message
    print(f"\n{Colors.BOLD}{Colors.CYAN}{provider_name} API Key Setup{Colors.RESET}")
    print(f"{Colors.CYAN}{'-' * 30}{Colors.RESET}")
    print(f"{Colors.WHITE}Environment variable: {env_var_name}{Colors.RESET}")
    if setup_url:
        print(f"{Colors.BRIGHT_CYAN}You can get one from: {setup_url}{Colors.RESET}")
    print()
    while True:
        try:
            api_key = getpass.getpass(f"{Colors.YELLOW}Enter your {provider_name} API key (or press Enter to cancel):{Colors.RESET} ")
            if not api_key.strip():
                warning_message("No API key provided. Configuration cancelled.")
                return None
            if len(api_key) < 10:
                error_message("API key seems too short. Please check your key.")
                continue
            return api_key
        except KeyboardInterrupt:
            print(f"\n{Colors.BRIGHT_YELLOW}Operation cancelled.{Colors.RESET}")
            return None
        except Exception as e:
            error_message(f"Error reading input: {e}")
            return None


def prompt_for_google_api_key():
    return prompt_for_api_key("Google", "GOOGLE_API_KEY or GEMINI_API_KEY", "https://aistudio.google.com/app/apikey")


def select_google_model():
    from ai_agent.utils.settings_manager import get_settings_manager
    from ai_agent.utils.curses_menu import get_curses_menu
    settings_manager = get_settings_manager()
    current_model = settings_manager.get_google_model()
    menu = get_curses_menu("\U0001f680 Select Gemini Model", "Choose your preferred Gemini model:")
    menu.add_item("Gemini 3 Flash", "Fast and efficient • Cost-effective for most tasks", "gemini-3-flash-preview", "\U0001f680")
    menu.add_item("Gemini 3.1 Pro", "Advanced reasoning • Best for complex problem-solving", "gemini-3.1-pro-preview", "\U0001f9e0")
    selected_model = menu.show()
    if selected_model is None:
        return current_model
    settings_manager.set_google_model(selected_model)
    return selected_model


def show_config_summary(provider, model=None):
    from rich.panel import Panel
    from rich.table import Table
    from ai_agent.utils.settings_manager import get_settings_manager
    settings_manager = get_settings_manager()
    console = get_console()

    provider_info = {
        "ollama": ("Ollama (Local)", settings_manager.get_ollama_model()),
        "google": ("Google Gemini", model or settings_manager.get_google_model()),
        "openai": ("OpenAI", model or settings_manager.get_openai_model()),
        "anthropic": ("Anthropic Claude", model or settings_manager.get_anthropic_model()),
        "xai": ("xAI Grok", model or settings_manager.get_xai_model()),
        "meta": ("Meta Llama", model or settings_manager.get_meta_model()),
        "groq": ("Groq", model or settings_manager.get_groq_model()),
        "deepseek": ("DeepSeek", model or settings_manager.get_deepseek_model()),
        "together": ("Together AI", model or settings_manager.get_together_model()),
        "microsoft": ("Microsoft Azure", model or settings_manager.get_microsoft_model()),
        "mistral": ("Mistral AI", model or settings_manager.get_mistral_model()),
        "amazon": ("Amazon Bedrock", model or settings_manager.get_amazon_model()),
        "cohere": ("Cohere", model or settings_manager.get_cohere_model()),
        "minimax": ("MiniMax", model or settings_manager.get_minimax_model()),
        "zhipuai": ("ZhipuAI", model or settings_manager.get_zhipuai_model()),
        "openrouter": ("OpenRouter", model or settings_manager.get_openrouter_model()),
    }

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("key", style=Theme.TEXT_SECONDARY, min_width=14)
    table.add_column("value", style="bold white")

    if provider in provider_info:
        provider_name, model_name = provider_info[provider]
        table.add_row("Provider", provider_name)
        if model_name:
            display_model = format_model_display_name(provider, model_name)
            table.add_row("Model", display_model)
    else:
        table.add_row("Provider", "Unknown")
        table.add_row("Model", model or "Unknown")

    console.print()
    console.print(
        Panel(
            table,
            title=f"[bold {Theme.SUCCESS}]Configuration Complete[/]",
            border_style=Theme.SUCCESS,
            padding=(1, 3),
        )
    )
    console.print()




def format_model_display_name(provider, model):
    model_display_map = {
        "google": {
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-3-flash-preview": "Gemini 3 Flash",
            "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
            "gemini-1.5-pro": "Gemini 1.5 Pro",
            "gemini-1.5-flash": "Gemini 1.5 Flash",
        },
        "openai": {
            "gpt-4o": "GPT-4o", "gpt-4o-mini": "GPT-4o Mini",
            "gpt-4-turbo": "GPT-4 Turbo", "gpt-3.5-turbo": "GPT-3.5 Turbo",
        },
        "anthropic": {
            "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
            "claude-3-opus-20240229": "Claude 3 Opus",
            "claude-3-sonnet-20240229": "Claude 3 Sonnet",
            "claude-3-haiku-20240307": "Claude 3 Haiku",
        },
        "minimax": {
            "minimax-m2.7": "MiniMax M2.7 (Latest)",
            "minimax-m2.5": "MiniMax M2.5",
            "minimax-m2": "MiniMax M2 (Legacy)",
        },
    }
    if provider in model_display_map and model in model_display_map[provider]:
        return model_display_map[provider][model]
    return model


def configure_google_provider():
    from ai_agent.utils.settings_manager import get_settings_manager
    from ai_agent.utils.interactive_menu import Colors, info_message, warning_message
    settings_manager = get_settings_manager()
    model = select_google_model()
    if model is None:
        model = settings_manager.get_google_model()
    info_message("Configuring Google Gemini Provider")
    import os
    existing_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not existing_key and not settings_manager.has_google_api_key():
        api_key = prompt_for_google_api_key()
        if api_key is None:
            warning_message("No API key provided - Google Gemini requires an API key.")
            return None, None
        settings_manager.set_google_api_key(api_key)
    settings_manager.set_preferred_provider("google")
    print(f"{Colors.GREEN}\u2713 Google Gemini configured successfully!{Colors.RESET}")
    return "google", model


def ensure_ollama_model_available(model_name):
    from ai_agent.utils.interactive_menu import Colors, success_message, error_message, warning_message
    from ai_agent.utils.ollama_error_handler import handle_ollama_error
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            available_models = result.stdout.strip().split('\n')
            if len(available_models) > 1:
                model_names = [line.split()[0] for line in available_models[1:] if line.strip()]
                if model_name in model_names:
                    success_message(f"Model {model_name} is already available")
                    return True
        warning_message(f"Model {model_name} not found locally, pulling...")
        print(f"{Colors.CYAN}This may take several minutes depending on model size and network speed.{Colors.RESET}")
        import threading, time
        stop_spinner = threading.Event()
        def spinner():
            spinner_chars = ['\u280b', '\u2819', '\u2839', '\u2838', '\u283c', '\u2834', '\u2826', '\u2827', '\u2807', '\u280f']
            i = 0
            while not stop_spinner.is_set():
                print(f"{Colors.CYAN}\r{spinner_chars[i % len(spinner_chars)]} Downloading {model_name}...{Colors.RESET}", end='', flush=True)
                time.sleep(0.1)
                i += 1
        spinner_thread = threading.Thread(target=spinner)
        spinner_thread.daemon = True
        spinner_thread.start()
        pull_result = None
        try:
            pull_result = subprocess.run(["ollama", "pull", model_name], capture_output=False, text=True, timeout=600)
        except KeyboardInterrupt:
            stop_spinner.set()
            print(f"\n{Colors.YELLOW}\u26a0\ufe0f Download cancelled by user{Colors.RESET}")
            return False
        finally:
            stop_spinner.set()
            spinner_thread.join(timeout=0.5)
            print(f"\r{' ' * 50}\r", end='', flush=True)
        if pull_result is None or pull_result.returncode != 0:
            return False
        success_message(f"\u2705 Successfully pulled Ollama model: {model_name}")
        return True
    except subprocess.TimeoutExpired:
        error_message(f"Timeout pulling model {model_name}")
        return False
    except FileNotFoundError:
        error_message("Ollama command not found")
        return False
    except Exception as e:
        error_message(f"Error ensuring model availability: {e}")
        return False


def configure_ollama_provider():
    from ai_agent.utils.config_flow import configure_provider_and_model
    provider, model, _ = configure_provider_and_model()
    return (provider, model) if provider else (None, None)


def select_model_provider(_recursion_depth=0):
    from ai_agent.utils.config_flow import configure_provider_and_model, sync_selection_to_config
    if _recursion_depth > 5:
        from ai_agent.utils.interactive_menu import error_message
        error_message("Too many configuration attempts. Please try again later.")
        return None, None
    provider, model, api_key = configure_provider_and_model()
    if provider is None:
        return None, None
    sync_selection_to_config(provider, model, api_key)
    if provider == "ollama":
        if not ensure_ollama_model_available(model):
            from ai_agent.utils.interactive_menu import warning_message
            warning_message("Model will be pulled on first use")
    show_config_summary(provider, model)
    return provider, model


def get_valid_api_key(prompt):
    from ai_agent.utils.interactive_menu import Colors, warning_message
    while True:
        try:
            api_key = input(prompt).strip()
        except EOFError:
            print()
            return None
        if not api_key:
            return None
        if len(api_key) < 10:
            warning_message("API key seems too short. Please check and try again.")
            continue
        return api_key


def _restore_terminal_history(project_root):
    try:
        _th_dir = project_root / "peripherals" / "terminal_history"
        if not _th_dir.exists():
            return []
        _sessions = sorted(
            [f for f in _th_dir.glob("*.json") if not f.name.endswith(".bak")],
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if not _sessions:
            return []
        _latest = _sessions[0]
        import json as _json
        with open(_latest, "r", encoding="utf-8") as _f:
            _data = _json.load(_f)
        _entries = _data.get("entries", [])
        if not _entries:
            return []
        _log_lines = []
        for _e in _entries[-200:]:
            _ts_raw = _e.get("timestamp", 0)
            try:
                _ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(_ts_raw)))
            except Exception:
                _ts = str(_ts_raw)
            _etype = _e.get("entry_type", "?")
            _content = _e.get("content", "")[:300]
            _rc = _e.get("return_code")
            _rc_str = f" (exit={_rc})" if _rc is not None else ""
            _log_lines.append(f"[{_ts}] [{_etype}]{_rc_str} {_content}")
        print(f"\u2705 Restored {len(_log_lines)} terminal history entries from {_latest.name}")
        return _log_lines
    except Exception as _exc:
        print(f"\u26a0\ufe0f Could not restore terminal history: {_exc}")
        return []


def _startup_cleanup():
    import shutil as _shutil
    from pathlib import Path as _Path
    # FIX #4: Use a reliable project root. _PROJECT_ROOT may not be defined
    # if cleanup runs early. Always derive from __file__ which is stable.
    try:
        _project_root = _Path(__file__).parent.resolve()
    except NameError:
        _project_root = _Path(".").resolve()
    _ctx_dir = _project_root / ".context"
    if _ctx_dir.exists():
        for _pattern in ["*.tmp", "*.bak", "*.swp"]:
            for _f in _ctx_dir.glob(_pattern):
                try:
                    _f.unlink()
                except Exception:
                    pass
    try:
        _usage = _shutil.disk_usage(str(_project_root))
        _free_gb = _usage.free / (1024 ** 3)
        if _free_gb < 1:
            print(f"\u26a0\ufe0f  Low disk space: {_free_gb:.1f}GB free")
    except Exception:
        pass


def _reset_config_yaml():
    import yaml as _yaml
    config_path = Path(__file__).parent.resolve() / "config.yaml"
    clean = {
        "api": {
            "preferred_provider": "",
            "api_keys": {
                "google": "", "groq": "", "openai": "", "anthropic": "", "xai": "",
                "meta": "", "mistral": "", "microsoft": "", "cohere": "", "deepseek": "",
                "together": "", "minimax": "", "zhipuai": "", "openrouter": "",
            },
            "local_endpoint": "http://localhost:11434",
            "local_model": "llama3.2:3b",
            "models": {
                "ollama": "llama3.2:3b", "google": "gemini-3.1-pro-preview",
                "groq": "llama-3.3-70b-versatile", "openai": "gpt-4o",
                "anthropic": "claude-opus-4-6-20260219", "xai": "grok-4.1",
                "meta": "llama-4-scout-17b-16e-instruct", "mistral": "mistral-large-latest",
                "microsoft": "gpt-4o", "amazon": "anthropic.claude-opus-4-6-20260219-v1:0",
                "cohere": "command-r-plus", "deepseek": "deepseek-chat",
                "together": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
                "minimax": "MiniMax-Text-01", "zhipuai": "glm-5",
                "openrouter": "openrouter/owl-alpha",
            },
            "timeout": 120, "max_retries": 3,
        },
        "security": {
            "enable_command_blocking": False, "enable_confirmation_prompts": False,
            "enable_sudo_warning": False, "enable_shell_pipe_warning": False,
            "enable_sandbox": True,
        },
        "execution": {
            "safety_mode": True, "dry_run": False, "verify_commands": True,
            "command_timeout": 1800, "task_timeout": 7200, "max_iterations": 500,
            "auto_recovery": True, "show_thought_log": True, "idle_behavior": "fairy",
        },
        "logging": {"level": "INFO", "file": "clio_agent.log", "json_format": False, "console": True},
        "cache": {"enabled": True, "max_size": 1000, "ttl": 3600, "persist_to_disk": True},
        "cost": {"daily_budget": None, "monthly_budget": None, "per_request_budget": None,
                  "warning_threshold": 0.8, "critical_threshold": 0.95},
        "performance": {"max_concurrent_tasks": 1, "memory_limit_mb": 1024},
        "user": {"name": "", "preferred_style": "detailed", "auto_confirm": False, "show_progress": True},
        "telegram": {
            "enabled": False, "bot_token": "", "bot_username": "", "api_id": 0, "api_hash": "",
            "session_name": "clio_agent_telegram", "authorized_users": [], "allowed_user_ids": [],
            "enable_input_listener": True, "max_history_length": 50, "bot_name": "Clio Agent",
        },
        "discord": {
            "enabled": False, "bot_token": "", "authorized_users": [],
            "allowed_user_ids": [], "max_history_length": 50, "bot_name": "Clio Agent",
        },
        "custom_system_prompt": "",
    }
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            _yaml.dump(clean, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print("config.yaml has been reset to defaults.")
    except Exception as e:
        print(f"Could not reset config.yaml: {e}")


def _run_health_check():
    """Beautiful health check using Rich panels."""
    import platform as _plat
    import shutil as _shutil
    from rich.panel import Panel
    from rich.align import Align
    console = get_console()

    console.print()
    console.print(
        Panel(
            Align.center("[bold]🩺 Clio Agent Self-Diagnostic[/]"),
            border_style=Theme.ACCENT,
            padding=(1, 4),
        )
    )

    rows = [
        ("Python", sys.version.split()[0]),
        ("Platform", f"{_plat.system()} {_plat.release()} ({_plat.machine()})"),
        ("PID", str(os.getpid())),
    ]
    console.print(status_panel("System", rows, border_color=Theme.ACCENT))

    # Disk
    try:
        _usage = _shutil.disk_usage(str(Path(__file__).parent))
        _free = _usage.free / (1024 ** 3)
        _total = _usage.total / (1024 ** 3)
        _pct = (_usage.used / _usage.total) * 100
        if _free > 5:
            dcolor = Theme.SUCCESS
            dicon = "✅"
        elif _free > 1:
            dcolor = Theme.WARNING
            dicon = "⚠️ "
        else:
            dcolor = Theme.ERROR
            dicon = "❌"
        drows = [
            ("Status", f"{dicon} {_free:.1f} GB free / {_total:.1f} GB total"),
            ("Usage", f"{_pct:.0f}% used"),
        ]
        console.print(status_panel("Disk", drows, border_color=dcolor))
    except Exception as e:
        console.print(status_panel("Disk", [("Error", str(e))], border_color=Theme.ERROR))

    # Memory
    try:
        import psutil as _ps
        _mem = _ps.virtual_memory()
        _free_gb = _mem.available / (1024 ** 3)
        _used_pct = _mem.percent
        if _used_pct < 85:
            mcolor = Theme.SUCCESS
            micon = "✅"
        elif _used_pct < 95:
            mcolor = Theme.WARNING
            micon = "⚠️ "
        else:
            mcolor = Theme.ERROR
            micon = "❌"
        mrows = [
            ("Status", f"{micon} {_free_gb:.1f} GB available"),
            ("Usage", f"{_used_pct:.0f}% used"),
        ]
        console.print(status_panel("Memory", mrows, border_color=mcolor))
    except Exception:
        console.print(status_panel("Memory", [("Status", "⚠️  psutil not available")], border_color=Theme.WARNING))

    # Venv
    _in_venv = (
        hasattr(sys, 'real_prefix')
        or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
        or os.getenv('VIRTUAL_ENV') is not None
    )
    vcolor = Theme.SUCCESS if _in_venv else Theme.ERROR
    vrows = [("Virtual Env", "✅ Yes" if _in_venv else "❌ No")]
    console.print(status_panel("Environment", vrows, border_color=vcolor))

    # Context files
    _ctx_dir = Path(__file__).parent / ".context"
    ctx_rows = []
    for label, fname in [("Sleep", "sleep_state.json"), ("Exit", "exit_state.json"), ("Heartbeat", "watchdog_heartbeat.json")]:
        fpath = _ctx_dir / fname
        if fpath.exists():
            ctx_rows.append((label, f"✅ {fname}"))
        else:
            ctx_rows.append((label, f"— (no {fname})"))
    console.print(status_panel("Context Files", ctx_rows, border_color=Theme.INFO))
    console.print()

# ── Forward declarations for the functions we kept from the original run.py ──
# These are the large model-selection functions that were in the original run.py.
# Including them here to keep run.py as the full-featured entry point.

def select_model_with_arrows(provider_name, models):
    from ai_agent.utils.curses_menu import get_curses_menu
    if provider_name.lower() == "openai":
        return select_openai_model_with_categories(models)
    menu = get_curses_menu(
        f"\U0001f916 {provider_name.upper()} Model Selection",
        "Choose your preferred model using arrow keys:")
    model_descriptions = {
        "gpt-5.4": "GPT-5.4 \u2022 OpenAI flagship \u2022 1M context \u2022 Best reasoning & coding",
        "gpt-5.4-mini": "GPT-5.4 Mini \u2022 Strong mini model \u2022 Coding & computer use",
        "gpt-5.4-nano": "GPT-5.4 Nano \u2022 Cheapest GPT-5.4 \u2022 High volume tasks",
        "gpt-4.1": "GPT-4.1 \u2022 1M context \u2022 Smarter & more efficient",
        "gpt-4.1-mini": "GPT-4.1 Mini \u2022 Fast & cost-effective",
        "gpt-4.1-nano": "GPT-4.1 Nano \u2022 Ultra-fast \u2022 Cheapest",
        "o3": "O3 \u2022 Advanced reasoning \u2022 STEM & complex tasks \u2022 200K context",
        "o4-mini": "O4 Mini \u2022 Fast reasoning \u2022 Cost-effective \u2022 200K context",
        "o3-mini": "O3 Mini \u2022 Efficient reasoning \u2022 200K context",
        "claude-opus-4-6-20260219": "Claude Opus 4.6 \u2022 Most capable \u2022 1M context \u2022 Agent teams",
        "claude-sonnet-4-6-20260219": "Claude Sonnet 4.6 \u2022 Near-Opus performance \u2022 Balanced",
        "claude-opus-4-5-20251125": "Claude Opus 4.5 \u2022 Outperforms humans on coding exams",
        "claude-sonnet-4-5-20251125": "Claude Sonnet 4.5 \u2022 Efficient & capable",
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro \u2022 2M context \u2022 Advanced agentic coding",
        "gemini-3-flash-preview": "Gemini 3 Flash \u2022 Frontier performance \u2022 Cost-effective",
        "gemini-3.1-flash-lite-preview": "Gemini 3.1 Flash Lite \u2022 Ultra-efficient \u2022 New",
        "gemini-2.5-pro": "Gemini 2.5 Pro \u2022 1M context \u2022 Advanced reasoning",
        "gemini-2.5-flash": "Gemini 2.5 Flash \u2022 Fast & efficient",
        "grok-4.1": "Grok 4.1 \u2022 State-of-the-art \u2022 #1 on LMArena \u2022 Real-time",
        "grok-4.1-fast": "Grok 4.1 Fast \u2022 Quick responses \u2022 Dec 2025",
        "grok-4.1-thinking": "Grok 4.1 Thinking \u2022 Deep reasoning mode",
        "llama-4-scout-17b-16e-instruct": "Llama 4 Scout \u2022 10M context \u2022 17B active \u2022 Text",
        "llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick \u2022 1M context \u2022 128 experts \u2022 Text",
        "meta-llama/Llama-4-Scout-17B-16E-Instruct": "Llama 4 Scout \u2022 Together hosted \u2022 10M context",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct": "Llama 4 Maverick \u2022 Together hosted \u2022 1M context",
        "deepseek-chat": "DeepSeek Chat \u2022 General conversation",
        "deepseek-coder": "DeepSeek Coder \u2022 Code generation specialist",
        "deepseek-reasoner": "DeepSeek Reasoner \u2022 Advanced reasoning",
        "llama-3.3-70b-versatile": "Llama 3.3 70B \u2022 Groq hosted \u2022 Ultra-fast",
        "llama-3.1-8b-instant": "Llama 3.1 8B \u2022 Groq hosted \u2022 Low latency",
        "mixtral-8x7b-32768": "Mixtral 8x7B \u2022 Groq hosted \u2022 MoE architecture",
        "mistral-large-latest": "Mistral Large \u2022 Latest version \u2022 Strong capabilities",
        "mistral-medium-latest": "Mistral Medium \u2022 Balanced performance",
        "mistral-small-latest": "Mistral Small \u2022 Fast & efficient",
        "command-r-plus": "Command R+ \u2022 Cohere's best \u2022 Long context",
        "command-r": "Command R \u2022 Balanced performance",
        "command": "Command \u2022 Legacy Cohere model",
        "glm-5": "GLM-5 \u2022 Zhipu AI latest \u2022 744B parameters \u2022 Advanced coding",
        "glm-5.1": "GLM-5.1 \u2022 Zhipu AI enhanced \u2022 Feb 2026 release",
        "glm-4-plus": "GLM-4 Plus \u2022 Strong general performance",
        "glm-4": "GLM-4 \u2022 Base model \u2022 Capable generalist",
        "MiniMax-Text-01": "MiniMax Text-01 \u2022 Latest general model",
        "abab6.5s": "ABAB 6.5S \u2022 MiniMax chat model",
    }
    for model in models:
        description = model_descriptions.get(model, f"{model} \u2022 Standard model")
        if "new" in description.lower():
            icon = "\u2728"
        elif "latest" in description.lower() or "newest" in description.lower():
            icon = "\U0001f680"
        else:
            icon = "\U0001f9e0"
        menu.add_item(model, description, model, icon)
    return menu.show()


def select_openai_model_with_categories(models):
    from ai_agent.utils.curses_menu import get_curses_menu
    menu = get_curses_menu("\U0001f916 OpenAI Model Selection", "Choose your preferred OpenAI model:")
    latest_models = [m for m in models if m in ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.3-codex", "gpt-oss-20b", "gpt-oss-120b"]]
    legacy_models = [m for m in models if m not in latest_models]
    for model in latest_models:
        description = get_model_description(model)
        icon = "\u2728" if "new" in description.lower() else ("\U0001f680" if "latest" in description.lower() else "\U0001f9e0")
        menu.add_item(model, description, model, icon)
    if legacy_models:
        menu.add_item("\U0001fda2 Legacy Models", f"Older models organized by type ({len(legacy_models)} models)", "category_legacy", "\U0001fda2")
    selected_category = menu.show()
    if selected_category == "category_legacy":
        return show_models_with_subcategories("Legacy Models", legacy_models, "\U0001fda2")
    return selected_category if selected_category in latest_models else None


def get_model_description(model):
    model_descriptions = {
        "gpt-5.4": "GPT-5.4 \u2022 OpenAI flagship \u2022 1M context \u2022 Best reasoning & coding",
        "gpt-5.4-mini": "GPT-5.4 Mini \u2022 Strong mini model \u2022 Coding & computer use",
        "gpt-5.4-nano": "GPT-5.4 Nano \u2022 Cheapest GPT-5.4 \u2022 High volume tasks",
        "gpt-4.1": "GPT-4.1 \u2022 1M context \u2022 Smarter & more efficient",
        "gpt-4.1-mini": "GPT-4.1 Mini \u2022 Fast & cost-effective",
        "gpt-4.1-nano": "GPT-4.1 Nano \u2022 Ultra-fast \u2022 Cheapest",
        "o3": "O3 \u2022 Advanced reasoning \u2022 STEM & complex tasks \u2022 200K context",
        "o4-mini": "O4 Mini \u2022 Fast reasoning \u2022 Cost-effective \u2022 200K context",
        "o3-mini": "O3 Mini \u2022 Efficient reasoning \u2022 200K context",
        "claude-opus-4-6-20260219": "Claude Opus 4.6 \u2022 Most capable \u2022 1M context \u2022 Agent teams",
        "claude-sonnet-4-6-20260219": "Claude Sonnet 4.6 \u2022 Near-Opus performance \u2022 Balanced",
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro \u2022 2M context \u2022 Advanced agentic coding",
        "grok-4.1": "Grok 4.1 \u2022 State-of-the-art \u2022 #1 on LMArena \u2022 Real-time",
        "llama-4-scout-17b-16e-instruct": "Llama 4 Scout \u2022 10M context \u2022 17B active \u2022 Text",
    }
    return model_descriptions.get(model, f"{model} \u2022 Standard model")


def show_models_in_category(category_name, models, category_icon):
    from ai_agent.utils.curses_menu import get_curses_menu
    if category_name in ["O Series Models", "GPT Series Models"]:
        return show_models_with_subcategories(category_name, models, category_icon)
    menu = get_curses_menu(f"{category_icon} {category_name}", "Select your preferred model:")
    model_descriptions = {
        "o3": "O3 \u2022 Advanced reasoning \u2022 STEM & complex tasks \u2022 200K context",
        "o4-mini": "O4 Mini \u2022 Fast reasoning \u2022 Cost-effective \u2022 200K context",
        "o3-mini": "O3 Mini \u2022 Efficient reasoning \u2022 200K context",
    }
    for model in models:
        description = model_descriptions.get(model, f"{model} \u2022 Standard model")
        icon = "\u2728" if "new" in description.lower() else ("\U0001f680" if "latest" in description.lower() else "\U0001f9e0")
        menu.add_item(model, description, model, icon)
    return menu.show()


def show_models_with_subcategories(category_name, models, category_icon):
    from ai_agent.utils.curses_menu import get_curses_menu
    menu = get_curses_menu(f"{category_icon} {category_name}", "Choose model type:")
    o_series = [m for m in models if m.startswith("o") and not m.startswith("omni")]
    gpt_series = [m for m in models if m.startswith("gpt") and not m.startswith("omni")]
    codex = [m for m in models if "codex" in m]
    other = [m for m in models if m not in o_series + gpt_series + codex]
    if o_series:
        menu.add_item("\U0001f9e0 O Series Models", f"O1, O3, O4 reasoning models ({len(o_series)} models)", "subcategory_o_series", "\U0001f9e0")
    if gpt_series:
        menu.add_item("\U0001f4ac GPT Series Models", f"GPT-3, GPT-4, GPT-5 legacy models ({len(gpt_series)} models)", "subcategory_gpt_series", "\U0001f4ac")
    if codex:
        menu.add_item("\U0001f4bb Codex Models", f"Code generation models ({len(codex)} models)", "subcategory_codex", "\U0001f4bb")
    if other:
        menu.add_item("\U0001f527 Other Models", f"Specialized and utility models ({len(other)} models)", "subcategory_other", "\U0001f527")
    selected = menu.show()
    if selected == "subcategory_o_series":
        return show_o_series_subcategories(o_series)
    elif selected == "subcategory_gpt_series":
        return show_gpt_series_subcategories(gpt_series)
    elif selected == "subcategory_codex":
        return show_models_in_category("Codex Models", codex, "\U0001f4bb")
    elif selected == "subcategory_other":
        return show_models_in_category("Other Models", other, "\U0001f527")
    return None


def show_o_series_subcategories(models):
    from ai_agent.utils.curses_menu import get_curses_menu
    menu = get_curses_menu("\U0001f9e0 O Series Models", "Choose O Series generation:")
    o1 = [m for m in models if m.startswith("o1")]
    o3 = [m for m in models if m.startswith("o3")]
    o4 = [m for m in models if m.startswith("o4")]
    if o1: menu.add_item("\U0001f7b9 O1 Series", f"First generation reasoning models ({len(o1)} models)", "subcategory_o1", "\U0001f7b9")
    if o3: menu.add_item("\U0001f7b9 O3 Series", f"Advanced reasoning models ({len(o3)} models)", "subcategory_o3", "\U0001f7b9")
    if o4: menu.add_item("\U0001f7b9 O4 Series", f"Next generation reasoning models ({len(o4)} models)", "subcategory_o4", "\U0001f7b9")
    sel = menu.show()
    if sel == "subcategory_o1": return show_models_in_category("O1 Series", o1, "\U0001f7b9")
    if sel == "subcategory_o3": return show_models_in_category("O3 Series", o3, "\U0001f7b9")
    if sel == "subcategory_o4": return show_models_in_category("O4 Series", o4, "\U0001f7b9")
    return None


def show_gpt_series_subcategories(models):
    from ai_agent.utils.curses_menu import get_curses_menu
    menu = get_curses_menu("\U0001f4ac GPT Series Models", "Choose GPT Series generation:")
    gpt3 = [m for m in models if "gpt-3.5" in m or (m.startswith("gpt-3") and "3.5" not in m)]
    gpt4 = [m for m in models if "gpt-4" in m]
    gpt5 = [m for m in models if "gpt-5" in m and m not in ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.3-codex"]]
    if gpt3: menu.add_item("\U0001f7b9 GPT-3 Series", f"Third generation models ({len(gpt3)} models)", "subcategory_gpt3", "\U0001f7b9")
    if gpt4: menu.add_item("\U0001f7b9 GPT-4 Series", f"Fourth generation models ({len(gpt4)} models)", "subcategory_gpt4", "\U0001f7b9")
    if gpt5: menu.add_item("\U0001f7b9 GPT-5 Legacy", f"Fifth generation legacy models ({len(gpt5)} models)", "subcategory_gpt5", "\U0001f7b9")
    sel = menu.show()
    if sel == "subcategory_gpt3": return show_models_in_category("GPT-3 Series", gpt3, "\U0001f7b9")
    if sel == "subcategory_gpt4": return show_models_in_category("GPT-4 Series", gpt4, "\U0001f7b9")
    if sel == "subcategory_gpt5": return show_models_in_category("GPT-5 Legacy", gpt5, "\U0001f7b9")
    return None


def check_venv_prerequisites():
    print("Checking virtual environment prerequisites...")
    try:
        import venv
        print("\u2713 venv module is available")
        return True
    except ImportError:
        print("\u2717 venv module is not available")
        return False


def create_virtual_environment():
    project_root = Path(__file__).parent
    venv_path = project_root / VENV_DIR
    print(f"Creating virtual environment at {venv_path}...")
    if venv_path.exists():
        venv_python = get_venv_python_path()
        if venv_python:
            try:
                result = subprocess.run([venv_python, "--version"], capture_output=True, text=True, timeout=10)
                if result.returncode != 0:
                    shutil.rmtree(venv_path)
                else:
                    pip_check = subprocess.run([venv_python, "-m", "pip", "--version"], capture_output=True, text=True, timeout=10)
                    if pip_check.returncode != 0:
                        shutil.rmtree(venv_path)
                    else:
                        print("Virtual environment already exists and is functional")
                        return True
            except Exception:
                shutil.rmtree(venv_path)
        else:
            shutil.rmtree(venv_path)
    try:
        result = subprocess.run([sys.executable, "-m", "venv", str(venv_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            if "ensurepip is not available" in error_msg or "python3-venv" in error_msg:
                print("\u2717 Virtual environment creation failed: python3-venv package not installed")
                print(f"  sudo apt install python3.{sys.version_info.minor}-venv")
                return False
            print(f"\u2717 Failed: {error_msg}")
            return False
        print("\u2713 Virtual environment created successfully")
        return True
    except subprocess.TimeoutExpired:
        print("\u2717 Virtual environment creation timed out")
        return False
    except Exception as e:
        print(f"\u2717 Error creating virtual environment: {e}")
        return False


def restart_in_venv():
    venv_python = get_venv_python_path()
    if not venv_python:
        print("Error: Could not find virtual environment Python executable")
        return False
    project_root = Path(__file__).parent
    # FIX #9: Filter out flags that should not be passed through on restart
    # to avoid infinite loops (e.g. --repair calling restart calling repair).
    _SKIP_FLAGS = {"--repair", "--fix", "--check", "-c", "--health-check",
                   "--quick-setup", "--auto-config", "--install-sdks", "--sdk-status"}
    _filtered_args = []
    _skip_next = False
    for _arg in sys.argv[1:]:
        if _skip_next:
            _skip_next = False
            continue
        if _arg in _SKIP_FLAGS:
            # Also skip the next arg if this flag takes a value
            if _arg in ("--max-iterations", "--instruction-file"):
                _skip_next = True
            continue
        if _arg.startswith("--max-iterations=") or _arg.startswith("--instruction-file="):
            continue
        _filtered_args.append(_arg)
    new_argv = [venv_python, str(project_root / "run.py"), VENV_RESTART_FLAG] + _filtered_args
    print(f"Restarting in virtual environment: {venv_python}")
    try:
        os.execv(venv_python, new_argv)
    except OSError as e:
        print(f"OS error restarting in virtual environment: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error restarting in virtual environment: {e}")
        sys.exit(1)
    return True


def install_dependencies():
    project_root = Path(__file__).parent
    venv_python = get_venv_python_path()
    if not venv_python:
        print("Error: Virtual environment Python not found")
        return False
    print("Installing dependencies...")
    try:
        import socket
        socket.create_connection(("pypi.org", 443), timeout=10)
        print("\u2713 Network connectivity OK")
    except Exception as e:
        print(f"Warning: Network connectivity issue: {e}")
    try:
        pip_check = subprocess.run([venv_python, "-m", "pip", "--version"], capture_output=True, text=True, timeout=10)
        if pip_check.returncode != 0:
            print("pip not found, bootstrapping with ensurepip...")
            ensurepip_result = subprocess.run([venv_python, "-m", "ensurepip", "--upgrade"], capture_output=True, text=True, timeout=60)
            if ensurepip_result.returncode != 0:
                print("Attempting get-pip.py...")
                import urllib.request, tempfile
                getpip_url = "https://bootstrap.pypa.io/get-pip.py"
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.py', delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    urllib.request.urlretrieve(getpip_url, tmp_path)
                    getpip_result = subprocess.run([venv_python, tmp_path], capture_output=True, text=True, timeout=120)
                    if getpip_result.returncode != 0:
                        print("Failed to bootstrap pip. Try deleting 'venv' and running again.")
                        return False
                    print("\u2713 pip installed via get-pip.py")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            else:
                print("\u2713 pip bootstrapped via ensurepip")
        else:
            print(f"\u2713 pip available: {pip_check.stdout.strip()}")
    except Exception as e:
        print(f"Error checking pip: {e}")
        return False
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"Retry {attempt + 1}/{max_retries} upgrading pip...")
            else:
                print("Upgrading pip...")
            result = subprocess.run([venv_python, "-m", "pip", "install", "--upgrade", "pip"], capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                print("\u2713 pip upgraded")
                break
            elif attempt == max_retries - 1:
                print(f"pip upgrade failed after {max_retries} attempts")
        except Exception:
            if attempt == max_retries - 1:
                print("pip upgrade error, continuing...")
    requirements_files = [
        project_root / "peripherals" / "requirements-core.txt",
        project_root / "peripherals" / "requirements.txt",
        project_root / "peripherals" / "requirements-optional.txt",
    ]
    for requirements_file in requirements_files:
        if requirements_file.exists():
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        print(f"Retry {attempt + 1}/{max_retries} installing {requirements_file.name}...")
                    else:
                        print(f"Installing from {requirements_file.name}...")
                    result = subprocess.run([venv_python, "-m", "pip", "install", "-r", str(requirements_file)], capture_output=True, text=True, timeout=600)
                    if result.returncode == 0:
                        print(f"\u2713 {requirements_file.name} installed")
                        if requirements_file.name in ("requirements.txt", "requirements-optional.txt"):
                            break
                        break
                    else:
                        if attempt == max_retries - 1:
                            print(f"{requirements_file.name} installation failed: {result.stderr.strip()}")
                            if requirements_file.name == "requirements-core.txt":
                                return False
                        else:
                            print(f"{requirements_file.name} attempt {attempt + 1} failed, retrying...")
                except Exception:
                    if attempt == max_retries - 1:
                        if requirements_file.name == "requirements-core.txt":
                            return False
    pyproject_file = project_root / "peripherals" / "pyproject.toml"
    if pyproject_file.exists():
        try:
            print("Installing project in editable mode...")
            result = subprocess.run([venv_python, "-m", "pip", "install", "-e", str(project_root)], capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                print("\u2713 project installed")
            else:
                print(f"project installation warning: {result.stderr}")
        except Exception as e:
            print(f"project installation error: {e}")
    return True


def bootstrap_environment():
    print("Bootstrapping environment...")
    if not check_venv_prerequisites():
        print(f"  sudo apt install python3.{sys.version_info.minor}-venv")
        return False
    if not create_virtual_environment():
        return False
    if not install_dependencies():
        return False
    print("\u2713 Environment bootstrap complete")
    return True



def _cmd_install_global():
    """Install Clio-Agent globally so Clio-Agent and clio-agent work from any directory."""
    import shutil
    import site

    project_root = Path(__file__).parent.resolve()
    console = get_console()

    console.print()
    console.print(gradient_text("Clio-Agent Global Installer"))
    console.print()

    # Strategy A: pipx (preferred, isolated, works on all platforms)
    pipx_path = shutil.which("pipx")
    if pipx_path:
        console.print("[bold]Strategy:[/] pipx (isolated, recommended)")
        result = subprocess.run(
            [pipx_path, "install", str(project_root)],
            capture_output=False, text=True,
        )
        if result.returncode == 0:
            console.print("[bold green]OK Installed via pipx[/]")
            _post_install_hint(pipx_path)
            return
        console.print("[yellow]pipx install failed, trying alternative...[/]")

    # Strategy B: pip install --break-system-packages
    console.print("[bold]Strategy:[/] pip --break-system-packages")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--break-system-packages",
         "-e", str(project_root)],
        capture_output=False, text=True,
    )
    if result.returncode == 0:
        console.print("[bold green]OK Installed via pip --break-system-packages[/]")
        _post_install_hint(None)
        return
    console.print("[yellow]pip --break-system-packages failed, trying alternative...[/]")

    # Strategy C: pip install --user
    console.print("[bold]Strategy:[/] pip --user")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user",
         "-e", str(project_root)],
        capture_output=False, text=True,
    )
    if result.returncode == 0:
        console.print("[bold green]OK Installed via pip --user[/]")
        _post_install_hint(None)
        return
    console.print("[yellow]pip --user failed, trying fallback...[/]")

    # Strategy D: Manual PATH entry in shell config
    console.print("[bold]Strategy:[/] Manual PATH configuration")
    venv_python = get_venv_python_path()
    venv_bin = str(Path(venv_python).parent) if venv_python else None
    _add_to_shell_path(project_root, venv_bin)


def _post_install_hint(pipx_path):
    console = get_console()
    console.print()
    console.print("[bold green]OK Clio-Agent is now available globally![/]")
    console.print()
    console.print("[bold]Try it from any directory:[/]")
    console.print("  [white]Clio-Agent --help[/]")
    console.print("  [white]clio-agent --help[/]")
    if pipx_path:
        console.print()
        console.print("[dim]Managed by pipx. To uninstall: pipx uninstall clio_agent-cli[/]")


def _add_to_shell_path(project_root, venv_bin=None):
    import site
    console = get_console()

    bin_dirs = []
    if venv_bin:
        bin_dirs.append(venv_bin)

    user_base = site.getuserbase() if hasattr(site, "getuserbase") else None
    if user_base:
        for suffix in ("bin", "Scripts"):
            p = Path(user_base) / suffix
            if p.exists():
                bin_dirs.append(str(p))

    for p in site.getsitepackages():
        for suffix in ("bin", "Scripts"):
            d = Path(p).parent / suffix
            if d.exists():
                bin_dirs.append(str(d))

    bin_dirs = list(dict.fromkeys(bin_dirs))
    if not bin_dirs:
        console.print("[red]Could not determine bin directory.[/]")
        console.print("Add the following to your shell config manually:")
        console.print(f'  export PATH="{project_root / "venv" / "bin"}:$PATH"')
        return

    home = Path.home()
    shell_configs = []
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        shell_configs.append(home / ".zshrc")
    elif "bash" in shell:
        for candidate in (".bashrc", ".bash_profile", ".profile"):
            p = home / candidate
            if p.exists():
                shell_configs.append(p)
                break
        if not shell_configs:
            shell_configs.append(home / ".bashrc")
    shell_configs.append(home / ".profile")

    seen = set()
    unique = []
    for p in shell_configs:
        if str(p) not in seen:
            seen.add(str(p))
            unique.append(p)
    shell_configs = unique

    added_any = False
    for bin_dir in bin_dirs:
        path_line = f'export PATH="{bin_dir}:$PATH"'
        for cfg in shell_configs:
            if _append_to_file_if_missing(cfg, path_line):
                console.print(f"[green]OK Added to {cfg}[/]")
                added_any = True
                break
        if added_any:
            break

    if added_any:
        console.print()
        console.print("[bold green]OK PATH updated![/]")
        console.print("[yellow]Restart your terminal or run:[/]")
        console.print(f"  [white]source {shell_configs[0]}[/]")
    else:
        console.print("[yellow]Could not auto-update shell config.[/]")
        console.print("Add this line to your shell config manually:")
        console.print(f"  [white]{path_line}[/]")


def _append_to_file_if_missing(filepath, line):
    """Append a line to a file only if it's not already present.

    FIX #15: Use line-by-line exact matching instead of substring matching
    to avoid false positives from comments or partial matches.
    """
    filepath = Path(filepath)
    stripped_line = line.strip()
    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        # Check for exact line match (stripped), not substring
        for existing_line in existing.splitlines():
            if existing_line.strip() == stripped_line:
                return False
    with open(filepath, "a", encoding="utf-8") as f:
        f.write("\n# Clio-Agent\n" + line + "\n")
    return True


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        show_help()
        sys.exit(0)

    if "--install-global" in sys.argv:
        sys.argv.remove("--install-global")
        _cmd_install_global()
        sys.exit(0)

    _startup_cleanup()

    if "--health-check" in sys.argv:
        _run_health_check()
        sys.exit(0)

    if "--supervisor" in sys.argv:
        sys.argv.remove("--supervisor")
        print("\U0001f6e1\ufe0f Starting Eternal Supervisor")
        try:
            from ai_agent.utils.eternal_supervisor import start_eternal_agent
            start_eternal_agent(agent_args=sys.argv[1:])
        except ImportError as e:
            print(f"\u26a0\ufe0f Supervisor not available: {e}")
        except Exception as e:
            print(f"\u274c Supervisor failed: {e}")
            sys.exit(1)
        return

    _sleep_requested = False
    if "--sleep" in sys.argv:
        sys.argv.remove("--sleep")
        _sleep_requested = True
        print("\U0001f6cf Sleep requested")

    _self_heal_mode = False
    if "--self-heal" in sys.argv:
        sys.argv.remove("--self-heal")
        _self_heal_mode = True
        print("\U0001fa79 Enhanced self-healing mode enabled")

    if "--check" in sys.argv or "-c" in sys.argv:
        print("\U0001f50d Running environment check...")
        run_environment_check(fix_mode=False)
        sys.exit(0)

    if "--fix" in sys.argv:
        print("\U0001f527 Running environment check with auto-fix...")
        run_environment_check(fix_mode=True)
        sys.exit(0)

    if "--repair" in sys.argv:
        sys.argv.remove("--repair")
        repair_environment()
        sys.exit(0)

    if VENV_RESTART_FLAG in sys.argv:
        sys.argv.remove(VENV_RESTART_FLAG)
    if not _is_in_venv():
        print("WARNING: Not running inside a virtual environment.")
        print("         Attempting to run directly — dependencies will be checked on first import.")
        # Try to import core deps; if missing, install them in the current environment
        _missing_deps = []
        for _mod, (_pkg, _minv) in CORE_DEPENDENCIES.items():
            try:
                __import__(_mod)
            except ImportError:
                _missing_deps.append(_pkg)
        if _missing_deps:
            print(f"Installing missing core dependencies: {', '.join(_missing_deps)}")
            for _pkg in _missing_deps:
                try:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", _pkg],
                        capture_output=True, text=True, timeout=300,
                    )
                except Exception as _e:
                    print(f"  Failed to install {_pkg}: {_e}")
        print("Continuing without virtual environment...")

    _project_root = Path(__file__).parent.resolve()

    try:
        import importlib, sys as _sys
        _src_str = str(_project_root / "src")
        if _src_str not in _sys.path:
            _sys.path.insert(0, _src_str)
        _cm = importlib.import_module("ai_agent.core_processing.context_manager")
        _cm.set_context_dir(_project_root / ".context")
    except Exception:
        pass

    _sleep_state = None
    _exit_state = None
    try:
        from ai_agent.core_processing.autonomous_loop_engine import AutonomousLoopEngine
        _sleep_state = AutonomousLoopEngine.check_and_handle_sleep_restart(project_root=_project_root)
        if _sleep_state:
            print("\u2705 Restored context from sleep: " + str(_sleep_state.get("goal", "")))
    except Exception:
        pass

    try:
        import json as _json
        _exit_state_file = _project_root / ".context" / "exit_state.json"
        if _exit_state_file.exists():
            with open(_exit_state_file, "r", encoding="utf-8") as _f:
                _exit_state = _json.load(_f)
            print("\u2705 Restored context from previous exit: " + str(_exit_state.get("goal", "")))
    except Exception:
        _exit_state = None

    if _sleep_state and _exit_state:
        _sleep_ts = _sleep_state.get("timestamp", 0) or 0
        _exit_ts = _exit_state.get("timestamp", 0) or 0
        if _exit_ts > _sleep_ts:
            _sleep_state = None
        else:
            _exit_state = None

    _terminal_history_log = _restore_terminal_history(_project_root)

    _context_displayed = False
    if _sleep_state or _exit_state:
        try:
            from ai_agent.core_processing.context_manager import display_context_in_terminal
            display_context_in_terminal()
            _context_displayed = True
        except Exception:
            pass
    if not _context_displayed:
        try:
            from ai_agent.core_processing.context_manager import context_files_exist, get_context_summary
            if context_files_exist():
                print(get_context_summary())
        except Exception:
            pass

    _context_log_file = _project_root / ".context" / "context_log.txt"
    if _context_log_file.exists():
        try:
            print("\n" + "=" * 60)
            print("  \U0001f4cb CONTEXT LOG")
            print("=" * 60)
            print(_context_log_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if _terminal_history_log:
        try:
            print("\n" + "=" * 60)
            print("  \U0001f4cb TERMINAL HISTORY")
            print("=" * 60)
            for _line in _terminal_history_log[-80:]:
                print(f"  {_line}")
        except Exception:
            pass

    _resume_instruction = None
    _restore_state = _sleep_state or _exit_state
    if _restore_state or _terminal_history_log:
        _compressed = _restore_state.get("compressed_context", "") if _restore_state else ""
        _goal = _restore_state.get("goal", "") if _restore_state else ""
        _iterations = _restore_state.get("iteration_count", 0) if _restore_state else 0
        _aux = _restore_state.get("auxiliary", {}) if _restore_state else {}
        _errors = _aux.get("errors", "(none)")
        _git_diff = _aux.get("git_diff", "(none)")
        if not _compressed:
            _clog = _project_root / ".context" / "context_log.txt"
            if _clog.exists():
                try:
                    _compressed = _clog.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        if _restore_state:
            _source = "sleep" if _sleep_state else "exit"
            _resume_instruction = (
                f"I have just been restarted (previously exited via {_source}). "
                "I must resume working immediately.\n\n"
                f"## Compressed Context from Before {_source.capitalize()}\n"
                f"{_compressed}\n\n"
                "## Summary\n"
                f"- Goal before {_source}: {_goal}\n"
                f"- Iterations completed: {_iterations}\n"
                f"- Errors encountered: {_errors}\n"
                f"- Git changes: {_git_diff}\n\n"
            )
        else:
            _resume_instruction = "I have just been restarted. Resume working immediately.\n\n"
        if _terminal_history_log:
            _history_block = "\n".join(_terminal_history_log[-100:])
            _resume_instruction += (
                "## TERMINAL EXECUTION LOG\n"
                f"{_history_block}\n\n"
            )
        _resume_instruction += (
            "Resume work immediately from where I left off. Act immediately — do not wait.\n\n"
            "## TELEGRAM REPORTING (MANDATORY)\n"
            "Send progress updates via telegram() every 5-10 iterations."
        )
        if _restore_state:
            if not os.getenv(RESTART_PROVIDER_ENV) and _restore_state.get("restart_provider"):
                os.environ[RESTART_PROVIDER_ENV] = _restore_state["restart_provider"]
            if not os.getenv(RESTART_MODEL_ENV) and _restore_state.get("restart_model"):
                os.environ[RESTART_MODEL_ENV] = _restore_state["restart_model"]

    current_dir = Path(__file__).parent
    src_dir = current_dir / "src"
    _src_str = str(src_dir)
    if _src_str not in sys.path:
        sys.path.insert(0, _src_str)

    try:
        from ai_agent.core_processing.context_manager import set_context_dir
        set_context_dir(_project_root / ".context")
    except Exception:
        pass

    _restore_restart_settings_from_env()
    # Check for plaintext API keys in config.yaml
    try:
        import yaml as _yaml
        _cfg_path = current_dir / "config.yaml"
        if _cfg_path.exists():
            with open(_cfg_path, 'r') as _f:
                _cfg_check = _yaml.safe_load(_f) or {}
            _keys = _cfg_check.get('api', {}).get('api_keys', {}) or {}
            if any(v for v in _keys.values() if v):
                from ai_agent.utils.interactive_menu import warning_message
                warning_message("API keys stored in plaintext config.yaml - consider using env vars")
    except Exception:
        pass
    if USER_RESTART_FLAG in sys.argv:
        sys.argv.remove(USER_RESTART_FLAG)
        print("\u2713 Restarted with previous provider, model, and API settings")

    instruction_args = []
    skip_next_arg = False
    flags_with_values = {"--max-iterations", "--instruction-file"}
    for arg in sys.argv[1:]:
        if skip_next_arg:
            skip_next_arg = False
            continue
        if arg in flags_with_values:
            skip_next_arg = True
            continue
        if arg.startswith("--"):
            continue
        instruction_args.append(arg)
    instruction = " ".join(instruction_args) if instruction_args else None
    _original_instruction = instruction

    if _resume_instruction:
        instruction = _resume_instruction

    if "--instruction-file" in sys.argv:
        try:
            idx = sys.argv.index("--instruction-file")
            if idx + 1 < len(sys.argv):
                instruction_file = sys.argv[idx + 1]
                if Path(instruction_file).exists():
                    instruction = Path(instruction_file).read_text(encoding="utf-8").strip()
        except (ValueError, IndexError):
            pass

    if _sleep_requested and not _resume_instruction:
        instruction = "Execute sleep immediately. Your very first command must be: sleep"
        print("Sleep instruction injected")

    sdk_only_commands = ["--install-sdks", "--sdk-status"]
    if any(flag in sys.argv for flag in sdk_only_commands):
        pass

    debug_mode = "--debug" in sys.argv

    if "--install-sdks" in sys.argv:
        print("\U0001f527 Installing missing AI provider SDKs...")
        try:
            subprocess.run([sys.executable, str(current_dir / "peripherals" / "manage_sdks.py"), "install"], capture_output=False, text=True, cwd=current_dir)
        except Exception as e:
            print(f"\u274c Failed: {e}")
        sys.exit(0)

    if "--sdk-status" in sys.argv:
        print("\U0001f50d Checking SDK status...")
        try:
            subprocess.run([sys.executable, str(current_dir / "peripherals" / "manage_sdks.py"), "status"], capture_output=False, text=True, cwd=current_dir)
        except Exception as e:
            print(f"\u274c Failed: {e}")
        sys.exit(0)

    requested_telegram_cli = "--telegram" in sys.argv
    requested_discord_cli = "--discord" in sys.argv

    # Determine the selected mode from (in priority order):
    #   1. Explicit CLI flags (--telegram / --discord)
    #   2. Restart environment variable (set by a previous session)
    #   3. Config file (telegram.enabled / discord.enabled)
    #   4. Default to "console" (no bot)
    if requested_telegram_cli:
        sys.argv.remove("--telegram")
        selected_mode = "telegram"
    elif requested_discord_cli:
        sys.argv.remove("--discord")
        selected_mode = "discord"
    else:
        restart_mode = os.getenv(RESTART_MODE_ENV, "")
        if restart_mode in ("telegram", "discord"):
            selected_mode = restart_mode
        else:
            # Check config for enabled bot mode only when no explicit CLI flag
            import yaml as _yaml_cfg
            _cfg_path_mode = current_dir / "config.yaml"
            _telegram_cfg_enabled = False
            _discord_cfg_enabled = False
            try:
                if _cfg_path_mode.exists():
                    with open(_cfg_path_mode, "r", encoding="utf-8") as _fmc:
                        _cfg_mc = _yaml_cfg.safe_load(_fmc) or {}
                    _telegram_cfg_enabled = bool(_cfg_mc.get("telegram", {}).get("enabled", False))
                    _discord_cfg_enabled = bool(_cfg_mc.get("discord", {}).get("enabled", False))
            except Exception:
                pass
            if _telegram_cfg_enabled and not _discord_cfg_enabled:
                selected_mode = "telegram"
            elif _discord_cfg_enabled and not _telegram_cfg_enabled:
                selected_mode = "discord"
            elif _telegram_cfg_enabled and _discord_cfg_enabled:
                # Both enabled: default to Telegram as primary
                selected_mode = "telegram"
            else:
                selected_mode = "console"

    force_reconfigure = "--setting" in sys.argv

    selected_provider = os.getenv(RESTART_PROVIDER_ENV)
    selected_model = os.getenv(RESTART_MODEL_ENV)
    if selected_provider:
        print(f"\nUsing restart provider: {selected_provider}")

    if (selected_provider is None or force_reconfigure) and "--no-prompt" not in sys.argv:
        result = select_model_provider()
        if isinstance(result, tuple) and len(result) == 2:
            selected_provider, selected_model = result
        else:
            selected_provider = result
    elif selected_provider is None:
        try:
            from ai_agent.utils.config import ConfigManager
            config_path = current_dir / "config.yaml"
            config_manager = ConfigManager(str(config_path)) if config_path.exists() else None
            if config_manager:
                config = config_manager.load_config()
                if hasattr(config, 'api') and hasattr(config.api, 'preferred_provider'):
                    selected_provider = config.api.preferred_provider
        except Exception as e:
            print(f"\u26a0\ufe0f Could not load config: {e}")
        if not selected_provider:
            from ai_agent.utils.settings_manager import get_settings_manager
            settings_manager = get_settings_manager()
            selected_provider = settings_manager.get_preferred_provider()
        if not selected_provider:
            if "--no-prompt" in sys.argv:
                from ai_agent.utils.settings_manager import get_settings_manager
                settings_manager = get_settings_manager()
                _saved_ollama_model = settings_manager.get_ollama_model()
                # Detect Ollama: check CLI in PATH and verify service responds
                _ollama_available = False
                if shutil.which("ollama"):
                    try:
                        _proc = subprocess.run(
                            ["ollama", "list"],
                            capture_output=True, text=True, timeout=10
                        )
                        if _proc.returncode == 0:
                            _ollama_available = True
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass
                if _ollama_available:
                    selected_provider = "ollama"
                    selected_model = _saved_ollama_model
                    if not selected_model:
                        try:
                            _proc = subprocess.run(
                                ["ollama", "list"],
                                capture_output=True, text=True, timeout=10
                            )
                            _lines = [l.strip() for l in _proc.stdout.strip().splitlines()
                                      if l.strip() and not l.strip().startswith("NAME")]
                            if _lines:
                                selected_model = _lines[0].split()[0]
                            else:
                                selected_model = "llama3.2:3b"
                        except Exception:
                            selected_model = "llama3.2:3b"
                    print(f"\n\u2139\ufe0f Auto-selected Ollama with model: {selected_model}")
                else:
                    _cloud_providers = [
                        ("openai", "OPENAI_API_KEY", "gpt-4o-mini"),
                        ("anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-4-20250514"),
                        ("mistral", "MISTRAL_API_KEY", "mistral-large-latest"),
                        ("together", "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
                        ("openrouter", "OPENROUTER_API_KEY", "openrouter/auto"),
                        ("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
                        ("groq", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
                        ("gemini", "GOOGLE_API_KEY", "gemini-2.0-flash"),
                    ]
                    _found = False
                    for _pk, _ev, _dm in _cloud_providers:
                        if os.environ.get(_ev, "").strip():
                            selected_provider = _pk
                            selected_model = _dm
                            print(f"\n\u2139\ufe0f Auto-selected {_pk} from environment variable {_ev}")
                            _found = True
                            break
                    if not _found:
                        print("\n\u26a0\ufe0f No provider configured and Ollama is not available.")
                        print("   Options:")
                        print("   1. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh")
                        print("   2. Run 'Clio-Agent' (without --no-prompt) for interactive setup")
                        print("   3. Set a provider via config.yaml or environment variables")
                        sys.exit(1)
            else:
                print("\u274c No provider configured. Run Clio-Agent without --no-prompt to configure.")
                sys.exit(1)

    if _original_instruction and _original_instruction.strip() == "/restart":
        print("\U0001f504 Restarting with current settings...")
        restart_with_current_settings(selected_mode, selected_provider, selected_model, debug_mode)

    try:
        import yaml
        _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        if os.path.exists(_cfg_path):
            with open(_cfg_path, "r") as _cfg_f:
                _cfg = yaml.safe_load(_cfg_f)
            if _cfg and isinstance(_cfg, dict):
                _api_cfg = _cfg.get("api", {})
                if isinstance(_api_cfg, dict):
                    _keys = [k for k, v in _api_cfg.get("api_keys", {}).items() if v]
                    if _keys:
                        print(f"\n\u26a0\ufe0f Security Notice: API keys detected in config.yaml for: {', '.join(_keys)}. Consider using environment variables instead.")
    except Exception:
        pass

    print(f"\nClio Agent starting in {selected_mode.capitalize()} mode...")
    max_iterations = 0
    if "--max-iterations" in sys.argv:
        try:
            idx = sys.argv.index("--max-iterations")
            if idx + 1 < len(sys.argv):
                max_iterations = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    try:
        from ai_agent.user_interface.five_phase_app import AutonomousAIAgent
        from ai_agent.external_integration.telegram_bot import create_telegram_bot
        from ai_agent.external_integration.discord_bot import create_discord_bot

        telegram_bot = None
        discord_bot = None
        config_path = current_dir / "config.yaml"

        if selected_mode == "discord":
            try:
                discord_bot = create_discord_bot(str(config_path) if config_path.exists() else None)
            except Exception:
                discord_bot = None
            if discord_bot:
                print("\u2713 Discord bot initialized")
            # Also try Telegram as secondary
            try:
                telegram_bot = create_telegram_bot(str(config_path) if config_path.exists() else None)
            except Exception:
                telegram_bot = None
            if telegram_bot:
                print("\u2713 Telegram bot initialized (secondary)")
        else:
            try:
                telegram_bot = create_telegram_bot(str(config_path) if config_path.exists() else None)
            except Exception:
                telegram_bot = None
            if telegram_bot:
                print("\u2713 Telegram bot initialized")
            # Also try Discord as secondary
            try:
                discord_bot = create_discord_bot(str(config_path) if config_path.exists() else None)
            except Exception:
                discord_bot = None
            if discord_bot:
                print("\u2713 Discord bot initialized (secondary)")

        def _config_enabled(section_name):
            try:
                import yaml as _yaml
                if not config_path.exists():
                    return False
                with open(config_path, "r", encoding="utf-8") as _f:
                    _cfg = _yaml.safe_load(_f) or {}
                _section = _cfg.get(section_name, {})
                return bool(_section.get("enabled", False)) if isinstance(_section, dict) else False
            except Exception:
                return False

        if selected_mode == "telegram" and telegram_bot is None and (
            requested_telegram_cli or _config_enabled("telegram")
        ):
            print("\n\u274c Telegram mode was requested, but no valid Telegram bot could be initialized.")
            print("")
            print("   Possible causes:")
            print("   - python-telegram-bot is not installed")
            print("     Fix: pip install 'python-telegram-bot[job-queue]>=21.0.0'")
            print("   - Bot token is missing or invalid in config.yaml")
            print("   - Network connectivity issues")
            print("")
            print("   To configure Telegram interactively, run:")
            print("     python3 run.py --setting")
            print("   Then select a provider and choose 'Telegram' as the messaging app.")
            print("")
            print("   Or set telegram.bot_token directly in config.yaml:")
            print("     telegram:")
            print("       enabled: true")
            print("       bot_token: '123456789:AA...'  (from @BotFather)")
            print("")
            print("   To run without Telegram, use console mode:")
            print("     python3 run.py")
            print("   (Make sure telegram.enabled is false in config.yaml)")
            sys.exit(1)

        # Select the PRIMARY bot based on the user-selected mode so that the
        # engine's mode flags, the printed label, and the polling bot all
        # agree. Falling back to whichever bot is available keeps the agent
        # reachable even if only the secondary integration is configured.
        if selected_mode == "discord":
            active_bot = discord_bot or telegram_bot
        else:
            active_bot = telegram_bot or discord_bot

        # Keep the engine's mode flags consistent with the actual primary bot.
        if active_bot is telegram_bot and active_bot is not None:
            selected_mode = "telegram"
        elif active_bot is discord_bot and active_bot is not None:
            selected_mode = "discord"

        # Restore the chat/user id from the previous session (sleep/exit state)
        # so that, after an automatic restart, the agent still knows which
        # conversation to reply to. Without this, the first wake-up message and
        # any subsequent progress updates would be dropped until the user sends
        # a fresh message — breaking 24/7 reply continuity.
        try:
            _restored_uid = None
            if _restore_state:
                _restored_uid = _restore_state.get("restart_telegram_user_id")
            if _restored_uid is not None:
                _restored_uid = int(_restored_uid)
                if active_bot is not None:
                    active_bot._boot_user_id = _restored_uid
                    active_bot._last_user_id = _restored_uid
        except (ValueError, TypeError, AttributeError):
            pass

        agent = AutonomousAIAgent(
            provider=selected_provider, model=selected_model,
            config_path=str(config_path) if config_path.exists() else None,
            telegram_bot=telegram_bot, discord_bot=discord_bot,
        )

        command_timeout = 1800
        task_timeout = 7200
        try:
            from ai_agent.utils.config import ConfigManager
            if config_path.exists():
                cfg_mgr = ConfigManager(str(config_path))
                cfg = cfg_mgr.load_config()
                if hasattr(cfg, 'execution'):
                    command_timeout = getattr(cfg.execution, 'command_timeout', 1800)
                    task_timeout = getattr(cfg.execution, 'task_timeout', 7200)
        except Exception:
            pass

        options = {"debug": debug_mode, "command_timeout": command_timeout, "task_timeout": task_timeout, "self_heal": _self_heal_mode}
        os.environ['CLIO_TELEGRAM_MODE'] = 'true' if selected_mode == 'telegram' else 'false'
        os.environ['CLIO_DISCORD_MODE'] = 'true' if selected_mode == 'discord' else 'false'

        if active_bot:
            bot_label = "Discord" if selected_mode == "discord" else "Telegram"
            print(f"\n\U0001f4f1 Starting {bot_label} bot mode...")
            active_bot.set_message_callback(None)

            def process_restart(user_id):
                restart_with_current_settings(selected_mode, selected_provider, selected_model, debug_mode, max_iterations)

            active_bot.set_restart_callback(process_restart)
            from ai_agent.external_integration.telegram_bot import ConversationHistory
            shared_history = ConversationHistory(user_id=0, max_length=50)
            active_bot.set_shared_conversation_history(shared_history)
            active_bot.set_user_message_callback(agent.engine.add_user_message)

            import threading as _th

            def _run_agent():
                try:
                    agent.run_autonomous_boot(
                        options, conversation_history=shared_history,
                        telegram_bot=telegram_bot, discord_bot=discord_bot,
                        initial_instruction=instruction,
                    )
                except KeyboardInterrupt:
                    pass

            agent_thread = _th.Thread(target=_run_agent, daemon=True)
            agent_thread.start()

            secondary_thread = None
            if discord_bot and telegram_bot:
                secondary_bot = telegram_bot if selected_mode == "discord" else discord_bot
                secondary_thread = _th.Thread(target=lambda: secondary_bot.start_bot(), daemon=True)
                secondary_thread.start()

            try:
                active_bot.start_bot()
            except KeyboardInterrupt:
                print(f"\n\nStopping {bot_label} bot...")
                try:
                    _ctx = getattr(agent.engine, "_current_context", None)
                    if _ctx is not None:
                        agent.engine._handle_exit(_ctx, fast=True, project_root=_project_root)
                except Exception:
                    pass
                active_bot.stop_bot()
                if secondary_thread:
                    secondary_bot.stop_bot()
                print("Bot stopped.")
                sys.exit(0)
        else:
            print("\n\U0001f916 Running in autonomous mode...")
            try:
                agent.run_autonomous_boot(
                    options, telegram_bot=telegram_bot, discord_bot=discord_bot,
                    initial_instruction=instruction,
                )
            except KeyboardInterrupt:
                print("\n\nStopping agent...")
                try:
                    _ctx = getattr(agent.engine, "_current_context", None)
                    if _ctx is not None:
                        agent.engine._handle_exit(_ctx, fast=True, project_root=_project_root)
                except Exception:
                    pass
                print("Agent stopped.")

    except ImportError as e:
        print(f"Import error: {e}")
        print("Try deleting the 'venv' directory and running Clio-Agent again.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        if debug_mode:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
