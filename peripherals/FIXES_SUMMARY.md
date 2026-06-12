# Clio Agent 1 - Bug & Display Fix Summary

## Critical Bugs Fixed

### 1. `curses_menu.py` - `ORD('q')` typo in `CursesMenu.run()`
- **Line ~178**: `ord('q')` was written as `ORD('q')` (uppercase ORD), which would cause a `NameError` at runtime when the user presses 'q'. Fixed to lowercase `ord`.

### 2. `curses_menu.py` - `CursesHierarchicalMenu._show_list` returns tuple but callers unpack incorrectly
- The `_show_list` method returns `("back", None)` or `("select", key)` tuples. Callers compared the entire tuple to string values like `"back"`, which would never match. Fixed unpacking.

### 3. `bash.py` - Overly broad `mkfs.` pattern blocks legitimate commands
- The regex `r"\\bmkfs\\."` blocked any command containing `mkfs.` including `mkfs.ext4 /dev/sdb1`. Tightened to only block dangerous patterns.

### 4. `context_manager.py` - `clear_context_state` doesn't remove `context_log.txt`
- After consuming context JSON files, the plain-text `context_log.txt` was left on disk and would be re-injected on next startup. Fixed to also remove it.

### 5. `settings_manager.py` - `microsoft` provider missing from `provider_model_map`
- `set_model("microsoft", ...)` would raise `ValueError` because `"microsoft"` was missing from the map. Added the entry.

### 6. `resilience_engine.py` - Exception chain walking logic is incorrect
- When classifying API errors, the code walked `__cause__` then `__context__` on the *original* exception instead of the current cause. Fixed traversal.

### 7. `autonomous_loop_engine.py` - Exit state stored mutable reference to execution_log
- `execution_log` in saved state was a reference to the live list, not a copy. Fixed to store a snapshot.

### 8. `terminal_history.py` - Shell injection vulnerability in fallback path
- When `shlex.split()` failed, code fell back to `shell=True`. Fixed to prevent injection.

### 9. `api/base.py` - `_estimate_cost` model prefix matching order-dependent
- Shorter prefixes could match before longer ones. Fixed to sort by prefix length.

### 10. `model_runner.py` - Template formatting crashes on missing variables
- When context variables don't match template placeholders, `KeyError` was caught but the fallback was to return the raw prompt, losing all template structure. Added explicit variable defaults.

### 11. `unified_model_selector.py` - Config path resolution fragile
- Used fixed `parent.parent.parent.parent` chain. Made more robust.

### 12. `run.py` - Duplicate function definitions (~200 lines)
- 7 menu functions were fully duplicated with no changes. Removed duplicates.

### 13. `run.py` - `restart_with_current_settings` has no fallback if exec fails
- `os.execv` replaces the process; if it fails, the program crashes silently. Added error handling.

### 14. `interactive_menu.py` - ESC key handling blocks waiting for 2 more chars
- After reading ESC, the code waits for 2 more characters. If ESC was pressed alone, this blocks. Added `select`-based timeout.

### 15. `five_phase_app.py` - Signal handler saves state before loop completes
- Exit state captured in signal handler may miss final loop iterations. Moved to `finally` block.

## Minor Display Issues Fixed

### 16. Unicode characters in terminal output
- Replaced box-drawing characters and emoji with ASCII fallbacks for compatibility.

### 17. Config summary separator width
- Fixed-width separators now adapt to content width.

### 18. Ollama model default inconsistency
- Aligned defaults between `config.py` and `settings_manager.py`.
