from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clio.config import ClioConfig
from clio.observability.logger import get_logger

logger = get_logger("clio.threads.context")


def setup_worktree(config: ClioConfig, thread_id: str, base_dir: Path | None = None) -> Path:
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


# ------------------------------------------------- workspace leases (3.2)

LEASE_KINDS = frozenset({"git_worktree", "snapshot"})
LEASE_STATUSES = frozenset({"active", "committed", "rolled_back"})


@dataclass
class WorkspaceLease:
    """One transactional lease: an isolated worktree/snapshot plus fences.

    `sandbox_id` is where the lease is *contained*: the sandbox mounts this
    lease's root at `/workspace`, and every tool call a project's steps make
    runs against that mount. A lease without a sandbox (a thread's worktree,
    a deployment that opted out of containment) still carries the isolation
    of its own root; the id is how the difference is visible in the record.
    """

    id: str
    root_path: Path
    intention_id: Any = None
    kind: str = "git_worktree"
    base_ref: str | None = None
    target_path: Path | None = None
    budget_limit_usd: float | None = None
    allowed_paths: list[str] = field(default_factory=list)
    status: str = "active"
    sandbox_id: str | None = None
    sandbox_backend: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "intention_id": str(self.intention_id) if self.intention_id else None,
            "kind": self.kind, "root_path": str(self.root_path),
            "base_ref": self.base_ref,
            "target_path": str(self.target_path) if self.target_path else None,
            "budget_limit_usd": self.budget_limit_usd,
            "allowed_paths": list(self.allowed_paths), "status": self.status,
            "sandbox_id": self.sandbox_id, "sandbox_backend": self.sandbox_backend,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


def check_allowed_path(lease: WorkspaceLease, path: str | Path) -> bool:
    """Whether `path` is inside the lease root and its allowed_paths fence."""
    path = Path(str(path))
    root = lease.root_path
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if not lease.allowed_paths:
        return True
    rel_str = rel.as_posix()
    for allowed in lease.allowed_paths:
        allowed = str(allowed).strip("/")
        if rel_str == allowed or rel_str.startswith(allowed + "/"):
            return True
    return False


class WorkspaceLeaseManager:
    """In-process lease registry with per-target commit serialization.

    A git worktree is acquired for git repos, a copy-on-write/hardlink
    snapshot tree otherwise. `commit_lease` integrates under a per-target
    lock; `rollback_lease` restores and trashes artifacts.
    """

    def __init__(self, config: ClioConfig, snapshots: Any = None, trash: Any = None,
                 sandbox_manager: Any = None, db: Any = None):
        self.config = config
        self.snapshots = snapshots
        self.trash = trash
        # The containment: when a sandbox manager is present, every project
        # lease is provisioned inside a sandbox mounted at /workspace; when it
        # is not (a thread's worktree, a stripped-down test), the lease root
        # itself remains the boundary and the record says so.
        self.sandbox_manager = sandbox_manager
        self.db = db
        self.leases: dict[str, WorkspaceLease] = {}
        self._target_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    async def provision_sandbox(self, lease: WorkspaceLease, project_id: str, *,
                                space_id: str = "primary", tenant_id: str = "local",
                                network: bool = True,
                                egress_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        """Put a project's lease inside a sandbox: one project, one sandbox.

        The lease's root is what the sandbox mounts at `/workspace`; the
        sandbox id is written back onto the lease and into `sandbox_registry`
        so a caller holding either can find the other. A deployment without a
        sandbox manager gets an empty report rather than an exception: the
        lease is still isolated, and the absence of a sandbox is a fact in
        its own record, not a failure of the project.
        """
        if self.sandbox_manager is None:
            return {}
        spec = await self.sandbox_manager.provision_for_project(
            project_id, lease.root_path, space_id=space_id, tenant_id=tenant_id,
            network=network, egress_policy=egress_policy,
        )
        lease.sandbox_id = spec.sandbox_id
        lease.sandbox_backend = spec.backend
        lease.updated_at = time.time()
        return {"sandbox_id": spec.sandbox_id, "backend": spec.backend}

    def _lock_for(self, target: str) -> threading.Lock:
        with self._lock:
            return self._target_locks.setdefault(target, threading.Lock())

    def acquire(
        self,
        *,
        intention_id: Any = None,
        target_path: str | Path | None = None,
        kind: str | None = None,
        budget_limit_usd: float | None = None,
        allowed_paths: list[str] | None = None,
        base_ref: str | None = None,
    ) -> WorkspaceLease:
        target = Path(str(target_path)).expanduser() if target_path else self.config.paths.workspace.expanduser()
        lease_id = f"lease-{uuid.uuid4().hex[:12]}"
        is_git = (target / ".git").exists() or (target / ".git").is_file()
        lease_kind = kind or ("git_worktree" if is_git else "snapshot")
        if lease_kind not in LEASE_KINDS:
            raise ValueError(f"unknown lease kind: {lease_kind!r}")
        if lease_kind == "git_worktree" and is_git:
            root = setup_worktree(
                self.config, lease_id, base_dir=target,
            )
        elif lease_kind == "snapshot":
            root = self._make_snapshot_root(target, lease_id)
        else:
            root = self.config.paths.threads.expanduser() / lease_id
            root.mkdir(parents=True, exist_ok=True)
        lease = WorkspaceLease(
            id=lease_id, root_path=root, intention_id=intention_id,
            kind=lease_kind, base_ref=base_ref,
            target_path=target,
            budget_limit_usd=budget_limit_usd,
            allowed_paths=list(allowed_paths or []),
        )
        with self._lock:
            self.leases[lease_id] = lease
        return lease

    def _make_snapshot_root(self, source: Path, lease_id: str) -> Path:
        root = self.config.paths.threads.expanduser() / lease_id
        root.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            return root
        for dirpath, dirs, files in os.walk(source):
            rel = Path(dirpath).relative_to(source)
            dest_dir = root / rel
            dest_dir.mkdir(parents=True, exist_ok=True)
            for name in dirs:
                (dest_dir / name).mkdir(exist_ok=True)
            for name in files:
                src = Path(dirpath) / name
                dst = dest_dir / name
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
        return root

    def get(self, lease_id: str) -> WorkspaceLease | None:
        with self._lock:
            return self.leases.get(lease_id)

    def commit_lease(self, lease_id: str, *, verify_ok: bool = True) -> dict[str, Any]:
        """Integrate lease changes into the target under a per-target lock."""
        lease = self.get(lease_id)
        if lease is None:
            return {"status": "missing", "lease_id": lease_id}
        if lease.status != "active":
            return {"status": lease.status, "lease_id": lease_id}
        if not verify_ok:
            return self.rollback_lease(lease_id, reason="verification failed")
        target = lease.target_path
        lock = self._lock_for(str(target))
        with lock:
            if target is not None and lease.root_path.exists():
                self._copy_tree(lease.root_path, target, skip_names={".git"})
            lease.status = "committed"
            lease.updated_at = time.time()
            return {"status": "committed", "lease_id": lease_id, "target": str(target)}

    def rollback_lease(self, lease_id: str, *, reason: str = "") -> dict[str, Any]:
        """Restore the snapshot and trash artifacts created in the lease."""
        lease = self.get(lease_id)
        if lease is None:
            return {"status": "missing", "lease_id": lease_id}
        try:
            if lease.root_path.exists():
                for child in sorted(lease.root_path.rglob("*"), key=lambda p: len(str(p)), reverse=True):
                    if child.is_symlink() or child.is_file():
                        try:
                            if self.trash is not None:
                                import asyncio

                                try:
                                    asyncio.get_running_loop().create_task(
                                        self.trash.move(child, kind="lease_rollback", reason=reason)
                                    )
                                except RuntimeError:
                                    child.unlink(missing_ok=True)
                            else:
                                child.unlink(missing_ok=True)
                        except OSError:
                            pass
        finally:
            lease.status = "rolled_back"
            lease.updated_at = time.time()
        return {"status": "rolled_back", "lease_id": lease_id, "reason": reason}

    @staticmethod
    def _copy_tree(source: Path, target: Path, *, skip_names: set[str] | None = None) -> None:
        skip_names = skip_names or set()
        for dirpath, dirs, files in os.walk(source):
            if ".git" in dirs:
                dirs.remove(".git")
            rel = Path(dirpath).relative_to(source)
            dest_dir = target / rel
            dest_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                if name in skip_names:
                    continue
                src = Path(dirpath) / name
                dst = dest_dir / name
                try:
                    if dst.exists() and src.samefile(dst):
                        continue
                except OSError:
                    pass
                shutil.copy2(src, dst)

    async def persist_lease(self, lease: WorkspaceLease, db: Any) -> None:
        """Write the lease row (workspace_leases table)."""
        if db is None:
            return
        try:
            await db.execute(
                "INSERT INTO workspace_leases (id, intention_id, kind, root_path, base_ref, "
                "target_path, budget_limit_usd, allowed_paths, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9) "
                "ON CONFLICT (id) DO UPDATE SET status = $9, updated_at = now()",
                lease.id, lease.intention_id, lease.kind, str(lease.root_path),
                lease.base_ref, str(lease.target_path) if lease.target_path else None,
                lease.budget_limit_usd, json.dumps(list(lease.allowed_paths)), lease.status,
            )
        except Exception:
            logger.exception("lease.persist_failed", lease_id=lease.id)
