from __future__ import annotations

from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.whatsapp")

GRAPH_HOST = "https://graph.facebook.com"


def normalize_phone(value: str) -> str:
    """WhatsApp's identifier as the API wants it: E.164 digits, no `+`.

    Normalized rather than passed through because the same person arrives with
    different punctuation depending on who is asking — a phone number read from
    a profile is `+81 90-1234-5678`, from a webhook it is already digits, and
    from a hand-edited config it might be either. Two spellings of one number
    would be two people, which would split the relationship record and give one
    person two interruption budgets.
    """
    return "".join(ch for ch in str(value or "") if ch.isdigit())


class WhatsappAdapter(ChannelAdapter):
    """WhatsApp Cloud API: a signed webhook in, Graph API sends out.

    There is no polling here and there cannot be. Meta's API has no "give me
    everything since last time" — it POSTs to whatever callback URL was declared
    in the dashboard, so inbound only exists if something publicly reachable
    answers that URL. The receiver is the other half of this adapter; see
    `ethos.comms.webhook`.

    Outbound is a plain HTTPS call, and it is subject to a rule the Cloud API
    imposes that no other channel here does: free-form text is only allowed
    inside a 24-hour window opened by the person's own last message. Outside it
    Meta refuses the send, and refuses it in a way that looks like a server
    error. That refusal is reported as a refusal — the transcript says the
    message was not delivered and why — rather than counted as sent, because a
    surface that believes a message arrived when it did not is the specific lie
    `tests/unit/test_reply_path.py` exists to prevent.
    """

    name = "whatsapp"

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        allowed_phone_numbers: list[str],
        graph_version: str = "v23.0",
        accept_from_anyone: bool = False,
        http: Any = None,
    ):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.graph_version = graph_version
        self.allowed = {normalize_phone(n) for n in allowed_phone_numbers if normalize_phone(n)}
        self.accept_from_anyone = accept_from_anyone
        self.http = http
        self._owns_http = http is None
        # Meta redelivers anything it does not see a 2xx for, and a redelivery
        # that gets recorded twice is a message the agent deliberates on twice.
        # Bounded, because this is a gap to bridge across restarts rather than a
        # history to keep.
        self._seen_message_ids: set[str] = set()

    async def start(self) -> None:
        if self.http is None:
            import httpx

            self.http = httpx.AsyncClient(timeout=20.0)
        logger.info("comms.whatsapp.started", phone_number_id=self.phone_number_id,
                    allowed=len(self.allowed), open_to_all=self.accept_from_anyone)

    async def listen(self) -> None:
        """Nothing on the adapter: the receiver is the host's, not this object's.

        The webhook listener is one loopback port shared by every push channel, so
        it is owned by `CommsHost` and started once, by whichever process is the
        listening one. The adapter owns parsing and sending; the receiver owns the
        door.
        """

    async def stop(self) -> None:
        if self._owns_http and self.http is not None:
            await self.http.aclose()
        self.http = None

    def accepts(self, phone: str) -> bool:
        """Whether this number is a person the agent will answer.

        An empty allowlist admits nobody. That is the same choice the Telegram
        adapter makes, and for the same reason: the alternative is a business
        number that replies to every stranger who finds it.

        `accept_from_anyone` overrides it, explicitly and only when it is set: a
        deployment whose agent is meant to answer the public has no list to write
        down, because there is no list of everybody who might write. It is a
        separate flag rather than "an empty list means yes" because the two
        states mean opposite things and a reader of this file cannot tell which
        one it is in.
        """
        return self.accept_from_anyone or normalize_phone(phone) in self.allowed

    def mark_seen(self, message_id: str) -> bool:
        """True the first time an id is offered, False for every redelivery."""
        if not message_id:
            return True
        if message_id in self._seen_message_ids:
            return False
        self._seen_message_ids.add(message_id)
        if len(self._seen_message_ids) > 2048:
            # Dropped oldest-first by insertion order, which for a delivery
            # stream is the order they arrived in — the right end to forget.
            for stale in list(self._seen_message_ids)[:512]:
                self._seen_message_ids.discard(stale)
        return True

    def parse(self, payload: dict[str, Any]) -> list[tuple[str, str, str, str | None]]:
        """
        The messages in a webhook body, as `(wa_id, provider_id, text, name)`.

        Total on purpose, and a function rather than inline code because this is
        the part of the adapter that has to be right about a shape it does not
        control: Meta nests it three deep, adds a `statuses` array alongside
        `messages` carrying delivery receipts rather than words, and can add
        fields without calling it a breaking change. Anything unrecognized yields
        nothing rather than an exception, because a webhook that answers 500 on
        an unknown message type is a webhook Meta retries forever.

        The name comes from the `contacts` array beside `messages` in the same
        `value`, which Meta keys by `wa_id` — the same number as `messages[].from`.
        So the display name a WhatsApp user set for themselves is already in the
        body this was reading, and reading `messages` alone threw it away: a
        message arrived from a phone number with no name attached to it, and the
        owner of the number was recorded as `wa:819012345678` and nothing else.
        WhatsApp has no handle concept at all, so the name is the only name
        available and the tuple's fourth element may be `None` for a contact
        Meta could not name.
        """
        names = _contact_names(payload)
        out: list[tuple[str, str, str, str | None]] = []
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes") or []:
                if not isinstance(change, dict):
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                for message in value.get("messages") or []:
                    if not isinstance(message, dict):
                        continue
                    sender = message.get("from")
                    body = message.get("text")
                    text = ""
                    if isinstance(body, dict):
                        text = str(body.get("body") or "")
                    elif isinstance(body, str):
                        text = body
                    if not sender or not text.strip():
                        continue
                    out.append((
                        str(sender),
                        str(message.get("id") or ""),
                        text.strip(),
                        names.get(normalize_phone(sender)),
                    ))
        return out

    async def handle_webhook(self, payload: dict[str, Any], on_inbound: Any) -> int:
        accepted = 0
        for sender, provider_id, text, name in self.parse(payload):
            if not self.accepts(sender):
                logger.info("comms.whatsapp.sender_not_allowed", sender=sender)
                continue
            if not self.mark_seen(provider_id):
                continue
            number = normalize_phone(sender)
            try:
                await on_inbound(Message(
                    channel=self.name,
                    person_id=f"wa:{number}",
                    text=text,
                    conversation_key=f"whatsapp:{number}",
                    sender_name=name,
                ))
                accepted += 1
            except Exception:
                logger.exception("comms.whatsapp.inbound_failed", sender=sender)
        return accepted

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        if self.http is None:
            return {"status": "failed", "error": "whatsapp adapter not started"}
        if not person_id or not person_id.startswith("wa:"):
            return {"status": "failed", "error": "whatsapp adapter requires wa: person_id"}
        to = normalize_phone(person_id.split(":", 1)[1])
        if not to:
            return {"status": "failed", "error": "no destination number"}
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text[:4096]},
        }
        url = f"{GRAPH_HOST}/{self.graph_version}/{self.phone_number_id}/messages"
        try:
            response = await self.http.post(
                url, json=body,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
        except Exception as exc:
            logger.exception("comms.whatsapp.send_failed")
            return {"status": "failed", "error": str(exc)}
        if response.status_code >= 400:
            # The 24-hour window and an unverified number are both refusals
            # Meta states plainly in the body. Passing the detail through is what
            # lets the transcript distinguish "the agent chose silence" from "the
            # agent tried to speak and was not allowed to".
            detail = _error_detail(response)
            logger.warning("comms.whatsapp.send_refused", status=response.status_code, detail=detail)
            return {"status": "failed", "error": detail, "http_status": response.status_code}
        message_id = _sent_message_id(response)
        return {"status": "sent", "to": to, "provider_message_id": message_id}


def _contact_names(payload: dict[str, Any]) -> dict[str, str]:
    """Every display name a webhook body carries, keyed by normalized number.

    `contacts` is a flat list beside `messages` in the same `value`, with a
    `wa_id` that is the same number as `messages[].from`, so this is a lookup
    rather than a join. Keyed on the *normalized* number because that is what
    `person_id` is built from, and joining an un-normalized `wa_id` against one
    would miss on exactly the punctuation differences `normalize_phone` exists
    to absorb.

    The profile name is whatever the sender chose, so it is passed on as-is and
    trimmed rather than cleaned here: the sanitizing belongs in one place, on
    the field every reader shares, so a name cannot come out clean on one surface
    and raw on another.
    """
    names: dict[str, str] = {}
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for contact in value.get("contacts") or []:
                if not isinstance(contact, dict):
                    continue
                number = normalize_phone(contact.get("wa_id") or "")
                profile = contact.get("profile")
                name = ""
                if isinstance(profile, dict):
                    name = str(profile.get("name") or "")
                if number and name.strip():
                    names[number] = name.strip()
    return names


def _error_detail(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"http_{response.status_code}"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        detail = error.get("error_user_msg") or error.get("message") or error.get("code")
        if detail:
            return str(detail)[:300]
    return f"http_{response.status_code}"


def _sent_message_id(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return str(messages[0].get("id") or "")
    return ""
