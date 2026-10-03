"""
The page the agent is shown, assembled once per pass.

The blocks were a faithful rendering of each subsystem's current state and nothing
else: `- [open] Ship the 2.1 release notes (priority=0.7, deadline=..., commissioned_by=mira)`,
`State: THINKING_FOCUSED`, `Energy: 0.82`. Every value in those lines is right and
none of them is legible as English, so the fields a decision turns on — who is
waiting, whether anything is owed, how much of the day is left — were present in the
prompt and unavailable to read.

The changes here are all one of two kinds. Either a field that had no meaning without
its scale was given one, or a machine-shaped line was rendered as a sentence. Nothing
here changes what is true in the prompt, only whether it can be acted on.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ethos.engine.workspace import BLOCK_ORDER, STABLE_BLOCKS, WorkspaceAssembler
from ethos.prompts.identity import build_identity_kernel
from ethos.schemas.gsl import Focus


def assemble(**overrides):
    kwargs = {
        "identity_doc": build_identity_kernel("Aria"),
        "tools": [],
        "focus": None,
        "percepts": [],
        "state": "THINKING_FOCUSED",
        "energy": 0.82,
        "budget_summary": "day $1.20/$5.00",
        "commitments": [],
        "memories": [],
        "thoughts": [],
        "now": datetime(2026, 10, 2, 9, 33, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return WorkspaceAssembler(None).assemble(**kwargs)


def block_of(workspace, name: str) -> str:
    return next(b.text for b in workspace.blocks if b.name == name)


# ------------------------------------------------------------------- the page


def test_the_name_is_not_repeated_among_the_facts_of_the_moment():
    """
    It used to be a line of the situation block, from the same store the identity
    document is built from.

    Repeating it could only ever disagree with the identity by the assembler being
    wrong, so it bought nothing. What it cost is subtler: a name appearing both as
    part of the agent's identity and among the volatile facts of the moment reads as
    something that varies, and it is the one field in this prompt that is a person's
    word for the thing they are talking to — the last one to look like weather.
    """
    workspace = assemble()
    assert "- Name:" not in block_of(workspace, "situation")
    assert "Aria" in block_of(workspace, "identity")


def test_energy_carries_the_scale_it_is_on():
    """
    `0.82` is not a reading of anything without knowing what 1.0 is and when the
    floor is — and the floor is the part that matters, because it is above the
    low-energy threshold, so an agent seeing a low number has to know that the night
    is not a reason to stop.
    """
    situation = block_of(assemble(energy=0.82), "situation")
    assert "0.82 of 1.00" in situation
    assert "0.55" in situation


def test_a_focus_with_no_bookmark_has_no_line_for_the_thing_it_has_not_got():
    """
    It used to render `Mental context: -` unconditionally, so a focus with nothing
    bookmarked carried a line that looked like a value and was the absence of one —
    on the page where the agent is about to decide how to spend the next few minutes.
    """
    focus = Focus(title="Ship the release notes", kind="commitment", why="Mira asked")
    block = block_of(assemble(focus=focus), "focus")
    assert "Mental context" not in block
    assert "not started" in block


def test_a_bookmarked_focus_says_where_it_left_off():
    """The other half of the same change: when there is one, it is worth the line."""
    focus = Focus(
        title="Ship the release notes", kind="commitment", why="Mira asked",
        bookmark={"next_planned_step": "draft the changelog",
                  "mental_context": "tags are pushed; only the notes are missing"},
    )
    block = block_of(assemble(focus=focus), "focus")
    assert "draft the changelog" in block
    assert "tags are pushed" in block


# --------------------------------------------------------------- commitments


def test_a_commitment_says_who_is_waiting():
    """
    `commissioned_by=mira` is a column. "asked for by Mira" is a reason to do it
    today, and it is the whole difference between a promise and a note.
    """
    block = block_of(assemble(commitments=[
        {"status": "open", "title": "Ship the 2.1 notes", "priority": 0.7,
         "deadline": None, "commissioned_by": "mira"},
    ]), "commitments")
    assert "Ship the 2.1 notes" in block
    assert "asked for by mira" in block
    assert "priority=" not in block


def test_a_commitment_nobody_asked_for_is_marked_as_the_agents_own():
    """
    `commissioned_by=self` was the value for both "I chose this" and "nobody set it",
    and the fallback in the old rendering was `commissioned_by=self` for a row where
    the column was simply empty. An intention I took on myself still outranks whatever
    I thought of next, so it is worth being able to see that it is mine.
    """
    block = block_of(assemble(commitments=[
        {"status": "open", "title": "Read the post on causal inference", "priority": 0.3,
         "deadline": None, "commissioned_by": None},
    ]), "commitments")
    assert "taken on by me" in block


def test_a_deadline_is_shown_and_its_absence_is_not_left_to_inference():
    """`deadline=none` reads as a column too. Both halves have to be plain."""
    with_deadline = block_of(assemble(commitments=[
        {"status": "open", "title": "Ship the notes", "priority": 0.7,
         "deadline": "2026-10-03T09:00:00+00:00", "commissioned_by": "mira"},
    ]), "commitments")
    assert "due 2026-10-03T09:00:00+00:00" in with_deadline
    without = block_of(assemble(commitments=[
        {"status": "open", "title": "Ship the notes", "priority": 0.7,
         "deadline": None, "commissioned_by": "mira"},
    ]), "commitments")
    assert "no deadline" in without


def test_no_commitments_says_nothing_is_owed():
    """`(none open)` is a database result. What it means is that nothing is owed."""
    assert "nothing is owed" in block_of(assemble(), "commitments")


# ------------------------------------------------------------------- shape


def test_the_block_order_and_the_cache_boundary_are_unchanged():
    """
    The identity, tools and limits blocks are what every turn shares, and the
    boundary between them and everything else is what makes the prefix cacheable.

    A change to any of the three is a cache miss on the next call and nothing more;
    a change to the order moves the boundary, which invalidates the prefix for as
    long as the new arrangement persists. This is the cheap guard against a framing
    change quietly becoming a cost.
    """
    workspace = assemble()
    assert [b.name for b in workspace.blocks] == [b for b in BLOCK_ORDER if b
                                                 in {x.name for x in workspace.blocks}]
    assert STABLE_BLOCKS == 3
    assert [b.name for b in workspace.blocks[:STABLE_BLOCKS]] == [
        "identity", "tools", "limits"]


def test_the_identity_block_still_leads_the_stable_prefix():
    """The first thing read has to be what this is, before what it can do."""
    assert "I am Aria, an AI agent based on Clio Agent 3 Beta 1." in \
        assemble().stable_prefix_text()
