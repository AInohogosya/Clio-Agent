from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from ethos.schemas.messages import Message


def offer_seen(
    seen: set[Any], key: Any, *, limit: int, drop: int, numeric: bool = False,
    guard_empty: bool = True,
) -> bool:
    """True the first time `key` is offered, False for every redelivery.

    The one dedupe set every delivery channel needs, in one place: providers
    redeliver anything they do not see acknowledged for, and a redelivery that
    gets recorded twice is a message the agent deliberates on twice. Bounded,
    because this bridges a restart rather than keeping a history — and when it
    is over the limit it drops from the front, which for a delivery stream is
    the order things arrived in. `numeric` sorts instead, for the two channels
    whose ids are integers where "lowest" is "oldest". `guard_empty` is off for
    exactly those: an integer id space starts at a real zero, while an empty
    string id is the absence of an id.
    """
    if guard_empty and not key:
        return True
    if key in seen:
        return False
    seen.add(key)
    if len(seen) > limit:
        for stale in (sorted(seen) if numeric else list(seen))[:drop]:
            seen.discard(stale)
    return True


def http_error_detail(response: Any, pick: Callable[[dict[str, Any]], Any]) -> str:
    """The refusal reason an HTTP error response carries, in the provider's words.

    Every send path reports a refused message with the detail the provider
    stated — the 24-hour window, the missing scope — rather than a bare status
    code, because the transcript has to distinguish "the agent chose silence"
    from "the agent tried to speak and was not allowed to". The three providers
    put that detail in three different fields; `pick` reads the caller's.
    """
    try:
        payload = response.json()
    except Exception:
        return f"http_{response.status_code}"
    if isinstance(payload, dict):
        detail = pick(payload)
        if detail:
            return str(detail)[:300]
    return f"http_{response.status_code}"


async def emit_inbound(
    on_inbound: Any, message: Message, *, log_event: str, logger: Any,
) -> bool:
    """Hand one inbound message to the host, and never let it take the door down.

    Per-message isolation is the contract: one malformed handler call must cost
    one message, not the rest of a webhook batch or the channel's listener.
    Returns whether the message was accepted, which is what the batch handlers
    count.
    """
    try:
        await on_inbound(message)
        return True
    except Exception:
        logger.exception(log_event)
        return False


async def await_cancelled(*tasks: asyncio.Task[Any] | None) -> None:
    """Wait out tasks that were just cancelled, swallowing what they die with.

    A cancelled task raises `CancelledError` at whatever `await` it was parked
    on, and an already-failed task raises again on await; neither is a condition
    the closer can do anything about. The explicit tuple is deliberate —
    `CancelledError` is not an `Exception` on any Python this runs.
    """
    for task in tasks:
        if task is None:
            continue
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


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
    #: The allowlist state `_accepts_id` reads. Set by every adapter that uses it.
    allowed: set[str] = set()
    accept_from_anyone: bool = False
    #: The lazy HTTP client `_ensure_http`/`_close_http` manage, and whether this
    #: adapter created it (an injected client is a test seam and never closed).
    http: Any = None
    _owns_http: bool = True

    @staticmethod
    def is_transient(exc: BaseException) -> bool:
        """Whether a failed `start` or `listen` is worth trying again.

        The host retries a door that failed this way, and gives up immediately on
        one that did not — because the two failures mean opposite things. A refused
        token or a malformed webhook URL will be refused identically on the third
        try, and retrying only delays the error somebody needs to read. A socket
        that timed out on the way to an API is a fact about that second, not about
        the configuration, and the alternative — treating it as permanent — is how a
        twelve-second network blip closes a door for the life of the process.

        `OSError` covers connections refused and reset, DNS failures, and (since it
        is an `OSError` subclass) `TimeoutError`. Adapters whose library names its
        network failures something an `OSError` check cannot see override this.
        """
        return isinstance(exc, (OSError, TimeoutError))

    def _accepts_id(self, user: str | None, channel: str | None) -> bool:
        """Whether this author, in this conversation, is somebody to answer.

        An empty allowlist admits nobody: the alternative is a bot whose name is
        public answering whoever finds it. Either id may be on the list — a
        person is identified by their user id but reached by a channel, and an
        operator who has only seen one of the two should not have to work out
        which half this is. `accept_from_anyone` overrides it, explicitly and
        only when set, because "nobody is configured yet" and "everybody is
        welcome" are opposite statements that an empty list must not conflate.
        """
        if self.accept_from_anyone:
            return True
        if not self.allowed:
            return False
        return bool(
            (user and str(user).strip() in self.allowed)
            or (channel and str(channel).strip() in self.allowed)
        )

    def _destination(self, person_id: str | None, prefix: str) -> dict[str, str] | str:
        """The bare destination out of a prefixed person_id, or a failed result.

        The send preamble every HTTP channel shares: refuse anything sent before
        `start`, refuse an id on some other channel's format, and split the
        destination off. Refusing with zero HTTP calls is the point — a foreign
        person_id is a routing bug, not a delivery attempt.
        """
        if self.http is None:
            return {"status": "failed", "error": f"{self.name} adapter not started"}
        if not person_id or not person_id.startswith(f"{prefix}:"):
            return {
                "status": "failed",
                "error": f"{self.name} adapter requires {prefix}: person_id",
            }
        destination = person_id.split(":", 1)[1].strip()
        if not destination:
            return {"status": "failed", "error": "no destination"}
        return destination

    async def _ensure_http(self, timeout: float = 20.0) -> None:
        if self.http is None:
            import httpx

            self.http = httpx.AsyncClient(timeout=timeout)

    async def _close_http(self) -> None:
        if self._owns_http and self.http is not None:
            await self.http.aclose()
        self.http = None

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
