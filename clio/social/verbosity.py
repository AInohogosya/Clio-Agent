from __future__ import annotations

import re

VERBOSITY_QUIET = "quiet"
VERBOSITY_BALANCED = "balanced"
VERBOSITY_DETAILED = "detailed"

VERBOSITY_LEVELS = frozenset({VERBOSITY_QUIET, VERBOSITY_BALANCED, VERBOSITY_DETAILED})

DEFAULT_VERBOSITY = VERBOSITY_BALANCED

# How often proactive progress checks may consider a person, per level.
# Minutes. These are ceilings, not schedules: a check that finds nothing
# worth reporting sends nothing, so a short interval cannot spam.
VERBOSITY_INTERVAL_MIN = {
    VERBOSITY_QUIET: 60.0,
    VERBOSITY_BALANCED: 15.0,
    VERBOSITY_DETAILED: 3.0,
}


def normalise_verbosity(level: str | None) -> str:
    level = (level or "").strip().lower()
    if level in VERBOSITY_LEVELS:
        return level
    return DEFAULT_VERBOSITY


def verbosity_interval_min(level: str | None) -> float:
    return VERBOSITY_INTERVAL_MIN[normalise_verbosity(level)]


_MORE_PATTERNS = (
    r"report more",
    r"more detail",
    r"more detailed",
    r"more frequent",
    r"more often",
    r"keep me posted",
    r"keep me updated",
    r"update me often",
    r"more proactive",
    r"be more proactive",
    r"more verbose",
    r"talk more",
    r"chat more",
    r"more details on progress",
    r"report your progress",
    r"more progress",
    r"let me know more",
    r"tell me more",
    r"report everything",
    r"don't hold back",
    r"do not hold back",
    r"報告.*多い|報告.*細か|詳しく.*報告",
    r"こまめに.*報告|進捗.*教えて",
    r"もっと.*報告",
    r"細かく.*教えて",
)

_LESS_PATTERNS = (
    r"don't report so much",
    r"do not report so much",
    r"report less",
    r"less detail",
    r"less detailed",
    r"less frequently",
    r"less often",
    r"report very little",
    r"very little",
    r"only when done",
    r"only when it's done",
    r"only when finished",
    r"don't update me so much",
    r"do not update me so much",
    r"don't bother me",
    r"do not bother me",
    r"stay quiet",
    r"keep quiet",
    r"be quiet",
    r"quiet mode",
    r"minimal.*report",
    r"brief.*only",
    r"報告.*少な|報告.*控え|あまり報告",
    r"静か.*いて|報告.*しないで",
    r"終わったら.*報告",
)

_BALANCED_PATTERNS = (
    r"report normally",
    r"normal amount",
    r"balanced.*report",
    r"normal report",
    r"back to normal",
    r"default.*report",
    r"普通.*報告",
)


def detect_verbosity_request(text: str | None) -> str | None:
    """Level requested by one message, or None when it asks for none.

    Checked less-before-more because "don't report so much" contains the
    substring shape "report so much" is never scanned as "report more",
    and negation must win: a message that says "don't report more, report
    less" is a request for less, not more. So negation is checked first
    explicitly, then quiet, then detailed, then balanced.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return None
    if re.search(r"\b(?:don't|do not|stop|no more)\b[^.\n]{0,40}\b(?:report|update)", lowered):
        # "don't report more" -> quiet as well; "stop reporting" -> quiet.
        # Plain "don't report so much" also lands here, but so does
        # "don't report more", which is the same request.
        return VERBOSITY_QUIET
    for pat in _LESS_PATTERNS:
        if re.search(pat, lowered):
            return VERBOSITY_QUIET
    for pat in _MORE_PATTERNS:
        if re.search(pat, lowered):
            return VERBOSITY_DETAILED
    for pat in _BALANCED_PATTERNS:
        if re.search(pat, lowered):
            return VERBOSITY_BALANCED
    return None


_VERBOSITY_NOTES = {
    VERBOSITY_QUIET: (
        "This person has asked to be told very little. Report only when work is "
        "done, parked, or genuinely blocked — never mid-task progress. Keep "
        "those reports to one short line. Silence is the normal state."
    ),
    VERBOSITY_BALANCED: (
        "This person wants a balanced, human amount of reporting. Share "
        "proactive progress notes when something meaningful happened "
        "(a checked step, a delivered file, a change of plan), and always "
        "report when work finishes or stops. A few sentences, in your own "
        "words — what got done, where to find it, what is next. Do not report "
        "every action, and do not go silent for an hour while work is clearly "
        "moving."
    ),
    VERBOSITY_DETAILED: (
        "This person has asked for more detail. Report proactively and "
        "generously: share each verified milestone, name the files and what "
        "changed in them, quote what you verified, and say what you are doing "
        "next. A few short messages are welcome. Still do not narrate your own "
        "machinery or repeat yourself — detail means substance, not padding."
    ),
}


def verbosity_note(level: str | None) -> str:
    return _VERBOSITY_NOTES[normalise_verbosity(level)]
