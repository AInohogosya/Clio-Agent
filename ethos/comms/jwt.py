"""Bearer-token proofs for the two doors that sign a token rather than a body.

Three of the push channels here sign what they send, and the schemes split three
ways. Meta and LINE put an HMAC of the body in a header, which is why
`webhook.meta_signature` and `adapters.line.line_signature` are a function each
and `WebhookRoute` carries an `authentic`. Microsoft and Google do not: they
arrange an OAuth token on a channel that cannot be reached over plain HTTP
without a secret, and the proof that the request came from them is that this
token verifies against keys they publish and names *this* deployment in its
audience. There is no body to check and no signature header to compare, so there
is nothing here for `WebhookRoute` to compare — the answer is whether a JWT is
genuine, aimed here, and still in date.

That makes this file the second kind of `Authenticator`, and the reason it is one
file rather than three is that all three of its answers are the same three
questions asked of three different publishers:

    **was it signed by somebody** — against keys fetched from a URL the provider
    names, and refreshed rather than cached forever, because a provider that
    rotates its signing keys without warning would otherwise be a door that
    silently stops answering;
    **was it meant for us** — the `aud` claim, compared exactly, which is the only
    claim that stops a token minted for one bot from being replayed at another;
    **is it still worth anything** — `exp`, with a *small* tolerance, and `nbf`
    where it is present.

The fourth question — *is this our own bot talking to ourselves* — is not asked
here. It is the provider's: Google marks its own app's user as `BOT` and the Bot
Framework sends the bot its own activity, and each adapter knows which fact it is
looking at. An adapter that did not check it would answer itself.

Why this is not `pyjwt`: three reasons, in order of weight. The dependency is
used by exactly two adapters and the code it replaces is a base64 decode, a
signature check and four comparisons — all of which `cryptography` does and none
of which is where a mistake is likely. Pinning the algorithm rather than reading
it from the header closes the classic JWT failure (an `alg` of `none`, or of
`HS256` with the public key used as the HMAC secret) at the one line where it
would otherwise be decided. And the algorithms this accepts are *one* —
`RS256` — which is the only thing Microsoft and Google both sign with, so a
general-purpose library would be several hundred lines defending algorithms no
provider here will ever send.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from collections.abc import Callable
from typing import Any

from ethos.comms.webhook import Authenticator
from ethos.observability.logger import get_logger

logger = get_logger("ethos.comms.jwt")

#: The one algorithm accepted, and it is accepted because it is the only one any
#: publisher here uses: both Microsoft and Google sign channel tokens RS256. Read
#: from the header instead of pinned here, an attacker picks the algorithm and
#: this becomes a token *parser* rather than a token *verifier* — `alg: none`
#: takes no key at all, and `alg: HS256` is signed with the public key the
#: verifier just fetched.
ALGORITHM = "RS256"

#: How long a fetched key set is believed before it is fetched again.
#:
#: Long, because a fetch per inbound message would put a network round trip on
#: the path of every message for no benefit, and short, because the whole point
#: of a cached key set is that a provider can change its signing keys without
#: telling anybody — which is exactly what an hour bounds. An hour is also how
#: often Microsoft documents rotating, so this is not shorter than the thing it
#: defends against.
KEY_TTL_S = 3600.0

#: How far off the clock this process may be and still believe an `exp`.
#:
#: Deliberately smaller than `adapters.slack.MAX_SIGNATURE_AGE_S`, which is zero,
#: and much smaller than the five minutes the Bot Framework SDK allows: those two
#: numbers are about a *sender's* clock disagreeing with ours over a body that is
#: also signed, whereas a token's `exp` is a fact about a credential the caller
#: is trying to spend. A wide window here is a window an attacker chooses how to
#: enter, so it is sized to NTP drift and not to convenience.
CLOCK_SKEW_S = 60


class JwtUnavailable(RuntimeError):
    """`cryptography` is not installed, so no token on this door can be trusted.

    Its own exception rather than a refusal inside the verifier because the two
    mean different things and are worth different logs: a refused token is an
    attack or a misconfiguration arriving one request at a time, and this is an
    install that cannot verify anything ever, which is a broken deployment.
    """


_RSA: dict[str, Any] | None = None


def load_rsa() -> dict[str, Any]:
    """`cryptography`'s RSA primitives, cached, or a clear reason there are none.

    Imported here rather than at module scope for the same reason the lazy HTTP
    client on `ChannelAdapter` is: an agent whose deployment has no Teams and no
    Google Chat door should not fail to start because a dependency those two
    adapters would have used is absent. Only reaching a verification raises it,
    and only the two adapters that can raise it import this module's users.

    Cached because it is reached once per inbound message on these two doors, and
    `import` inside a function is a cache hit but not a free one.
    """
    global _RSA
    if _RSA is not None:
        return _RSA
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate
    except ImportError as exc:  # pragma: no cover - an install-time fact
        raise JwtUnavailable(
            "JWT verification needs the cryptography package; install it or turn "
            "the Teams and Google Chat doors off"
        ) from exc
    _RSA = {
        "hashes": hashes,
        "padding": padding,
        "rsa": rsa,
        "serialization": serialization,
        "load_pem_x509_certificate": load_pem_x509_certificate,
        "load_der_x509_certificate": load_der_x509_certificate,
    }
    return _RSA


def decode_segment(segment: str) -> bytes:
    """One base64url segment of a JWT as bytes.

    Padding is added back rather than stripped, because `base64` refuses a
    segment whose length is not a multiple of four and two thirds of JWTs are not
    — a decoder that raised on the shorter forms would refuse most valid tokens
    and accept only the ones it had been tested with.
    """
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def parse_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes] | None:
    """`(header, claims, signing_input, signature)` out of a token, or `None`.

    None rather than an exception for everything malformed, because every caller
    of this is on the path of deciding whether a stranger may speak, and a
    malformed token is the most ordinary thing a stranger sends. The one thing
    that is *not* forgone is the algorithm check: a token that says anything but
    `RS256` is refused here rather than being carried onward to be verified
    against keys it was never entitled to name.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    head, body, signature = parts
    try:
        header = json.loads(decode_segment(head))
        claims = json.loads(decode_segment(body))
        raw_signature = decode_segment(signature)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return None
    if header.get("alg") != ALGORITHM:
        logger.warning("comms.jwt.wrong_algorithm", alg=str(header.get("alg"))[:32])
        return None
    return header, claims, f"{head}.{body}".encode("ascii", "ignore"), raw_signature


def _key_of(entry: dict[str, Any]) -> Any:
    """An RSA public key out of one JWKS entry, or `None` if it is not usable.

    A JWKS entry is a modulus and an exponent; a certificate is a certificate.
    Both are published by the providers involved, and refusing one of them would
    mean the same door working or not depending on which format the publisher
    happened to choose that day.
    """
    crypto = load_rsa()
    if "x5c" in entry and isinstance(entry["x5c"], list) and entry["x5c"]:
        # An `x5c` entry is base64-encoded DER, not PEM: the markers the PEM
        # loader wants are not there, and passing it to that loader is an
        # exception rather than a key. Both shapes are published — a JWKS entry
        # with `n`/`e` and one with `x5c` — so both are read.
        try:
            der = base64.b64decode(entry["x5c"][0])
            return crypto["load_der_x509_certificate"](der).public_key()
        except Exception:
            return None
    if "n" in entry and "e" in entry:
        try:
            n = int.from_bytes(decode_segment(str(entry["n"])), "big")
            e = int.from_bytes(decode_segment(str(entry["e"])), "big")
            return crypto["rsa"].RSAPublicNumbers(e, n).public_key()
        except (ValueError, TypeError, binascii.Error):
            return None
    return None


def _certificate_of(entry: Any) -> Any:
    """The public key out of a bare PEM certificate string.

    Google's `service_accounts/v1/metadata/x509` endpoint publishes a mapping of
    key id to a PEM *certificate* rather than a JWKS, and it is the publisher
    Chat signs its project-number tokens with. Parsed separately from `_key_of`
    because the shape is a different document: no header, no modulus, just DER
    inside PEM markers.
    """
    if not isinstance(entry, str) or "BEGIN CERTIFICATE" not in entry:
        return None
    crypto = load_rsa()
    try:
        # Bytes, not the string: the loader has always wanted bytes and the
        # providers publish strings, so this is the one conversion between them.
        return crypto["load_pem_x509_certificate"](entry.encode("utf-8")).public_key()
    except Exception:
        return None


def _entries_of(payload: Any) -> list[dict[str, Any]]:
    """The key entries out of whichever document a key URL returned.

    Two shapes, because two publishers: a JWKS is `{"keys": [ … ]}` and the
    metadata-x509 endpoint is `{"<key id>": "-----BEGIN CERTIFICATE-----…"}`.
    Accepting both is what lets one cache serve both doors without either of them
    growing a special case, and refusing either is what would make a door stop
    working on a provider's format change.
    """
    if not isinstance(payload, dict):
        return []
    keys = payload.get("keys")
    if isinstance(keys, list):
        return [entry for entry in keys if isinstance(entry, dict)]
    out: list[dict[str, Any]] = []
    for kid, value in payload.items():
        if isinstance(value, str) and "BEGIN CERTIFICATE" in value:
            out.append({"kid": kid, "pem": value})
    return out


def _public_key(entry: dict[str, Any]) -> Any:
    return _certificate_of(entry.get("pem")) if entry.get("pem") else _key_of(entry)


class PublicKeys:
    """A provider's published signing keys, fetched once and refreshed on age.

    Kept out of the request path on purpose. `Authenticator` is synchronous
    because `WebhookRoute.authentic_body` is called from the middle of an `await`
    chain that has already read and parsed a body, and making it await would mean
    every push door in the system could block on a network call. So the keys are
    fetched at `start`, and a request that arrives later verifies against whatever
    is in memory.

    A cache that is only ever filled once is a door that works until a provider
    rotates its keys, which is the failure that arrives on a Tuesday with no
    error and no explanation. So `ensure_current` is called on the request path
    and *schedules* a refresh rather than performing one: correctness never
    depends on it — an unknown key id is simply not verified, and the publisher
    retries — while the next request after the TTL is verified against fresh
    ones. A refresh already in flight is not started again, which matters because
    the trigger is every inbound message on a busy channel.
    """

    def __init__(self, urls: tuple[str, ...], *, ttl_s: float = KEY_TTL_S, now_fn: Any = time.time):
        self.urls = urls
        self.ttl_s = ttl_s
        self.now_fn = now_fn
        self._keys: list[tuple[str | None, Any]] = []
        self._fetched_at: float | None = None
        self._refreshing = False
        # The client `refresh` was last given, kept only so the request path can
        # schedule the next refresh without holding one of its own.
        self.http: Any = None

    @property
    def empty(self) -> bool:
        """Whether nothing is believed, which makes every verification fail.

        Worth a property rather than a `len()` at the call site because "no keys"
        and "keys that did not match" are the same answer to *is this request
        from the provider* and completely different things to say in a log, and
        the first is a broken door rather than an attack.
        """
        return not self._keys

    def candidates(self, kid: str | None) -> list[Any]:
        """The keys a token might verify under, the named one first.

        Every key is offered when the name is absent or unknown rather than
        refusing outright, and that is not a fallback that weakens anything: a
        signature verifies under exactly one of them or under none. Treating the
        name as a hint rather than an index is what makes key rotation — the
        publisher adds a key before it uses it and stops using the old one after
        — a non-event, rather than a window in which every message is refused.
        Google's own reference implementation does the same thing, for the same
        reason: its `metadata/x509` endpoint does not promise that a token's
        `kid` matches the key it was signed with.
        """
        named = [key for name, key in self._keys if kid and name == kid]
        rest = [key for name, key in self._keys if not (kid and name == kid)]
        return named + rest

    async def refresh(self, http: Any) -> bool:
        """Fetch the key sets and believe them. False when nothing usable arrived.

        Partial success is success: a provider publishing from two URLs and
        failing on one is still authenticating with the other, and refusing to
        believe *any* key because one fetch failed would turn a transient fault
        into a closed door.
        """
        self._refreshing = True
        # Kept so `ensure_current` can schedule the next one. The request path
        # never holds this client's reference for any other reason.
        self.http = http
        try:
            collected: list[tuple[str | None, Any]] = []
            for url in self.urls:
                try:
                    response = await http.get(url, timeout=10.0)
                    if response.status_code >= 400:
                        logger.warning("comms.jwt.keys_refused", url=url, status=response.status_code)
                        continue
                    payload = response.json()
                except Exception:
                    logger.warning("comms.jwt.keys_unreadable", url=url)
                    continue
                for entry in _entries_of(payload):
                    key = _public_key(entry)
                    if key is not None:
                        kid = entry.get("kid")
                        collected.append((str(kid) if isinstance(kid, str) else None, key))
            if not collected:
                logger.warning("comms.jwt.no_keys", urls=list(self.urls))
                return False
            self._keys = collected
            self._fetched_at = self.now_fn()
            logger.info("comms.jwt.keys_loaded", count=len(collected), urls=list(self.urls))
            return True
        finally:
            self._refreshing = False

    def ensure_current(self) -> None:
        """Schedule a refresh when the cache has aged out. Never blocks.

        Called from the request path, so it must be cheap and must not raise: the
        only thing it does on the common path is compare two numbers and return.
        Anything that goes wrong here is swallowed, because the answer to "should
        these keys be refreshed" is never a reason to refuse a message.
        """
        try:
            if self._refreshing:
                return
            if self._fetched_at is not None and self.now_fn() - self._fetched_at < self.ttl_s:
                return
            loop = asyncio.get_running_loop()
            http = getattr(self, "http", None)
            if http is None:
                return
            loop.create_task(self.refresh(http))  # noqa: RUF006 - fire and forget, see above
        except Exception:
            logger.debug("comms.jwt.refresh_not_scheduled")


def verify(
    token: str | None,
    *,
    keys: PublicKeys,
    audience: str,
    require: Callable[[dict[str, Any]], bool] = lambda claims: True,
    now_fn: Any = time.time,
) -> dict[str, Any] | None:
    """The claims of a genuine, unexpired token aimed at `audience`, or `None`.

    Every claim this checks is checked here rather than in an adapter, because
    the order matters and is the same order every time: shape, then signature,
    then audience, then time, then the publisher's own identity. A verifier that
    checked the audience before the signature would be answering "does this token
    claim to be for us" about a token anybody could have written.

    `require` is the publisher-specific last question — *is this our Chat app*,
    *is this the Bot Framework* — handed in as a predicate rather than as an
    issuer list, because Google's answer is about two claims at once and a list
    of strings cannot express that.
    """
    if not token or not keys or not audience:
        return None
    parsed = parse_jwt(token.strip())
    if parsed is None:
        return None
    header, claims, signing_input, signature = parsed
    kid = header.get("kid")
    named_kid = str(kid) if isinstance(kid, str) else None
    candidates = keys.candidates(named_kid)
    if not candidates:
        # The one case worth a refresh on the spot: a token naming a key that is
        # not in the set is most likely a key that was just published. Scheduled
        # rather than awaited, for `PublicKeys`' reason — and refused regardless,
        # because "no key was believed" is a closed door and not a pause.
        keys.ensure_current()
        logger.warning("comms.jwt.no_keys_for", kid=str(kid)[:64])
        return None
    try:
        for key in candidates:
            try:
                key.verify(signature, signing_input, _padding(), _hash())
                break
            except Exception:
                continue
        else:
            # A malformed signature, a key of the wrong shape, a genuinely bad
            # signature. All of them are one answer, and none of them is worth
            # distinguishing to the caller: a request that failed its proof is a
            # request refused, whatever the reason was.
            logger.info("comms.jwt.signature_rejected", kid=str(kid)[:64])
            return None
    except JwtUnavailable:
        raise
    except Exception:
        logger.info("comms.jwt.signature_rejected", kid=str(kid)[:64])
        return None
    if not _audience_matches(claims.get("aud"), audience):
        logger.info("comms.jwt.wrong_audience")
        return None
    if not _unexpired(claims, now_fn()):
        return None
    if not require(claims):
        logger.info("comms.jwt.wrong_identity")
        return None
    return claims


def bearer(
    keys: PublicKeys,
    *,
    audience: str,
    require: Callable[[dict[str, Any]], bool] = lambda claims: True,
) -> Authenticator:
    """A route's proof, for a provider that puts a bearer token in a header.

    Closed over `keys` rather than holding them, so the refresh the adapter
    performs at `start` — and any refresh after it — is what the route sees,
    rather than a snapshot taken while the plan was being built.
    """
    def verify_headers(headers: dict[str, str], raw: bytes) -> bool:
        header = headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.strip().lower() != "bearer" or not token.strip():
            return False
        keys.ensure_current()
        try:
            return verify(token, keys=keys, audience=audience, require=require) is not None
        except JwtUnavailable:
            # Logged and treated as a refusal. The door is shut either way; what
            # differs is whether the operator is told it is shut because a
            # package is missing rather than because somebody is forging tokens.
            logger.error("comms.jwt.cryptography_missing")
            return False

    return verify_headers


def sign_rs256(claims: dict[str, Any], private_key_pem: str, *, now_fn: Any = time.time) -> str:
    """A compact JWT this deployment signed with its own key.

    The other half of `verify`, and needed because a Google Chat app holds a
    service account rather than a token: it has to mint a *signed assertion* to
    exchange for an access token, and a service account's whole credential is
    that private key. So the door that needs no `pyjwt` for reading needs a few
    lines of writing.

    `iat` is set here rather than trusted from `claims`, because a claim assertion
    whose issued-at is in the future is refused by the token endpoint and the
    error says nothing useful about which of the two timestamps was wrong.
    """
    crypto = load_rsa()
    try:
        key = crypto["serialization"].load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None,
        )
    except Exception as exc:
        raise JwtUnavailable(f"the Google Chat private key could not be read: {exc}") from exc
    issued = int(now_fn())
    body = {**claims, "iat": issued}
    header = {"alg": ALGORITHM, "typ": "JWT"}
    signing_input = b".".join([
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")),
    ])
    signature = key.sign(signing_input, _padding(), _hash())
    return f"{signing_input.decode('ascii')}.{_b64url(signature).decode('ascii')}"


def _b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _padding() -> Any:
    return load_rsa()["padding"].PKCS1v15()


def _hash() -> Any:
    return load_rsa()["hashes"].SHA256()


def _audience_matches(claim: Any, wanted: str) -> bool:
    """Whether a token names this deployment.

    A claim may be a string or a list, and which one it is depends on the
    publisher rather than on anything in our control. A list is accepted when the
    wanted audience is *one of* its entries, and refused when it is merely
    somewhere in the string — "not us, but contains us" is not a claim about who
    a token is for, and substring matching on an audience is how a token minted
    for `other-bot-2` comes to be accepted here.
    """
    if isinstance(claim, str):
        return hmac_equals(claim, wanted)
    if isinstance(claim, list):
        return any(isinstance(entry, str) and hmac_equals(entry, wanted) for entry in claim)
    return False


def hmac_equals(left: str, right: str) -> bool:
    """Constant-time string equality.

    Every audience comparison is a comparison against a guess, and a
    short-circuiting `==` leaks how much of a guess was right. Small enough to be
    here rather than imported, because the only import it would be is `hmac` for
    a one-line function that exists in the interest of not being the kind of
    subtle that nobody revisits.
    """
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _unexpired(claims: dict[str, Any], now: float) -> bool:
    """Whether the token is inside its own validity window.

    `exp` absent is a refusal rather than a pass. A token with no expiry is a
    permanent credential, and one that a stranger can obtain by asking a provider
    for a malformed token is not a credential at all — so the absence of the
    claim is treated as a claim that says no.
    """
    expires = claims.get("exp")
    if not isinstance(expires, (int, float)):
        return False
    if now - CLOCK_SKEW_S > float(expires):
        return False
    not_before = claims.get("nbf")
    if isinstance(not_before, (int, float)) and now + CLOCK_SKEW_S < float(not_before):
        return False
    return True


__all__ = [
    "JwtUnavailable", "PublicKeys", "bearer", "decode_segment", "load_rsa",
    "parse_jwt", "sign_rs256", "verify",
]
