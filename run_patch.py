#!/usr/bin/env python3
"""Helper script to apply targeted patches to run.py"""

with open('run.py', 'r', encoding='utf-8') as f:
    content = f.read()

patches_applied = 0

# -- Patch 1: Replace _pip_install with retry logic --
old_pip = (
    'def _pip_install(venv_python, args, timeout=600):\n'
    '    """Run `pip install <args>` in the venv. Returns (ok, stderr)."""\n'
    '    try:\n'
    '        r = subprocess.run([venv_python, "-m", "pip", "install", *args],\n'
    '                           capture_output=True, text=True, timeout=timeout)\n'
    '        return r.returncode == 0, (r.stderr or "").strip()\n'
    '    except Exception as e:\n'
    '        return False, str(e)\n'
)

new_pip = (
    'def _pip_install(venv_python, args, timeout=600, retries=3):\n'
    '    """Run `pip install <args>` in the venv with retry logic for network failures.\n'
    '\n'
    '    Retries on common transient errors: timeouts, SSL errors, connection drops.\n'
    '    Returns (ok, stderr).\n'
    '    """\n'
    '    _TRANSIENT_ERRS = ("connectionerror", "timeout", "sslerror",\n'
    '                       "temporaryfailure", "retriableerror",\n'
    '                       "network", "proxyerror", "reset by peer",\n'
    '                       "connection reset", "broken pipe")\n'
    '    for attempt in range(1, retries + 1):\n'
    '        try:\n'
    '            r = subprocess.run([venv_python, "-m", "pip", "install", *args],\n'
    '                               capture_output=True, text=True, timeout=timeout)\n'
    '            if r.returncode == 0:\n'
    '                return True, ""\n'
    '            err_lower = (r.stderr or "").lower()\n'
    '            is_transient = any(e in err_lower for e in _TRANSIENT_ERRS)\n'
    '            if is_transient and attempt < retries:\n'
    '                delay = 2 * attempt\n'
    '                print("  pip attempt %d/%d failed (transient); retrying in %ds ..." % (attempt, retries, delay))\n'
    '                print("    Error: %s" % (r.stderr or "").strip()[:200])\n'
    '                time.sleep(delay)\n'
    '                continue\n'
    '            return False, (r.stderr or "").strip()\n'
    '        except subprocess.TimeoutExpired:\n'
    '            if attempt < retries:\n'
    '                delay = 2 * attempt\n'
    '                print("  pip attempt %d/%d timed out; retrying in %ds ..." % (attempt, retries, delay))\n'
    '                time.sleep(delay)\n'
    '                continue\n'
    '            return False, "pip timed out after %ds" % timeout\n'
    '        except Exception as e:\n'
    '            if attempt < retries:\n'
    '                delay = 2 * attempt\n'
    '                print("  pip attempt %d/%d errored (%s); retrying in %ds ..." % (attempt, retries, e, delay))\n'
    '                time.sleep(delay)\n'
    '                continue\n'
    '            return False, str(e)\n'
)

if old_pip in content:
    content = content.replace(old_pip, new_pip, 1)
    patches_applied += 1
    print("Patch 1: Replaced _pip_install with retry logic")
else:
    print("SKIP Patch 1: _pip_install not found (may already be patched)")

# -- Patch 2: Fix _version_tuple to handle local/dev versions --
old_vt = (
    'def _version_tuple(value):\n'
    '    """Parse a dotted version string into a comparable tuple of ints.\n'
    '\n'
    '    Non-numeric suffixes (e.g. \'1.2.3rc1\', \'2.0.0.post1\') are ignored so the\n'
    '    comparison stays robust across the many packaging conventions in the wild.\n'
    '    """\n'
    '    parts = []\n'
    '    for chunk in str(value).split("."):\n'
    '        num = ""\n'
    '        for ch in chunk:\n'
    '            if ch.isdigit():\n'
    '                num += ch\n'
    '            else:\n'
    '                break\n'
    '        if num == "":\n'
    '            break\n'
    '        parts.append(int(num))\n'
    '    return tuple(parts) if parts else (0,)\n'
)

new_vt = (
    'def _version_tuple(value):\n'
    '    """Parse a dotted version string into a comparable tuple of ints.\n'
    '\n'
    '    Non-numeric suffixes (e.g. \'1.2.3rc1\', \'2.0.0.post1\') are ignored so the\n'
    '    comparison stays robust across the many packaging conventions in the wild.\n'
    '\n'
    '    Also strips local version identifiers (e.g. \'1.0.0+git.abc123\') and\n'
    '    dev markers (e.g. \'1.0.0.dev0\') so these don\'t break parsing.\n'
    '    """\n'
    '    # Strip local version identifier (PEP 440): everything after \'+\'\n'
    '    raw = str(value).split("+")[0]\n'
    '    # Strip dev markers, pre/post release suffixes for comparison\n'
    '    raw = raw.split("-")[0]\n'
    '    parts = []\n'
    '    for chunk in raw.split("."):\n'
    '        num = ""\n'
    '        for ch in chunk:\n'
    '            if ch.isdigit():\n'
    '                num += ch\n'
    '            else:\n'
    '                break\n'
    '        if num == "":\n'
    '            break\n'
    '        parts.append(int(num))\n'
    '    return tuple(parts) if parts else (0,)\n'
)

if old_vt in content:
    content = content.replace(old_vt, new_vt, 1)
    patches_applied += 1
    print("Patch 2: Fixed _version_tuple for local/dev versions")
else:
    print("SKIP Patch 2: _version_tuple not found (may already be patched)")

# -- Patch 3: Fix _inspect_venv_deps subprocess _vt for edge cases --
# Use positional replacement since the escaped newlines are tricky
start_marker = '"def _vt(s):\\n"'
end_marker = '"    return tuple(out) if out else (0,)\\n"'

start = content.find(start_marker)
if start != -1:
    end = content.find(end_marker)
    if end != -1:
        end += len(end_marker)
        old_block = content[start:end]
        # Build the replacement with same structure
        new_block = (
            '"def _vt(s):\\n"\n'
            '        "    raw = str(s).split(\\'+\\')[0]\\n"\n'
            '        "    raw = raw.split(\\'-\\')[0]\\n"\n'
            '        "    out=[]\\n"\n'
            '        "    for c in raw.split(\\'.\\'):\\n"\n'
            "        \"        n=''\\n\"\n"
            '        "        for ch in c:\\n"\n'
            '        "            if ch.isdigit(): n+=ch\\n"\n'
            '        "            else: break\\n"\n'
            "        \"        if n=='': break\\n\"\n"
            '        "        out.append(int(n))\\n"\n'
            '        "    return tuple(out) if out else (0,)\\n"\n'
        )
        content = content[:start] + new_block + content[end:]
        patches_applied += 1
        print("Patch 3: Fixed _inspect_venv_deps subprocess _vt for edge cases")
    else:
        print("SKIP Patch 3: end marker not found")
else:
    print("SKIP Patch 3: start marker not found (may already be patched)")

# -- Patch 4: Improve _venv_python_is_healthy --
old_healthy = (
    'def _venv_python_is_healthy(venv_python):\n'
    '    """Return True if the venv interpreter actually runs and has a working pip.\n'
    '\n'
    '    Detects the common \'stale venv\' failure where the base Python that created\n'
    '    the venv was upgraded or removed (very common with Homebrew/pyenv), leaving\n'
    '    a venv whose python symlink is dead. Such a venv must be rebuilt.\n'
    '    """\n'
    '    try:\n'
    '        ver = subprocess.run([venv_python, "-c",\n'
    '                              "import sys; print(\'%d.%d\' % sys.version_info[:2])"],\n'
    '                             capture_output=True, text=True, timeout=15)\n'
    '        if ver.returncode != 0:\n'
    '            return False\n'
    '        try:\n'
    '            major, minor = (int(x) for x in ver.stdout.strip().split(".")[:2])\n'
    '            if (major, minor) < MIN_PYTHON:\n'
    '                print(f"Virtual environment uses Python {major}.{minor} "\n'
    '                      f"(< {MIN_PYTHON[0]}.{MIN_PYTHON[1]} required).")\n'
    '                return False\n'
    '        except Exception:\n'
    '            pass\n'
    '        pip = subprocess.run([venv_python, "-m", "pip", "--version"],\n'
    '                             capture_output=True, text=True, timeout=15)\n'
    '        return pip.returncode == 0\n'
    '    except Exception:\n'
    '        return False\n'
)

new_healthy = (
    'def _venv_python_is_healthy(venv_python):\n'
    '    """Return True if the venv interpreter actually runs and has a working pip.\n'
    '\n'
    '    Detects the common \'stale venv\' failure where the base Python that created\n'
    '    the venv was upgraded or removed (very common with Homebrew/pyenv), leaving\n'
    '    a venv whose python symlink is dead. Such a venv must be rebuilt.\n'
    '\n'
    '    Also handles cases where the venv python exists but is a symlink to\n'
    '    a now-missing interpreter (broken symlink returns FileNotFoundError).\n'
    '    """\n'
    '    from pathlib import Path as _P\n'
    '    # First check: does the file actually exist (not a broken symlink)?\n'
    '    try:\n'
    '        if not _P(venv_python).exists():\n'
    '            return False\n'
    '    except OSError:\n'
    '        return False\n'
    '    try:\n'
    '        ver = subprocess.run([venv_python, "-c",\n'
    '                              "import sys; print(\'%d.%d\' % sys.version_info[:2])"],\n'
    '                             capture_output=True, text=True, timeout=15)\n'
    '        if ver.returncode != 0:\n'
    '            return False\n'
    '        ver_str = ver.stdout.strip()\n'
    '        if "." not in ver_str:\n'
    '            return False\n'
    '        try:\n'
    '            major, minor = (int(x) for x in ver_str.split(".")[:2])\n'
    '            if (major, minor) < MIN_PYTHON:\n'
    '                print("Virtual environment uses Python %d.%d "\n'
    '                      "(< %d.%d required)." % (major, minor, MIN_PYTHON[0], MIN_PYTHON[1]))\n'
    '                return False\n'
    '        except Exception:\n'
    '            pass\n'
    '        pip = subprocess.run([venv_python, "-m", "pip", "--version"],\n'
    '                             capture_output=True, text=True, timeout=15)\n'
    '        return pip.returncode == 0\n'
    '    except Exception:\n'
    '        return False\n'
)

if old_healthy in content:
    content = content.replace(old_healthy, new_healthy, 1)
    patches_applied += 1
    print("Patch 4: Improved _venv_python_is_healthy with broken symlink detection")
else:
    print("SKIP Patch 4: _venv_python_is_healthy not found (may already be patched)")

# -- Patch 5: Fix _repair_venv_deps fallback for platform deps --
old_repair = (
    '    # 1. Try the whole project first so version constraints resolve together.\n'
    '    print("Installing/updating project dependencies ...")\n'
    '    success, err = _pip_install(venv_python, ["-e", str(project_root)])\n'
    '    if not success:\n'
    '        print(f"Editable install failed: {err[:300]}")\n'
    '        print("Falling back to non-editable project install ...")\n'
    '        _pip_install(venv_python, [str(project_root)])\n'
)

new_repair = (
    '    # 1. Try the whole project first so version constraints resolve together.\n'
    '    print("Installing/updating project dependencies ...")\n'
    '    success, err = _pip_install(venv_python, ["-e", str(project_root)])\n'
    '    if not success:\n'
    '        print("Editable install failed: %s" % err[:300])\n'
    '        print("Falling back to non-editable project install ...")\n'
    '        success, err = _pip_install(venv_python, [str(project_root)])\n'
    '        if not success:\n'
    '            # Platform-specific deps (pyobjc, pywin32, xlib) may fail on wrong OS.\n'
    '            # Install core deps individually as last resort, skipping failed ones.\n'
    '            print("Full project install failed: %s" % err[:300])\n'
    '            print("Falling back to installing core deps individually ...")\n'
    '            _pip_install(venv_python, ["--upgrade", "pip", "setuptools", "wheel"])\n'
    '            for _mod, (_pkg, _minv) in CORE_DEPENDENCIES.items():\n'
    '                _spec = "%s>=%s" % (_pkg, _minv) if _minv else _pkg\n'
    '                _pip_install(venv_python, [_spec])\n'
    '            # Try project with --no-deps to get the package registered\n'
    '            _pip_install(venv_python, ["--no-deps", str(project_root)])\n'
)

if old_repair in content:
    content = content.replace(old_repair, new_repair, 1)
    patches_applied += 1
    print("Patch 5: Improved _repair_venv_deps fallback for platform-specific dep failures")
else:
    print("SKIP Patch 5: _repair_venv_deps fallback not found (may already be patched)")

# -- Patch 6: Fix _get_venv_python to resolve symlinks --
old_get = (
    'def _get_venv_python():\n'
    '    project_root = Path(__file__).parent\n'
    '    venv_path = project_root / "venv"\n'
    '    if not venv_path.exists():\n'
    '        return None\n'
    '    if platform.system() == "Windows":\n'
    '        python_exe = venv_path / "Scripts" / "python.exe"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "Scripts" / "pythonw.exe"\n'
    '    else:\n'
    '        python_exe = venv_path / "bin" / "python"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "bin" / "python3"\n'
    '    return str(python_exe) if python_exe.exists() else None\n'
)

new_get = (
    'def _get_venv_python():\n'
    '    project_root = Path(__file__).parent\n'
    '    venv_path = project_root / "venv"\n'
    '    if not venv_path.exists():\n'
    '        return None\n'
    '    if platform.system() == "Windows":\n'
    '        python_exe = venv_path / "Scripts" / "python.exe"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "Scripts" / "pythonw.exe"\n'
    '    else:\n'
    '        python_exe = venv_path / "bin" / "python"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "bin" / "python3"\n'
    '    if not python_exe.exists():\n'
    '        return None\n'
    '    # Resolve symlinks to find the real path (handles pyenv, Homebrew, etc.)\n'
    '    try:\n'
    '        resolved = python_exe.resolve()\n'
    '        if resolved.exists():\n'
    '            return str(resolved)\n'
    '    except (OSError, ValueError):\n'
    '        pass\n'
    '    return str(python_exe)\n'
)

if old_get in content:
    content = content.replace(old_get, new_get, 1)
    patches_applied += 1
    print("Patch 6: Fixed _get_venv_python to resolve symlinks")
else:
    print("SKIP Patch 6: _get_venv_python not found (may already be patched)")

# -- Patch 7: Add Windows encoding fix --
old_enc = (
    '# Fix encoding issues on macOS / environments with surrogateescape\n'
    'if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("utf") and sys.stdout.errors == "surrogateescape":\n'
    '    sys.stdout.reconfigure(errors="surrogatepass")\n'
    'if sys.stderr.encoding and sys.stderr.encoding.lower().startswith("utf") and sys.stderr.errors == "surrogateescape":\n'
    '    sys.stderr.reconfigure(errors="surrogatepass")\n'
    'os.environ.setdefault("PYTHONIOENCODING", "utf-8")\n'
)

new_enc = (
    '# Fix encoding issues on macOS / environments with surrogateescape\n'
    'if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("utf") and sys.stdout.errors == "surrogateescape":\n'
    '    sys.stdout.reconfigure(errors="surrogatepass")\n'
    'if sys.stderr.encoding and sys.stderr.encoding.lower().startswith("utf") and sys.stderr.errors == "surrogateescape":\n'
    '    sys.stderr.reconfigure(errors="surrogatepass")\n'
    '# Windows consoles may use cp1252 or cp65001; force UTF-8 where possible\n'
    'if sys.platform == "win32":\n'
    '    os.environ["PYTHONIOENCODING"] = "utf-8"\n'
    '    try:\n'
    '        sys.stdout.reconfigure(encoding="utf-8", errors="replace")\n'
    '        sys.stderr.reconfigure(encoding="utf-8", errors="replace")\n'
    '    except Exception:\n'
    '        pass\n'
    'os.environ.setdefault("PYTHONIOENCODING", "utf-8")\n'
)

if old_enc in content:
    content = content.replace(old_enc, new_enc, 1)
    patches_applied += 1
    print("Patch 7: Added Windows encoding fix")
else:
    print("SKIP Patch 7: encoding setup not found (may already be patched)")

# -- Patch 8: Fix _resolve_venv_python to detect symlink health --
old_resolve = (
    'def _resolve_venv_python():\n'
    '    """Return (venv_python_path, needs_install, deps_ok).\n'
    '\n'
    '    needs_install is True when the venv exists but pip is missing/broken.\n'
    '    deps_ok is True only when every core dependency is present and new enough.\n'
    '    """\n'
    '    project_root = Path(__file__).parent.resolve()\n'
    '    venv_path = project_root / "venv"\n'
    '    if platform.system() == "Windows":\n'
    '        candidates = [venv_path / "Scripts" / "python.exe", venv_path / "Scripts" / "pythonw.exe"]\n'
    '    else:\n'
    '        candidates = [venv_path / "bin" / "python", venv_path / "bin" / "python3"]\n'
    '    for p in candidates:\n'
    '        if p.exists():\n'
    '            try:\n'
    '                r = subprocess.run([str(p), "-m", "pip", "--version"],\n'
    '                                   capture_output=True, text=True, timeout=10)\n'
    '                if r.returncode == 0:\n'
    '                    deps_ok = _check_venv_deps(str(p))\n'
    '                    return str(p), False, deps_ok\n'
    '            except Exception:\n'
    '                pass\n'
    '            return str(p), True, False  # venv exists but pip is broken\n'
    '    return None, False, False\n'
)

new_resolve = (
    'def _resolve_venv_python():\n'
    '    """Return (venv_python_path, needs_install, deps_ok).\n'
    '\n'
    '    needs_install is True when the venv exists but pip is missing/broken.\n'
    '    deps_ok is True only when every core dependency is present and new enough.\n'
    '    """\n'
    '    from pathlib import Path as _P\n'
    '    project_root = Path(__file__).parent.resolve()\n'
    '    venv_path = project_root / "venv"\n'
    '    if platform.system() == "Windows":\n'
    '        candidates = [venv_path / "Scripts" / "python.exe", venv_path / "Scripts" / "pythonw.exe"]\n'
    '    else:\n'
    '        candidates = [venv_path / "bin" / "python", venv_path / "bin" / "python3"]\n'
    '    for p in candidates:\n'
    '        try:\n'
    '            # Skip broken symlinks (symlink target doesn\'t exist)\n'
    '            rp = p.resolve()\n'
    '            if not rp.exists():\n'
    '                continue\n'
    '        except (OSError, ValueError):\n'
    '            if not p.exists():\n'
    '                continue\n'
    '            rp = p\n'
    '        try:\n'
    '            r = subprocess.run([str(rp), "-m", "pip", "--version"],\n'
    '                               capture_output=True, text=True, timeout=10)\n'
    '            if r.returncode == 0:\n'
    '                deps_ok = _check_venv_deps(str(rp))\n'
    '                return str(rp), False, deps_ok\n'
    '        except Exception:\n'
    '            pass\n'
    '        return str(rp), True, False  # venv exists but pip is broken\n'
    '    return None, False, False\n'
)

if old_resolve in content:
    content = content.replace(old_resolve, new_resolve, 1)
    patches_applied += 1
    print("Patch 8: Fixed _resolve_venv_python to skip broken symlinks")
else:
    print("SKIP Patch 8: _resolve_venv_python not found (may already be patched)")

# -- Patch 9: Fix get_venv_python_path (public) for same symlink issue --
old_pub = (
    'def get_venv_python_path():\n'
    '    project_root = Path(__file__).parent\n'
    '    venv_path = project_root / VENV_DIR\n'
    '    if not venv_path.exists():\n'
    '        return None\n'
    '    if platform.system() == "Windows":\n'
    '        python_exe = venv_path / "Scripts" / "python.exe"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "Scripts" / "pythonw.exe"\n'
    '    else:\n'
    '        python_exe = venv_path / "bin" / "python"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "bin" / "python3"\n'
    '    return str(python_exe) if python_exe.exists() else None\n'
)

new_pub = (
    'def get_venv_python_path():\n'
    '    from pathlib import Path as _P\n'
    '    project_root = Path(__file__).parent\n'
    '    venv_path = project_root / VENV_DIR\n'
    '    if not venv_path.exists():\n'
    '        return None\n'
    '    if platform.system() == "Windows":\n'
    '        python_exe = venv_path / "Scripts" / "python.exe"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "Scripts" / "pythonw.exe"\n'
    '    else:\n'
    '        python_exe = venv_path / "bin" / "python"\n'
    '        if not python_exe.exists():\n'
    '            python_exe = venv_path / "bin" / "python3"\n'
    '    if not python_exe.exists():\n'
    '        return None\n'
    '    # Resolve symlinks to get real interpreter path\n'
    '    try:\n'
    '        resolved = python_exe.resolve()\n'
    '        if resolved.exists():\n'
    '            return str(resolved)\n'
    '    except (OSError, ValueError):\n'
    '        pass\n'
    '    return str(python_exe)\n'
)

if old_pub in content:
    content = content.replace(old_pub, new_pub, 1)
    patches_applied += 1
    print("Patch 9: Fixed get_venv_python_path to resolve symlinks")
else:
    print("SKIP Patch 9: get_venv_python_path not found (may already be patched)")

# Write the modified content
with open('run.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("\n%d patches applied successfully!" % patches_applied)
