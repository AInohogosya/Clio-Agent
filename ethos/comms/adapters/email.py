from __future__ import annotations

import asyncio
import re
import ssl
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.email")

THREAD_TAG_RE = re.compile(r"\[ethos:([0-9a-f-]+)\]")

# How many message UIDs the dedupe set remembers, and how many it drops when it
# is over. The same shape and the same numbers the Telegram adapter uses, for the
# same reason: this set bridges a restart rather than being a log, and a mailbox
# polled every 30 seconds is a stream that never ends. It was the only adapter
# whose set did not have a ceiling, so a long-running poller on a busy mailbox
# grew one Python int per message forever — and unlike its siblings it had nobody
# else's habit to copy.
SEEN_UID_LIMIT = 4096
SEEN_UID_DROP = 1024


class EmailAdapter(ChannelAdapter):
    """IMAP idle polling + SMTP sending. Conversations carry an [ethos:<id>] tag."""

    name = "email"

    def __init__(
        self,
        address: str,
        password: str,
        imap_host: str,
        smtp_host: str,
        poll_interval_s: float,
        on_inbound: Any,
    ):
        self.address = address
        self.password = password
        self.imap_host = imap_host
        self.smtp_host = smtp_host
        self.poll_interval_s = poll_interval_s
        self.on_inbound = on_inbound
        self._task: asyncio.Task | None = None
        self._running = False
        self._seen_uids: set[int] = set()

    def mark_seen_uid(self, uid: int) -> bool:
        """True the first time a UID is offered, False for every redelivery.

        IMAP hands out strictly increasing UIDs, so "oldest" and "lowest" are the
        same end and the eviction below drops the oldest messages first — which
        is the right end to forget, since anything above the window has already
        been delivered and will not be offered again.
        """
        if uid in self._seen_uids:
            return False
        self._seen_uids.add(uid)
        if len(self._seen_uids) > SEEN_UID_LIMIT:
            for stale in sorted(self._seen_uids)[:SEEN_UID_DROP]:
                self._seen_uids.discard(stale)
        return True

    async def start(self) -> None:
        # Nothing: sending is SMTP and `send` opens its own connection, so this
        # channel has no sending state to prepare. The IMAP loop is `listen`'s
        # work, because a mailbox fetched by two processes is one process
        # consuming the other's mail — the second login sees nothing, and neither
        # reports why.
        self._running = False

    async def listen(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("comms.email.listening", address=self.address,
                    interval_s=self.poll_interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("comms.email.poll_failed")
            await asyncio.sleep(self.poll_interval_s)

    async def _poll_once(self) -> int:
        import aioimaplib

        client = aioimaplib.IMAP4_SSL(host=self.imap_host)
        await client.wait_hello_from_server()
        await client.login(self.address, self.password)
        await client.select("INBOX")
        result, data = await client.uid("search", None, "UNSEEN")
        if result != "OK":
            return 0
        uids = (data[0] or b"").split()
        count = 0
        for uid in uids[:20]:
            uid_int = int(uid)
            if not self.mark_seen_uid(uid_int):
                continue
            result, fetched = await client.uid("fetch", uid, "(RFC822)")
            if result != "OK" or not fetched:
                continue
            raw = None
            for item in fetched:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw = item[1]
                    break
            if raw is None:
                continue
            message = self._parse_email(raw)
            if message is not None and message.text.strip():
                await self.on_inbound(message)
                count += 1
        await client.logout()
        return count

    def _parse_email(self, raw: bytes) -> Message | None:
        try:
            parsed = message_from_bytes(raw)
            subject = str(make_header(decode_header(parsed.get("Subject", ""))))
            sender_name, sender_addr = parseaddr(parsed.get("From", ""))
            body = ""
            if parsed.is_multipart():
                for part in parsed.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = parsed.get_payload(decode=True).decode("utf-8", errors="replace")
            tag_match = THREAD_TAG_RE.search(subject)
            in_reply_to = tag_match.group(1) if tag_match else None
            person_id = f"email:{sender_addr}"
            return Message(
                channel=self.name,
                person_id=person_id,
                text=f"{subject}\n\n{body}".strip(),
                conversation_key=f"email:{sender_addr}",
                attachments=[],
                in_reply_to=in_reply_to,
                # `parseaddr` has been splitting the `From` header into a display
                # name and an address since before this adapter existed, and the
                # name half was assigned to a local variable and never read: every
                # email arrived from the agent's point of view as a bare address.
                # The address is not usable as a name — `alice@example.com` in a
                # prompt reads as a person called `alice`, and where the local part
                # is not a name at all it reads as noise — so it is dropped here
                # rather than shown as though it were chosen.
                sender_name=_header_name(sender_name, sender_addr),
            )
        except Exception:
            logger.exception("comms.email.parse_failed")
            return None

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        from email.message import EmailMessage

        import aiosmtplib

        if not person_id or not person_id.startswith("email:"):
            return {"status": "failed", "error": "email adapter requires email: person_id"}
        to_addr = person_id.split(":", 1)[1]
        first_line = text.strip().splitlines()[0][:60] if text.strip() else "(reply)"
        subject = f"Re: {first_line}"
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(text)
        try:
            await aiosmtplib.send(
                msg, hostname=self.smtp_host, port=587, start_tls=True,
                username=self.address, password=self.password,
            )
            return {"status": "sent", "to": to_addr}
        except Exception as exc:
            logger.exception("comms.email.send_failed")
            return {"status": "failed", "error": str(exc)}


def _header_name(sender_name: str | None, sender_addr: str) -> str | None:
    """The `From` display name, or `None` when there is not one worth showing.

    Two spellings arrive here and they mean different things. `parseaddr` returns
    `("Alice Smith", "alice@example.com")` for a name the sender chose and
    `("", "alice@example.com")` for a bare address — and it also returns
    `("alice@example.com", "")` for a header it could not parse at all, which is
    why an empty address has to give up rather than fall through.

    A display name that is *just* the address is not a display name. Mail clients
    send `From: alice@example.com` constantly, and rendering that as a person's
    name is the difference between "the agent knows who wrote to it" and "the
    agent knows a string that looks like a name". So it returns `None` and the
    address — which is already `person_id` — is what the agent sees.
    """
    name = str(sender_name or "").strip()
    if not name:
        return None
    address = str(sender_addr or "").strip()
    if address and name.lower() == address.lower():
        return None
    # A quoted display name arrives with its quotes intact from `parseaddr`, and
    # the quotes are part of RFC 5322's syntax rather than of the name.
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = name[1:-1].strip()
    return name or None


def _ssl_context_if_needed() -> ssl.SSLContext | None:
    return None
