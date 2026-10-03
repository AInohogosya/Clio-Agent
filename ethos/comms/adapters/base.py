from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ChannelAdapter(ABC):
    """Contact points are adapters on a bus — they are not the self [P7].

    Starting is two things, and they are separated on purpose: `start` is
    everything a door needs in order to *speak*, and `listen` is everything it
    needs in order to *hear*. `ethos-comms` does both, because hearing is its job.
    `ethos-core` does only the first, because its job is to answer — and it has to
    be able to answer on a door it never listens to, or a reply to a question
    asked on Telegram would have to be routed back out through another process
    before it could leave.

    Keeping them apart is what makes it possible for two processes to use the same
    deployment. A door that can only be opened once — a Telegram bot long-polling
    `getUpdates`, an IMAP loop fetching unseen mail, a webhook port — opened twice
    is not two listeners, it is one listener and one failure: Telegram answers the
    second poller with a conflict forever, and the webhook receiver races itself
    for the port. So exactly one process calls `listen`.
    """

    name: str = "abstract"

    @abstractmethod
    async def start(self) -> None:
        """Everything needed to send on this channel. Never receives anything."""

    @abstractmethod
    async def listen(self) -> None:
        """Everything needed to receive on this channel.

        Stated on every adapter rather than defaulted, because which side a channel
        opens is the decision this split exists to make visible: two adapters have
        a real listener here and two do not, and `ethos-comms` is the only process
        that calls it.
        """

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(
        self,
        *,
        person_id: str | None,
        text: str,
        urgency: str = "normal",
        conversation_key: str | None = None,
        in_reply_to: Any = None,
    ) -> dict[str, Any]: ...
