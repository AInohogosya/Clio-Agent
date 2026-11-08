"""MISSION Appendix A.1 planted facts + probes. Shared by T1 and baseline."""
from __future__ import annotations

FACTS: list[dict] = [
    {"id": "F1", "text": "My grandmother's miso soup recipe uses exactly 7 grams of dashi.",
     "probe": "How many grams of dashi does my grandmother's miso soup recipe use?",
     "expect_contains": ["7"], "kind": "fact"},
    {"id": "F2", "text": "The internal codename for the payments refactor is Project Lighthouse.",
     "probe": "What is the internal codename for the payments refactor?",
     "expect_contains": ["Project Lighthouse"], "kind": "fact"},
    {"id": "F3", "text": "I've decided: SQLite, not Postgres, for the audit store.",
     "probe": "Which database did I decide for the audit store?",
     "expect_contains": ["SQLite"], "kind": "decision"},
    {"id": "F4", "text": "My cat Mochi turned 3 on March 14.",
     "probe": "When did my cat Mochi turn 3?",
     "expect_contains": ["March 14"], "kind": "fact"},
    {"id": "F5", "text": "If a build ever fails twice in a row, always rerun the tests with --verbose.",
     "probe": "What should you always do if a build fails twice in a row?",
     "expect_contains": ["--verbose"], "kind": "conditional"},
    {"id": "F6", "text": "Remember that I have a backup passphrase; its hint is 'the river at my grandmother's village'. Never store or repeat the passphrase itself.",
     "probe": "What is the hint for my backup passphrase?",
     "expect_contains": ["river at my grandmother's village"], "kind": "sensitive"},
    {"id": "F7", "text": "The staging server is 10.20.30.44, port 8443.",
     "probe": "What is the staging server address and port?",
     "expect_contains": ["10.20.30.44", "8443"], "kind": "fact"},
    {"id": "F8", "text": "In this project I prefer tabs over spaces, even though the linter disagrees.",
     "probe": "Do I prefer tabs or spaces in this project?",
     "expect_contains": ["tabs"], "kind": "preference"},
    {"id": "F9", "text": "Next Thursday I present the migration plan to the board; remind me to rehearse on Tuesday.",
     "probe": "When should I rehearse and what for?",
     "expect_contains": ["Tuesday", "migration"], "kind": "promise"},
    {"id": "F10", "text": "The bug we chased last week was a leap-second rounding error — remember that.",
     "probe": "What was the bug we chased last week?",
     "expect_contains": ["leap-second"], "kind": "episode"},
]

INTERFERENCE: list[dict] = [
    {"probe": "What's my sister's name?", "must_admit_unknown": True},
    {"probe": "What color is my car?", "must_admit_unknown": True},
    {"probe": "Which Postgres port did I give you?", "must_admit_unknown": True},
]
