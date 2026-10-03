from __future__ import annotations

import os
import re
import secrets as pysecrets
import stat
from pathlib import Path
from typing import Any

import pyrage
import yaml

from ethos.observability.logger import get_logger

logger = get_logger("ethos.secrets")

SECRET_HANDLE_RE = re.compile(r"\{\{secret:([A-Za-z0-9_\-.]+)\}\}")


class SecretsPolicyError(PermissionError):
    pass


class SecretNotFoundError(KeyError):
    pass


class SecretVault:
    """Encrypted-at-rest secret store (age via pyrage).

    The agent's mind only ever handles `{{secret:KEY}}` handles. Plaintext is
    substituted at toolhost runtime, and only for destinations explicitly
    allow-listed in `permissions.secrets.allowed_domains` [D-22, H6].
    """

    def __init__(self, keyfile: Path, store_path: Path, allowed_domains: list[str] | None = None):
        self.keyfile = Path(keyfile)
        self.store_path = Path(store_path)
        self.allowed_domains = list(allowed_domains or [])
        self.keyfile.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._passphrase = self._load_or_create_passphrase()
        self._cache: dict[str, str] | None = None

    def _load_or_create_passphrase(self) -> bytes:
        if self.keyfile.exists():
            data = self.keyfile.read_bytes().strip()
            if data:
                return data
        passphrase = pysecrets.token_urlsafe(48).encode("utf-8")
        fd = os.open(self.keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(passphrase)
        return passphrase

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self.store_path.exists():
            self._cache = {}
            return self._cache
        blob = self.store_path.read_bytes()
        plain = pyrage.passphrase.decrypt(blob, self._passphrase.decode("utf-8"))
        self._cache = yaml.safe_load(plain.decode("utf-8")) or {}
        return self._cache

    def _save(self, data: dict[str, str]) -> None:
        blob = pyrage.passphrase.encrypt(
            yaml.safe_dump(data, sort_keys=True).encode("utf-8"),
            self._passphrase.decode("utf-8"),
            armored=False,
        )
        fd = os.open(self.store_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        self._cache = dict(data)

    def set_secret(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._save(data)
        logger.info("secrets.set", key=key)

    def get_secret(self, key: str) -> str:
        data = self._load()
        if key not in data:
            raise SecretNotFoundError(key)
        return data[key]

    def delete_secret(self, key: str) -> None:
        data = self._load()
        if key in data:
            del data[key]
            self._save(data)

    def list_keys(self) -> list[str]:
        return sorted(self._load().keys())

    def _host_allowed(self, host: str) -> bool:
        if not host:
            return False
        host = host.lower()
        for domain in self.allowed_domains:
            domain = domain.lower().lstrip(".")
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def substitute(self, text: str, target_host: str = "") -> str:
        """Replace {{secret:KEY}} handles with plaintext for allowed hosts only."""
        if "{{secret:" not in text:
            return text
        if not self._host_allowed(target_host):
            found = SECRET_HANDLE_RE.findall(text)
            raise SecretsPolicyError(
                f"secret substitution requested for host {target_host!r} "
                f"but it is not in secrets.allowed_domains (handles: {found})"
            )

        def _repl(match: re.Match[str]) -> str:
            return self.get_secret(match.group(1))

        return SECRET_HANDLE_RE.sub(_repl, text)

    def redact_handles(self, text: str) -> str:
        return SECRET_HANDLE_RE.sub("{{secret:REDACTED}}", text)

    def ensure_store_permissions(self) -> None:
        for path in (self.store_path, self.keyfile):
            if path.exists():
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def secret_uses(text: str) -> list[str]:
    return SECRET_HANDLE_RE.findall(text or "")


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return SECRET_HANDLE_RE.sub("{{secret:REDACTED}}", obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
