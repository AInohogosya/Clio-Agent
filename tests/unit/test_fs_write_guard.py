"""The whole-file rewrite, stopped at the tool rather than in the prompt.

`fs.edit` was correct, documented, in the catalog, and used almost never. The
agent wrote files whole instead -- through `fs.write`, and mostly through a shell
heredoc, which nothing could have stopped -- and the reason is visible in what it
produced: a Snake game in one shot with a single closing brace mangled into `}n`,
which is one character in eight thousand lines and made the entire file a syntax
error. The four hundred correct lines went with it. Nothing noticed, because
nothing looked: every tool reported `ok: true` and the only evidence that anything
was wrong was in a file nothing was going to read.

Prose about this went into the step prompt, into both tool descriptions, and into
a shared rule. It changed the frequency and not the principle: the tools that
could do either were peers, and the one with the widest reach looked like the
safe default.

So the cost is here instead. Replacing a file that exists now takes
`overwrite: true`, which is a second thought rather than a refusal -- the rewrite
still works, and it works with a diff of what it replaced in the response, so the
loss is visible in the same call that causes it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ethos.config import PathsConfig
from ethos.toolhost.server import ToolContext
from ethos.toolhost.tools.fs import FsWriteTool

CTX = ToolContext(thread_id="fs-write")

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


def _write(tmp_path: Path, text: str = SOURCE, name: str = "sample.html") -> Path:
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    return target


async def test_writing_a_file_that_does_not_exist_is_untouched(tmp_path: Path) -> None:
    """The ordinary case, and the one that must not acquire friction."""
    target = tmp_path / "new.html"
    result = await FsWriteTool().run(
        {"path": str(target), "content": "<h1>hi</h1>"}, CTX, _host(tmp_path),
    )
    assert result["created"] is True
    assert target.read_text() == "<h1>hi</h1>"


async def test_replacing_a_file_that_exists_is_refused(tmp_path: Path) -> None:
    """The rule. A rewrite is one guess about every character in the file."""
    target = _write(tmp_path)
    with pytest.raises(ValueError):
        await FsWriteTool().run(
            {"path": str(target), "content": "<h1>rebuilt</h1>"}, CTX, _host(tmp_path),
        )
    assert target.read_text() == SOURCE, "and the file is untouched, not half-replaced"


async def test_the_refusal_says_how_to_proceed_and_how_big_the_file_is(tmp_path: Path) -> None:
    """An error nobody can act on is a second round trip spent for nothing."""
    target = _write(tmp_path)
    with pytest.raises(ValueError) as raised:
        await FsWriteTool().run(
            {"path": str(target), "content": "x"}, CTX, _host(tmp_path),
        )
    message = str(raised.value)
    assert "fs.edit" in message, "the tool for the job it probably wanted"
    assert "overwrite: true" in message, "and the way to mean it"
    assert f"{len(SOURCE)} bytes" in message, \
        "which is the fact that makes the choice -- this is not a small file"


async def test_overwriting_on_purpose_still_works(tmp_path: Path) -> None:
    """A deliberate rewrite is a real request and the strict rule must not refuse it."""
    target = _write(tmp_path)
    result = await FsWriteTool().run(
        {"path": str(target), "content": "<h1>deliberately rebuilt</h1>\n",
         "overwrite": True},
        CTX, _host(tmp_path),
    )
    assert result["clobbered"] is True
    assert target.read_text().startswith("<h1>deliberately rebuilt")


async def test_a_deliberate_rewrite_reports_what_it_lost(tmp_path: Path) -> None:
    """The diff comes back in the call that did the damage.

    There is nothing to compare against otherwise: the pre-image backup exists for
    undo, and undo is a thing a person does afterwards.
    """
    target = _write(tmp_path)
    result = await FsWriteTool().run(
        {"path": str(target), "content": "<h1>rebuilt</h1>\n", "overwrite": True},
        CTX, _host(tmp_path),
    )
    diff = result["replaced_diff_preview"]
    assert "<title>Snake</title>" in diff, "what was there"
    assert "+<h1>rebuilt</h1>" in diff, "and what replaced it"


async def test_appending_is_not_a_rewrite(tmp_path: Path) -> None:
    """It was the model saying it meant to replace the file."""
    target = _write(tmp_path)
    await FsWriteTool().run(
        {"path": str(target), "content": "<!-- tail -->\n", "append": True},
        CTX, _host(tmp_path),
    )
    assert target.read_text().endswith("<!-- tail -->\n")
    assert target.read_text().startswith("<!doctype html>")


async def test_an_empty_file_is_not_something_to_refuse_over(tmp_path: Path) -> None:
    """There is nothing to lose, and `touch` followed by a write is a normal pair."""
    target = tmp_path / "empty.txt"
    target.touch()
    result = await FsWriteTool().run(
        {"path": str(target), "content": "now it has content\n", "overwrite": True},
        CTX, _host(tmp_path),
    )
    assert result["bytes"] > 0


def test_the_schema_says_the_flag_is_needed() -> None:
    """The contract lives where the model is choosing.

    A parameter that is not in the schema is a parameter the model will not pass,
    and a refusal it cannot see coming is a round trip spent discovering it.
    """
    props = FsWriteTool.input_schema["properties"]
    assert "overwrite" in props, "the opt-in is in the schema the catalog renders"
    assert props["overwrite"]["type"] == "boolean"
    description = " ".join(FsWriteTool.description.split())
    assert "overwrite: true" in description
    assert "fs.edit" in description, "and the tool it should have used instead"
    assert "diff of what was replaced" in description, \
        "which is what makes the rewrite reviewable rather than silent"
