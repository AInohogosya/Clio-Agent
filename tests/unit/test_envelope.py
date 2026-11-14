"""Envelope encryption: per-space DEKs, master-key wrapping, shredding."""
from __future__ import annotations

import json

import pytest

from clio.security.envelope import (
    EnvelopeCrypto,
    EnvelopeError,
    EnvMasterKey,
    ShreddedSpaceError,
)


class FakeEnvelopeDB:
    """The two queries `EnvelopeCrypto` makes, answered from a dict."""

    def __init__(self) -> None:
        self.deks: dict[tuple[str, str, int], bytes] = {}

    async def fetchrow(self, query: str, *args):
        if "SELECT max(key_version)" in query:
            space, tenant = args
            versions = [v for (s, t, v) in self.deks if s == space and t == tenant]
            return {"v": max(versions) if versions else None}
        if len(args) == 2:
            space, tenant = args
            versions = [v for (s, t, v) in self.deks if s == space and t == tenant]
            if not versions:
                return None
            newest = max(versions)
            return {"wrapped_dek": self.deks[(space, tenant, newest)], "key_version": newest}
        space, tenant, version = args
        wrapped = self.deks.get((space, tenant, version))
        if wrapped is None:
            return None
        return {"wrapped_dek": wrapped, "key_version": version}

    async def execute(self, query: str, *args):
        if query.startswith("DELETE"):
            space, tenant = args
            doomed = [k for k in self.deks if k[0] == space and k[1] == tenant]
            for k in doomed:
                del self.deks[k]
            return f"DELETE {len(doomed)}"
        space, tenant, wrapped, version, _algo = args
        self.deks[(space, tenant, version)] = bytes(wrapped)
        return "INSERT 0 1"


@pytest.fixture()
def master() -> EnvMasterKey:
    return EnvMasterKey(b"\x11" * 32)


@pytest.fixture()
def crypto(master) -> EnvelopeCrypto:
    return EnvelopeCrypto(FakeEnvelopeDB(), master)


async def test_a_seal_round_trips_through_the_dek(crypto: EnvelopeCrypto):
    sealed = await crypto.seal("a sensitive body", "primary", "local")
    envelope = json.loads(sealed)
    assert envelope["space"] == "primary" and envelope["kv"] == 1
    assert "sensitive" not in sealed, "the plaintext is not in the envelope"
    assert await crypto.open(sealed, "primary", "local") == b"a sensitive body"


async def test_two_spaces_never_share_a_key(crypto: EnvelopeCrypto):
    a = await crypto.seal("space a", "alpha")
    b = await crypto.seal("space b", "beta")
    assert await crypto.open(a, "alpha") == b"space a"
    assert await crypto.open(b, "beta") == b"space b"
    with pytest.raises(EnvelopeError):
        # The envelope names its space; opening under a different one fails
        # authentication rather than returning the other space's bytes.
        await crypto.open(a, "beta")


async def test_shredding_a_space_destroys_its_payloads(crypto: EnvelopeCrypto):
    sealed = await crypto.seal("gone when the key is", "doomed")
    assert await crypto.open(sealed, "doomed") == b"gone when the key is"
    destroyed = await crypto.shred_space("doomed")
    assert destroyed >= 1
    with pytest.raises(ShreddedSpaceError):
        await crypto.open(sealed, "doomed")


async def test_rotation_mints_a_new_version_and_old_envelopes_still_open(crypto: EnvelopeCrypto):
    old = await crypto.seal("before rotation", "primary")
    version = await crypto.rotate("primary")
    assert version == 2
    fresh = await crypto.seal("after rotation", "primary")
    assert json.loads(fresh)["kv"] == 2
    assert await crypto.open(old, "primary") == b"before rotation"
    assert await crypto.open(fresh, "primary") == b"after rotation"


async def test_an_associated_data_mismatch_is_a_tamper_not_a_read(crypto: EnvelopeCrypto):
    sealed = await crypto.seal("checkpoint state", "primary", aad="checkpoint")
    tampered = json.loads(sealed)
    tampered["aad"] = "journal"
    with pytest.raises(EnvelopeError):
        await crypto.open(json.dumps(tampered), "primary")


async def test_a_wrong_master_key_cannot_unwrap_the_dek(crypto: EnvelopeCrypto):
    sealed = await crypto.seal("secret", "primary")
    other = EnvelopeCrypto(FakeEnvelopeDB(), EnvMasterKey(b"\x22" * 32))
    with pytest.raises(EnvelopeError):
        await other.open(sealed, "primary")


def test_a_short_master_key_is_refused():
    with pytest.raises(EnvelopeError):
        EnvMasterKey(b"too short")


def test_the_stub_marks_a_row_sealed_without_leaking(crypto: EnvelopeCrypto):
    stub = crypto.stub()
    assert stub == {"sealed": True}
    assert "payload" not in json.dumps(stub)
