from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ethos.config import EthosConfig
from ethos.observability.logger import get_logger

logger = get_logger("ethos.threads.context")


def setup_worktree(config: EthosConfig, thread_id: str, base_dir: Path | None = None) -> Path:
    """Isolated workspace per Thread: a real git worktree when the base is a
    repository, otherwise a plain directory under ~/work/.threads/<id>/ [D-29]."""
    threads_root = config.paths.threads.expanduser()
    threads_root.mkdir(parents=True, exist_ok=True)
    workdir = threads_root / thread_id
    base = (base_dir or config.paths.workspace).expanduser()
    if workdir.exists():
        return workdir
    if (base / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(base), "worktree", "add", str(workdir)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            logger.info("threads.worktree_created", workdir=str(workdir))
            return workdir
        logger.warning("threads.worktree_failed", error=result.stderr[:300])
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "README.md").write_text(
        f"# Thread {thread_id}\n\nIsolated workspace for delegated thread.\n",
        encoding="utf-8",
    )
    return workdir


def cleanup_worktree(config: EthosConfig, thread_id: str) -> None:
    threads_root = config.paths.threads.expanduser()
    workdir = threads_root / thread_id
    if not workdir.exists():
        return
    marker = workdir / ".git"
    if marker.is_file():
        git_path = marker.read_text(encoding="utf-8").strip().replace("gitdir: ", "")
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(workdir)],
            cwd=git_path.split("/.git/worktree")[0] if "/.git/worktree" in git_path else None,
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return
    shutil.rmtree(workdir, ignore_errors=True)


class ThreadContext:
    """Everything a delegated Thread shares with the Self: identity kernel,
    memory, and its isolated workdir. Same code, same tools, same identity [D-01]."""

    def __init__(
        self,
        thread_id: str,
        role: str,
        goal: str,
        framing: str,
        workdir: Path,
        parent_intention_id: str | None,
        budget_usd: float | None,
        identity_doc: str | None = None,
        provider_hint: str | None = None,
        verify_payload: dict[str, Any] | None = None,
        depth: int = 1,
    ):
        self.thread_id = thread_id
        self.role = role
        self.goal = goal
        self.framing = framing
        self.workdir = workdir
        self.parent_intention_id = parent_intention_id
        self.budget_usd = budget_usd
        self.identity_doc = identity_doc
        self.provider_hint = provider_hint
        self.verify_payload = verify_payload
        self.depth = depth
