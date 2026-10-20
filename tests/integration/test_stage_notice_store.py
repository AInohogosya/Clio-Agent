"""A stage notice against a real database, and the two halves reading the same row.

The unit tests for this mechanism stub the database, which is the right way to test
what the *prompts* say and what the loop *does*, and the wrong way to test SQL. These
are here for the statements: that the unique constraint really does make a stage
arriving twice one notice rather than two, that the retry schedule is honoured by the
query that claims a debt, and that the row the solver loop writes is the row the
deliberation reads back — because the whole mechanism is two readers agreeing on one
fact, and two readers of two different queries can disagree in ways no assertion on
either one alone would show.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ethos.gateway.embeddings import HashingEmbedder
from ethos.memory.store import MemoryStore
from ethos.schemas.gsl import IntentionRecord, IntentionStatus
from ethos.social.deliberation import SocialCognition
from tests.conftest import make_config

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def no_debt_left_behind(db):
    """This module's rows are removed, and the notices are *paid*.

    Two reasons, and the second is the one that bites. The suite shares one
    database, so anything written here is still there for the scenario tests —
    an unhandled inbound `messages` row is a pending message, and an unpaid
    `stage_notices` row is a word the loop still owes. The second makes a real
    behavioural difference rather than a bookkeeping one: a `notify` pass takes
    the pass it is owed, so a leftover debt makes a scenario that was testing the
    drives or the focus hand its pass to an unrelated notification instead.

    Which is the correct behaviour, and precisely why this fixture pays rather
    than only deleting. Deleting alone would have hidden a real interaction.
    """
    yield
    await db.execute(
        "UPDATE stage_notices SET delivered_at = now() WHERE delivered_at IS NULL"
    )
    await db.execute("DELETE FROM stage_notices")
    await db.execute("DELETE FROM intentions WHERE kind = 'venture'")
    await db.execute("DELETE FROM intentions WHERE commissioned_by = 'mira'")
    # The commissioning message too: an inbound row with no `social_decision` is a
    # *pending* message, so leaving it behind hands the next scenario a question
    # nobody asked. `'mira'` is this module's own person and belongs to nobody else.
    await db.execute("DELETE FROM messages WHERE person_id IN ('mira', 'zed')")
    await db.execute("DELETE FROM intentions WHERE commissioned_by = 'zed'")


async def make_memory(db, config) -> MemoryStore:
    return MemoryStore(db, HashingEmbedder(1024), config)


async def _commission(db, memory, person_id: str = "mira", title: str = "fix the parser",
                      text: str = "please fix the parser, and tell me when it is done") -> IntentionRecord:
    """A commissioned intention, with the inbound message that commissioned it.

    The message is not decoration: the notice resolves its channel and conversation
    from the newest inbound row for that person, so a test that wrote the intention
    without it would be testing a notice with no address.

    `title` is a parameter because these tests share one database and the reply-path
    query reads every recent notice, not only this test's. A fixed title would make
    every assertion that looks for its own row find its neighbours'.
    """
    await db.execute(
        "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
        "text, attachments, delivery) "
        "VALUES ($1, now(), 'inbound', 'telegram', 'telegram:mira', $2, $3, '[]'::jsonb, "
        "'{\"status\": \"received\"}'::jsonb)",
        uuid4(), person_id, text,
    )
    intent_id = await memory.create_intention(
        title=title, desired_end_state="the parser handles the empty case",
        kind="commitment", origin="commission", commissioned_by=person_id,
    )
    intention = await memory.get_intention(intent_id)
    assert intention is not None
    return intention


async def test_a_commissioned_stage_is_one_notice_with_the_address_it_needs(db, tmp_path):
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory)

    notice = await memory.record_stage_notice(
        intention, IntentionStatus.done.value, "verified at rung 2",
    )

    assert notice is not None
    assert notice.stage == "done"
    assert notice.person_id == "mira"
    assert notice.channel == "telegram", "resolved from the message that commissioned it"
    assert notice.conversation_key == "telegram:mira"
    assert notice.delivered_at is None, "written as a debt, not as a record of a word said"


async def test_the_same_stage_reached_twice_is_still_one_notice(db, tmp_path):
    """The constraint is what makes idempotence a fact rather than a hope.

    The solver loop reaches `done` through more than one path, and a cycle that
    crashed between closing the intention and writing its episode re-enters the same
    terminal state on the next pass. Two notices for one stage would be the same
    promise announced twice.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory)

    first = await memory.record_stage_notice(intention, IntentionStatus.done.value, "one")
    second = await memory.record_stage_notice(intention, IntentionStatus.done.value, "two")

    assert first is not None and second is not None
    assert first.id == second.id, "the conflict resolves to the row already written"
    assert second.detail == "one", "and does not overwrite the first record of it"

    rows = await memory.list_stage_notices(limit=10)
    matching = [n for n in rows if n.intention_id == intention.id and n.stage == "done"]
    assert len(matching) == 1


async def test_different_stages_of_one_task_are_separate_notices(db, tmp_path):
    """A stage is a point in the work, and the points are distinct facts.

    A task that reaches `awaiting_review` and is then approved reaches `done`. Both
    are things the person asked to hear about, and collapsing them would lose the
    first — which is the one that says the work is waiting on them.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory)

    await memory.record_stage_notice(intention, IntentionStatus.awaiting_review.value, "review")
    await memory.record_stage_notice(intention, IntentionStatus.done.value, "approved")

    owed = await memory.list_stage_notices(only_owed=True, limit=10)
    stages = sorted(n.stage for n in owed if n.intention_id == intention.id)
    assert stages == ["awaiting_review", "done"]


async def test_a_waiting_debt_is_not_due_until_its_wait_is_over(db, tmp_path):
    """The curve is what stops a dead channel becoming a pass per tick.

    A notice nobody can deliver is a promise this loop will otherwise keep retrying
    every few seconds for as long as the fault lasts — spending a pass and a model
    call on each attempt, which is an outage wearing a notification's clothes.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory)
    notice = await memory.record_stage_notice(intention, IntentionStatus.done.value, "")
    assert notice is not None

    fresh = await memory.due_stage_notices(limit=200)
    assert any(n.id == notice.id for n in fresh), "a new notice is due at once"

    await memory.mark_stage_notice(
        notice.id, delivered=False, delivery={"detail": "held"},
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )

    after = await memory.due_stage_notices(limit=200)
    assert not any(n.id == notice.id for n in after), (
        "and not offered again until the wait has elapsed"
    )
    still_owed = await memory.list_stage_notices(only_owed=True, limit=50)
    assert any(n.id == notice.id for n in still_owed), "while still being a debt"

    await memory.mark_stage_notice(
        notice.id, delivered=False, delivery={"detail": "held"},
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    due_again = await memory.due_stage_notices(limit=200)
    assert any(n.id == notice.id for n in due_again), "and due again once it has"


async def test_a_settled_notice_is_evidence_to_a_reply_and_no_longer_a_debt(db, tmp_path):
    """The two halves of the mechanism, on one row, after delivery.

    This is the join that did not exist. Before it, the loop's record of the outcome
    and the reply path's record of the same fact were separate, so work finished
    hours ago was invisible to the only prompt that writes replies — which is what
    produced "I have not done that" about something that was done.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    title = "reticulate the settled splines"
    intention = await _commission(
        db, memory, title=title, text="reticulate the settled splines and tell me",
    )
    notice = await memory.record_stage_notice(
        intention, IntentionStatus.done.value, "verified at rung 2 on 3 checks",
    )
    assert notice is not None

    # Scoped to this task's own line: the query reads every recent notice, because
    # the agent's finished work is the agent's whichever person commissioned it, and
    # the block is what lets a reply answer "did you do it" without inferring.
    def mine(state) -> list[str]:
        return [line for line in state.stages if title in line]

    # While owed, the deliberation can see it and knows it has not been said.
    social = SocialCognition(config, db=db)
    while_owed = await social._execution_state(None)
    assert mine(while_owed), "finished work is visible to the reply path"
    assert any("not told them yet" in line for line in mine(while_owed))

    await memory.mark_stage_notice(
        notice.id, delivered=True, delivery={"detail": "it's in"},
    )

    settled = await social._execution_state(None)
    assert mine(settled), (
        "a delivered notice is still evidence, long after the words were sent"
    )
    assert all("not told them yet" not in line for line in mine(settled))

    due = await memory.due_stage_notices(limit=200)
    assert not any(n.id == notice.id for n in due), "and no longer a debt to pay"


async def test_work_nobody_commissioned_leaves_no_notice_and_no_debt(db, tmp_path):
    """A venture has nobody to owe a word to, and a row with no address is a debt
    the loop would keep paying into a void."""
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intent_id = await memory.create_intention(
        title="a venture of my own", kind="venture", origin="reverie",
    )
    intention = await memory.get_intention(intent_id)
    assert intention is not None

    assert await memory.record_stage_notice(intention, IntentionStatus.done.value) is None
    due = await memory.due_stage_notices(limit=200)
    assert not any(n.intention_id == intention.id for n in due)


async def test_the_notice_settles_through_the_plain_dict_the_loop_carries(db, tmp_path):
    """The id crosses a string boundary, and the column is a uuid.

    `decide` puts the notice on `Decision.stage` as JSON and `act` reads it back, so
    the id arrives at the store as a `str` for a `uuid` column. Every other write
    into `stage_notices` goes through a typed model and would raise here rather
    than fail quietly, so the round trip is asserted against the real column
    instead of against a mock that accepts anything.
    """
    from datetime import timedelta

    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory, person_id="zed",
                                  title="tidy the vector store", text="tidy the vector store")
    notice = await memory.record_stage_notice(intention, IntentionStatus.done.value, "ok")
    assert notice is not None

    # Exactly what `Life._settle_stage_notice` does with `Decision.stage`.
    await memory.mark_stage_notice(
        str(notice.id), delivered=True, delivery={"detail": "sent"},
    )

    settled = await memory.get_stage_notice(intention.id, "done")
    assert settled is not None and settled.delivered_at is not None
    assert settled.delivery == {"detail": "sent"}

    # And the deferral path, which writes a timestamp rather than one.
    await db.execute(
        "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
        "text, attachments, delivery) VALUES (gen_random_uuid(), now(), 'inbound', 'web', "
        "'web:zed', 'zed', 'park it', '[]'::jsonb, '{}'::jsonb)"
    )
    second_id = await memory.create_intention(
        title="park me", kind="commitment", origin="commission", commissioned_by="zed",
    )
    second = await memory.get_intention(second_id)
    assert second is not None
    parked = await memory.record_stage_notice(second, IntentionStatus.parked.value, "stalled")
    assert parked is not None

    await memory.mark_stage_notice(
        str(parked.id), delivered=False, delivery={"detail": "held"},
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )

    waited = await memory.get_stage_notice(second_id, "parked")
    assert waited is not None
    assert waited.attempts == 1, "the attempt is counted"
    assert waited.next_attempt_at is not None, "and the wait is stored with it"
    assert waited.delivered_at is None, "while the debt is still owed"


# --------------------------------------------------------------------------
# find_open_intention: the same work asked for twice is one row, not two
# --------------------------------------------------------------------------


async def test_asking_twice_for_the_same_work_finds_the_row_that_is_already_open(db, tmp_path):
    """The query that stops a promise from being restarted from nothing.

    The agent writes the work down when it answers somebody. A person who asks
    again is asking because the first ask did not visibly happen, and the answer
    to that is to carry on with the row that is already open — not to write a
    second one. This is the lookup that decides, and it is asserted against the
    real rows because the whole failure was rows: three intentions with one
    title, `understanding` re-written to each in turn, and no plan ever made.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory, person_id="mira",
                                  title="Use APEX to change device mode from dark to light",
                                  text="please have APEX switch it to light mode")

    found = await memory.find_open_intention(
        "Use APEX to change device mode from dark to light", commissioned_by="mira",
    )

    assert found is not None, "the work already on the books was not found"
    assert found.id == intention.id, "so the second ask opened a different row"


async def test_the_open_row_is_found_whatever_the_person_typed_this_time(db, tmp_path):
    """Titles arrive from a model's `intention_spec`, and the same request spelled
    a second time is not byte-identical: case and spacing drift while the work does
    not. Matching has to survive that or it does not fire on the case it exists for.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory, person_id="mira",
                                  title="Use APEX to change device mode from dark to light",
                                  text="please have APEX switch it to light mode")

    found = await memory.find_open_intention(
        "  use apex to change device   mode from dark to light  ",
        commissioned_by="mira",
    )

    assert found is not None and found.id == intention.id


async def test_different_work_and_other_people_are_not_the_same_work(db, tmp_path):
    """What the lookup deliberately does not do.

    Both halves of this are wrong answers rather than missed ones. Merging two
    genuinely different pieces of work loses one of them outright; matching across
    commissioners would hand a stranger a promise somebody else made. So neither
    matches, and the caller opens a new row.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    await _commission(db, memory, person_id="mira", title="switch the desktop to light mode",
                      text="switch the desktop to light mode")

    other_work = await memory.find_open_intention(
        "switch the desktop to dark mode", commissioned_by="mira",
    )
    other_person = await memory.find_open_intention(
        "switch the desktop to light mode", commissioned_by="zed",
    )

    assert other_work is None, "a different piece of work was adopted as the same one"
    assert other_person is None, "somebody else's promise was adopted as this person's"


async def test_closed_work_is_not_resurrected_by_being_asked_for_again(db, tmp_path):
    """`done` and `parked` are conclusions, and a conclusion is not a pause.

    Being asked for the same thing again after it was closed is new work: the
    owner is not asking for the old attempt to resume, they are asking for it to
    happen. Returning the closed row would make the agent answer a settled
    question by re-running a decision somebody already made.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    intention = await _commission(db, memory, person_id="mira", title="rotate the deploy key",
                                  text="rotate the deploy key")
    await memory.update_intention(
        intention.id, status=IntentionStatus.done.value, closed_reason="rotated on the 3rd",
    )

    assert await memory.find_open_intention("rotate the deploy key", commissioned_by="mira") is None


async def test_the_row_adopted_is_the_one_that_got_furthest(db, tmp_path):
    """Adopting must not cost progress.

    Duplicates already exist in the field, so this lookup has to choose between
    them, and the choice is between throwing a plan away and keeping it. The
    furthest-along open row wins; `resume_state` is what it carries.
    """
    config = make_config(tmp_path)
    config.paths.ensure()
    memory = await make_memory(db, config)
    old = await _commission(db, memory, person_id="mira", title="reconcile the ledger",
                            text="reconcile the ledger")
    fresh = await _commission(db, memory, person_id="mira", title="Reconcile  the Ledger",
                              text="still, reconcile the ledger")
    await memory.update_intention(
        old.id, status=IntentionStatus.in_progress.value,
        resume_state={"understanding": "the ledger drifted on the 3rd", "plan": {"steps": []}},
    )

    found = await memory.find_open_intention("reconcile the ledger", commissioned_by="mira")

    assert found is not None and found.id == old.id, \
        "the row that had planned the work was not the one kept"
    assert found.resume_state.get("plan") is not None, "so its progress survived"
    assert found.id != fresh.id
