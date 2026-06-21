"""Docker sandbox management for secure agent containment."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from neuro_scaffold.config.settings import Settings

logger = structlog.get_logger(__name__)


@dataclass
class SandboxConfig:
    """Configuration for a Docker sandbox container."""
    container_name: str = "neuro-scaffold-sandbox"
    image: str = "neuro-scaffold:latest"
    workspace_volume: str = "/workspace"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = False
    readonly_rootfs: bool = True
    user: str = "neuro:neuro"
    capabilities_drop: list[str] = field(default_factory=lambda: ["ALL"])
    seccomp_profile: str | None = None
    apparmor_profile: str | None = "neuro-scaffold"
    env_vars: dict[str, str] = field(default_factory=dict)
    extra_volumes: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: Settings) -> SandboxConfig:
        return cls(
            memory_limit=settings.container_memory_limit,
            cpu_limit=settings.container_cpu_limit,
            network_enabled=settings.container_network_enabled,
            readonly_rootfs=settings.container_readonly_rootfs,
            user=f"{settings.container_user}:{settings.container_user}",
            capabilities_drop=settings.container_capabilities_drop,
            seccomp_profile=settings.container_seccomp_profile,
            apparmor_profile=settings.container_apparmor_profile,
            workspace_volume=settings.container_workspace,
        )


class SandboxError(Exception):
    """Raised when sandbox operations fail."""
    pass


class SandboxManager:
    """Manages Docker containers for secure agent execution."""

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self._config = config or SandboxConfig()
        self._running_containers: set[str] = set()

    def _docker_cmd(self, *args: str) -> list[str]:
        return ["docker"] + [a for a in args if a]

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = self._docker_cmd(*args)
        logger.debug("Running docker command", cmd=" ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check,
                timeout=120,
            )
            return result
        except subprocess.CalledProcessError as exc:
            logger.error(
                "Docker command failed",
                cmd=" ".join(cmd),
                returncode=exc.returncode,
                stderr=exc.stderr,
            )
            raise SandboxError(f"Docker command failed: {exc.stderr}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError("Docker command timed out") from exc

    def build_image(
        self,
        dockerfile_path: Path | None = None,
        build_context: Path | None = None,
        tag: str | None = None,
    ) -> str:
        """Build the Docker image for the sandbox."""
        tag = tag or self._config.image
        context = str(build_context or Path("."))
        args = ["build", "-t", tag]
        if dockerfile_path:
            args.extend(["-f", str(dockerfile_path)])
        args.append(context)
        result = self._run(*args)
        logger.info("Docker image built", tag=tag, output=result.stderr[-500:] if result.stderr else "")
        return tag

    def create_container(
        self,
        name: str | None = None,
        workspace_path: Path | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> str:
        """Create a new sandbox container."""
        name = name or self._config.container_name
        args = [
            "create",
            "--name", name,
            "--memory", self._config.memory_limit,
            "--cpus", str(self._config.cpu_limit),
            "--user", self._config.user,
            "--read-only" if self._config.readonly_rootfs else "",
            "--network", "none" if not self._config.network_enabled else "bridge",
            "--security-opt", "no-new-privileges:true",
            "--cap-drop", ",".join(self._config.capabilities_drop),
            "--tmpfs", "/tmp:size=100m,noexec,nosuid",
            "--tmpfs", "/var/tmp:size=50m,noexec,nosuid",
            "--pids-limit", "256",
            "--stop-timeout", "30",
        ]

        if self._config.seccomp_profile:
            args.extend(["--security-opt", f"seccomp={self._config.seccomp_profile}"])
        if self._config.apparmor_profile:
            args.extend(["--security-opt", f"apparmor={self._config.apparmor_profile}"])

        # Workspace volume mount (read-write, nosuid, noexec)
        if workspace_path:
            real_workspace = str(workspace_path.resolve())
            args.extend([
                "--mount",
                f"type=bind,source={real_workspace},target={self._config.workspace_volume},readonly=false,bind-propagation=private",
            ])

        for vol in self._config.extra_volumes:
            args.extend(["--mount", vol])

        merged_env = {**self._config.env_vars, **(env_vars or {})}
        for k, v in merged_env.items():
            args.extend(["-e", f"{k}={v}"])

        args.append(self._config.image)

        result = self._run(*args)
        container_id = result.stdout.strip()[:12]
        self._running_containers.add(container_id)
        logger.info("Container created", name=name, container_id=container_id)
        return container_id

    def start_container(self, container_id: str) -> None:
        """Start an existing container."""
        self._run("start", container_id)
        logger.info("Container started", container_id=container_id)

    def stop_container(self, container_id: str, timeout: int = 30) -> None:
        """Stop a running container."""
        self._run("stop", "-t", str(timeout), container_id, check=False)
        self._running_containers.discard(container_id)
        logger.info("Container stopped", container_id=container_id)

    def destroy_container(self, container_id: str) -> None:
        """Force-remove a container."""
        self._run("rm", "-f", container_id, check=False)
        self._running_containers.discard(container_id)
        logger.info("Container destroyed", container_id=container_id)

    def exec_in_container(
        self,
        container_id: str,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> tuple[str, str, int]:
        """Execute a command inside the container. Returns (stdout, stderr, exit_code)."""
        args = ["exec"]
        if workdir:
            args.extend(["--workdir", workdir])
        if env:
            for k, v in env.items():
                args.extend(["-e", f"{k}={v}"])
        args.extend([container_id, "sh", "-c", command])
        try:
            result = subprocess.run(
                self._docker_cmd(*args),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", f"Command timed out after {timeout}s", -1

    def is_running(self, container_id: str) -> bool:
        try:
            result = self._run("inspect", "-f", "{{.State.Running}}", container_id, check=False)
            return result.stdout.strip() == "true"
        except SandboxError:
            return False

    def cleanup_all(self) -> None:
        """Stop and remove all tracked containers."""
        for cid in list(self._running_containers):
            try:
                self.stop_container(cid, timeout=10)
                self.destroy_container(cid)
            except Exception as exc:
                logger.warning("Failed to cleanup container", container_id=cid, error=str(exc))
        self._running_containers.clear()
