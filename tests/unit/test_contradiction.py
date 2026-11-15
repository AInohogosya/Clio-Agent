"""Speed-1 inline contradiction resolution: verdicts, coerce, lexical fallback."""
from __future__ import annotations

from clio.memory.contradiction import (
    InlineContradictionResolver,
    Neighbor,
    coerce_verdict,
    lexical_verdict,
)


def _neighbor(statement: str, cosine: float = 0.9) -> Neighbor:
    return Neighbor(id="old-1", subject="deadline", statement=statement, cosine=cosine)


async def test_a_judge_supersede_verdict_is_carried_through():
    async def judge(new, neighbors):
        return {"verdict": "supersede_old", "reason": "the date changed"}

    resolver = InlineContradictionResolver(judge=judge)
    verdicts = await resolver.resolve(
        {"id": "new-1", "subject": "deadline", "statement": "the deadline is Friday"},
        [_neighbor("the deadline is Thursday")],
    )
    assert verdicts[0].verdict == "supersede_old"
    assert verdicts[0].basis == "model"


async def test_an_unreadable_judge_answer_keeps_both_facts():
    async def judge(new, neighbors):
        return {"verdict": "explode"}

    resolver = InlineContradictionResolver(judge=judge)
    verdicts = await resolver.resolve(
        {"statement": "x"}, [_neighbor("y")],
    )
    assert verdicts[0].verdict == "keep_new"


async def test_a_merge_without_a_merged_statement_is_demoted_to_keep():
    async def judge(new, neighbors):
        return {"verdict": "merge", "merged_statement": "  "}

    resolver = InlineContradictionResolver(judge=judge)
    verdicts = await resolver.resolve({"statement": "x"}, [_neighbor("y")])
    assert verdicts[0].verdict == "keep_new"


async def test_a_failing_judge_keeps_both_rather_than_erroring():
    async def judge(new, neighbors):
        raise RuntimeError("no model")

    resolver = InlineContradictionResolver(judge=judge)
    verdicts = await resolver.resolve({"statement": "x"}, [_neighbor("y")])
    assert verdicts[0].verdict == "keep_new"


async def test_neighbors_below_the_threshold_are_not_judged():
    calls: list[str] = []

    async def judge(new, neighbors):
        calls.append("judged")
        return {"verdict": "supersede_old"}

    resolver = InlineContradictionResolver(judge=judge)
    verdicts = await resolver.resolve(
        {"statement": "x"}, [_neighbor("y", cosine=0.5)],
    )
    assert verdicts == [] and calls == []


def test_the_lexical_fallback_supersedes_only_on_explicit_markers():
    marker = lexical_verdict("the deadline is Friday instead of Thursday", _neighbor("x"))
    assert marker.verdict == "supersede_old" and marker.basis == "lexical"
    quiet = lexical_verdict("the deadline is Friday", _neighbor("x"))
    assert quiet.verdict == "keep_new"


async def test_no_judge_means_the_lexical_rule():
    resolver = InlineContradictionResolver(judge=None)
    verdicts = await resolver.resolve(
        {"statement": "the office has moved to Shibuya"},
        [_neighbor("the office is in Ebisu")],
    )
    assert verdicts[0].verdict == "supersede_old"


def test_coerce_accepts_each_documented_verdict():
    for verdict in ("keep_new", "merge", "supersede_old"):
        raw = {"verdict": verdict}
        if verdict == "merge":
            raw["merged_statement"] = "both"
        assert coerce_verdict(raw, _neighbor("n")).verdict == verdict


async def test_resolution_against_many_neighbors():
    async def judge(new, neighbors):
        neighbor = neighbors[0]
        return {"verdict": "supersede_old"} if "thursday" in neighbor.statement.lower() \
            else {"verdict": "keep_new"}

    resolver = InlineContradictionResolver(judge=judge)
    verdicts = await resolver.resolve(
        {"statement": "the deadline is Friday"},
        [
            Neighbor("a", "deadline", "the deadline is Thursday", 0.93),
            Neighbor("b", "deadline", "deadlines matter", 0.85),
        ],
    )
    by_id = {v.neighbor_id: v.verdict for v in verdicts}
    assert by_id == {"a": "supersede_old", "b": "keep_new"}
