# Tools → Thinking Tool (`ThinkingTool`)

The `thinking` tool records the agent's internal monologue into the context log.
It is for the agent's own reasoning — **not** for the user.

## `think(thought=None, text=None, content=None, context=None, note=None, message=None)`

- The canonical keyword is `thought=`.
- It also accepts common aliases the model may emit: `text`, `content`, `context`,
  `note`, `message`.
- The thought is appended to the context log as a `thinking` entry.
- Calling it with no text returns a clear error.

## Why it exists

- Lets the model "show its work" to itself across turns.
- Provides auditable reasoning that feeds future prompts.
- Kept separate from user-facing replies (see [Say Tool](say-tool.md)).

## Test coverage

`tests/test_thinking_tool.py` pins the alias behavior and the missing-argument
error:

- `test_canonical_thought_keyword_succeeds`
- `test_content_and_context_aliases_accepted`
- `test_text_note_message_aliases_accepted`
- `test_missing_thought_returns_error`

See also: [Context Management](../architecture/context-management.md),
[Tools Overview](overview.md).
