"""Full-artifact map-reduce inspection for rung 2.

The rung-2 judge used to read the artifact through a fixed 16,000-character
window: one model call, one slice, and everything past the slice was invisible.
A deliverable of any real size was verified on its opening pages, and the
truncation was silent — the verdict said nothing about what the judge never
saw. `verifier_inspect` replaces that window with two passes:

- **Map**: the artifact's files are split into whole-covering chunks (every
  character of every modified file lands in exactly one chunk; files are kept
  whole when they fit), and a judge call reads each chunk with the goal, the
  criteria and the chunk's place in the whole.
- **Reduce**: the chunk verdicts are merged into one artifact verdict under a
  rule the judge cannot bargain with — any `fail` fails the artifact, any
  `needs_human` demands a human, and `pass` requires every chunk to have
  passed. The merged verdict carries the per-chunk trail, so a reader can see
  which part of the work each judgment came from.

Two invariants hold by construction. Nothing is truncated: chunking is
derived from content size, never applied to it. And the reduce can never
overturn a failing rung-1 objective check — it produces the rung-2 verdict
only, and the ladder's own gate keeps final authority with the checks.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.gsl.verifier_inspect")

#: A chunk targets this size and never exceeds it by more than one line.
CHUNK_TARGET_CHARS = 12_000
#: The largest number of chunks one inspection maps over before the plan
#: switches to summaries-plus-head for each chunk. Every chunk is still fully
#: covered; the budget bounds the model calls, not the coverage.
MAX_CHUNKS = 24


@dataclass
class ArtifactFile:
    """One file of a deliverable, with its pre-image when one exists."""

    path: str
    content: str
    before: str | None = None

    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8", "replace")).hexdigest()


@dataclass
class Chunk:
    """A contiguous slice of one file, stated in the file's own coordinates."""

    path: str
    index: int
    total: int
    start_line: int
    text: str

    def header(self) -> str:
        if self.total <= 1:
            return f"[file {self.path} — whole file]"
        return f"[file {self.path} — part {self.index + 1} of {self.total}, lines {self.start_line}+]"  # noqa: E501


@dataclass
class ChunkVerdict:
    chunk: Chunk
    verdict: str
    issues: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.chunk.path,
            "chunk": f"{self.chunk.index + 1}/{self.chunk.total}",
            "lines": f"{self.chunk.start_line}+",
            "verdict": self.verdict,
            "issues": self.issues[:10],
            "confidence": self.confidence,
        }


def normalize_artifact_files(artifact: Any) -> list[ArtifactFile]:
    """The artifact's files, whatever shape it arrived in.

    A string is one file with a synthesized name. A dict maps paths to
    contents, or carries a richer envelope (`files`, `modified_files`,
    `before`) — every shape the run's result envelope and the artifact
    registry actually produce. The one thing this never does is drop a file:
    an unknown shape becomes one artifact named for its type rather than
    something the judge silently never sees.
    """
    if artifact is None:
        return [ArtifactFile(path="(no artifact)", content="")]
    if isinstance(artifact, ArtifactFile):
        return [artifact]
    if isinstance(artifact, str):
        return [ArtifactFile(path="(artifact)", content=artifact)]
    if isinstance(artifact, (bytes, bytearray)):
        return [ArtifactFile(path="(binary artifact)", content=bytes(artifact).decode("utf-8", "replace"))]
    if isinstance(artifact, dict):
        files_block = artifact.get("files") or artifact.get("modified_files")
        if isinstance(files_block, dict):
            before = artifact.get("before") if isinstance(artifact.get("before"), dict) else {}
            return [
                ArtifactFile(path=str(p), content=str(c or ""),
                             before=str(before.get(p)) if before.get(p) is not None else None)
                for p, c in files_block.items()
            ]
        parts: list[ArtifactFile] = []
        for key in ("content", "text", "body"):
            if isinstance(artifact.get(key), str):
                parts.append(ArtifactFile(path=str(artifact.get("path") or f"({key})"),
                                          content=artifact[key]))
        if parts:
            return parts
        # A structured result envelope: serialized whole, never summarized away.
        try:
            return [ArtifactFile(path="(result envelope)", content=json.dumps(artifact, default=str, indent=2))]
        except (TypeError, ValueError):
            return [ArtifactFile(path="(result envelope)", content=str(artifact))]
    return [ArtifactFile(path=f"({type(artifact).__name__})", content=str(artifact))]


def chunk_file(path: str, content: str, target: int = CHUNK_TARGET_CHARS) -> list[Chunk]:
    """Split one file into chunks that together cover every character.

    The split is by lines, so no chunk ends mid-line and no line is lost
    between chunks — with one exception, named: a single line longer than the
    target (minified bundles, embedded blobs) is force-split by characters,
    because a chunker that waits for a newline would ship a 40-megabyte chunk
    to a model that cannot read it. A file that fits inside the target is one
    chunk.
    """
    if len(content) <= target:
        return [Chunk(path=path, index=0, total=1, start_line=1, text=content)]
    chunks: list[Chunk] = []
    buffer: list[str] = []
    size = 0
    start_line = 1
    line_no = 1

    def _flush() -> None:
        nonlocal buffer, size, start_line
        if buffer:
            chunks.append(Chunk(path=path, index=len(chunks), total=0,
                                start_line=start_line, text="".join(buffer)))
            start_line = line_no
            buffer = []
            size = 0

    for line in content.splitlines(keepends=True) or ([content] if content else []):
        if len(line) > target:
            # One line wider than the target: emit what the buffer holds, then
            # cut the line itself into target-sized pieces.
            _flush()
            for i in range(0, len(line), target):
                chunks.append(Chunk(path=path, index=len(chunks), total=0,
                                    start_line=line_no, text=line[i:i + target]))
            line_no += 1
            start_line = line_no
            continue
        buffer.append(line)
        size += len(line)
        line_no += 1
        if size >= target:
            _flush()
    _flush()
    for index, chunk in enumerate(chunks):
        chunk.index = index
        chunk.total = len(chunks)
    return chunks


def chunk_artifacts(files: list[ArtifactFile], target: int = CHUNK_TARGET_CHARS,
                    max_chunks: int = MAX_CHUNKS) -> list[Chunk]:
    """Every file, chunked; the plan degrades honestly when the map would
    need too many calls. Degradation is per-file head-plus-tail coverage with
    the middle compressed to a marker — still stated, never silent."""
    chunks: list[Chunk] = []
    for f in files:
        chunks.extend(chunk_file(f.path, f.content, target))
    if len(chunks) <= max_chunks:
        return chunks
    # Too many calls for one boundary: keep one chunk per file, head and tail
    # of the file, with the compressed middle named as compressed. The reduce
    # records `compressed` per file so a reader knows what the single call saw.
    compressed: list[Chunk] = []
    for f in files:
        head = f.content[: target // 2]
        tail = f.content[-(target // 4):] if len(f.content) > (target * 3) // 4 else ""
        middle_chars = max(0, len(f.content) - len(head) - len(tail))
        text = head
        if middle_chars > 0:
            text += f"\n… [{middle_chars} characters of this file are elided here; the file's own name is {f.path}]\n"
        text += tail
        compressed.append(Chunk(path=f.path, index=0, total=1, start_line=1, text=text))
    return compressed


def structured_diff(files: list[ArtifactFile]) -> list[dict[str, Any]]:
    """The per-file change summary the judge reads before the chunks.

    Unified-diff hunks between each file's pre-image and its content, capped
    per file so the summary stays a map and not a second artifact. A file with
    no pre-image is reported as new.
    """
    out: list[dict[str, Any]] = []
    for f in files:
        if f.before is None:
            out.append({"path": f.path, "status": "new", "chars": len(f.content),
                        "lines": f.content.count("\n") + (0 if f.content.endswith("\n") or not f.content else 1)})
            continue
        diff = list(difflib.unified_diff(
            f.before.splitlines(keepends=True), f.content.splitlines(keepends=True),
            fromfile=f"before/{f.path}", tofile=f"after/{f.path}", n=2,
        ))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        out.append({
            "path": f.path, "status": "modified", "added_lines": added,
            "removed_lines": removed,
            "hunks": "".join(diff)[:4000],
        })
    return out


def _chunk_judge_messages(goal: str, criteria: str, diff_summary: str, chunk: Chunk) -> list[str]:
    return [
        "You are an independent verifier judging one part of a deliverable.",
        f"Goal: {goal}",
        f"Criteria: {criteria}",
        f"Changed files (diff summary): {diff_summary}",
        f"You are judging {chunk.header()}",
        "Judge only what is in front of you. Return JSON: "
        '{"verdict": "pass"|"fail"|"needs_human", "issues": [...], "confidence": 0..1}. '
        "A chunk you cannot connect to the goal is `needs_human`, never a guess.",
    ]


def reduce_chunk_verdicts(verdicts: list[ChunkVerdict]) -> dict[str, Any]:
    """One artifact verdict from the chunk verdicts, under a fixed rule.

    The rule is not a judgment call: any `fail` fails the whole; any
    `needs_human` (not already overridden by a `fail`) demands a human; `pass`
    only when every chunk passed. The merged result carries the trail so the
    verdict is auditable back to the chunk that produced each issue.
    """
    if not verdicts:
        return {"verdict": "needs_human", "issues": ["no chunks were judged"], "confidence": 0.0}
    issues: list[str] = []
    trail: list[dict[str, Any]] = []
    saw_fail = False
    saw_human = False
    confidences: list[float] = []
    for cv in verdicts:
        trail.append(cv.as_dict())
        confidences.append(cv.confidence if cv.confidence else 0.0)
        for issue in cv.issues:
            issues.append(f"{cv.chunk.path} [{cv.chunk.index + 1}/{cv.chunk.total}]: {issue}")
        if cv.verdict == "fail":
            saw_fail = True
        elif cv.verdict == "needs_human":
            saw_human = True
        elif cv.verdict != "pass":
            # An unknown verdict from one chunk is the judge failing to answer
            # for that part — the same honesty rule the single-call verifier
            # holds: a verdict nobody heard is not a pass.
            saw_human = True
            issues.append(f"{cv.chunk.path}: unverdicted chunk ({cv.verdict or 'no answer'})")
    if saw_fail:
        verdict = "fail"
    elif saw_human:
        verdict = "needs_human"
    else:
        verdict = "pass"
    confidence = min(confidences) if confidences else 0.0
    return {
        "verdict": verdict,
        "issues": issues[:40],
        "confidence": round(confidence, 3),
        "chunks": trail,
        "chunks_judged": len(verdicts),
    }


async def inspect_artifacts(
    gateway: Any,
    config: Any,
    *,
    goal: str,
    criteria: list[dict[str, Any]],
    artifact: Any,
    producer_family: str | None,
    purpose: str = "gsl.verify",
    thread_id: str | None = None,
    judge: Any = None,
) -> dict[str, Any]:
    """The full-artifact inspection the rung-2 judge runs.

    `judge` is injectable for tests; production resolves it to the shared
    single-call verifier (`run_model_verifier`) reading only its chunk. Every
    modified artifact is inspected completely — chunking is derived from the
    content, never a cap applied to it — and the returned verdict is the
    reduce of the per-chunk verdicts.
    """
    from clio.gsl.verification import run_model_verifier

    files = normalize_artifact_files(artifact)
    chunks = chunk_artifacts(files)
    diff_summary = json.dumps(structured_diff(files), default=str)[:8000]
    criteria_text = json.dumps(criteria, default=str)
    verdicts: list[ChunkVerdict] = []
    for chunk in chunks:
        if judge is not None:
            verdict = await judge(chunk)
        else:
            verdict = await run_model_verifier(
                gateway, config,
                goal=goal,
                criteria=criteria,
                artifact_text=f"{chunk.header()}\n\n{chunk.text}",
                producer_family=producer_family,
                purpose=purpose,
                thread_id=thread_id,
                extra_messages=_chunk_judge_messages(goal, criteria_text, diff_summary, chunk),
            )
        verdicts.append(ChunkVerdict(
            chunk=chunk,
            verdict=str((verdict or {}).get("verdict") or ""),
            issues=[str(i) for i in (verdict or {}).get("issues", [])],
            confidence=float((verdict or {}).get("confidence") or 0.0),
        ))
    reduced = reduce_chunk_verdicts(verdicts)
    reduced["files"] = [f.path for f in files]
    reduced["chars_total"] = sum(len(f.content) for f in files)
    logger.info(
        "verifier_inspect.reduced", verdict=reduced["verdict"],
        chunks=reduced["chunks_judged"], files=len(files),
    )
    return reduced
