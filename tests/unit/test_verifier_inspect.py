"""Full-artifact map-reduce verification: every byte judged, no silent slice."""
from __future__ import annotations

from typing import Any

import pytest

from clio.gsl.verifier_inspect import (
    ArtifactFile,
    Chunk,
    ChunkVerdict,
    chunk_artifacts,
    chunk_file,
    normalize_artifact_files,
    reduce_chunk_verdicts,
    structured_diff,
)


def test_chunking_covers_every_character_of_a_large_file():
    body = "\n".join(f"line {i}: " + "x" * 40 for i in range(1500))
    chunks = chunk_file("src/big.py", body)
    assert len(chunks) > 1
    rebuilt = "".join(c.text for c in chunks)
    assert rebuilt == body, "chunking must lose nothing between chunks"
    assert all(c.total == len(chunks) for c in chunks)
    assert chunks[0].start_line == 1
    assert chunks[1].start_line > 1


def test_a_small_file_is_one_chunk():
    chunks = chunk_file("a.py", "print('hi')\n")
    assert len(chunks) == 1 and chunks[0].header().endswith("whole file]")


def test_chunking_artifacts_never_truncates_a_multi_file_deliverable():
    files = [
        ArtifactFile(path="a.py", content="a" * 30_000),
        ArtifactFile(path="b.py", content="b" * 30_000),
        ArtifactFile(path="notes.md", content="small"),
    ]
    chunks = chunk_artifacts(files)
    covered = sum(len(c.text) for c in chunks)
    assert covered >= 60_000, "every character of the big files is in some chunk"
    paths = {c.path for c in chunks}
    assert {"a.py", "b.py", "notes.md"} <= paths


def test_too_many_chunks_degrade_with_the_middle_named_not_silent():
    files = [ArtifactFile(path=f"f{i}.py", content="z" * 40_000) for i in range(40)]
    chunks = chunk_artifacts(files, target=1_000, max_chunks=8)
    assert len(chunks) <= 8 * 40 or len(chunks) <= 40
    joined = "".join(c.text for c in chunks)
    assert "elided" in joined, "the compressed middle is named as compressed"
    assert "z" in joined, "the head still carries real content"


def test_structured_diff_reports_new_and_modified_files():
    files = [
        ArtifactFile(path="new.py", content="x = 1\n"),
        ArtifactFile(path="mod.py", content="y = 2\n", before="y = 1\n"),
    ]
    summary = structured_diff(files)
    by_path = {entry["path"]: entry for entry in summary}
    assert by_path["new.py"]["status"] == "new"
    assert by_path["mod.py"]["status"] == "modified"
    assert by_path["mod.py"]["added_lines"] == 1
    assert by_path["mod.py"]["removed_lines"] == 1
    assert "-y = 1" in by_path["mod.py"]["hunks"] and "+y = 2" in by_path["mod.py"]["hunks"]


def test_normalize_never_drops_an_unknown_artifact_shape():
    weird = object()
    files = normalize_artifact_files(weird)
    assert len(files) == 1
    assert "object" in files[0].path
    envelope = {"files": {"src/a.py": "content a", "src/b.py": "content b"},
                "before": {"src/a.py": "old a"}}
    files = normalize_artifact_files(envelope)
    assert {f.path for f in files} == {"src/a.py", "src/b.py"}
    assert files[0].before is not None or files[1].before is not None


def test_reduce_any_fail_fails_the_whole():
    verdicts = [
        ChunkVerdict(chunk=Chunk("a.py", 0, 2, 1, "text"), verdict="pass", confidence=0.9),
        ChunkVerdict(chunk=Chunk("a.py", 1, 2, 400, "text"), verdict="fail", confidence=0.8,
                     issues=["off-by-one in the parser"]),
    ]
    reduced = reduce_chunk_verdicts(verdicts)
    assert reduced["verdict"] == "fail"
    assert any("off-by-one" in issue for issue in reduced["issues"])
    assert reduced["chunks_judged"] == 2
    assert reduced["confidence"] == pytest.approx(0.8)


def test_reduce_pass_requires_every_chunk_to_pass():
    verdicts = [
        ChunkVerdict(chunk=Chunk("a.py", 0, 2, 1, "text"), verdict="pass", confidence=0.9),
        ChunkVerdict(chunk=Chunk("a.py", 1, 2, 400, "text"), verdict="needs_human",
                     issues=["cannot connect this chunk to the goal"]),
    ]
    reduced = reduce_chunk_verdicts(verdicts)
    assert reduced["verdict"] == "needs_human"


def test_reduce_an_unverdicted_chunk_is_never_a_pass():
    verdicts = [ChunkVerdict(chunk=Chunk("a.py", 0, 1, 1, "text"), verdict="", issues=[])]
    reduced = reduce_chunk_verdicts(verdicts)
    assert reduced["verdict"] == "needs_human"
    assert any("unverdicted" in i for i in reduced["issues"])


def test_reduce_issues_carry_their_chunk_provenance():
    verdicts = [
        ChunkVerdict(chunk=Chunk("big.py", 1, 3, 500, "text"), verdict="fail",
                     issues=["syntax error"], confidence=1.0),
    ]
    reduced = reduce_chunk_verdicts(verdicts)
    assert reduced["issues"][0].startswith("big.py [2/3]")


def test_reduce_no_chunks_is_needs_human():
    assert reduce_chunk_verdicts([])["verdict"] == "needs_human"


async def test_inspect_routes_every_chunk_through_the_judge():
    from clio.gsl.verifier_inspect import inspect_artifacts

    calls: list[str] = []

    async def fake_verifier(chunk: Any) -> dict[str, Any]:
        calls.append(chunk.text)
        return {"verdict": "pass", "issues": [], "confidence": 0.9}

    big = {"files": {f"src/f{i}.py": "a" * 30_000 for i in range(3)}}
    verdict = await inspect_artifacts(
        None, None, goal="build it", criteria=[], artifact=big,
        producer_family=None, judge=fake_verifier,
    )
    assert verdict["verdict"] == "pass"
    assert verdict["chunks_judged"] >= 3
    total = sum("a" * 30_000 in call or call.count("a") > 1000 for call in calls)
    assert total == len(calls)
    assert verdict["chars_total"] == 90_000


async def test_inspect_failing_chunk_fails_the_deliverable():
    from clio.gsl.verifier_inspect import inspect_artifacts

    async def fake_verifier(chunk: Any) -> dict[str, Any]:
        return {"verdict": "fail", "issues": ["bad"]} if "part 2" in chunk.header() \
            else {"verdict": "pass", "issues": [], "confidence": 0.9}

    big = {"files": {"src/one.py": "a" * 30_000}}
    verdict = await inspect_artifacts(
        None, None, goal="g", criteria=[], artifact=big,
        producer_family=None, judge=fake_verifier,
    )
    assert verdict["verdict"] == "fail"
