from __future__ import annotations

import os

# The legacy `ETHOS_` spelling. Reading it is the documented fallback for
# deployments configured before the `clio` rename; nothing writes it first.
LEGACY_PREFIX = "ETHOS_"
PREFIX = "CLIO_"


def env(name: str, default: str | None = None) -> str | None:
    """One environment variable, under its canonical `CLIO_` spelling.

    `CLIO_<name>` is read first and `ETHOS_<name>` is the fallback, so a
    deployment that set `ETHOS_DSN` before the rename keeps answering the same
    database until somebody edits its unit files — and a deployment that sets
    both gets the `CLIO_` value, which is the one the current documentation
    describes. Callers name the variable *without* either prefix: `env("DSN")`,
    never `env("CLIO_DSN")`, because the prefix is the one thing this function
    exists to keep out of every call site.
    """
    value = os.environ.get(f"{PREFIX}{name}")
    if value is None:
        value = os.environ.get(f"{LEGACY_PREFIX}{name}")
    return default if value is None else value


def env_named(name: str, default: str | None = None) -> str | None:
    """Read a *full* variable name, applying the `CLIO_`/`ETHOS_` fallback rule.

    Config files name the variable a channel's token lives in
    (`channels.token_env`), so the value that arrives here is already spelled —
    most often `CLIO_WEB_TOKEN` under the current documentation. A `CLIO_`-spelled
    name falls back to its `ETHOS_` twin and an `ETHOS_`-spelled name falls back
    to its `CLIO_` twin, which covers both a deployment upgraded in place and one
    still holding the old spelling in its channel file.
    """
    if name.startswith(PREFIX):
        return env(name[len(PREFIX):], default)
    if name.startswith(LEGACY_PREFIX):
        return env(name[len(LEGACY_PREFIX):], default)
    return os.environ.get(name, default)


def set_env(name: str, value: str) -> None:
    """Write one variable under both spellings, canonical first.

    A value this process writes may be read back by a child process built from
    either revision of the package — the supervisor starts children with
    `sys.executable -m clio`, but a long-lived machine can still hold an old
    worker mid-restart — so both spellings carry it and neither side of a
    rolling restart reads a variable the other never wrote.
    """
    os.environ[f"{PREFIX}{name}"] = value
    os.environ[f"{LEGACY_PREFIX}{name}"] = value


def pop_env(name: str) -> str | None:
    """Remove one variable under both spellings, returning the canonical value."""
    value = env(name)
    os.environ.pop(f"{PREFIX}{name}", None)
    os.environ.pop(f"{LEGACY_PREFIX}{name}", None)
    return value


def env_int(name: str, default: int) -> int:
    """`env()` as an integer, with the default surviving a non-numeric value."""
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    """`env()` as a float, with the default surviving a non-numeric value."""
    raw = env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
