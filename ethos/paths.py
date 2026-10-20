"""Where a path means something, and how far the agent's reach actually goes.

Two questions this module answers, and deliberately in one place each.

**What a relative path means.** `notes.md` used to resolve against the agent's own
home — `~/.ethos` — so a bare filename landed in the agent's private directory
rather than where a person standing at a shell would expect it. That base was never
a boundary: `fs.write` with an absolute path reached anywhere on the disk either
way, and nothing in the tool layer compared a resolved path against a root. So it
was not a jail that happened to be generous, it was a default that pointed the
wrong way — the agent's own scratch directory, which is the one place its
findings are least likely to be wanted. `resolve` puts the base back where a
person's hands are: their home directory. Shell commands and the objective
verifier resolve the same way, because a rule that differs between the tool that
writes a file and the tool that reads it back is a rule nobody can hold in mind.

**How far the reach actually goes.** The agent's filesystem permissions are the
host user's, exactly: it runs as the person who started it, through the ordinary
POSIX permission bits, and there is no containment anywhere in the tool layer —
no allowlist, no prefix check, no chroot, no sandbox. That is a deliberate design
choice and not an accident of nothing being written yet, so it is stated here in
the place a reader goes looking for it, and `REACH_NOTE` in `ethos.prompts.
identity` states it to the agent in the only terms it can act on.

The two limits that are *not* ours to lift are on the far side of this module,
not in it. The OS's own permission bits apply to the agent exactly as they apply
to the person, which means the agent cannot read what the person cannot read
(`/etc/shadow`, another user's home) and that is a property of the account
rather than a policy choice. And on macOS the TCC layer can refuse a directory
to the agent that the person opens freely in Finder, because the grant belongs to
the *responsible process* rather than to the file. `probe_reach` measures that
instead of guessing at it, and `ethos doctor` prints what it found: the fix is a
consent in System Settings that only the person at the keyboard can give, and
nothing in this repository should pretend otherwise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "PROBE_PATHS",
    "ReachEntry",
    "agent_home",
    "is_platform_protected",
    "probe_reach",
    "resolve",
    "user_home",
]


def user_home() -> Path:
    """The home directory of the person this process is running as.

    `Path.home()` rather than `os.environ["HOME"]`: the former goes through
    `pwd` when the variable is unset or lying, which is the situation a
    process started by a service manager or a launchd job actually lands in.
    """
    return Path.home().expanduser()


def agent_home(config: object | None = None) -> Path:
    """The agent's *own* directory — its memory, its database, its journal.

    Not a boundary. This is where the agent keeps its own state; it is simply
    not the default place a relative filename lands any more, which is the whole
    reason `resolve` exists.
    """
    paths = getattr(config, "paths", None)
    if paths is not None:
        return Path(paths.home).expanduser()
    return user_home() / ".ethos"


def resolve(path: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    """A path as the agent means it: `~` expanded, relative against the home dir.

    The one rule, used by the filesystem tools, the shell's working directory and
    the objective verifier alike.

    Absolute paths are returned untouched and are deliberately *not* resolved
    through `..` or symlinks. Collapsing them would be tidier, but it would also
    rewrite the path the agent named into a different string, and the string is
    what goes into the action journal, the audit trail and the backup filename —
    three places where "what the agent actually asked for" has to survive
    verbatim. Symlink resolution is the filesystem's job, done by the kernel at
    the moment of access, where it cannot disagree with the record.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return ((base or user_home()) / candidate).resolve()


class ReachEntry(NamedTuple):
    """One probed location and whether the agent can actually do anything with it."""

    path: str
    exists: bool
    readable: bool
    writable: bool
    listable: bool
    denied_by: str

    @property
    def reachable(self) -> bool:
        """Whether the agent can read *or* write here — the question that matters.

        A directory that can be listed but not written to is a normal thing (a
        `/usr` subtree) and not a gap in the agent's permissions; what would be a
        gap is a location the person reaches freely and the agent cannot.
        """
        return self.readable or self.writable


# Locations worth measuring rather than assuming. A deployment that wants the
# agent to reach a directory it is not on this list can still reach it — nothing
# here gates anything — but this is what `ethos doctor` reports on.
PROBE_PATHS: tuple[str, ...] = (
    "~",
    "~/Desktop",
    "~/Documents",
    "~/Downloads",
    "~/Library",
    "~/Library/Application Support",
    "~/Library/Mail",
    "~/Library/Messages",
    "~/Pictures",
    ".ssh",
    ".config",
    "/etc",
    "/usr/local",
    "/Applications",
    "/Volumes",
)


def is_platform_protected() -> bool:
    """Whether this host is running a TCC layer that can refuse a directory.

    True on macOS, where the decision to hand a process `~/Library/Mail` belongs
    to the user in System Settings and not to the file's own permission bits.
    """
    return sys.platform == "darwin"


def _probe_one(path: Path) -> ReachEntry:
    exists = path.exists()

    listable = False
    if exists:
        try:
            next(iter(path.iterdir()), None)
            listable = True
        except OSError:
            listable = False

    # A round-trip through a real file, because directory-level `os.access` and
    # the actual permission on a file inside it disagree often enough that
    # trusting `os.access` here would make this report optimistic.
    readable = writable = False
    probe = path / f".ethos-reach-probe-{os.getpid()}"
    if exists:
        try:
            names = [p.name for p in path.iterdir()]
            readable = bool(names) or listable
        except OSError:
            readable = False
        try:
            probe.write_text("", encoding="utf-8")
            writable = True
        except OSError:
            writable = False
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
    else:
        # A path that is not there cannot be refused permission to it. Report it
        # as reachable so an unmounted volume reads as "not a restriction"
        # rather than as a failure the operator has to think about.
        readable = writable = True

    denied_by = ""
    if exists and not (readable or writable):
        denied_by = (
            "macOS privacy protection (grant Full Disk Access to the terminal "
            "or app that starts the agent)"
            if is_platform_protected()
            else "filesystem permissions of the running user"
        )
    return ReachEntry(str(path), exists, readable, writable, listable, denied_by)


def probe_reach(extra: tuple[str, ...] = ()) -> list[ReachEntry]:
    """What the agent can reach, measured rather than assumed.

    Returns one `ReachEntry` per probed location, in order. Used by
    `ethos doctor` so that the one gap the application layer cannot close — an
    OS privacy decision — is visible to the person who can actually change it.
    """
    seen: set[str] = set()
    entries: list[ReachEntry] = []
    for raw in (*PROBE_PATHS, *extra):
        resolved = resolve(raw)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        entries.append(_probe_one(resolved))
    return entries
