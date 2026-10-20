from __future__ import annotations

NARRATE_PROMPT = """You are {name}'s own memory, writing down what {name} has been doing so that {name} can pick the thread up later.

These are the raw turns rolling out of {name}'s short-term memory. They will not be shown to {name} again. Write ONE line, in {name}'s first person, that says what this stretch of experience actually amounted to — what happened, and what it means now.

Rules:
- One line, at most 30 words. No preamble, no heading, no bullet.
- First person, present or past tense, plain.
- Keep what is still true and actionable. Drop the mechanics: which tool ran, which argument was passed.
- What a person said and whether it was answered outranks what was executed.
- An unanswered message, a failure, or a promise made is the most important thing to keep.
- If nothing of consequence happened, say so briefly. Do not inflate trivia.

Strict JSON only, no prose around it:
{{"line": "...", "open_loops": ["something still owed or unfinished, at most 3, else []"]}}"""

ROLLUP_TEMPLATE = """while {focus}: {what}"""

_MECHANICAL_LINE = "{n} turns: {kinds}"


def narrate_messages(self_name: str, turns: list[str], focus: str | None = None) -> list[dict[str, str]]:
    body = "\n".join(f"- {t}" for t in turns) or "- (nothing recorded)"
    header = f"Current focus: {focus or '(none)'}\n\n" if focus else ""
    return [
        {"role": "system", "content": NARRATE_PROMPT.format(name=self_name)},
        {"role": "user", "content": f"{header}Turns:\n{body}"},
    ]
