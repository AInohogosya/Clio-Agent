from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from ethos.social.deliberation import SocialCognition
from tests.conftest import make_config

pytestmark = pytest.mark.scenario

"""
A question nobody managed to answer is still a question.

The queue retires a message by writing to it, and that is the right end for a
decision: the agent heard the person and chose something, so there is nothing
left to do about that row. It is the wrong end for a turn in which no model
answered at all, which is what a provider outage or a model that ran out of room
before writing anything produces. Those were closed the same way, so somebody
asked a question, got silence, was told the agent had chosen not to reply, and
the row said so too -- with nothing anywhere recording that a model had been
tried and had not managed to think.

So a failed deliberation is counted, written down with its reason, and left where
the next cycle will find it. The field default is no cap at all; this fixture
restores a bounded one, because the cap is the behaviour under test here.
"""


async def _ask(db: Any, text: str) -> tuple[str, str]:
    """One pending question, on a conversation key of its own.

    The key is unique per test because the queue is read by conversation-agnostic
    SQL and this database is shared with anything else running against it: a
    neighbouring row would make an assertion about "the message" ambiguous.
    """
    message_id = uuid4()
    key = f"web:test-{message_id}"
    await db.execute(
        "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
        "text, social_decision) VALUES ($1, now(), 'inbound', 'web', $2, "
        "'owner', $3, NULL)",
        message_id, key, text,
    )
    return str(message_id), key


async def _queue(social: SocialCognition, key: str) -> list[str]:
    """This conversation's pending messages, oldest first."""
    return [str(m.id) for m in await social.pending_inbound(limit=50)
            if m.conversation_key == key]


async def _stored(db: Any, message_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "SELECT social_decision FROM messages WHERE id = $1", message_id)
    return json.loads(row["social_decision"]) if isinstance(row["social_decision"], str) \
        else dict(row["social_decision"] or {})


@pytest.fixture()
def social(tmp_path, db) -> SocialCognition:
    config = make_config(tmp_path / "home")
    # The cap under test, restored here: the field default is unlimited, and
    # this fixture pins a small one so the bounded behaviour can be walked.
    config.social.max_deliberation_attempts = 3
    config.paths.ensure()
    return SocialCognition(config=config, db=db)


async def test_a_message_nobody_answered_stays_on_the_queue(db, social):
    message_id, key = await _ask(db, "are you still there?")
    assert await _queue(social, key) == [message_id], "it is waiting to be answered"

    written = await social.record_failure(
        (await social.pending_inbound(limit=50))[0], {
        "action": "act_silently",
        "reason": "the model thought at length and wrote no answer",
        "decided": False,
    })

    assert await _queue(social, key) == [message_id], (
        "nothing was decided, so the question has not been dealt with, and a "
        "model that recovers answers it without the person asking twice"
    )
    assert written["attempts"] == 1
    assert "no answer" in written["reason"], "the reason outlives the attempt"


async def test_the_attempts_are_counted_and_bounded(db, social):
    capped = social.config.social.max_deliberation_attempts
    message_id, key = await _ask(db, "are you still there?")
    for expected in range(1, capped + 1):
        queue = await _queue(social, key)
        assert queue == [message_id], f"attempt {expected} is still owed"
        pending = await social.pending_inbound(limit=50)
        row = next(m for m in pending if str(m.id) == message_id)
        written = await social.record_failure(row, {
            "action": "act_silently", "reason": "still no answer", "decided": False,
        })
        assert written["attempts"] == expected, "each failure counts once"

    assert await _queue(social, key) == [], (
        "and then it stops: a model that can never answer must not be asked "
        "every cycle, at two calls apiece, for as long as the agent runs"
    )
    final = await _stored(db, message_id)
    assert final["decided"] is False, "the row is left as the readable record it is"
    assert final["attempts"] == capped
    assert "still no answer" in final["reason"], \
        "with the reason a person or the agent reads back afterwards"


async def test_an_uncapped_question_is_never_dropped_but_is_spaced_out(db, social):
    """The field contract: no cap on the asking, a curve on the pace.

    With the cap lifted, a question nobody can answer yet stays owed
    forever -- but after the first few immediate attempts the retries move
    onto the escalation curve, so the promise to answer costs one attempt
    a day rather than one every cycle. `next_retry_after_epoch` is what
    the queue's SQL reads to hold the row, and the attempt count keeps
    growing rather than freezing. The waits are real (an hour, then five,
    then a day), so the test walks past each one by backdating the
    deadline -- the same thing time does, only faster.
    """
    import uuid as uuid_module

    social.config.social.max_deliberation_attempts = 0
    message_id, key = await _ask(db, "are you still there?")
    for expected in range(1, 7):
        queue = await _queue(social, key)
        assert queue == [message_id], f"attempt {expected} is still owed with no cap"
        pending = await social.pending_inbound(limit=50)
        row = next(m for m in pending if str(m.id) == message_id)
        written = await social.record_failure(row, {
            "action": "act_silently", "reason": "still no answer", "decided": False,
        })
        assert written["attempts"] == expected
        if expected <= 3:
            assert not written.get("next_retry_after_epoch"), \
                "the first attempts are immediate, as they always were"
        else:
            assert written.get("next_retry_after_epoch"), \
                "late unbounded retries are spaced on the escalation curve"
            # The wait elapses; the question is owed again.
            await db.execute(
                "UPDATE messages SET social_decision = jsonb_set("
                "social_decision, '{next_retry_after_epoch}', '0') WHERE id = $1",
                uuid_module.UUID(message_id),
            )


async def test_a_decided_message_is_not_asked_about_again(db, social):
    """The other side of that, so the retry cannot quietly widen.

    A turn that did produce an opinion is retired on the spot, however many
    attempts are already written against the row: the count belongs to the
    failures, and a failure followed by an answer is an answered message.
    """
    message_id, key = await _ask(db, "what now?")
    pending = await social.pending_inbound(limit=50)
    row = next(m for m in pending if str(m.id) == message_id)
    await social.record_failure(row, {
        "action": "act_silently", "reason": "no answer", "decided": False,
    })
    await social.mark_handled(
        row, {"action": "reply_now", "reply_texts": ["This."], "decided": True})

    assert await _queue(social, key) == []
    stored = await _stored(db, message_id)
    assert stored["action"] == "reply_now", "and it records what was decided"
    assert stored["decided"] is True
