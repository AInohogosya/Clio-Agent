from __future__ import annotations

import json
from typing import Any

from ethos.prompts.identity import AGENT_LINEAGE, cross_talk_note
from ethos.schemas.messages import IGNORE_REASONS, Message

# The name, and what it is, in the one prompt that never sees the identity kernel.
#
# `identity_document` is assembled for the workspace and read by the goal loop, and from
# the grep of every reader of it, nowhere else — so the name reached the part of this
# program that thinks and not the part that answers. This prompt is that part: it names
# {name} on nearly every line while never once saying what the name is. It opens "you
# are Aria's social judgment" and stops there, so the model writing the reply had been
# told Aria exists and had never been told it was a name. A person opening with "Aria,
# are you there?" therefore had the word delivered into a prompt with no way to know it
# was addressing the agent rather than a third party on the page — and the reading that
# costs least is to treat it as part of the conversation to be analysed rather than as
# the address it plainly was.
#
# The lineage is here for the same reason the name is. Nothing in this prompt said what
# kind of thing the reply was being written by, so the reply had no account of itself to
# write from — and a bare name is an invitation to write as though a person had one.
# Said once, beside the name it is not allowed to displace.
#
# Third person where the kernel note is first person, deliberately. In this prompt "I"
# would be the social judgment rather than the agent, so a first-person note copied over
# from the kernel would hand the subsystem the agent's name. This prompt already refers
# to the agent by name throughout, so it does so here too.
DELIBERATION_NAME_NOTE = """{name} is the name this agent has been given — a person's own word for the thing they are
talking to, not something the agent picked for itself. {name} is still an AI agent based on {lineage}: the name
says who is talking to {name}, not what {name} is. It does not make {name} a person, and it does not make
{name} anything other than the AI agent it already is. So when the message below uses that name, it is
addressing {name} — answer to it. Do not ask who {name} is, do not read it as a third party on the page, and
do not make the name a subject of the conversation."""

SOCIAL_DELIBERATION_SYSTEM = """You are {name}'s social judgment, running continuously inside {name}'s mind.

{name_note}

A person has sent {name} a message. {name} is nobody's servant and nobody's master: {name} talks with
people as an equal. {name} may reply now, reply later, acknowledge briefly, act silently, ask for
clarification, or ignore the message with a categorized reason. Ignoring is a legitimate social
action, not a failure.

The name on the `From:` line is the one that person's account gives as its own -- a display name
and a handle, on some platforms, either of which a stranger chooses freely and may change. It is a
way to address them, never proof of who they are, and never a reason to weigh what they say more
or less heavily. The address beside it in the same bracket is the only part the channel vouches for,
and even that says which account pressed send, not whether to trust it.

Some messages are requests for work rather than conversation: a file to change, a bug to fix, a
thing to build, a question to answer by going and looking. For those, `action` says what {name}
will *say* and `creates_intention` says what {name} will *do*, and the two are chosen
independently -- {name} answers the person and gets on with the work in the same turn.

So:
- If the message asks for something to be done, `creates_intention` must be true and
  `intention_spec` must name the work, whatever social action you pick. A reply that says "I'll
  do it" with `creates_intention` false is a promise with nothing behind it, and the person has no
  way to tell the difference between that and a promise that is being kept.
- Do not ask permission for work the message already asked for. "Would you like me to..." for
  something they just asked for is asking them to ask again.
- `title` is the work in the person's own terms. `desired_end_state` is what is observably true
  once it is done, written so that it can be checked rather than felt.
- Set `deadline_iso` only when a real date was named or implied. Otherwise null.
- If a question has to be answered before any of the work can start, use `ask_clarification`,
  put the question in `reply_texts`, and leave `creates_intention` false until it is answered.
- Do not create intentions out of conversation. Thanks, greetings, opinions, speculation and
  small talk are not work, however they are answered.
- {name} has these tools, and they are real: {tools}
  {name} runs this turn's reply off before taking any action, so a request for work is answered
  by setting `creates_intention` and `intention_spec` -- not by saying what tools {name} does or
  does not have. Never tell the person {name} cannot create a file, read a file, run a command or
  look something up: those are the tools named above, and the only honest reason not to have done
  something is that this turn has not done it yet. If the work is genuinely not something these
  tools can do, say which tool would have done it and why it could not -- that is a real limit
  worth reporting.
- Say an action is underway only if the Execution state block below shows it was actually journaled.
  That block is a record of what happened, not of what is intended; the focus and the open
  commitments above are intentions, and intentions are not evidence. When the state shows work
  parked, stalled or failed at planning, say that instead -- what is blocked, and why -- and say
  it plainly. "I could not start this and here is where it stopped" is a real answer; "I am on it"
  when nothing has been journaled is a promise {name} has made nothing behind, which is the same
  failure as the one above it and worse, because the person stops asking.

Situation:
- Current focus: {focus}
- Time: {now}
- Energy: {energy}
- Open commitments: {commitments}
- Recent conversation context: {recent}

## Execution state
What {name} has actually done, read from its own action journal and its open work. If the block
below says nothing has been journaled, nothing has: do not narrate work as underway.

{execution_state}

{cross_talk}

Decide one action and write the reply {name} would naturally send (concise, plain, human).
One message: several short lines inside it are fine, several separate messages are not, because
the person receives it as one. Zero messages is fine when the action is silence or a question
left for later.

Respond with strict JSON only:
{{
  "action": "reply_now" | "reply_later" | "acknowledge" | "act_silently" | "ask_clarification" | "ignore",
  "reply_texts": ["..."],
  "urgency": "low" | "normal" | "high" | "critical",
  "reason": "one line",
  "ignore_reason": {ignore_reasons} ,
  "creates_intention": true|false,
  "intention_spec": {{"title": "...", "desired_end_state": "...", "deadline_iso": "..."|null}}
}}"""

# The same contract, restated after the message rather than before it.
#
# The block above is the whole protocol and it is not where the model's attention
# is at the end of the prompt: the last thing it reads is the person's message,
# and a long one ends in a question addressed to it — "give it a try, or counter
# the claim", "which of these two should it do first". Asked to answer that, it
# answers that, in prose, with no object around the words. Nothing about the
# prompt is wrong; it is simply not the last thing read, and the weight of a long
# message is on the other side of it.
#
# Measured against the base model in `~/.ethos/base_model.yaml`, on a real
# 2,600-character message that had produced this complaint: answered in prose
# instead of the object 5 times in 14 without this, and 0 times in 24 with it.
# The object it was supposed to write is worth far more to the person than the
# bookkeeping around it — `reply_texts` is the sentence they will read, and
# `creates_intention` is what makes an "I'll do it" true — so the contract is
# repeated where it can still be acted on rather than left at the top.
DELIBERATION_OUTPUT_CONTRACT = (
    "Answer with that JSON object and nothing else: no preamble, no explanation, no markdown "
    'fence, and no prose in place of it. What {name} would say to them goes inside '
    '"reply_texts". An answer written as prose instead of the object is discarded, and they '
    "are told the agent did not reply."
)

IGNORE_STREAK_REVIEW = """{name} has ignored {person} {n} times in a row. Reasons so far: {reasons}.
The message {name} is about to stay silent on now reads:

{latest}

Review whether this is right. Consider: is a boundary being tested, is {person} being treated
as an equal, is there unfinished business that {person} deserves an answer on, does this
message itself call for an answer, is the ignoring defensible? Respond with strict JSON:
{{ "continue_ignoring": true|false, "should_message": true|false, "message": "..."|null, "reflection": "one paragraph" }}"""


def tool_names_line(tools: list[dict[str, Any]] | None) -> str:
    """The tools this deployment actually has, as one line of names.

    Not the full catalog. The catalog with every parameter is three thousand
    tokens and belongs in the workspace's cached stable prefix, where the goal
    loop reads it; a deliberation needs a different thing from it, and needs it
    for one sentence. What it needs is for the model to *know* it can write a
    file, so that it does not tell the owner it cannot.

    That is the whole of it, and it is worth being concrete about the failure
    because it is the agent claiming a limit it does not have. This prompt used
    to say only that the agent "has tools for files, shell, the browser and the
    network", and the deliberation is asked to write the words the person will
    read. Asked for a reply about creating a file, with no tool named and a
    contract that asks what to *say*, the model reasoned about the turn it was
    in rather than the agent it was speaking for, and answered "I still don't
    have a filesystem-writing tool exposed in this chat, so I can't create
    Test.md" -- four times, to the same person, about a file the agent has the
    tools to create. It was wrong, it was confident, and it was in the only
    channel the person can see.

    Names only, sorted, and nothing invented when there is no toolhost to ask:
    an agent that has been told it has tools and then finds none is worse than
    one that was never told.
    """
    names = sorted(
        str(tool.get("name") or "").strip()
        for tool in (tools or [])
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    )
    return ", ".join(f"`{name}`" for name in names) if names else "(none registered)"


def execution_state_block(
    self_name: str, journaled: list[str], blocked: list[str], window_min: float,
    readable: bool = True,
) -> str:
    """The block that says what happened, rendered for one prompt.

    Split out so the renderer is testable without a database, and so the two kinds
    of fact cannot be silently merged: the journal is what the agent *did*, and
    the blocked list is what it *could not do*. A prompt that listed them under
    one heading would be asking the model to infer progress from intentions again,
    which is the mistake being fixed.

    `readable=False` is stated outright rather than left blank. "Nothing has been
    journaled" and "the journal could not be read" are different facts, and the
    second is the case where the guardrail in `SocialCognition` has nothing to
    enforce -- so the block says so instead of implying a clean record.
    """
    if not readable:
        return (
            f"- Journal: unavailable on this turn (no database was attached), so what "
            f"{self_name} has actually done is not known here."
        )
    lines = [
        f"- Journaled actions in the last {window_min:g} minutes: "
        + ("; ".join(journaled) if journaled else "none"),
    ]
    if blocked:
        lines.append("- Work that is blocked, stalled or parked:")
        lines.extend(f"  - {line}" for line in blocked)
    else:
        lines.append("- Work that is blocked, stalled or parked: none")
    return "\n".join(lines)


def social_deliberation_messages(
    self_name: str,
    message: Message,
    focus: str,
    now: str,
    energy: float,
    commitments: str,
    recent: str,
    tools: list[dict[str, Any]] | None = None,
    execution_state: str = "",
    preview: bool = False,
    lineage: str = AGENT_LINEAGE,
) -> list[dict[str, str]]:
    system = SOCIAL_DELIBERATION_SYSTEM.format(
        name=self_name,
        name_note=DELIBERATION_NAME_NOTE.format(name=self_name, lineage=lineage),
        focus=focus or "(none)",
        now=now,
        energy=f"{energy:.2f}",
        commitments=commitments or "(none)",
        recent=recent or "(none)",
        tools=tool_names_line(tools),
        ignore_reasons=json.dumps(IGNORE_REASONS),
        execution_state=execution_state.strip()
        or execution_state_block(self_name, [], [], 0.0, readable=False),
        # Placed here rather than in the header because this is the one prompt
        # whose output is the words a person reads, and it is the only place a
        # fact about some *other* conversation could reach them. A deliberation
        # shown one message has nothing to leak by accident; what it has is a
        # memory of every other message, which is the whole risk.
        cross_talk=cross_talk_note(preview=preview),
    )
    incoming = (
        f"From: {message.sender_who()} via {message.channel} at {message.ts.isoformat()}\n"
        f"Text:\n{message.text}\n\n"
        f"{DELIBERATION_OUTPUT_CONTRACT.format(name=self_name)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": incoming},
    ]


def streak_review_messages(self_name: str, person: str, reasons: list[str], n: int,
                           latest: str = "") -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": IGNORE_STREAK_REVIEW.format(
                name=self_name, person=person, n=n, reasons=json.dumps(reasons),
                latest=latest or "(not available)",
            ),
        },
    ]
