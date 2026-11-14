"""Envelope encryption at rest, keyed per space.

What this module is for: the payloads nobody but the owner's key should be able
to read once they are on disk — episode bodies, fact provenance, journal
arguments, continuity checkpoints, connector tokens, portable workspace
archives. Each space gets its own Data Encryption Key (DEK); every payload is
sealed under AES-256-GCM with that DEK; and the DEK itself never rests in
plaintext — it is wrapped by the deployment's master key (an `age` identity or
an environment-held root key). Opening a payload means unwrapping the DEK with
the master key and opening the envelope with the DEK, which is what "envelope"
means: the master key is needed only to start, the DEK is needed for every
seal, and neither lives beside the data it protects.

The security property that matters most is the last one: `shred_space` deletes
the space's wrapped DEK. After that, every sealed payload of that space is
cryptographically gone — a full copy of the database without the master key is
already unreadable, and a copy *with* the master key is still unreadable,
because the thing that turned a master key into the space's key no longer
exists. Deleting a space or destroying its sandbox shreds it this way rather
than by deleting rows and hoping no backup survives.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as _secrets
from pathlib import Path
from typing import Any, Protocol

import pyrage

SEALED_STUB = {"sealed": True}
ENVELOPE_VERSION = 1
_ALGO = "AES-256-GCM"


class EnvelopeError(RuntimeError):
    """An envelope that cannot be opened, for any reason that is not a bug."""


class ShreddedSpaceError(EnvelopeError):
    """The space's DEK is gone: every sealed payload of the space is unreadable."""


class MasterKey(Protocol):
    """The root key that wraps and unwraps per-space DEKs."""

    def wrap(self, dek: bytes, context: str) -> bytes: ...

    def unwrap(self, wrapped: bytes, context: str) -> bytes: ...


class AgeMasterKey:
    """A master key held by an `age` identity file (via pyrage).

    Same machinery the SecretVault uses, one level up: the identity file
    encrypts the DEKs rather than the secrets themselves. A deployment that
    already keeps `secrets.age` encrypted with a passphrase keeps its key in
    the same place, and a stolen database without the identity file is a
    directory of opaque bytes.
    """

    def __init__(self, keyfile: Path) -> None:
        self._keyfile = Path(keyfile)
        self._passphrase: str | None = None

    def _key(self) -> str:
        if self._passphrase is None:
            self._keyfile.parent.mkdir(parents=True, exist_ok=True)
            if self._keyfile.exists():
                self._passphrase = self._keyfile.read_text(encoding="utf-8").strip()
            else:
                self._passphrase = base64.b64encode(os.urandom(48)).decode("ascii")
                fd = os.open(self._keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(self._passphrase)
        return self._passphrase

    def wrap(self, dek: bytes, context: str) -> bytes:
        return pyrage.passphrase.encrypt(dek, self._key())

    def unwrap(self, wrapped: bytes, context: str) -> bytes:
        try:
            return pyrage.passphrase.decrypt(wrapped, self._key())
        except Exception as exc:  # pyrage raises assorted native errors
            raise EnvelopeError(f"master key could not unwrap a DEK: {exc}") from exc


class EnvMasterKey:
    """A master key carried in `CLIO_MASTER_KEY`, for headless deployments.

    The environment variable holds 32 bytes of entropy, hex or base64. HKDF
    splits it into distinct wrapping keys per context, so a DEK wrapped for
    space A cannot be unwrapped as if it had been wrapped for space B even by
    somebody holding the root key — the context is part of the key derivation.
    """

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < 32:
            raise EnvelopeError("CLIO_MASTER_KEY must carry at least 32 bytes of entropy")
        self._master = master_key

    @classmethod
    def from_env(cls, raw: str | None = None) -> EnvMasterKey:
        value = raw if raw is not None else os.environ.get("CLIO_MASTER_KEY", "")
        if not value:
            raise EnvelopeError("CLIO_MASTER_KEY is not set")
        try:
            return cls(bytes.fromhex(value))
        except ValueError:
            try:
                return cls(base64.b64decode(value, validate=True))
            except Exception as exc:
                raise EnvelopeError("CLIO_MASTER_KEY is neither hex nor base64") from exc

    @staticmethod
    def _derive(master: bytes, context: str) -> bytes:
        # HKDF-SHA256 with the context as salt: RFC 5869, extract then expand.
        prk = hashlib.sha256(b"\x00" * 32 + master + b"clio-envelope").digest()
        okm = b""
        prev = b""
        counter = 1
        while len(okm) < 32:
            prev = hashlib.sha256(prev + context.encode("utf-8") + bytes([counter])).digest()
            okm += hashlib.sha256(prk + prev + bytes([counter])).digest()
            counter += 1
        return okm[:32]

    def wrap(self, dek: bytes, context: str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        kek = self._derive(self._master, context)
        nonce = os.urandom(12)
        return nonce + AESGCM(kek).encrypt(nonce, dek, context.encode("utf-8"))

    def unwrap(self, wrapped: bytes, context: str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(wrapped) < 13:
            raise EnvelopeError("wrapped DEK is too short to be an envelope")
        nonce, ct = wrapped[:12], wrapped[12:]
        kek = self._derive(self._master, context)
        try:
            return AESGCM(kek).decrypt(nonce, ct, context.encode("utf-8"))
        except Exception as exc:
            raise EnvelopeError("master key could not unwrap a DEK (wrong key or context)") from exc


class EnvelopeCrypto:
    """Seal and open payloads under a per-space DEK.

    `db` is the asyncpg-backed `clio.db.Database`. The wrapped DEKs live in the
    `space_deks` table; a DEK is created on first use for a space and unwrapped
    into an in-memory cache, so one seal costs one AES-GCM operation and no
    master-key work at all.
    """

    def __init__(self, db: Any, master: MasterKey) -> None:
        self._db = db
        self._master = master
        self._deks: dict[tuple[str, str], tuple[bytes, int]] = {}

    # ---- DEK management --------------------------------------------------------

    async def dek(self, space_id: str, tenant_id: str = "local") -> tuple[bytes, int]:
        key = (space_id, tenant_id)
        if key in self._deks:
            return self._deks[key]
        row = await self._db.fetchrow(
            "SELECT wrapped_dek, key_version FROM space_deks "
            "WHERE space_id = $1 AND tenant_id = $2 ORDER BY key_version DESC LIMIT 1",
            space_id, tenant_id,
        )
        if row is None:
            dek = _secrets.token_bytes(32)
            version = await self._store_new_dek(space_id, tenant_id, dek, 1)
        else:
            version = int(row["key_version"])
            context = f"{tenant_id}:{space_id}:v{version}"
            dek = self._master.unwrap(bytes(row["wrapped_dek"]), context)
        self._deks[key] = (dek, version)
        return self._deks[key]

    async def _store_new_dek(self, space_id: str, tenant_id: str, dek: bytes, version: int) -> int:
        context = f"{tenant_id}:{space_id}:v{version}"
        wrapped = self._master.wrap(dek, context)
        await self._db.execute(
            "INSERT INTO space_deks (space_id, tenant_id, wrapped_dek, key_version, algorithm) "
            "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (space_id, tenant_id, key_version) DO NOTHING",
            space_id, tenant_id, wrapped, version, _ALGO,
        )
        return version

    async def rotate(self, space_id: str, tenant_id: str = "local") -> int:
        """Mint the next DEK version. Old envelopes stay openable (old rows
        remain until the space is shredded); new seals use the new key."""
        self._deks.pop((space_id, tenant_id), None)
        row = await self._db.fetchrow(
            "SELECT max(key_version) AS v FROM space_deks WHERE space_id = $1 AND tenant_id = $2",
            space_id, tenant_id,
        )
        version = int(row["v"] or 0) + 1
        dek = _secrets.token_bytes(32)
        await self._store_new_dek(space_id, tenant_id, dek, version)
        return version

    async def shred_space(self, space_id: str, tenant_id: str = "local") -> int:
        """Destroy the space's wrapped DEKs: cryptographic shredding.

        Every sealed payload of the space becomes permanently unreadable, on
        this database and in any backup taken after this ran. There is no
        undo and there is not supposed to be one.
        """
        self._deks.pop((space_id, tenant_id), None)
        result = await self._db.execute(
            "DELETE FROM space_deks WHERE space_id = $1 AND tenant_id = $2", space_id, tenant_id,
        )
        return int(result.split()[-1]) if result else 0

    # ---- seal / open -------------------------------------------------------------

    async def seal(self, payload: bytes | str, space_id: str, tenant_id: str = "local",
                   *, aad: str = "") -> str:
        """Seal a payload; the result is a JSON envelope string for a BYTEA column."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        dek, version = await self.dek(space_id, tenant_id)
        nonce = os.urandom(12)
        associated = f"v{version}:{aad}".encode()
        ct = AESGCM(dek).encrypt(nonce, payload, associated)
        envelope = {
            "v": ENVELOPE_VERSION,
            "space": space_id,
            "tenant": tenant_id,
            "kv": version,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ct": base64.b64encode(ct).decode("ascii"),
            "aad": aad,
        }
        return json.dumps(envelope)

    async def open(self, sealed: bytes | str | None, space_id: str, tenant_id: str = "local") -> bytes:
        if sealed is None:
            raise EnvelopeError("nothing sealed to open")
        if isinstance(sealed, memoryview):
            sealed = bytes(sealed)
        if isinstance(sealed, bytes):
            sealed = sealed.decode("utf-8")
        try:
            envelope = json.loads(sealed)
        except (TypeError, ValueError) as exc:
            raise EnvelopeError("sealed payload is not an envelope") from exc
        if not isinstance(envelope, dict) or "ct" not in envelope:
            raise EnvelopeError("sealed payload is not an envelope")
        # The envelope names the space it was sealed for, and the caller names
        # the space they believe they are reading. When the two disagree, the
        # payload has been copied across spaces — a substitution, which is
        # exactly what the authentication below would otherwise miss — so the
        # mismatch is refused before any key is unwrapped.
        if envelope.get("space", space_id) != space_id or envelope.get("tenant", tenant_id) != tenant_id:
            raise EnvelopeError(
                "envelope belongs to space "
                f"{envelope.get('tenant', tenant_id)}:{envelope.get('space', space_id)!r}, "
                f"not {tenant_id}:{space_id!r}"
            )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        version = int(envelope.get("kv", 1))
        context = f"{tenant_id}:{space_id}:v{version}"
        row = await self._db.fetchrow(
            "SELECT wrapped_dek FROM space_deks WHERE space_id = $1 AND tenant_id = $2 AND key_version = $3",
            space_id, tenant_id, version,
        )
        if row is None:
            raise ShreddedSpaceError(
                f"the DEK for space {space_id!r} v{version} is gone: "
                "the payload was shredded and cannot be read by anyone"
            )
        dek = self._master.unwrap(bytes(row["wrapped_dek"]), context)
        nonce = base64.b64decode(envelope["nonce"])
        ct = base64.b64decode(envelope["ct"])
        associated = f"v{version}:{envelope.get('aad', '')}".encode()
        try:
            return AESGCM(dek).decrypt(nonce, ct, associated)
        except Exception as exc:
            raise EnvelopeError("envelope failed authentication (tampered or wrong key)") from exc

    @staticmethod
    def stub() -> dict[str, Any]:
        """The plaintext marker that replaces a sealed payload."""
        return dict(SEALED_STUB)


def build_envelope(db: Any, config: Any) -> EnvelopeCrypto | None:
    """The envelope a deployment gets, or None when it opted out.

    `CLIO_MASTER_KEY` selects the environment-held root key; a keyfile path in
    `config.paths.data_dir / "envelope.key"` (created on demand) selects the
    age one. `CLIO_ENVELOPE=off` opts a deployment out entirely — the stores
    then write plaintext exactly as they always did.
    """
    from clio.env import env

    if env("ENVELOPE", "on").strip().lower() in ("off", "false", "no"):
        return None
    if env("MASTER_KEY"):
        return EnvelopeCrypto(db, EnvMasterKey.from_env(env("MASTER_KEY")))
    keyfile = Path(config.paths.data_dir) / "envelope.key"
    return EnvelopeCrypto(db, AgeMasterKey(keyfile))
