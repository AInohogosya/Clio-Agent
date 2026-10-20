"""LINE, Microsoft Teams and Google Chat, and the bearer-token proof they share.

The three doors here were added together because two of them need the same
machinery, and the tests follow the same split: one section per door for the
parts that are its own, and one for `ethos.comms.jwt`, which is the part that
would silently weaken every door using it if it were wrong.

Each test says which behaviour it is standing on. The ones worth reading rather
than running are the refusal ones — a signature nobody checks, an audience nobody
compares, and a `serviceUrl` nobody constrains are each one line of code away
from a door that answers strangers.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from ethos.comms.adapters.googlechat import GoogleChatAdapter, chat_address
from ethos.comms.adapters.line import LineAdapter, line_signature
from ethos.comms.adapters.teams import TeamsAdapter
from ethos.comms.jwt import JwtUnavailable, PublicKeys, bearer, sign_rs256, verify
from ethos.schemas.messages import Message

# A fixed RSA keypair, generated once per test session and used for both sides of
# every JWT in this file. Generated rather than committed, because a key in the
# repository is a key anybody can sign with — which is exactly the attack these
# tests are meant to show being refused.
KEYS: Any = None


def setup_module() -> None:
    global KEYS
    KEYS = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _certificate() -> x509.Certificate:
    """A self-signed certificate carrying the test key, as Google's x509 metadata does."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.UTC)
    return (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(KEYS.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(KEYS, hashes.SHA256()))


def _pem_private() -> str:
    return KEYS.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwks(*, kid: str = "test-key") -> dict[str, Any]:
    numbers = KEYS.public_key().public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                      "n": _b64(n), "e": _b64(e)}]}


def _token(claims: dict[str, Any], *, alg: str = "RS256", kid: str | None = "test-key") -> str:
    """A token signed the way a provider would sign one, for the verifier to read."""
    header: dict[str, Any] = {"typ": "JWT"}
    if kid:
        header["kid"] = kid
    if alg:
        header["alg"] = alg
    head = _b64(json.dumps(header).encode())
    body = _b64(json.dumps(claims).encode())
    if alg == "none":
        return f"{head}.{body}."
    signing_input = f"{head}.{body}".encode()
    signature = KEYS.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(signature)}"


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base: dict[str, Any] = {"iss": "https://api.botframework.com", "aud": "app-id",
                            "exp": now + 600, "iat": now, "nbf": now - 10}
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """The HTTP calls an adapter makes, recorded rather than dialled.

    One object per test, answering what that test says the provider would answer.
    The point is never the transport: it is that the adapter spends exactly the
    calls it says it spends, and no send is counted as delivered because a status
    code happened to be 200.
    """

    def __init__(self, *, jwks: dict[str, Any] | None = None,
                 post_responses: list[tuple[int, dict[str, Any]]] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.jwks = jwks if jwks is not None else _jwks()
        self.post_responses = list(post_responses or [(200, {"ok": True})])

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", url))
        return FakeResponse(200, self.jwks)

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", url, kwargs))
        status, payload = self.post_responses.pop(0) if self.post_responses else (200, {})
        return FakeResponse(status, payload)


# --------------------------------------------------------------------------- LINE


def line_event(text: str = "hello", user: str = "U4af4980629c8f9c1d8f4f4f4f4f4f4f4",
               event_id: str = "El1") -> dict[str, Any]:
    return {"destination": "Udeadbeef", "events": [{
        "type": "message", "replyToken": "rt", "id": event_id,
        "source": {"type": "user", "userId": user},
        "message": {"id": "msg1", "type": "text", "text": text},
    }]}


async def test_line_accepts_only_listed_users():
    """The shared rule, and the reason it has to be a rule at all.

    A bot token is not a secret to LINE's users: the bot's id is a fixed
    33-character string and its display name is a first name, so an unfiltered
    LINE agent answers whoever finds its QR code.
    """
    assert LineAdapter("t", "s", []).accepts("U1", None) is False
    assert LineAdapter("t", "s", ["U1"]).accepts("U1", None) is True
    assert LineAdapter("t", "s", ["U1"]).accepts("U2", None) is False
    assert LineAdapter("t", "s", [], accept_from_anyone=True).accepts("U9", None) is True


async def test_line_skips_groups_rooms_and_non_text():
    """Three shapes that are not a person talking, and one that is.

    A room is a conversation rather than a person and a reply to one goes to
    everybody in it, which is the same line Telegram draws with `allow_groups`.
    The other skips are ordinary noise: LINE sends at least five event types this
    agent has no use for, and none of them may become a 500.
    """
    adapter = LineAdapter("t", "s", [])
    payload = line_event()
    payload["events"][0]["source"] = {"type": "group", "groupId": "C1", "userId": "U1"}
    assert adapter.parse(payload) == []
    payload["events"][0]["source"] = {"type": "user", "userId": "U1"}
    payload["events"][0]["message"] = {"id": "msg1", "type": "sticker", "stickerId": "1"}
    assert adapter.parse(payload) == []
    payload["events"][0]["message"] = {"id": "msg1", "type": "text", "text": "hi"}
    assert adapter.parse(payload) == [("U1", "El1", "hi")]


async def test_line_signature_verifies_over_the_raw_bytes():
    """LINE's proof is base64 HMAC-SHA256, and it must be computed over the bytes.

    A hex digest is the shape every other provider here uses, and comparing against
    one would refuse every genuine LINE request — which is the kind of bug that
    looks like a broken tunnel rather than a wrong line of code.
    """
    raw = json.dumps(line_event()).encode()
    good = line_signature("secret")(headers={
        "x-line-signature": base64.b64encode(
            hmac.new(b"secret", raw, hashlib.sha256).digest()).decode()}, raw=raw)
    assert good is True
    assert line_signature("secret")(headers={}, raw=raw) is False
    assert line_signature("other")(headers={
        "x-line-signature": base64.b64encode(
            hmac.new(b"secret", raw, hashlib.sha256).digest()).decode()}, raw=raw) is False


async def test_line_dedupes_a_redelivered_event():
    """LINE redelivers on a timeout or a cut connection it believes it saw.

    A redelivery recorded twice is a message the agent deliberates on twice, which
    for an agent that answers is two answers to one question.
    """
    adapter = LineAdapter("t", "s", [])
    assert adapter.mark_seen("El1") is True
    assert adapter.mark_seen("El1") is False


async def test_line_send_pushes_to_the_user_and_reports_refusals():
    """Push, not reply, and the reason is not a preference.

    `replyToken` is single-use and short-lived; an agent that thinks for longer
    than it lives cannot use it. A push also has a failure mode worth testing
    here: LINE refuses with a plain-language body, and a send that reported a
    refusal as a success would put a lie in the transcript.
    """
    http = FakeHttp(post_responses=[(200, {"sentMessages": [{"id": "push-1"}]})])
    adapter = LineAdapter("token", "s", http=http)
    await adapter.start()
    result = await adapter.send(person_id="line:U1", text="hello")
    await adapter.stop()
    assert result["status"] == "sent"
    assert result["provider_message_id"] == "push-1"
    method, url, kwargs = http.calls[-1]
    assert method == "POST" and url.endswith("/message/push")
    assert kwargs["json"] == {"to": "U1", "messages": [{"type": "text", "text": "hello"}]}

    refused = FakeHttp(post_responses=[(400, {"message": "The user has not added the bot",
                                              "details": [{"message": "blocked"}]})])
    adapter = LineAdapter("token", "s", http=refused)
    await adapter.start()
    result = await adapter.send(person_id="line:U1", text="hello")
    await adapter.stop()
    assert result["status"] == "failed" and "blocked" in result["error"]


async def test_line_refuses_a_foreign_person_id_before_any_http():
    """A wrong prefix is a routing bug, and zero HTTP calls is the point.

    Sending the agent's words to a destination nobody chose is the failure this
    check exists to prevent, and it costs nothing to prevent.
    """
    http = FakeHttp()
    adapter = LineAdapter("token", "s", http=http)
    await adapter.start()
    result = await adapter.send(person_id="wa:819012345678", text="hello")
    await adapter.stop()
    assert result["status"] == "failed"
    assert http.calls == []


# -------------------------------------------------------------------------- Teams


def teams_activity(text: str = "hello", *, from_id: str = "29:abc-def",
                   conversation: str = "a:1v2pHM", service_url: str = "https://smba.trafficmanager.net/amer/") -> dict[str, Any]:
    return {
        "type": "message", "id": "act-1", "text": text,
        "from": {"id": from_id, "name": "Ada Lovelace"},
        "conversation": {"id": conversation, "isGroup": False},
        "channelId": "msteams", "serviceUrl": service_url,
        "recipient": {"id": "app-id", "name": "Ethos"},
    }


async def test_teams_verifies_a_genuine_channel_token_and_names_the_sender():
    """The whole inbound path, from a signed token to a message on the bus.

    A Teams activity carries the sender's name — the Connector resolves it from
    Azure AD before delivering — so there is no lookup and no cache, which is the
    one difference between this door and Slack's.
    """
    http = FakeHttp()
    adapter = TeamsAdapter("app-id", "secret", ["29:abc-def"], http=http)
    await adapter.start()
    received: list[Message] = []
    raw = json.dumps(teams_activity()).encode()
    headers = {"authorization": f"Bearer {_token(_claims())}"}
    assert adapter.authentic()(headers, raw) is True
    await adapter.handle_webhook(teams_activity(), received.append)
    await adapter.stop()
    assert len(received) == 1
    assert received[0].sender_name == "Ada Lovelace"
    assert received[0].person_id == "teams:a:1v2pHM"


async def test_teams_refuses_a_token_aimed_at_another_bot():
    """`aud` is the claim that stops one bot's token being replayed at another.

    Without it, any token Microsoft minted for any bot in the world would
    authenticate here. This is the one check that makes the audience mean
    something rather than decorate a log line.
    """
    http = FakeHttp()
    adapter = TeamsAdapter("app-id", "secret", http=http)
    await adapter.start()
    raw = b"{}"
    wrong_audience = adapter.authentic()(
        {"authorization": f"Bearer {_token(_claims(aud='other-bot'))}"}, raw)
    wrong_issuer = adapter.authentic()(
        {"authorization": f"Bearer {_token(_claims(iss='https://login.microsoftonline.com/other'))}"}, raw)
    expired = adapter.authentic()(
        {"authorization": f"Bearer {_token(_claims(exp=int(time.time()) - 3600))}"}, raw)
    forged = adapter.authentic()(
        {"authorization": f"Bearer {_token(_claims(), alg='none')}"}, raw)
    no_token = adapter.authentic()({}, raw)
    await adapter.stop()
    assert (wrong_audience, wrong_issuer, expired, forged, no_token) == (False,) * 5


async def test_teams_strips_its_own_mention_and_ignores_its_own_messages():
    """Two facts about how Teams behaves, both of which would otherwise be loops.

    An @mention arrives as `<at>Bot</at>` with the app's registered display name,
    so leaving it in opens every conversation with the agent's own name. And
    Teams delivers the bot its own messages, so without the sender check the
    agent answers itself until somebody notices.
    """
    adapter = TeamsAdapter("app-id", "secret", [], http=FakeHttp())
    await adapter.start()
    try:
        assert adapter.parse(teams_activity("<at>Ethos</at> please do the thing")) == \
            [("29:abc-def", "a:1v2pHM", "act-1", "please do the thing")]
        assert adapter.parse(teams_activity(from_id="app-id")) == []
        assert adapter.parse(teams_activity(text="  ")) == []
    finally:
        await adapter.stop()


async def test_teams_remembers_where_a_conversation_is_reached_and_checks_the_host():
    """`serviceUrl` is a URL this process will POST to, named by a request body.

    A genuine message from a cloud this deployment is not configured for is still
    a genuine message — so it is recorded, and the *reply* is what gets refused.
    Dropping it would throw away real words because of a configuration mismatch
    the operator can be told about instead.
    """
    adapter = TeamsAdapter("app-id", "secret", [], http=FakeHttp())
    assert adapter.remember_service_url("c1", "https://smba.trafficmanager.net/amer/") is True
    assert adapter.remember_service_url("c1", "https://smba.trafficmanager.net/amer/") is True
    # A host that merely *contains* an allowed suffix is refused, because
    # `trafficmanager.net.evil.example` is not a Microsoft host.
    assert adapter.remember_service_url("c2", "https://trafficmanager.net.evil.example/") is False
    assert adapter.remember_service_url("c3", "http://smba.trafficmanager.net/") is False
    assert adapter.service_url_allowed("https://smba.trafficmanager.net/amer/") is True


async def test_teams_send_uses_the_remembered_service_url_and_a_connector_token():
    """The half of the destination that cannot live in a `person_id`.

    The Connector token is exchanged once and reused — it authenticates the bot,
    not a destination — so one POST to the token endpoint should serve any number
    of sends.
    """
    http = FakeHttp(post_responses=[
        (200, {"token_type": "Bearer", "expires_in": 3600, "access_token": "tok"}),
        (200, {"id": "act-out"}),
    ])
    adapter = TeamsAdapter("app-id", "secret", http=http)
    await adapter.start()
    adapter.remember_service_url("a:1v2pHM", "https://smba.trafficmanager.net/amer/")
    result = await adapter.send(person_id="teams:a:1v2pHM", text="hello")
    await adapter.stop()
    assert result["status"] == "sent"
    token_calls = [call for call in http.calls if "oauth2" in call[1]]
    send_calls = [call for call in http.calls if "/v3/conversations/" in call[1]]
    assert len(token_calls) == 1 and len(send_calls) == 1
    assert send_calls[0][1] == "https://smba.trafficmanager.net/amer/v3/conversations/a:1v2pHM/activities"


async def test_teams_send_is_refused_without_a_remembered_service_url():
    """The honest consequence of a cache that does not survive a restart.

    An unsolicited Teams message after a restart cannot be sent, and the
    alternative to saying so is a door that looks open and is not.
    """
    adapter = TeamsAdapter("app-id", "secret", http=FakeHttp())
    await adapter.start()
    result = await adapter.send(person_id="teams:a:1v2pHM", text="hello")
    await adapter.stop()
    assert result["status"] == "failed"
    assert "service URL" in result["error"]


# -------------------------------------------------------------------- Google Chat


def chat_event(text: str = "hello", *, sender_name: str = "users/1234567890",
               space: str = "spaces/AAAA1111BBBB", sender_type: str = "HUMAN") -> dict[str, Any]:
    return {
        "type": "MESSAGE",
        "message": {"name": f"{space}/messages/msg-1", "text": text,
                    "sender": {"name": sender_name, "displayName": "Ada Lovelace",
                               "email": "ada@example.com", "type": sender_type}},
        "space": {"name": space, "type": "DM"},
        "user": {"name": sender_name, "displayName": "Ada Lovelace",
                 "email": "ada@example.com", "type": sender_type},
    }


def chat_token(**overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "chat@system.gserviceaccount.com", "aud": "1234567890",
        "exp": now + 600, "iat": now,
    }
    claims.update(overrides)
    return _token(claims)


async def test_google_chat_verifies_both_kinds_of_chat_token():
    """Two token kinds, one check — because which kind arrives is a console setting.

    A project-number audience is signed by Chat's own service account; an
    HTTP-endpoint audience is an ordinary Google ID token naming that account in
    its `email`. Both must pass, and a Google-signed token that is *not* Chat's
    must not.
    """
    http = FakeHttp()
    adapter = GoogleChatAdapter("bot@p.iam.gserviceaccount.com", _pem_private(),
                                "1234567890", http=http)
    await adapter.start()
    raw = b"{}"
    authentic = adapter.authentic()
    assert authentic({"authorization": f"Bearer {chat_token()}"}, raw) is True
    # The same identity, signed as an ordinary Google ID token, for a deployment
    # whose audience is the endpoint URL. It needs an adapter configured with
    # that audience, because `aud` is the claim that pins either kind.
    url_adapter = GoogleChatAdapter("bot@p.iam.gserviceaccount.com", _pem_private(),
                                    "https://example.com/hooks/googlechat", http=http)
    await url_adapter.start()
    url_authentic = url_adapter.authentic()
    assert url_authentic({"authorization": f"Bearer {chat_token(
        iss='https://accounts.google.com', email='chat@system.gserviceaccount.com',
        aud='https://example.com/hooks/googlechat')}"}, raw) is True
    # Google-signed, but for somebody else's project: refused on the audience,
    # and on the identity when the audience happens to match.
    assert authentic({"authorization": f"Bearer {chat_token(aud='99999999')}"}, raw) is False
    assert authentic({"authorization": f"Bearer {chat_token(
        iss='https://accounts.google.com', email='stranger@example.com',
        aud='1234567890')}"}, raw) is False
    await adapter.stop()
    await url_adapter.stop()


async def test_google_chat_refuses_its_own_messages_and_unrecognised_events():
    """A Chat app in a space receives its own messages, which is a self-answer loop.

    Workspace reports the sender's type, but often as *unspecified*, so this
    relies on the `BOT` marking only as far as it goes and says plainly in the
    adapter what that leaves open.
    """
    adapter = GoogleChatAdapter("bot@p.iam.gserviceaccount.com", _pem_private(),
                                "1234567890", http=FakeHttp())
    await adapter.start()
    try:
        assert adapter.parse(chat_event()) == [
            ("users/1234567890", "spaces/AAAA1111BBBB", "spaces/AAAA1111BBBB/messages/msg-1",
             "hello", "Ada Lovelace")]
        assert adapter.parse(chat_event(sender_type="BOT")) == []
        assert adapter.parse({"type": "ADDED_TO_SPACE"}) == []
        assert adapter.parse({"type": "MESSAGE"}) == []
    finally:
        await adapter.stop()


async def test_google_chat_send_mints_an_assertion_and_posts_to_the_space():
    """The outbound half, which is the reason this door holds a private key.

    Google issues no long-lived token to a non-interactive caller, so every send
    is preceded by a signed assertion. Threading is this system's own message id
    used as a `threadKey`, which is arbitrary to Google and meaningful here.
    """
    http = FakeHttp(post_responses=[
        (200, {"access_token": "tok", "expires_in": 3600}),
        (200, {"name": "spaces/AAAA/messages/msg-2"}),
    ])
    adapter = GoogleChatAdapter("bot@p.iam.gserviceaccount.com", _pem_private(),
                                "1234567890", http=http)
    await adapter.start()
    result = await adapter.send(person_id="gchat:spaces/AAAA1111BBBB", text="hello",
                                in_reply_to="018f3d2a-1111-7000-8000-000000000000")
    await adapter.stop()
    assert result["status"] == "sent"
    assertion_call = [call for call in http.calls if "oauth2.googleapis.com/token" in call[1]][0]
    assertion = assertion_call[2]["data"]["assertion"]
    header = json.loads(base64.urlsafe_b64decode(assertion.split(".")[0] + "=="))
    claims = json.loads(base64.urlsafe_b64decode(assertion.split(".")[1] + "=="))
    assert header["alg"] == "RS256" and claims["iss"] == "bot@p.iam.gserviceaccount.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/chat.bot"
    send_call = [call for call in http.calls if "spaces/AAAA1111BBBB" in call[1]][0]
    assert send_call[2]["json"]["thread"]["threadKey"] == "018f3d2a-1111-7000-8000-000000000000"


async def test_google_chat_sends_anyway_when_the_private_key_is_unusable():
    """A key that will not load breaks *sending*, not receiving — and that is deliberate.

    Inbound is verified against Google's public keys and has nothing to do with
    the service account, so a deployment that pasted a broken key should still
    hear people; what it loses is the half it cannot do.
    """
    adapter = GoogleChatAdapter("bot@p.iam.gserviceaccount.com", "not a pem key",
                                "1234567890", http=FakeHttp())
    await adapter.start()
    result = await adapter.send(person_id="gchat:spaces/AAAA", text="hello")
    await adapter.stop()
    assert result["status"] == "failed"
    assert "access token" in result["error"]


async def test_google_chat_address_is_a_space_and_refuses_anything_else():
    """The Chat API will not send to a person, so a space is the whole address."""
    assert chat_address("spaces/AAAA1111BBBB") == "gchat:spaces/AAAA1111BBBB"
    http = FakeHttp()
    adapter = GoogleChatAdapter("bot@p.iam", _pem_private(), "1", http=http)
    await adapter.start()
    result = await adapter.send(person_id="users/12345", text="hello")
    await adapter.stop()
    assert result["status"] == "failed"


# --------------------------------------------------------------------- the proof


async def test_the_bearer_proof_refuses_a_replayed_or_mangled_token():
    """Four refusals, each of which is one line of code away from being a pass.

    The audience, the expiry, the algorithm and the signature are four separate
    checks and four separate ways in. A verifier that read `alg` from the header
    instead of pinning it would accept `none` — a token that needs no key at all —
    and this test exists so that line of code cannot be written quietly.
    """
    keys = PublicKeys(())
    keys._keys = [("test-key", KEYS.public_key())]
    raw = b"{}"
    authentic = bearer(keys, audience="app-id", require=lambda claims: True)
    assert authentic({"authorization": f"Bearer {_token(_claims())}"}, raw) is True
    assert authentic({"authorization": f"Bearer {_token(_claims(aud='other'))}"}, raw) is False
    assert authentic({"authorization": f"Bearer {_token(_claims(exp=1))}"}, raw) is False
    assert authentic({"authorization": f"Bearer {_token(_claims(), alg='none')}"}, raw) is False
    assert authentic({"authorization": f"Bearer {_token(_claims(), alg='HS256')}"}, raw) is False
    assert authentic({"authorization": "Bearer not-a-token"}, raw) is False
    assert authentic({"authorization": "Basic zzz"}, raw) is False
    assert authentic({}, raw) is False


async def test_a_public_key_is_built_from_both_published_shapes():
    """A JWKS entry and a PEM certificate are different documents for the same key.

    Refusing either would mean the same door working or not depending on which
    format the publisher happened to choose that day.
    """
    from ethos.comms.jwt import _certificate_of, _entries_of, _key_of

    entry = _jwks()["keys"][0]
    cert = _certificate()
    assert _key_of(entry) is not None
    assert _certificate_of(cert.public_bytes(serialization.Encoding.PEM).decode()) is not None
    assert _certificate_of("not a certificate") is None
    assert len(_entries_of(_jwks())) == 1
    assert len(_entries_of({"kid": cert.public_bytes(serialization.Encoding.PEM).decode()})) == 1
    assert _entries_of("nonsense") == []


async def test_keys_are_refreshed_when_they_have_aged_out():
    """A cache that is only ever filled once is a door that works until a Tuesday.

    A provider rotates its signing keys without warning, so the refresh has to be
    on the request path — scheduled, because the request path cannot await.
    """
    now = {"t": 0.0}
    keys = PublicKeys(("https://keys",), ttl_s=3600, now_fn=lambda: now["t"])
    http = FakeHttp()
    await keys.refresh(http)
    assert len(http.calls) == 1
    # Young cache: no second fetch, whatever the request rate.
    for _ in range(3):
        keys.ensure_current()
    await asyncio.sleep(0.05)
    assert len(http.calls) == 1
    # Aged out: the refresh is scheduled, not awaited, so it lands on the loop
    # and the next request verifies against what it fetched.
    now["t"] = 4000.0
    keys.ensure_current()
    await asyncio.sleep(0.05)
    assert len(http.calls) == 2
    assert not keys.empty


async def test_sign_rs256_mints_an_assertion_the_token_endpoint_would_accept():
    """`sign_rs256` is the other half of `verify`, and this is the round trip.

    A service account's whole credential is that private key, so the signing path
    has to be right for the same reason the verifying one does — and the cheapest
    way to be sure is for the verifier to accept what the signer produced.
    """
    keys = PublicKeys(())
    keys._keys = [("k", _certificate().public_key())]
    token = sign_rs256({"iss": "bot@p.iam", "scope": "s", "aud": "a", "exp": int(time.time()) + 600},
                       _pem_private())
    claims = verify(token, keys=keys, audience="a",
                    require=lambda c: str(c.get("iss")) == "bot@p.iam")
    assert claims is not None and claims["iss"] == "bot@p.iam"

    with pytest.raises(JwtUnavailable):
        sign_rs256({"iss": "x"}, "not a pem key")


# ------------------------------------------------------------------- the receiver


async def test_a_push_route_without_a_secret_is_still_a_closed_door():
    """`secret=None` means "nothing to check against", and that is a refusal.

    The Teams and Google Chat routes set `secret` to a non-empty value because
    `WebhookRoute.authentic_body` refuses a route without one — but the refusal
    itself is worth testing, because it is the property that makes a
    half-configured door closed rather than published.
    """
    from ethos.comms.webhook import WebhookRoute

    route = WebhookRoute("/hooks/teams", None, lambda payload: None)
    assert route.authentic_body({"authorization": "Bearer zzz"}, b"{}") is False


async def test_the_receiver_answers_a_reachability_probe_with_a_constant():
    """LINE's console GETs the URL once, before anybody has been asked for a secret.

    A 404 there reads on the console as "this webhook URL is not valid", which is
    how a correctly configured door looks broken — so the probe is answered, with
    a constant, because there is nothing in the request to echo and nothing it
    could prove.
    """
    from ethos.comms.webhook import WebhookReceiver

    receiver = WebhookReceiver("127.0.0.1", 0, lambda message: None)
    receiver.route("/hooks/line", "s", lambda payload: None, ping=lambda: "OK")
    receiver.route("/hooks/slack", "s", lambda payload: None)
    assert receiver.started is False
    # A ping is answered; a route with none still answers 404, which is what
    # makes the handshake the only GET a route without one can serve.
    assert receiver.routes["/hooks/line"].ping is not None
    assert receiver.routes["/hooks/slack"].ping is None
