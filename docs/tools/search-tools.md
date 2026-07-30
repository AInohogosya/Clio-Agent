# Tools → Search Tools (`FileSearchTool`)

Search the local filesystem for files and content.

## `search_files(directory=None, pattern=None, path=None, ...)`

Find files whose names match `pattern` under `directory`.

- `directory` or `path` (alias) selects the root to search.
- `pattern` is a name/substring/ glob matcher (provider-dependent).
- Recurses through subdirectories.

## `search_content(directory=None, query=None, path=None, ...)`

Search file **contents** for `query` under `directory`.

- Returns matching files and the matching lines/snippets.
- Useful for "find where X is defined/used".

## `list_directory(directory=None, path=None, ...)`

List the entries of a directory.

- Accepts `directory` **or** the `path` alias (they behave identically).
- Reports an explicit entry count (e.g. `"2 entries"`, or `"0 entries"` for an
  empty directory).
- Directories are marked (e.g. `[DIR] subdir`).
- Access errors are **surfaced** in the output (e.g. `"ACCESS DENIED"` +
  `PermissionError`/`OSError`) rather than swallowed.
- Missing `directory`/`path` returns a clear error mentioning both keywords.

## Test coverage

`tests/test_tool_registry.py` pins these behaviors:
`test_path_alias_works_like_directory`, `test_empty_directory_reports_zero_entries`,
`test_permission_error_is_surfaced_in_output`, `test_os_error_is_surfaced_in_output`,
`test_missing_argument_returns_clear_error`.

See also: [Tools Overview](overview.md), [File Tools](file-tools.md).
