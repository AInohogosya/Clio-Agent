from __future__ import annotations

import subprocess
from pathlib import Path

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
