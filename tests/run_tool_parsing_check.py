"""
Standalone runner for the tool-parsing/feedback validation tests.

pytest is not installed in the environment, so this script drives the same
test functions directly and reports pass/fail. Run with:

    python3 tests/run_tool_parsing_check.py
"""

import asyncio
import importlib.util
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Import the pytest-style test module without running pytest.
spec = importlib.util.spec_from_file_location(
    "test_tool_parsing", REPO_ROOT / "tests" / "test_tool_parsing.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

results = []
for name in dir(mod):
    if name.startswith("test_") and callable(getattr(mod, name)):
        func = getattr(mod, name)
        try:
            if asyncio.iscoroutinefunction(func):
                asyncio.run(func())
            else:
                func()
            results.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()

print("\n=== Test results ===")
all_ok = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"[{status}] {name}" + (f" -> {detail}" if detail else ""))

print("\nSUMMARY:", "ALL PASS" if all_ok else "FAILURES PRESENT")
sys.exit(0 if all_ok else 1)
