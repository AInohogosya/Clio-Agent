"""
Editing part of a file, which is the capability the agent was never told to use.

`fs.edit` took exact search/replace, a list of operations, and a unified diff, and
it worked. Across ninety-nine operations on files the agent used it zero times and
wrote every file whole through a shell heredoc instead -- and one of those came
back with a single closing brace mangled into `}n`, which made eight thousand
characters of JavaScript a syntax error from end to end.

A rewrite is one guess about every character in the file. An edit is one guess
about the lines it quotes. That is the whole argument for this tool existing, and
the tests here are about the two ways it can still go wrong silently: matching the
wrong one of several identical places, and reporting success for an edit that did
not happen.

The ambiguity rule is the interesting one. `str.replace(text, search, replace, 1)`
takes the first match and says nothing, so a search string that occurs three times
-- a closing brace, an import line, an empty object -- edits whichever one comes
first and reports `ok: true`. Nothing downstream can tell it apart from an edit
the model meant, because it is the same file and the same call. Refusing is one
extra round trip; guessing wrong is a change nobody asked for, in a file that
still looks edited.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ethos.config import PathsConfig
from ethos.toolhost.server import ToolContext
from ethos.toolhost.tools.fs import FsEditTool, apply_search_replace

CTX = ToolContext(thread_id="fs-edit")

SOURCE = """<!doctype html>
<html>
  <head><title>Snake</title></head>
  <body>
    <canvas id="game"></canvas>
    <script>
      const head = {
        x: snake[0].x + direction.x,
        y: snake[0].y + direction.y
      }
      return head;
    </script>
  </body>
</html>
"""


def _host(tmp_path: Path) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(paths=PathsConfig(home=tmp_path / "agent-home")),
    )


def _write(tmp_path: Path, text: str = SOURCE) -> Path:
    target = tmp_path / "sample.html"
    target.write_text(text, encoding="utf-8")
    return target


# ------------------------------------------------------------------ the rule


def test_an_exact_match_is_replaced() -> None:
    out = apply_search_replace("a b c", "b", "B")
    assert out == "a B c"


def test_a_search_that_matches_nothing_is_an_error_not_a_silent_no_op() -> None:
    with pytest.raises(ValueError, match="not found"):
        apply_search_replace("a b c", "zzz", "B")


def test_an_empty_search_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        apply_search_replace("a b c", "", "B")


def test_an_ambiguous_search_is_refused_and_says_how_many() -> None:
    """The one that goes wrong silently otherwise.

    `}` appears twice in any JavaScript object and four times in anything with a
    function in it. Taking the first match is a coin toss, and the result is
    reported as success.
    """
    text = "function f() {\n  return 1;\n}\n\nconst g = () => {\n  return 2;\n};\n"
    assert text.count("}") == 2, "the fixture is two closing braces"
    with pytest.raises(ValueError) as raised:
        apply_search_replace(text, "}", "}")
    assert "ambiguous" in str(raised.value)
    assert "2 occurrences" in str(raised.value), "the count is what makes it actionable"


def test_the_ambiguity_error_says_how_to_resolve_it() -> None:
    with pytest.raises(ValueError) as raised:
        apply_search_replace("x\nx\n", "x", "y")
    message = str(raised.value).lower()
    assert "more of the lines" in message, "quoting more context is the answer"
    assert "replace_all" in message, "and so is saying you meant all of them"


def test_replace_all_is_the_opt_in_and_needs_asking_for() -> None:
    assert apply_search_replace("x\nx\nx", "x", "y", replace_all=True) == "y\ny\ny"
    with pytest.raises(ValueError):
        apply_search_replace("x\nx\nx", "x", "y")


def test_a_single_occurrence_is_unaffected_by_replace_all() -> None:
    assert apply_search_replace("x\ny\n", "x", "z", replace_all=True) == "z\ny\n"


def test_a_deliberate_no_op_is_allowed() -> None:
    """Verifying by editing is a legitimate thing to ask for."""
    assert apply_search_replace("a b", "a", "a") == "a b"


# --------------------------------------------------------------- the tool


async def test_editing_one_line_leaves_everything_else_byte_for_byte(tmp_path: Path) -> None:
    target = _write(tmp_path)
    before = target.read_text()
    result = await FsEditTool().run(
        {"path": str(target), "search": 'id="game"', "replace": 'id="board"'}, CTX,
        _host(tmp_path),
    )
    after = target.read_text()
    assert result["applied"] is True
    assert 'id="board"' in after
    assert after.count("\n") == before.count("\n"), "no lines added or lost"
    assert len(after) == len(before) - len('id="game"') + len('id="board"')


async def test_a_multiline_search_reaches_the_line_it_names(tmp_path: Path) -> None:
    """The realistic fix: the broken line and its neighbour, quoted together.

    Quoting one line is often not enough to be unique -- `}` is not -- and quoting
    three is no harder to write, which is why the ambiguity error points at it.
    """
    target = _write(tmp_path)
    result = await FsEditTool().run({
        "path": str(target),
        "search": "        y: snake[0].y + direction.y\n      }\n      return",
        "replace": "        y: snake[0].y + direction.y\n      };\n      return",
    }, CTX, _host(tmp_path))
    assert result["applied"] is True
    assert "};\n      return" in target.read_text()


async def test_several_operations_land_together(tmp_path: Path) -> None:
    target = _write(tmp_path)
    await FsEditTool().run({
        "path": str(target),
        "operations": [
            {"search": "<title>Snake</title>", "replace": "<title>Snake Game</title>"},
            {"search": "return head;", "replace": "return { head, moved: true };"},
        ],
    }, CTX, _host(tmp_path))
    text = target.read_text()
    assert "<title>Snake Game</title>" in text
    assert "moved: true" in text


async def test_one_failing_operation_writes_nothing(tmp_path: Path) -> None:
    """All or nothing, so a bad second edit cannot half-apply a change.

    The operations are applied to a copy and written once at the end, so the file
    on disk is either fully edited or untouched -- never the state between the two.
    """
    target = _write(tmp_path)
    before = target.read_text()
    with pytest.raises(ValueError, match="not found"):
        await FsEditTool().run({
            "path": str(target),
            "operations": [
                {"search": "<title>Snake</title>", "replace": "<title>Changed</title>"},
                {"search": "text that is not in this file", "replace": "x"},
            ],
        }, CTX, _host(tmp_path))
    assert target.read_text() == before, "the first operation did not land on its own"


async def test_replace_all_works_through_the_tool(tmp_path: Path) -> None:
    target = _write(tmp_path)
    result = await FsEditTool().run(
        {"path": str(target), "search": "  ", "replace": "    ", "replace_all": True},
        CTX, _host(tmp_path),
    )
    assert result["applied"] is True


async def test_the_response_carries_a_diff_of_what_changed(tmp_path: Path) -> None:
    """So an edit can be checked without reading the whole file back."""
    target = _write(tmp_path)
    result = await FsEditTool().run(
        {"path": str(target), "search": "<title>Snake</title>", "replace": "<title>Snake 2</title>"},
        CTX, _host(tmp_path),
    )
    preview = result["diff_preview"]
    assert "<title>Snake</title>" in preview and "<title>Snake 2</title>" in preview


async def test_a_unified_diff_is_accepted_instead(tmp_path: Path) -> None:
    target = _write(tmp_path)
    before = target.read_text()
    result = await FsEditTool().run({
        "path": str(target),
        "diff": "--- a/sample.html\n+++ b/sample.html\n@@ -1,1 +1,1 @@\n"
                "-<!doctype html>\n+<!DOCTYPE html>\n",
    }, CTX, _host(tmp_path))
    assert result["applied"] is True
    assert target.read_text().startswith("<!DOCTYPE html>")
    assert len(target.read_text()) == len(before), "the rest of the file survived"


def test_the_catalog_says_when_to_reach_for_it() -> None:
    """The description is the agent's only reference, and it was uninformative.

    It said "Edit a file via search/replace operations or a unified diff", which
    describes the mechanism and says nothing about when to prefer it over writing
    the file -- which is why a working tool went unused ninety-nine times.
    """
    description = FsEditTool.description
    assert "already exists" in description, "it says what the tool is for"
    assert "fs.write" in description, \
        "and names the one to use instead of rewriting -- it does not need to name itself"
    assert "must match once" in description, "including the rule it will be held to"
    assert "replace_all" in description
    props = FsEditTool.input_schema["properties"]
    assert "replace_all" in props, "and the escape hatch is in the schema it is shown"


def test_the_two_file_tools_describe_themselves_as_alternatives() -> None:
    """From the catalog alone the choice has to be legible.

    `fs.write` said "Write a file" and nothing about what it does to a file that
    is already there. So the two read as peers, and the tool with the widest
    reach -- `shell.run`, which can do either -- looked like the safer default.
    The contrast now exists in both descriptions, which is where a model chooses
    between them before it has read anything else.
    """
    from ethos.toolhost.tools.fs import FsWriteTool

    assert "replaces" in FsWriteTool.description, \
        "fs.write says outright that it replaces what is there"
    assert "fs.edit" in FsWriteTool.description, "and names the tool that does not"
    assert "leaves the rest of it alone" in FsWriteTool.description


def test_the_step_prompt_demonstrates_the_edit_and_the_shell_and_not_only_one() -> None:
    """The cause, not the symptom.

    `fs.edit` was implemented, correct, recommended in prose, and unused across
    ninety-nine operations on files. The step prompt's *only* worked example of
    an action was `{"tool": "shell.run", "args": {"command": "..."}}` -- and it
    sat last, at the point where the model chooses. A demonstration beats an
    instruction, especially one eight paragraphs earlier, so every step that
    touched a file reached for the shell regardless of what the prose said.

    Prose was added about this and it changed nothing, which is what identified
    the example as the thing doing the work. So the example now shows both
    shapes, names which is which, and puts the file edit first.
    """
    import re

    from ethos.prompts.gsl import STEP_PROMPT

    named = re.findall(r'"tool": "([^"<]+)"', STEP_PROMPT)
    assert "fs.edit" in named, "the edit shape is demonstrated, not just described"
    assert "shell.run" in named, "and so is the shell, so the model can tell them apart"
    assert named.index("fs.edit") < named.index("shell.run"), \
        "the file edit comes first, since that is the one being under-used"

    body = STEP_PROMPT.format(
        name="Clio", commissioned="C", editing="E", title="t", step_id="s1",
        step_description="x", plan_state="[]", memories="(none)", tools="(catalog)",
    )
    assert "not interchangeable" in body, "and the two are told apart explicitly"
    assert "whole-file rewrite" in body, \
        "with the reason, because the shell can do this and that is the trap"
    assert "A step that changes a file is an `fs.edit` step" in body
