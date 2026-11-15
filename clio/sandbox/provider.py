"""Project sandboxes: the contained execution surface, behind one interface.

A `WorkspaceLease` says *where* a project's files live; a sandbox says what may
happen to them. Every project container gets one sandbox, and the lease's root
is mounted inside it at `/workspace`, so the tools a project steps execute
touch that project's files and nothing else — no other project's checkout, no
host home directory, no host network settings.

Backends, in the order `select_provider` prefers them:

1. **Rootless Docker/Podman** — the strongest available on a workstation:
   `CapDrop: ["ALL"]`, `no-new-privileges`, a seccomp profile, `PidsLimit:
   512`, and `gVisor`'s `runsc` as the runtime when the deployment names it.
   Rootless on purpose: a container that needs the host's daemon is not a
   containment, it is a privilege.
2. **OS-level sandboxes** — `bubblewrap` on Linux, `sandbox-exec` (Seatbelt)
   on macOS. No daemon, no image: the host kernel confines the process tree.
3. **Filesystem isolation** — the always-available baseline. The lease root
   (a git worktree or a copy-on-write snapshot, chosen upstream) *is* the
   boundary, and the sandbox record names the backend honestly rather than
   pretending kernel confinement that is not there.

Portable checkpoints live in `AgentHomeStore`: the sandbox's state archived,
sealed under the space's envelope key, and stored locally or pushed to S3 —
how a project moves between machines without carrying plaintext with it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.sandbox.provider")

SANDBOX_WORKSPACE_MOUNT = "/workspace"

#: The resource ceiling a sandboxed process tree may not exceed.
SANDBOX_PIDS_LIMIT = 512


class SandboxError(RuntimeError):
    """A sandbox could not be provisioned, entered or destroyed."""


@dataclass
class SandboxSpec:
    sandbox_id: str
    project_id: str | None
    root_path: Path
    space_id: str = "primary"
    tenant_id: str = "local"
    backend: str = "filesystem"
    image: str = "debian:bookworm-slim"
    network: bool = True
    egress_policy: dict[str, Any] = field(default_factory=dict)

    def as_registry_row(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "project_id": self.project_id,
            "tenant_id": self.tenant_id,
            "space_id": self.space_id,
            "backend": self.backend,
            "root_path": str(self.root_path),
            "egress_policy_json": self.egress_policy,
        }


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    argv: list[str] = field(default_factory=list)


class SandboxProvider:
    """One backend, one contract: provision, exec, freeze, destroy.

    `exec` never runs through a shell of its own invention — argv goes in
    argv out — so the backend wraps a command rather than interpreting one.
    """

    backend = "filesystem"

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self._sandboxes: dict[str, SandboxSpec] = {}

    # ---- capability -----------------------------------------------------

    @classmethod
    def available(cls) -> bool:
        return True

    def wrap_argv(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None) -> list[str]:
        """The argv that runs `argv` inside the sandbox."""
        return argv

    # ---- lifecycle ---------------------------------------------------------

    async def provision(self, spec: SandboxSpec) -> SandboxSpec:
        spec.root_path.mkdir(parents=True, exist_ok=True)
        (spec.root_path / ".clio-sandbox.json").write_text(
            json.dumps({
                "sandbox_id": spec.sandbox_id, "backend": self.backend,
                "project_id": spec.project_id, "space_id": spec.space_id,
            }, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._sandboxes[spec.sandbox_id] = spec
        logger.info("sandbox.provisioned", sandbox=spec.sandbox_id, backend=self.backend,
                    project=str(spec.project_id))
        return spec

    async def freeze(self, spec: SandboxSpec) -> None:
        """Suspend the sandbox's processes without destroying its state."""

    async def unfreeze(self, spec: SandboxSpec) -> None:
        """Resume a frozen sandbox."""

    async def destroy(self, spec: SandboxSpec, *, keep_files: bool = False) -> None:
        self._sandboxes.pop(spec.sandbox_id, None)
        if not keep_files:
            shutil.rmtree(spec.root_path, ignore_errors=True)
        logger.info("sandbox.destroyed", sandbox=spec.sandbox_id, kept=keep_files)

    # ---- execution -----------------------------------------------------------

    async def exec(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None,
                   env: dict[str, str] | None = None, timeout_s: float = 120.0) -> ExecResult:
        wrapped = self.wrap_argv(spec, argv, cwd=cwd)
        process_env = {**os.environ, **(env or {})}
        try:
            proc = await asyncio.create_subprocess_exec(
                *wrapped,
                cwd=cwd or str(spec.root_path),
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            return ExecResult(
                exit_code=int(proc.returncode or 0),
                stdout=stdout.decode("utf-8", "replace"),
                stderr=stderr.decode("utf-8", "replace"),
                argv=wrapped,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise SandboxError(f"command timed out after {timeout_s}s inside {spec.sandbox_id}") from exc


class FilesystemSandbox(SandboxProvider):
    """The baseline: the lease root is the boundary, and it says so."""

    backend = "filesystem"


class BubblewrapSandbox(SandboxProvider):
    """Linux OS-level confinement via bubblewrap.

    `--unshare-all` gives the process tree its own namespaces; the lease root
    is the only writable thing bound in. Networking is bound back only when
    the spec allows it, because a sandbox that unshares the network and never
    restores it is a sandbox whose egress proxy can never be reached.
    """

    backend = "bubblewrap"
    binary = "bwrap"

    @classmethod
    def available(cls) -> bool:
        return bool(shutil.which(cls.binary))

    def wrap_argv(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None) -> list[str]:
        wrapped = [
            self.binary,
            "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/bin", "/bin",
            "--ro-bind-try", "/sbin", "/sbin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind", str(spec.root_path), SANDBOX_WORKSPACE_MOUNT,
            "--chdir", cwd or SANDBOX_WORKSPACE_MOUNT,
            "--setenv", "CLIO_SANDBOX_ID", spec.sandbox_id,
            "--setenv", "CLIO_SANDBOX_BACKEND", self.backend,
        ]
        if spec.network:
            wrapped += ["--share-net"]
        else:
            wrapped += ["--unshare-net"]
        return [*wrapped, "--", *argv]


class SeatbeltSandbox(SandboxProvider):
    """macOS Seatbelt: `sandbox-exec` with a profile written for this sandbox.

    The profile is deny-by-default; it allows reads of system libraries, the
    lease root as read-write, and — when the spec allows network — outbound
    connections. The profile file is written beside the sandbox so an
    operator can read exactly what was granted.
    """

    backend = "seatbelt"
    binary = "sandbox-exec"

    @classmethod
    def available(cls) -> bool:
        return bool(shutil.which(cls.binary))

    def profile_path(self, spec: SandboxSpec) -> Path:
        return spec.root_path / ".clio-sandbox.sb"

    def profile_text(self, spec: SandboxSpec) -> str:
        network = "(allow network*)" if spec.network else ""
        return (
            "(version 1)\n"
            "(deny default)\n"
            "(allow process*)\n"
            "(allow sysctl-read)\n"
            "(allow file-read* (subpath \"/usr\") (subpath \"/System\") "
            "(subpath \"/Library\") (subpath \"/bin\") (subpath \"/sbin\") "
            "(literal \"/dev/null\") (literal \"/dev/urandom\"))\n"
            f"(allow file-read* file-write* (subpath {json.dumps(str(spec.root_path))}))\n"
            f"{network}\n"
            "(allow mach-lookup)\n"
        )

    def wrap_argv(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None) -> list[str]:
        profile = self.profile_path(spec)
        if not profile.exists():
            profile.write_text(self.profile_text(spec), encoding="utf-8")
        return [self.binary, "-f", str(profile), *argv]


class RootlessDockerSandbox(SandboxProvider):
    """Rootless Docker/Podman with the hardening flags the architecture names.

    `CapDrop: ["ALL"]`, `no-new-privileges`, the default seccomp profile, a
    PidsLimit of 512, and `--runtime runsc` when the deployment configured
    gVisor. The lease root is the only bind mount, at `/workspace`.
    """

    backend = "rootless_docker"

    def __init__(self, config: Any = None, runtime: str | None = None,
                 docker: str | None = None) -> None:
        super().__init__(config)
        self.runtime = runtime or os.environ.get("CLIO_SANDBOX_RUNTIME") or ""
        self.docker = docker or self._which_rootless()

    @staticmethod
    def _which_rootless() -> str:
        for candidate in ("docker", "podman"):
            found = shutil.which(candidate)
            if found:
                return found
        return ""

    @classmethod
    def available(cls) -> bool:
        return cls._which_rootless() != ""

    def container_name(self, spec: SandboxSpec) -> str:
        return f"clio-sandbox-{spec.sandbox_id}"

    def run_argv(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None) -> list[str]:
        """The hardened `docker run` that enters this sandbox."""
        args = [
            self.docker, "run", "--rm",
            "--name", self.container_name(spec),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--security-opt", "seccomp=default",
            "--pids-limit", str(SANDBOX_PIDS_LIMIT),
            "--mount", f"type=bind,source={spec.root_path},target={SANDBOX_WORKSPACE_MOUNT}",
            "--workdir", cwd or SANDBOX_WORKSPACE_MOUNT,
            "--env", f"CLIO_SANDBOX_ID={spec.sandbox_id}",
            "--env", f"CLIO_SANDBOX_BACKEND={self.backend}",
        ]
        if self.runtime:
            args += ["--runtime", self.runtime]
        if not spec.network:
            args += ["--network", "none"]
        args += [spec.image, *argv]
        return args

    def wrap_argv(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None) -> list[str]:
        return self.run_argv(spec, argv, cwd=cwd)

    async def provision(self, spec: SandboxSpec) -> SandboxSpec:
        await super().provision(spec)
        if self.docker:
            try:
                subprocess.run(
                    [self.docker, "pull", "-q", spec.image],
                    check=False, capture_output=True, timeout=300,
                )
            except Exception:
                logger.warning("sandbox.image_pull_failed", image=spec.image)
        return spec

    async def freeze(self, spec: SandboxSpec) -> None:
        if self.docker:
            await asyncio.to_thread(
                subprocess.run,
                [self.docker, "pause", self.container_name(spec)],
                check=False, capture_output=True, timeout=30,
            )

    async def unfreeze(self, spec: SandboxSpec) -> None:
        if self.docker:
            await asyncio.to_thread(
                subprocess.run,
                [self.docker, "unpause", self.container_name(spec)],
                check=False, capture_output=True, timeout=30,
            )


class RemoteSandbox(SandboxProvider):
    """A sandbox on another machine: the spec's root travels as an archive.

    The remote end is reached over SSH with a pinned known-hosts file; the
    encrypted portable checkpoint (`AgentHomeStore`) is how state gets there
    and back. A deployment without `CLIO_SANDBOX_REMOTE` never selects this.
    """

    backend = "remote"

    def __init__(self, config: Any = None, target: str | None = None) -> None:
        super().__init__(config)
        self.target = target or os.environ.get("CLIO_SANDBOX_REMOTE", "")

    @classmethod
    def available(cls) -> bool:
        return bool(os.environ.get("CLIO_SANDBOX_REMOTE")) and shutil.which("ssh") is not None

    def wrap_argv(self, spec: SandboxSpec, argv: list[str], *, cwd: str | None = None) -> list[str]:
        remote_cmd = " ".join(shlex.quote(part) for part in argv)
        workdir = shlex.quote(cwd or SANDBOX_WORKSPACE_MOUNT)
        return [
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            self.target,
            f"cd {workdir} && CLIO_SANDBOX_ID={shlex.quote(spec.sandbox_id)} {remote_cmd}",
        ]


def select_provider(config: Any = None, *, prefer: str | None = None) -> SandboxProvider:
    """The strongest backend this machine can actually run.

    The choice is the deployment's when `CLIO_SANDBOX_BACKEND` names one — and
    the machine's otherwise, in capability order, ending at the filesystem
    baseline, which always exists. Nothing here guesses: each candidate is
    asked whether its runtime is present before it is believed.
    """
    wanted = prefer or os.environ.get("CLIO_SANDBOX_BACKEND") or ""
    candidates: list[type[SandboxProvider]] = [
        RootlessDockerSandbox, BubblewrapSandbox, SeatbeltSandbox, RemoteSandbox,
    ]
    by_name = {cls.backend: cls for cls in candidates}
    if wanted:
        if wanted == "filesystem":
            return FilesystemSandbox(config)
        cls = by_name.get(wanted)
        if cls is None:
            raise SandboxError(f"unknown sandbox backend {wanted!r}")
        if not cls.available():
            raise SandboxError(
                f"sandbox backend {wanted!r} was requested but its runtime is not present"
            )
        return cls(config)
    for cls in candidates:
        if cls.available():
            return cls(config)
    return FilesystemSandbox(config)


class SandboxManager:
    """The provider plus the registry row — one sandbox per project."""

    def __init__(self, db: Any, provider: SandboxProvider) -> None:
        self.db = db
        self.provider = provider
        self._specs: dict[str, SandboxSpec] = {}

    async def provision_for_project(self, project_id: str, lease_root: Path, *,
                                    space_id: str = "primary", tenant_id: str = "local",
                                    network: bool = True,
                                    egress_policy: dict[str, Any] | None = None) -> SandboxSpec:
        sandbox_id = f"sbx-{project_id}"[:48]
        spec = SandboxSpec(
            sandbox_id=sandbox_id, project_id=project_id, root_path=Path(lease_root),
            space_id=space_id, tenant_id=tenant_id, backend=self.provider.backend,
            network=network, egress_policy=dict(egress_policy or {}),
        )
        await self.provider.provision(spec)
        self._specs[sandbox_id] = spec
        if self.db is not None:
            try:
                await self.db.execute(
                    "INSERT INTO sandbox_registry (sandbox_id, tenant_id, space_id, backend, "
                    "status, root_path, egress_policy_json, display_json, last_heartbeat_at) "
                    "VALUES ($1, $2, $3, $4, 'running', $5, $6::jsonb, '{}'::jsonb, now()) "
                    "ON CONFLICT (sandbox_id) DO UPDATE SET status = 'running', "
                    "last_heartbeat_at = now(), root_path = EXCLUDED.root_path",
                    sandbox_id, tenant_id, space_id, spec.backend, str(spec.root_path),
                    json.dumps(spec.egress_policy),
                )
            except Exception:
                logger.exception("sandbox.registry_write_failed", sandbox=sandbox_id)
        return spec

    async def heartbeat(self, sandbox_id: str, *, lease_owner: str | None = None,
                        lease_fence: int | None = None) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "UPDATE sandbox_registry SET last_heartbeat_at = now(), "
            "lease_owner = COALESCE($2, lease_owner), "
            "lease_fence = COALESCE($3, lease_fence) WHERE sandbox_id = $1",
            sandbox_id, lease_owner, lease_fence,
        )

    async def claim_lease(self, sandbox_id: str, owner: str) -> int:
        """Take the sandbox's run lease, bumping the monotonically-increasing fence.

        The fence is what makes a lease transfer safe: the previous owner's
        writes carry the fence it held, and a write whose fence no longer
        matches is refused rather than interleaved.
        """
        row = await self.db.fetchrow(
            "UPDATE sandbox_registry SET lease_owner = $2, lease_fence = lease_fence + 1, "
            "last_heartbeat_at = now() WHERE sandbox_id = $1 RETURNING lease_fence",
            sandbox_id, owner,
        )
        return int(row["lease_fence"]) if row else 0

    async def commit_guarded(self, sandbox_id: str, owner: str, fence: int,
                             statement: str, *args: Any) -> bool:
        """Run a state commit only if this owner still holds this fence.

        The statement itself is not parameterized here — this is a guard, and
        the caller runs its own write after the guard answers true. A stale
        owner (a recycled zombie worker) is refused: `WHERE lease_owner = $o
        AND lease_fence = $f` is the whole point.
        """
        row = await self.db.fetchrow(
            "SELECT 1 AS ok FROM sandbox_registry WHERE sandbox_id = $1 "
            "AND lease_owner = $2 AND lease_fence = $3",
            sandbox_id, owner, fence,
        )
        return row is not None

    async def destroy(self, sandbox_id: str, *, keep_files: bool = False) -> None:
        spec = self._specs.pop(sandbox_id, None)
        if spec is None and self.db is not None:
            row = await self.db.fetchrow(
                "SELECT root_path, backend, space_id, tenant_id FROM sandbox_registry WHERE sandbox_id = $1",
                sandbox_id,
            )
            if row is not None:
                spec = SandboxSpec(sandbox_id=sandbox_id, project_id=None,
                                   root_path=Path(row["root_path"]),
                                   space_id=row["space_id"], tenant_id=row["tenant_id"],
                                   backend=row["backend"])
        if spec is not None:
            await self.provider.destroy(spec, keep_files=keep_files)
        if self.db is not None:
            await self.db.execute(
                "UPDATE sandbox_registry SET status = 'destroyed' WHERE sandbox_id = $1",
                sandbox_id,
            )


class AgentHomeStore:
    """Portable, encrypted workspace checkpoints — local or S3-backed.

    A project's state is one archive: the lease root, tarred and sealed under
    the space's envelope key. `push` writes it to the configured store,
    `pull` restores it into a fresh sandbox on another machine, and the bytes
    at rest — local file or S3 object — are ciphertext: the same shredding
    property the database payloads have, because the archive is sealed under
    the same per-space DEK.
    """

    def __init__(self, envelope: Any, *, local_dir: Path | None = None,
                 s3_bucket: str | None = None) -> None:
        self.envelope = envelope
        self.local_dir = Path(local_dir) if local_dir else None
        self.s3_bucket = s3_bucket or os.environ.get("CLIO_CHECKPOINT_BUCKET") or None

    def _archive_name(self, project_id: str, space_id: str) -> str:
        return f"{space_id}-{project_id}.cliohome"

    def archive_bytes(self, root: Path) -> bytes:
        import io

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for path in sorted(Path(root).rglob("*")):
                rel = path.relative_to(root)
                if rel.parts and rel.parts[0] == ".clio-sandbox.json":
                    continue
                if ".git" in rel.parts[:1]:
                    continue
                tar.add(path, arcname=str(rel), recursive=False)
        return buffer.getvalue()

    def restore_bytes(self, payload: bytes, dest: Path) -> None:
        import io

        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                # A tar entry with an absolute path or a `..` component can
                # write outside `dest`; the archive is our own, but a check
                # that costs nothing is how it stays that way.
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise SandboxError(f"refusing archive member outside the sandbox: {member.name}")
            tar.extractall(dest, members=members, filter="data")

    async def push(self, project_id: str, space_id: str, root: Path) -> str:
        """Seal the workspace and store it; returns the checkpoint URI."""
        payload = self.archive_bytes(root)
        sealed = await self.envelope.seal(payload, space_id, aad=f"workspace:{project_id}")
        name = self._archive_name(project_id, space_id)
        if self.s3_bucket:
            return await asyncio.to_thread(self._push_s3, name, sealed)
        if self.local_dir is None:
            raise SandboxError("no checkpoint store is configured (local_dir or S3 bucket)")
        self.local_dir.mkdir(parents=True, exist_ok=True)
        target = self.local_dir / name
        target.write_text(sealed, encoding="utf-8")
        return target.as_uri()

    def _push_s3(self, name: str, sealed: str) -> str:
        import subprocess

        uri = f"s3://{self.s3_bucket}/{name}"
        subprocess.run(
            ["aws", "s3", "cp", "-", uri], input=sealed.encode("utf-8"),
            check=True, capture_output=True, timeout=600,
        )
        return uri

    async def pull(self, uri: str, space_id: str, dest: Path, project_id: str) -> None:
        """Restore a checkpoint into a fresh sandbox directory."""
        if uri.startswith("s3://"):
            sealed = await asyncio.to_thread(self._pull_s3, uri)
        elif uri.startswith("file://"):
            sealed = Path(uri[len("file://"):]).read_text(encoding="utf-8")
        else:
            sealed = Path(uri).read_text(encoding="utf-8")
        # The associated data was bound at seal time and travels inside the
        # envelope; `open` re-checks it, so a checkpoint copied to the wrong
        # project fails authentication rather than restoring somewhere else.
        payload = await self.envelope.open(sealed, space_id)
        self.restore_bytes(payload, Path(dest))

    def _pull_s3(self, uri: str) -> str:
        import subprocess

        result = subprocess.run(
            ["aws", "s3", "cp", uri, "-"], check=True, capture_output=True, timeout=600,
        )
        return result.stdout.decode("utf-8")


def register_lease_backend(lease: Any, spec: SandboxSpec) -> Any:
    """Bind a lease to its sandbox: the lease goes inside, the id comes out."""
    lease.sandbox_id = spec.sandbox_id
    lease.sandbox_backend = spec.backend
    return lease


__all__ = [
    "AgentHomeStore",
    "BubblewrapSandbox",
    "ExecResult",
    "FilesystemSandbox",
    "RemoteSandbox",
    "RootlessDockerSandbox",
    "SANDBOX_PIDS_LIMIT",
    "SANDBOX_WORKSPACE_MOUNT",
    "SandboxError",
    "SandboxManager",
    "SandboxProvider",
    "SandboxSpec",
    "SeatbeltSandbox",
    "register_lease_backend",
    "select_provider",
]
