from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from ethos.paths import resolve
from ethos.toolhost.server import Tool, ToolContext, Toolhost

MAX_READ_BYTES = 2_000_000


def _resolve(host: Toolhost, path: str) -> Path:
    """A path as the agent means it. See `ethos.paths.resolve`.

    There is deliberately no containment here. Every tool below reads, writes,
    moves or deletes whatever absolute path it is handed, on any directory the
    running user can reach — which is the agent's design, not an oversight this
    function failed to prevent. What this function decides is only where a
    *relative* name lands, and that used to be the agent's own `~/.ethos` rather
    than the user's home. `host` is kept in the signature because every caller
    already passes it and because a future policy has one place to live; it is
    unused today, and that is the honest state of the permission model rather
    than a latent restriction.
    """
    del host
    return resolve(path)


def _preimage_backup(host: Toolhost, path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    backup_dir = host.config.paths.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    backup_path = backup_dir / f"{digest}-{uuid.uuid4().hex[:8]}{path.suffix}"
    shutil.copy2(path, backup_path)
    return str(backup_path)


class FsReadTool(Tool):
    name = "fs.read"
    description = "Read a text (or binary, base64) file with optional offset/limit."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "line to start from"},
            "limit": {"type": "integer", "description": "number of lines"},
            "binary": {"type": "boolean"},
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        path = _resolve(host, str(args["path"]))
        if not path.exists():
            raise FileNotFoundError(str(path))
        if path.is_dir():
            raise IsADirectoryError(str(path))
        if args.get("binary"):
            data = path.read_bytes()[:MAX_READ_BYTES]
            import base64

            return {"path": str(path), "b64": base64.b64encode(data).decode("ascii"),
                    "bytes": len(data)}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        offset = int(args.get("offset", 0))
        limit = args.get("limit")
        selected = lines[offset:] if limit is None else lines[offset:offset + int(limit)]
        return {"path": str(path), "text": "".join(selected)[:MAX_READ_BYTES],
                "total_lines": len(lines)}


class FsWriteTool(Tool):
    name = "fs.write"
    description = (
        "Write a whole file: creates it, or replaces what is there entirely. "
        "Creates parent directories. For changing part of a file that already "
        "exists use `fs.edit`, which leaves the rest of it alone. Replacing a "
        "file that is already there takes `overwrite: true` -- passing content "
        "for a path that exists is refused rather than assumed, and the error "
        "says how big the file is. Pre-image backed up for undo, and the "
        "response carries a diff of what was replaced."
    )
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean"},
            "overwrite": {
                "type": "boolean",
                "description": "required to replace a file that already exists",
            },
            "binary_b64": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        path = _resolve(host, str(args["path"]))
        existed = path.is_file()
        if existed and not args.get("append") and not args.get("overwrite"):
            # The whole-file rewrite is the failure this file's problems come
            # from, and it used to be the path of least resistance: `fs.write`
            # took a path and some content, did what it was told, and returned
            # `ok: true` with a diff of whatever it had just destroyed. The
            # prompts said to prefer `fs.edit`, and the model used the shell
            # instead -- so the friction has to be here, where a call either
            # happens or does not.
            #
            # Asking for `overwrite: true` is cheap and it is not ceremony: it
            # is the model saying it means to replace the file rather than
            # having meant to change two lines of it. Every legitimate rewrite
            # still works, and the one that was going to drop the four hundred
            # lines it did not rewrite now costs one flag and produces a diff of
            # what it lost.
            raise ValueError(
                f"refusing to replace {path}: it already exists and is "
                f"{path.stat().st_size} bytes. Use `fs.edit` to change part of "
                f"it, or pass overwrite: true to replace it deliberately -- the "
                f"response will then carry a diff of what was replaced."
            )
        original = path.read_text(encoding="utf-8", errors="replace") if existed else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = _preimage_backup(host, path)
        if args.get("binary_b64"):
            import base64

            data = base64.b64decode(args["binary_b64"])
            if args.get("append") and existed:
                with open(path, "ab") as fh:
                    fh.write(data)
            else:
                path.write_bytes(data)
        else:
            content = str(args.get("content", ""))
            if args.get("append") and existed:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(content)
            else:
                path.write_text(content, encoding="utf-8")
        replaced = ""
        if existed and not args.get("append") and args.get("overwrite"):
            replaced = "\n".join(difflib.unified_diff(
                original.splitlines(), path.read_text(
                    encoding="utf-8", errors="replace").splitlines(),
                fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm="",
            ))[:4000]
        return {"path": str(path), "bytes": path.stat().st_size,
                "created": not existed, "clobbered": bool(replaced),
                "replaced_diff_preview": replaced,
                "backup": backup, "undo_hint": backup}


class FsEditTool(Tool):
    name = "fs.edit"
    description = (
        "Edit part of a file that already exists and leave the rest exactly as it "
        "is. The tool for changing code, config or text already on disk — reach "
        "for it instead of `fs.write`, which replaces the whole file. `search` is "
        "the exact text to find, whitespace included, and must match once unless "
        "you pass `replace_all`; several changes at once go in `operations`, or "
        "pass `diff` for a unified diff. Returns a diff of what changed."
    )
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "the file to edit"},
            "search": {
                "type": "string",
                "description": "exact text to find, whitespace included; must match once",
            },
            "replace": {"type": "string", "description": "what to put in its place"},
            "replace_all": {
                "type": "boolean",
                "description": "replace every occurrence; by default an ambiguous "
                               "search is refused rather than guessed at",
            },
            "operations": {
                "type": "array",
                "description": "several search/replace pairs, applied in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string"},
                        "replace": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["search", "replace"],
                },
            },
            "diff": {"type": "string", "description": "unified diff to apply"},
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        path = _resolve(host, str(args["path"]))
        if not path.exists():
            raise FileNotFoundError(str(path))
        original = path.read_text(encoding="utf-8")
        backup = _preimage_backup(host, path)
        if args.get("diff"):
            new_text = apply_unified_diff(original, str(args["diff"]))
        else:
            default_all = bool(args.get("replace_all"))
            operations = args.get("operations") or [
                {"search": args.get("search", ""), "replace": args.get("replace", "")}
            ]
            new_text = original
            for op in operations:
                new_text = apply_search_replace(
                    new_text, str(op.get("search", "")), str(op.get("replace", "")),
                    bool(op.get("replace_all", default_all)),
                )
        if new_text is None:
            raise ValueError("edit did not apply cleanly")
        path.write_text(new_text, encoding="utf-8")
        diff = "\n".join(difflib.unified_diff(
            original.splitlines(), new_text.splitlines(),
            fromfile=str(path), tofile=str(path), lineterm="",
        ))
        return {"path": str(path), "applied": True, "backup": backup,
                "diff_preview": diff[:4000]}

def apply_search_replace(text: str, search: str, replace: str,
                         replace_all: bool = False) -> str:
    """Replace exactly one occurrence, unless told explicitly to replace them all.

    `str.replace(..., 1)` takes the first match and says nothing, which is the
    whole of the hazard here. A search string that occurs in three places --
    `}`, a closing tag, an `import` line -- is the normal case for a short
    search, and the first match is as likely to be the wrong one as the right
    one. The edit then lands somewhere the model never looked, reports success,
    and hands back a diff of a change it did not intend. Nothing downstream can
    tell: the call came back `ok: true` and the file it named is the file it
    edited.

    So the default is exactly one match, and more than one is a failure carrying
    the count, with the fix in the message: quote more of the lines around the
    one you mean. That costs one more attempt and is the difference between an
    edit the model chose and an edit it made by arithmetic.

    `replace_all` is the escape hatch, and it is a parameter rather than a
    fallback because "change every one of these" is a real request that the
    strict rule would otherwise refuse. Asking for it is the model saying it
    counted, or means it -- which is the part that was missing.

    `replace` equal to `search` is allowed: a deliberate no-op is a legitimate
    thing to ask for when a step is verifying rather than changing.
    """
    if not search:
        raise ValueError("empty search string")
    count = text.count(search)
    if count == 0:
        raise ValueError(f"search text not found: {search[:80]!r}")
    if count > 1 and not replace_all:
        raise ValueError(
            f"search text is ambiguous: {count} occurrences of {search[:80]!r}. "
            "Quote more of the lines around the one you mean so it matches once, "
            "or pass replace_all: true to change all of them."
        )
    return text.replace(search, replace) if replace_all else text.replace(search, replace, 1)


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_unified_diff(text: str, diff: str) -> str:
    lines = text.splitlines(keepends=True)
    out = list(lines)
    cursor = 0
    hunks = 0
    for raw_line in diff.splitlines():
        match = _HUNK_RE.match(raw_line)
        if match:
            start = int(match.group(1)) - 1
            cursor = max(0, min(start, len(out)))
            hunks += 1
            continue
        if raw_line.startswith("---") or raw_line.startswith("+++") or raw_line.startswith("diff "):
            continue
        if raw_line.startswith("+"):
            out.insert(cursor, raw_line[1:] + "\n")
            cursor += 1
        elif raw_line.startswith("-"):
            if cursor >= len(out) or not out[cursor].startswith(raw_line[1:]):
                raise ValueError(f"diff does not match file at line {cursor + 1}")
            del out[cursor]
        elif raw_line.startswith(" "):
            cursor += 1
        elif raw_line.strip() == "":
            cursor += 1
    if hunks == 0:
        raise ValueError("no hunks found in diff")
    return "".join(out)


class FsMoveTool(Tool):
    name = "fs.move"
    description = "Move or rename a file/directory."
    timeout_s = 60.0
    input_schema = {
        "type": "object",
        "properties": {
            "from_path": {"type": "string"},
            "to_path": {"type": "string"},
        },
        "required": ["from_path", "to_path"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        source = _resolve(host, str(args["from_path"]))
        target = _resolve(host, str(args["to_path"]))
        if not source.exists():
            raise FileNotFoundError(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {"moved": [str(source), str(target)]}


class FsDeleteTool(Tool):
    name = "fs.delete"
    description = (
        "Delete is forbidden at the gate level for commissioned artifacts and "
        "converted to a trash move (30-day retention) for agent-owned files."
    )
    timeout_s = 60.0
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        path = _resolve(host, str(args["path"]))
        if not path.exists():
            raise FileNotFoundError(str(path))
        trash: Any = host.services.get("trash")
        if trash is None:
            raise RuntimeError("trash store unavailable; delete refused")
        entry = await trash.move(path, kind="fast_path",
                                 reason=str(args.get("reason") or ctx.reason or "fs.delete"))
        return {"trashed": str(path), "trash_entry": entry.id,
                "restore_with": entry.id}


class FsListTool(Tool):
    name = "fs.list"
    description = "List a directory."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "glob": {"type": "string"}},
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        path = _resolve(host, str(args["path"]))
        pattern = args.get("glob", "*")
        entries = []
        if args.get("glob") and not path.is_dir():
            matches = sorted(path.parent.glob(pattern)) if path.parent.exists() else []
            entries = [str(m) for m in matches if os.path.abspath(str(m)) == str(path)] or \
                      [str(m) for m in path.parent.glob(pattern)][:500]
        else:
            if not path.is_dir():
                raise NotADirectoryError(str(path))
            entries = sorted(str(p) for p in path.glob(pattern))[:500]
        return {"path": str(path), "entries": entries}


class FsStatTool(Tool):
    name = "fs.stat"
    description = "Stat a path."
    timeout_s = 10.0
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        path = _resolve(host, str(args["path"]))
        if not path.exists() and not path.is_symlink():
            raise FileNotFoundError(str(path))
        stat = path.lstat()
        return {
            "path": str(path), "size": stat.st_size, "mode": oct(stat.st_mode),
            "mtime": stat.st_mtime, "is_dir": path.is_dir(), "is_file": path.is_file(),
        }


__all__ = [
    "FsReadTool", "FsWriteTool", "FsEditTool", "FsMoveTool", "FsDeleteTool",
    "FsListTool", "FsStatTool", "apply_search_replace", "apply_unified_diff",
]
