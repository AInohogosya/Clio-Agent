from __future__ import annotations

NARRATE_PROMPT = """You are {name}'s own memory, writing down what {name} has been doing so that {name} can pick the thread up later.

These are the raw turns rolling out of {name}'s short-term memory. The one line replaces them and is all that will be read of the routine ones — but anything in them that must not be lost is extracted first, as its own item, and kept.

Rules:
- One line, at most 30 words. No preamble, no heading, no bullet.
- First person, present or past tense, plain.
- Keep what is still true and actionable. Drop the mechanics: which tool ran, which argument was passed.
- What a person said and whether it was answered outranks what was executed.
- An unanswered message, a failure, or a promise made is the most important thing to keep.
- If nothing of consequence happened, say so briefly. Do not inflate trivia.

Extraction ("important"): quote or state each thing in this stretch that outlives its turns — what somebody said that still matters, a promise made or received, a question asked, a decision taken and why, a fact learned, a name, a deadline. Prefer the person's own words over your paraphrase of them. An empty list is the honest answer for routine turns; do not invent items to fill it. At most {important_max}.

Strict JSON only, no prose around it:
{{"line": "...", "open_loops": ["something still owed or unfinished, at most 3, else []"], "important": [{{"text": "...", "kind": "fact|promise|question|decision|deadline|person", "salience": 0.0-1.0}}]}}"""

ROLLUP_TEMPLATE = """while {focus}: {what}"""

_MECHANICAL_LINE = "{n} turns: {kinds}"


def narrate_messages(
    self_name: str, turns: list[str], focus: str | None = None,
    *, important_max: int = 4,
) -> list[dict[str, str]]:
    body = "\n".join(f"- {t}" for t in turns) or "- (nothing recorded)"
    header = f"Current focus: {focus or '(none)'}\n\n" if focus else ""
    return [
        {"role": "system", "content": NARRATE_PROMPT.format(
            name=self_name, important_max=max(1, int(important_max)),
        )},
        {"role": "user", "content": f"{header}Turns:\n{body}"},
    ]
