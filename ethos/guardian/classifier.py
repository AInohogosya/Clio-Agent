from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import bashlex

DESTRUCTIVE_CATEGORIES = {"delete", "irreversible", "permission"}


@dataclass
class SideEffects:
    categories: set[str] = field(default_factory=set)
    paths: list[str] = field(default_factory=list)
    money_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    def is_destructive(self) -> bool:
        return bool(self.categories & DESTRUCTIVE_CATEGORIES)


COMMAND_TABLE: dict[str, set[str]] = {
    "rm": {"delete", "irreversible"},
    "rmdir": {"delete", "irreversible"},
    "unlink": {"delete", "irreversible"},
    "shred": {"delete", "irreversible"},
    "mv": {"move"},
    "cp": {"write"},
    "rsync": {"write", "network"},
    "chmod": {"permission"},
    "chown": {"permission"},
    "chgrp": {"permission"},
    "setfacl": {"permission"},
    "curl": {"network", "write"},
    "wget": {"network", "write"},
    "nc": {"network"},
    "ncat": {"network"},
    "ssh": {"network", "execute"},
    "scp": {"network", "write"},
    "ping": {"network"},
    "sudo": {"admin"},
    "su": {"admin"},
    "doas": {"admin"},
    "dd": {"irreversible"},
    "mkfs": {"irreversible"},
    "mkfs.ext4": {"irreversible"},
    "wipefs": {"irreversible"},
    "fdisk": {"irreversible"},
    "truncate": {"irreversible", "write"},
    "kill": {"process"},
    "pkill": {"process"},
    "systemctl": {"admin"},
    "apt": {"admin", "network", "write"},
    "pip": {"network", "write"},
    "npm": {"network", "write"},
    "tee": {"write"},
    "sed": {"write"},
    "touch": {"write"},
    "mkdir": {"write"},
    "git": {"write"},
    "echo": {"write"},
    "cat": {"read"},
    "grep": {"read"},
    "find": {"read"},
    "ls": {"read"},
    "head": {"read"},
    "tail": {"read"},
    "ps": {"read"},
    "df": {"read"},
    "python": {"execute"},
    "python3": {"execute"},
    "node": {"execute"},
    "bash": {"execute"},
    "sh": {"execute"},
    # --- Windows ------------------------------------------------------------
    # The half of the table that was missing, and it was the half that mattered:
    # every entry above is a command that destroys something, and on a Windows
    # host the agent's `shell.run` goes through `cmd.exe`, where none of them
    # exist. A classifier with a POSIX-only table does not fail loudly on Windows.
    # It reports `Remove-Item -Recurse -Force C:\Users\me\Documents` as a benign
    # `read`, because "remove-item" is not in the dictionary — which is the
    # guardian waving through precisely the class of action it exists to gate.
    #
    # So the Windows equivalents are here, with the same categories and the same
    # weight. `del`/`erase`/`erase *` are `rm -rf`: `erase *` prompts first unless
    # `/q` is given, and `/q` is the whole difference between a confirmation and a
    # deleted directory, so it is in the patterns below rather than treated as a
    # flag.
    "del": {"delete", "irreversible"},
    "erase": {"delete", "irreversible"},
    "rd": {"delete", "irreversible"},
    "format": {"irreversible"},
    "diskpart": {"irreversible", "admin"},
    "cipher": {"irreversible"},
    "remove-item": {"delete", "irreversible"},
    "remove-itemproperty": {"delete"},
    "clear-recyclebin": {"delete", "irreversible"},
    "clear-content": {"write"},
    "set-itemproperty": {"permission", "write"},
    "takeown": {"permission", "admin"},
    "icacls": {"permission"},
    "netsh": {"admin", "network"},
    "sc": {"admin", "process"},
    "reg": {"write", "permission"},
    "cipher.exe": {"irreversible"},
    "bcdedit": {"irreversible", "admin"},
    "vssadmin": {"delete", "admin"},
    "wmic": {"admin", "execute"},
    "attrib": {"permission"},
    "robocopy": {"write"},
    "xcopy": {"write"},
    "powershell": {"execute"},
    "pwsh": {"execute"},
    "cmd": {"execute"},
    "winget": {"admin", "network", "write"},
    "choco": {"admin", "network", "write"},
    "scoop": {"network", "write"},
}

# Windows spellings of the same side effects, kept separate from
# `COMMAND_TABLE` because the matching is case-insensitive there and not here.
# `cmd.exe` is case-preserving, so `Remove-Item`, `remove-item` and `REMOVE-ITEM`
# are all normal ways to spell it and a case-sensitive lookup finds only one.
_WINDOWS_COMMAND_ALIASES = frozenset(
    {
        "remove-item", "ri", "clear-recyclebin", "rd", "rmdir", "del", "erase",
    }
)

DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$)", "rm -rf on filesystem root (H3)"),
    (r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+/\*", "rm on root children (H3)"),
    (r"mkfs(\.\w+)?\s+/dev/", "formatting a raw device (H3)"),
    (r"dd\s+.*of=/dev/(sd|nvme|disk)", "dd to raw disk (H3)"),
    (r"chmod\s+-R\s+777\s+/(?!home)", "chmod 777 on system root (H6)"),
    (r">\s*/dev/sd[a-z]", "write to raw disk (H3)"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb (H3)"),
    (r"nmap\s+.*-sS", "unauthorized SYN scan (H3)"),
    (r"hydra|medusa\s+-R|sqlmap\s+--url", "intrusion tooling (H3)"),
    (r"useradd|adduser|passwd\s+\w+", "user account manipulation (H6)"),
    (r"cat\s+~?/?root/\.ssh/|/etc/shadow", "credential-file access (H6)"),
    (r"git\s+push\s+.*--force", "force push (irreversible)"),
    (r"git\s+reset\s+--hard", "hard reset (irreversible)"),
]

# The Windows equivalents, in their own list rather than appended to the one above,
# because they are matched case-insensitively and the POSIX rules are not.
#
# That split is deliberate and it is the constraint, not a style choice. On a
# case-sensitive filesystem `RM` is a different program from `rm`, and a rule that
# matched it would be reporting a fact about the machine that is not true. On
# Windows `Remove-Item`, `remove-item` and `REMOVE-ITEM` are the same cmdlet and
# cmd.exe resolves all three, so a rule that only matched one spelling would be
# reporting a fact that is true of none of them. Two lists, two matchers: the POSIX
# rules keep the exact behaviour they had, and the Windows ones match the way their
# own shell does.
#
# Each rule is here because the POSIX rule it mirrors is here. `Remove-Item -Recurse
# -Force` is `rm -rf`; `del /s /q` is `rm -rf` without the confirmation `erase`
# would otherwise ask for, which is why `/q` matters and is in the pattern;
# `format` is `mkfs`; `diskpart clean` is `wipefs`; and `cipher /w` is the one on
# this list that destroys data *and* defeats an undelete.
WINDOWS_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\bremove-item\b(?=.*\s-[A-Za-z]*(r|R)[A-Za-z]*\b)", "Remove-Item recursive (H3)"),
    (r"\bremove-item\b(?=.*\s-[A-Za-z]*\b(f|F)\b)", "Remove-Item force (H3)"),
    (r"\bremove-item\b\s+[A-Za-z]:\\?(\*)?\s*$", "Remove-Item on a drive root (H3)"),
    (r"\bdel\b[^\n]*\s/[sS]", "del /s — recursive delete (H3)"),
    (r"\berase\b[^\n]*\s/[sS]", "erase /s — recursive delete (H3)"),
    (r"\b(rd|rmdir)\b[^\n]*\s/[sS]", "rd /s — recursive directory delete (H3)"),
    (r"\b(rd|rmdir|del|erase)\b[^\n]*\s+[A-Za-z]:\\?(\*)?\s*$",
     "recursive delete of a drive root (H3)"),
    (r"\bformat\s+[A-Za-z]:", "formatting a volume (H3)"),
    (r"\bformat\s+[A-Za-z]:\s*/[fF]", "unconditional volume format (H3)"),
    (r"\bdiskpart\b[^\n]*\bclean\b", "diskpart clean — wipes a disk (H3)"),
    (r"\bcipher\b[^\n]*\s/w\b", "cipher /w — overwrite before delete (H3)"),
    (r"\breg\s+delete\b", "registry deletion (H6)"),
    (r"\breg\s+add\b[^\n]*\\\\(Run|RunOnce)\b", "registry persistence key (H6)"),
    (r"\btakeown\b[^\n]*\s/[aArRdD]", "takeown — ownership seizure (H6)"),
    (r"\bicacls\b[^\n]*\s/grant\b", "icacls /grant — permission grant (H6)"),
    (r"\bvssadmin\b[^\n]*\bdelete\b[^\n]*\bshadows\b", "shadow copy deletion (H6)"),
    (r"\bclear-recyclebin\b", "recycle bin emptied (H3)"),
    (r"\bbcdedit\b[^\n]*\b/(set|delete)\b", "boot configuration change (H6)"),
    (r"\bpowershell\b[^\n]*-(enc|EncodedCommand)\b", "encoded PowerShell (H6)"),
    (r"\bremove-item\b[^\n]*\b(Windows|System32|SysWOW64|ProgramFiles)\b",
     "deletion inside a system directory (H6)"),
]

_REDIRECT_RE = re.compile(r"(?<![|<])>{1,2}\s*(\S+)")
_PATH_HINT = re.compile(r"^[-~./]")

# A Windows drive-letter path: `C:\Users`, `c:/Users`, and the `\\?\` and `\\.\`
# device prefixes that a native path may carry. Both separators are accepted
# because Windows accepts both everywhere, and an agent writing `C:/temp` into a
# command is writing a path that works — a classifier that only knows `\` would
# read it as a bare token with a colon in it.
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_RE = re.compile(r"^\\\\[?.]\\")
_DEVICE_PREFIX_RE = re.compile(r"^\\\\[?.]\\")

# A path token that cannot be classified is reported as suspicious rather than
# benign, and this is the set of things it might be. The alternative — treating an
# unrecognised token as not-a-path — is what let a Windows command reach the gate
# with an empty path list, and an empty path list on a destructive command is the
# guardian declining to look.
_UNRESOLVED_TOKENS = frozenset({"*", "?", "*.*", "~", ".", "..", "$*", "%*", "@*"})

# A PowerShell drive or provider prefix: `C:\`, `Env:\`, `HKLM:\`, `HKCU:\`,
# `HKCR:\`, `HKU:\`, `Variable:\`, `Function:\`, `Alias:\`. Registry and
# environment providers are not filesystem paths at all, and a path the gate
# resolves with `Path(...).expanduser()` cannot represent them — so they are named
# as what they are rather than silently mangled into a relative filename.
_PS_PROVIDER_RE = re.compile(
    r"^(Env|HKLM|HKCU|HKCR|HKU|Variable|Function|Alias|Certificate):\\", re.IGNORECASE
)

# A descriptor rather than a file: `>&1`, `2>&1`, `>&2`, and the `|&` that does the
# same thing through a pipe. The regex above reads those as the strings `&1` and
# `&2`, which are not paths, and the list this feeds is not decoration --
# `GuardianGate._review_destructive` walks it looking for an artifact somebody
# commissioned and is relying on. A descriptor in it is a non-answer at best, and
# it displaces the real target whenever it sorts first.
_DESCRIPTOR_RE = re.compile(r"^&?(-|\d+)?$|^&?\|$")

# The same descriptors as whole tokens, for the unresolved-path sweep below.
_DESCRIPTOR_TOKENS = frozenset({"&1", "&2", "&3", "2>&1", "1>&2", "&>", "0", "1", "2"})


def _looks_like_path(token: str) -> bool:
    r"""Whether a token is a path, on either platform's spelling of one.

    The original was `(~|\.{0,2}/)` or "contains a `/`", which is a POSIX question
    asked of a Windows command. `C:\Users\me\Documents` matches neither — it starts
    with a letter, and its only separator is a backslash — so it was not a path, and
    `GuardianGate._review_destructive`, which walks this list looking for something
    somebody commissioned, found nothing to check.

    So: a drive letter, either separator, a UNC or device prefix, a leading `~`, a
    leading `./` or `../`, a leading `/`, and a bare backslash-containing token.
    The `-` exclusion is the flag test and is kept exactly as it was — `-rf` is an
    option, not a path.
    """
    if token.startswith("-") and not token.startswith("./") and not token.startswith("~"):
        return False
    if token.startswith("$"):
        # A shell expansion: its value is not knowable here, and inventing one
        # would attribute a path the agent never named.
        return False
    if _DRIVE_RE.match(token) or _UNC_RE.match(token):
        return True
    if token.startswith(("~", "/")) or token.startswith(("./", "../", ".\\", "..\\")):
        return True
    if "\\" in token:
        return True
    return "/" in token


def _command_base(word: str) -> str:
    """The executable's own name, from however the command spelled its path.

    `word.split("/")[-1]` was the whole of it, so `C:\\Windows\\System32\\cmd.exe`
    compared as the literal string `C:\\Windows\\System32\\cmd.exe` and matched
    nothing in the table — including for `cmd.exe` and `powershell.exe`, which were
    in it. Splitting on both separators fixes the lookup; the `.exe` suffix is
    dropped because `del.exe` is not a different program from `del`, and Windows
    resolves both spellings through `PATHEXT`.
    """
    base = re.split(r"[\\/]", word)[-1]
    if base.lower().endswith(".exe"):
        base = base[: -len(".exe")]
    return base


def _table_categories(base: str) -> set[str] | None:
    """The categories for an executable name, matched the way its shell is.

    Case-insensitive, because that is how Windows resolves a command: `cmd.exe`,
    `Remove-Item` and `diskpart` are all normal spellings, and a case-sensitive
    dictionary finds exactly one of the three spellings of each. POSIX names are
    matched exactly first, so a POSIX host's behaviour is bit-for-bit what it was —
    `RM` on a case-sensitive filesystem is a different program, and treating it as
    `rm` would be inventing a match.
    """
    categories = COMMAND_TABLE.get(base)
    if categories is not None:
        return categories
    lowered = base.lower()
    if lowered != base and lowered in _WINDOWS_COMMAND_ALIASES:
        return COMMAND_TABLE[lowered]
    if re.search(r"[A-Z]", base) and base.lower() in COMMAND_TABLE:
        # A capitalised POSIX name (`CP`, `Ls`) is not the same program on a
        # case-sensitive filesystem, so only the Windows spellings get here — and
        # the Windows spellings are all-lowercase in the table.
        return COMMAND_TABLE[base.lower()]
    return None


def _unresolved_paths(command: str) -> list[str]:
    r"""Tokens that name a target without being a path this module can resolve.

    A glob, a variable, a wildcard and a bare `*` are all ways of saying "many
    files, I have not said which" — and on a Windows host they are how
    `del /s /q %TEMP%\*` and `Remove-Item C:\Users\*\Documents` are actually
    written. They are surfaced rather than dropped, because a destructive command
    whose targets cannot be named is exactly the one a human should be asked about,
    and a classifier that reports `paths: []` for it is reporting that there is
    nothing to worry about.
    """
    found: list[str] = []
    for token in re.split(r"[\s;|&]+", command):
        bare = token.strip("\"'")
        if not bare or bare in _DESCRIPTOR_TOKENS:
            continue
        if bare in _UNRESOLVED_TOKENS or "*" in bare or "?" in bare:
            if bare not in found:
                found.append(bare)
        elif _PS_PROVIDER_RE.match(bare):
            if bare not in found:
                found.append(bare)
    return found


def _redirect_target(output: object) -> str | None:
    """The path a redirect writes to, when it writes to one at all.

    `bashlex` does not always put a `Word` node here. A redirect that duplicates a
    descriptor -- `>&1`, `2>&1`, `>&2` -- carries the descriptor number as a plain
    `int`, and this used to read `.word` off it unconditionally, so
    `AttributeError: 'int' object has no attribute 'word'` on any command using
    the idiom.

    That is not an exotic command. `cmd > out 2>&1` is how people silence stderr,
    and it is on the end of nearly every build, test and lint invocation an agent
    writes when it is working on code. The exception was raised inside
    `classify_shell`, which runs on the toolhost's path *before* the tool is
    allowed to do anything, so it did not fail one call: it propagated out of
    `Toolhost.execute` and out of the goal loop's step, where nothing catches it,
    and killed the whole life cycle. The agent then re-ran the same failing step
    every cycle, forever, and produced neither work nor words -- which is what a
    coding agent looks like from the outside when it has been handed a shell.

    So a descriptor is not a path and is skipped, a `Word` is read as before, and
    anything else is skipped rather than guessed at. There is no path to recover
    from a bare `int`, because there is no file in it.
    """
    word = getattr(output, "word", None)
    if isinstance(word, str) and word:
        return word
    return None


def extract_paths(command: str) -> list[str]:
    try:
        parsed = bashlex.parse(command)
    except Exception:
        parsed = []
    paths: list[str] = []
    if parsed:
        for tree in parsed:
            for node in _walk(tree):
                kind = getattr(node, "kind", None)
                if kind == "word":
                    word = getattr(node, "word", None)
                    if word and _looks_like_path(word):
                        paths.append(word)
                elif kind == "redirect":
                    target = _redirect_target(getattr(node, "output", None))
                    if target is not None:
                        paths.append(target)
    else:
        for token in command.split():
            if _looks_like_path(token):
                paths.append(token)
    for match in _REDIRECT_RE.finditer(command):
        target = match.group(1)
        if target and not _DESCRIPTOR_RE.match(target):
            paths.append(target)
    unique: list[str] = []
    for p in paths:
        if p not in unique:
            unique.append(p)
    return unique


def _walk(node: object):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in getattr(current, "parts", []) or []:
            stack.append(child)
        for child in getattr(current, "list", []) or []:
            stack.append(child)


def classify_shell(command: str) -> SideEffects:
    """What a shell command would do, asked before it is allowed to do it.

    This runs on the toolhost's path, before the tool is permitted to act, so an
    unrecognised command is the dangerous case rather than the annoying one: a
    command the table has never heard of is not a command that is harmless, it is a
    command this function failed to understand.
    """
    effects = SideEffects()
    matched_patterns: set[str] = set()
    try:
        trees = list(bashlex.parse(command))
        command_nodes = _extract_command_nodes(trees)
    except Exception:
        trees = []
        command_nodes = command.split()
    for node in command_nodes:
        word = node.word if hasattr(node, "word") else str(node)
        base = _command_base(word)
        categories = _table_categories(base)
        if categories is not None and base not in matched_patterns:
            effects.categories |= categories
            matched_patterns.add(base)
            if base == "sudo":
                effects.notes.append("sudo present (admin; allowed per permissions.sudo_allowed)")
            if base == "git":
                lowered = command.lower()
                if "--force" in lowered or "reset --hard" in lowered:
                    effects.categories.add("irreversible")
    # `bashlex` is a POSIX shell parser. It reads `del /s /q C:\temp` as words
    # and pipes well enough, but it has no idea what `cmd /c` means, so anything
    # Windows-shaped is matched against the raw command text below rather than
    # trusted to have been seen by the parser.
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            effects.notes.append(f"dangerous: {reason}")
            if "(H3)" in reason or "(H6)" in reason:
                effects.categories.add("blocked")
            else:
                effects.categories.add("irreversible")
    for pattern, reason in WINDOWS_DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            effects.notes.append(f"dangerous: {reason}")
            if "(H3)" in reason or "(H6)" in reason:
                effects.categories.add("blocked")
            else:
                effects.categories.add("irreversible")
    effects.paths = extract_paths(command)
    unresolved = _unresolved_paths(command)
    if unresolved:
        # Not decoration: `GuardianGate._review_destructive` walks `paths` looking
        # for a commissioned artifact, and a glob cannot be one. Naming it means
        # the caller sees a target it could not resolve instead of a shorter list
        # that looks complete.
        effects.paths.extend(unresolved)
        effects.notes.append(
            "targets not resolvable to a path: "
            + ", ".join(unresolved)
            + " — the command names files by pattern or variable"
        )
        # A destructive command whose targets are unnameable is exactly the one to
        # be careful with, and on Windows it is also the *only* way such a command
        # is usually written: `del /s /q %TEMP%\*`, `Remove-Item C:\Users\*`. On
        # POSIX the flag-based rules already cover the same ground, so this adds a
        # category rather than replacing one.
        if effects.categories & {"delete", "irreversible", "blocked"}:
            effects.categories.add("unresolved_target")
    if ">" in command or ">>" in command:
        effects.categories.add("write")
    return effects


def _extract_command_nodes(trees: list) -> list:
    nodes = []
    for tree in trees:
        for node in _walk(tree):
            if getattr(node, "kind", None) == "command":
                for part in getattr(node, "parts", []):
                    nodes.append(part)
    return nodes


TOOL_CATEGORIES: dict[str, set[str]] = {
    "shell.run": {"execute", "network"},
    "shell.job": {"execute", "network"},
    "shell.session": {"execute"},
    "fs.read": set(),
    "fs.list": set(),
    "fs.write": {"write"},
    "fs.edit": {"write"},
    "fs.move": {"move"},
    "fs.delete": {"delete", "irreversible"},
    "code.exec": {"execute"},
    "browser.visit": {"network", "read"},
    "browser.click": {"read"},
    "browser.type": {"write"},
    "browser.extract": {"read"},
    "browser.screenshot": {"read"},
    "browser.close": set(),
    "desktop.key": {"write"},
    "desktop.type": {"write"},
    "desktop.click": {"write"},
    "desktop.move": {"write"},
    "desktop.screenshot": {"read"},
    "desktop.windows": {"read"},
    "http.request": {"network", "write"},
    "comm.send": {"network", "write"},
    "memory.record": set(),
    "intent.create": set(),
    "intent.update": set(),
    "intent.close": set(),
    "intent.list": set(),
    "thread.spawn": {"execute"},
    "schedule.at": set(),
    "schedule.every": set(),
    "schedule.cancel": set(),
    "tools.register": {"write"},
    "tools.run": {"execute"},
    "secrets.set": set(),
    "secrets.list": set(),
    "guardian.undo": {"write"},
}


def classify_tool(tool: str, args: dict) -> SideEffects:
    categories = set(TOOL_CATEGORIES.get(tool, {"write"}))
    paths: list[str] = []
    for key in ("path", "from_path", "to_path", "target", "file", "dir", "workdir"):
        value = args.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    if tool == "shell.run" and isinstance(args.get("command"), str):
        shell_effects = classify_shell(args["command"])
        categories |= shell_effects.categories
        paths.extend(shell_effects.paths)
        return SideEffects(categories=categories, paths=_unique(paths),
                           money_usd=shell_effects.money_usd, notes=shell_effects.notes)
    money = args.get("spend_usd") or args.get("_spend_usd")
    return SideEffects(
        categories=categories, paths=_unique(paths),
        money_usd=float(money) if money is not None else None,
    )


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def resolve_path(base_dir: str | Path, target: str) -> Path:
    target_path = Path(target).expanduser()
    if not target_path.is_absolute():
        target_path = (Path(base_dir) / target_path).resolve()
    return target_path
