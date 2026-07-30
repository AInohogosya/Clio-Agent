# Tools → File Tools (`FileEditTool`)

Read, write, append, and edit local files. All paths are expanded (`~` and
relative → absolute) and resolved before use.

## `read_file(filepath=None, max_lines=100, path=None)`

Read a file's contents.

- `filepath` (preferred) or `path` (alias) selects the target. At least one is
  required.
- Returns at most `max_lines` lines; if the file is longer, appends
  `"... (N lines shown, file continues)"`.
- Fails clearly if the file is missing or not a regular file.

## `write_file(filepath=None, content="", path=None)`

Create or **overwrite** a file with `content`.

- `filepath`/`path` select the target.
- Creates parent directories as needed.

## `append_file(filepath=None, content="", path=None)`

Append `content` to a file (creating it if necessary).

## `edit_file(filepath=None, old_text="", new_text="", path=None)`

Replace `old_text` with `new_text` in a file.

- If `old_text` is not found, the tool reports a clear error and does **not** modify
  the file.
- If `old_text` appears multiple times, the behavior is "first match replaced"
  (verify the result, since precise edits are safer).

## Safety notes

- These tools run with the privileges of the user that launched the agent — there
  is **no sandbox or path allow-list**. The agent can overwrite any file it can
  access.
- Pair with [Autonomous Mode](../usage/autonomous-mode.md) discipline and review
  [Safety](../operations/safety.md).

See also: [Tools Overview](overview.md), [Search Tools](search-tools.md).
