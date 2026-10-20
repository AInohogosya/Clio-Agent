"""
What the agent can reach, and what that costs.

The agent's filesystem permissions are the host user's, exactly. That is the
design and this file is the part of the suite that says so out loud, because an
unrestricted permission model that nothing tests is an accident waiting for the
next person who thinks a jail would be a nice idea. Three things have to hold,
and each of them was true by default rather than by decision until this file
existed:

  * every filesystem tool reaches any directory the user can — not the agent's
    own, not `~/workspace`, not a configured list of roots;
  * a relative name means a path relative to the *user's* home, which is a
    change, because it used to mean the agent's own `~/.ethos`; and
  * nothing in the permission model is a promise the code does not keep — which
    is the reason `ArtifactRegistry.roots` and `GuardianGate.review(home_dir=…)`
    are gone rather than documented.

The last group is the one that reads as policing and is not: two parameters that
were assigned and never read looked exactly like containment, and every reader
had to go looking for the check that was never written.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ethos.config import PathsConfig
from ethos.guardian.gate import GuardianGate
from ethos.guardian.registry import ArtifactRegistry
from ethos.paths import ReachEntry, agent_home, probe_reach, resolve, user_home
from ethos.prompts.identity import build_identity_kernel
from ethos.toolhost.server import ToolContext
from ethos.toolhost.tools.fs import (
    FsEditTool,
    FsListTool,
    FsMoveTool,
    FsReadTool,
    FsStatTool,
    FsWriteTool,
    _resolve,
)

CTX = ToolContext(thread_id="reach")


def _host(home: Path | None = None) -> Any:
    """A host carrying the *real* `PathsConfig`, pointed at a throwaway home.

    The real config object rather than a stub, so a test cannot pass because the
    double happened to have the attribute the tool wanted. The agent home is
    inside `tmp_path` and therefore inside the same temp tree as the files being
    read and written — so a test that accidentally depends on resolution against
    the agent's own home still has a chance of failing, rather than silently
    agreeing with the wrong behaviour.
    """
    return SimpleNamespace(
        config=SimpleNamespace(
            paths=PathsConfig(home=home or (Path.home() / ".ethos")),
        ),
    )


# ------------------------------------------------------- any directory, not mine


@pytest.mark.parametrize(
    ("name", "make"),
    [
        ("read", lambda t, p, h: FsReadTool().run({"path": str(p)}, CTX, h)),
        ("write", lambda t, p, h: FsWriteTool().run(
            {"path": str(p), "content": "written"}, CTX, h)),
        ("edit", lambda t, p, h: FsEditTool().run(
            {"path": str(p), "search": "written", "replace": "edited"}, CTX, h)),
        ("list", lambda t, p, h: FsListTool().run({"path": str(p)}, CTX, h)),
        ("stat", lambda t, p, h: FsStatTool().run({"path": str(p)}, CTX, h)),
    ],
)
async def test_a_tool_reaches_a_directory_outside_every_configured_root(
    tmp_path: Path, name: str, make: Any
) -> None:
    """The round trip that a jail would break, run somewhere a jail would forbid.

    `tmp_path` is under the system temp directory, which is outside `paths.home`,
    `paths.workspace`, `paths.work` and `paths.notes` simultaneously. A
    containment check added in the tool layer would fail here, which is the
    point: this test is the tripwire, and it is deliberately at the tool layer
    rather than at the gate, because the gate has never been where reach is
    decided.
    """
    host = _host(tmp_path / "agent-home")
    target = tmp_path / "outside" / "note.md"
    target.parent.mkdir(parents=True)

    if name == "edit":
        target.write_text("written", encoding="utf-8")
    if name in {"read", "list", "stat"}:
        target.write_text("body", encoding="utf-8")
        if name == "list":
            target = target.parent

    result = await make(name, target, host)

    if name == "write":
        assert target.read_text(encoding="utf-8") == "written"
    elif name == "edit":
        assert target.read_text(encoding="utf-8") == "edited"
    elif name == "read":
        assert result["text"] == "body"
    assert result is not None


async def test_write_creates_directories_it_has_never_seen(tmp_path: Path) -> None:
    host = _host(tmp_path / "agent-home")
    deep = tmp_path / "made" / "up" / "of" / "several" / "levels" / "deep.txt"
    await FsWriteTool().run({"path": str(deep), "content": "x"}, CTX, host)
    assert deep.read_text(encoding="utf-8") == "x"


async def test_dotfiles_and_awkward_names_are_ordinary_paths(tmp_path: Path) -> None:
    """No deny-list means no deny-list: `.git`, `node_modules` and spaces included."""
    host = _host(tmp_path / "agent-home")
    for name in (".hidden", "a file with spaces.md", "ünïcode.md", ".git"):
        target = tmp_path / name
        await FsWriteTool().run({"path": str(target), "content": name}, CTX, host)
        assert target.read_text(encoding="utf-8") == name


async def test_move_takes_both_ends_anywhere(tmp_path: Path) -> None:
    host = _host(tmp_path / "agent-home")
    source = tmp_path / "a" / "here.txt"
    destination = tmp_path / "b" / "there" / "here.txt"
    source.parent.mkdir(parents=True)
    source.write_text("moved", encoding="utf-8")

    await FsMoveTool().run(
        {"from_path": str(source), "to_path": str(destination)}, CTX, host,
    )

    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "moved"


async def test_a_symlink_is_followed_by_the_filesystem_not_prevented_by_us(
    tmp_path: Path,
) -> None:
    """`_resolve` deliberately does not collapse `..` or symlinks.

    Not tidiness. The resolved string is what reaches the action journal, the
    audit trail and the backup filename, and those three have to keep saying what
    the agent actually asked for. If we rewrote the path here, the record would
    name a file the agent never named.
    """
    host = _host(tmp_path / "agent-home")
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_text("through the link", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)

    assert _resolve(host, str(link)) == link  # not collapsed
    result = await FsReadTool().run({"path": str(link / "file.txt")}, CTX, host)
    assert result["text"] == "through the link"


# ----------------------------------------------------------------- relative paths


def test_a_relative_path_lands_in_the_users_home() -> None:
    """The change. It used to be the agent's own `~/.ethos`."""
    assert resolve("notes.md") == user_home() / "notes.md"
    assert resolve("deep/nested/notes.md") == user_home() / "deep/nested/notes.md"


def test_the_users_home_is_not_the_agents_home() -> None:
    """The distinction the whole change turns on.

    If these were ever the same directory again, "relative to my home" would be
    indistinguishable from the old behaviour and the reach note in the prompt
    would be making a promise about the agent's scratch directory.
    """
    assert user_home() != agent_home()
    assert resolve("x").parent != agent_home()


def test_a_tilde_path_still_means_the_users_home() -> None:
    assert resolve("~/notes.md") == user_home() / "notes.md"


def test_an_absolute_path_is_returned_untouched() -> None:
    for path in ("/etc/hosts", "/tmp/x", "/Users/somebody/Desktop/a.txt"):
        assert resolve(path) == Path(path)


def test_an_absolute_path_is_not_collapsed() -> None:
    """`..` and `.` are the kernel's business; see the symlink test for why."""
    assert resolve("/tmp/a/../b") == Path("/tmp/a/../b")


def test_resolution_ignores_where_the_process_happens_to_be_cwd(tmp_path: Path) -> None:
    """Otherwise the same call means different things in different directories."""
    import os

    (tmp_path / "elsewhere").mkdir()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        first = resolve("notes.md")
        os.chdir(tmp_path / "elsewhere")
        second = resolve("notes.md")
    finally:
        os.chdir(original)
    assert first == second == user_home() / "notes.md"


def test_a_relative_base_can_be_overridden_for_the_rare_caller_that_needs_it() -> None:
    assert resolve("x", base=Path("/opt")) == Path("/opt/x")


def test_the_fs_tools_use_the_shared_rule() -> None:
    """One rule, not seven copies of it.

    Each tool resolves through `ethos.paths.resolve`; this asserts the helper it
    delegates to is the shared one, so a future edit cannot quietly give one tool
    a private base directory and leave the other six on the user's home.
    """
    host = _host()
    assert _resolve(host, "notes.md") == resolve("notes.md") == user_home() / "notes.md"
    assert _resolve(host, "/etc") == Path("/etc")


# ------------------------------------------ the permission model keeps its promises


def test_the_registry_takes_no_roots() -> None:
    """`roots` was stored and never read, and read exactly like a jail."""
    params = inspect.signature(ArtifactRegistry.__init__).parameters
    assert "roots" not in params
    assert not hasattr(ArtifactRegistry, "roots")


def test_the_gate_takes_no_home_dir() -> None:
    """Same: a keyword the caller supplied and no comparison was ever made with."""
    assert "home_dir" not in inspect.signature(GuardianGate.review).parameters
    assert "home_dir" not in inspect.signature(GuardianGate._review_destructive).parameters


def test_the_toolhost_stops_passing_a_home_dir_to_the_gate() -> None:
    """The call site, not the signature.

    A parameter removed from a signature but still supplied at every call site
    fails loudly at import, which is the good case. The bad case is the reverse:
    a parameter added back to the signature and threaded through again by
    someone who has read this file and disagreed with it.
    """
    import ethos.toolhost.server as server_module

    assert "home_dir" not in inspect.getsource(server_module)


def test_nothing_in_the_fs_tools_compares_a_path_against_a_root() -> None:
    """The tripwire, stated as a property of the source.

    A single `is_relative_to` or `startswith` against a configured root, dropped
    into the tool layer, would confine the agent without failing any other test in
    the suite: the guardian tests assert reversibility rather than reach, and the
    integration tests all write inside `tmp_path`. So the check is on the code
    that would contain it.

    `paths.data_dir` is absent from this list deliberately — that is the backup
    directory, and backups are part of the permission model rather than a
    boundary within it.
    """
    import ethos.toolhost.tools.fs as fs_module

    source = inspect.getsource(fs_module)
    for containment in (
        "is_relative_to", "commonpath", "commonprefix", "relative_to",
        "paths.workspace", "paths.work", "paths.notes", "paths.toolshed",
    ):
        assert containment not in source, f"{containment} would confine the agent"


def test_the_reach_note_is_part_of_the_identity_block() -> None:
    """In the cached prefix, so it is not re-learned every cycle.

    `build_identity_kernel` *is* stable prefix block 1, and the reach note is
    inside it. What would break the guarantee is a change to the block count, so
    that is asserted too: `STABLE_BLOCKS` is where the prompt cache is cut, and
    moving it changes the price of every call in every thread.
    """
    from ethos.engine.workspace import BLOCK_ORDER, STABLE_BLOCKS

    kernel = build_identity_kernel("Aria", user_home_dir="/home/ana",
                                   agent_home_dir="/home/ana/.ethos")
    assert "## What I can reach" in kernel
    assert BLOCK_ORDER.index("identity") == 0
    assert STABLE_BLOCKS == 3
    assert BLOCK_ORDER[:STABLE_BLOCKS] == ("identity", "tools", "limits")


def test_the_reach_note_names_the_home_so_the_agent_need_not_probe() -> None:
    kernel = build_identity_kernel("Aria", user_home_dir="/home/ana",
                                   agent_home_dir="/home/ana/.ethos")
    body = " ".join(kernel.split())
    assert "relative to their home directory, `/home/ana`" in body
    assert "no directory I am confined to" in body
    # The grant is paired with what replaces a wall, or it reads as licence.
    assert "a way back" in body
    assert "backed up before it lands" in body
    assert "goes to trash" in body


def test_the_reach_note_tells_the_agent_how_to_report_a_refusal() -> None:
    """A refusal belongs to the machine. The agent must not present it as its own rule.

    The failure this prevents is the agent telling the owner "I am not permitted
    to read that folder", which is a claim about the agent and is false — the
    account was refused, and the account is the owner's.
    """
    body = " ".join(build_identity_kernel("Aria").split())
    assert "the machine saying no" in body
    assert "the system would not let me" in body


def test_the_reach_note_defaults_to_the_running_deployments_own_layout() -> None:
    kernel = " ".join(build_identity_kernel("Aria").split())
    assert f"relative to their home directory, `{user_home()}`" in kernel
    assert str(agent_home()) in kernel


# ----------------------------------------------------------------- the probe itself


def test_probe_reach_reports_a_directory_it_can_use() -> None:
    entries = {e.path: e for e in probe_reach(extra=(str(Path.home()),))}
    home = entries[str(user_home())]
    assert isinstance(home, ReachEntry)
    assert home.exists
    assert home.reachable


def test_probe_reach_leaves_nothing_behind() -> None:
    """`doctor` is run when somebody least expects their filesystem touched."""
    for entry in probe_reach(extra=(str(Path.home()),)):
        leftovers = [p.name for p in Path(entry.path).glob(".ethos-reach-probe-*")] \
            if entry.exists else []
        assert leftovers == []


def test_an_absent_path_is_not_reported_as_a_refusal() -> None:
    """An unmounted volume must not read as a permissions problem."""
    missing = ReachEntry("/nowhere/at/all", False, True, True, False, "")
    assert missing.reachable
    assert missing.denied_by == ""


def test_a_refusal_names_the_operating_system_rather_than_the_agent() -> None:
    """The agent must be able to say *who* said no.

    A denial that reads as "not permitted" is the agent's own rule; one that names
    a privacy layer or a permission bit is the account's. Only the first would
    make the agent lie about its own reach in a report to the owner.
    """
    tcc = ReachEntry("/somewhere", True, False, False, False,
                     "macOS privacy protection (grant Full Disk Access to the "
                     "terminal or app that starts the agent)")
    posix = ReachEntry("/somewhere", True, False, False, False,
                       "filesystem permissions of the running user")
    for entry in (tcc, posix):
        assert not entry.reachable
        assert entry.denied_by
        assert "privacy protection" in entry.denied_by or "permissions" in entry.denied_by


def test_probe_reach_deduplicates_its_paths() -> None:
    entries = probe_reach(extra=("~", "/etc"))
    paths = [e.path for e in entries]
    assert len(paths) == len(set(paths))


def test_a_directory_that_is_read_only_still_counts_as_reachable() -> None:
    """`/usr` is normal, not a gap. A gap is the user reaching it and me not."""
    read_only = ReachEntry("/usr", True, True, False, True, "")
    assert read_only.reachable
