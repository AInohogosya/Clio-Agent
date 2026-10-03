from __future__ import annotations

import asyncio
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.telegram")

# How long a long-poll waits for new messages, and how long the HTTP read may hang.
#
# Both are short on purpose, and the reason is the door-closing path rather than the
# running one. The library's poller checks for "please stop" only *between* reads, so
# closing a door means waiting out the read that is already in flight — and the
# library's default is a ten-second long-poll, which is ten seconds of somebody
# watching a settings screen wonder whether they pressed the right thing. A five
# second poll costs a little more traffic and buys a door that shuts in about five.
POLL_TIMEOUT_S = 5.0
POLL_READ_TIMEOUT_S = POLL_TIMEOUT_S + 5.0

# How long closing this door is given before it is abandoned and the poller is
# cancelled outright. Comfortably above `POLL_READ_TIMEOUT_S`, so the ordinary case
# is the library's own tidy shutdown and this cap is only reached when the
# cooperative path has already failed. See `stop`.
STOP_TIMEOUT_S = POLL_READ_TIMEOUT_S + 5.0


def _display_name(user: Any) -> str | None:
    """What a Telegram user calls themselves, or `None`.

    Telegram's own field for it, and the reason the result may be `None` for a
    perfectly real account: `first_name` is required by the API but may be a
    single character, a row of emoji or a phone number, and a user who set only a
    `@handle` has nothing here at all. The handle is a separate answer and is
    carried separately.

    Reads the `User` object rather than a dict because `python-telegram-bot`
    hands back an object here and this is the only place that touches one, so a
    dict-shaped fallback would be a second parser for a shape the library has
    already decided.
    """
    first = getattr(user, "first_name", None)
    last = getattr(user, "last_name", None)
    name = " ".join(part for part in (str(first or ""), str(last or "")) if part.strip())
    return name.strip() or None


def _handle(user: Any) -> str | None:
    """A Telegram `@handle`, stored without the `@` and `None` when there is none.

    Optional on Telegram's side too: a user may set a first and last name and no
    username at all, and a bot's token cannot be used to invent one.
    """
    username = getattr(user, "username", None)
    return str(username).strip().lstrip("@") or None if username else None


class TelegramAdapter(ChannelAdapter):
    """Telegram bot: long-polling inbound, direct sends outbound [Section 12].

    Long polling rather than a webhook, deliberately. Telegram will push to a
    public URL, but it will also let a process hold a connection open and ask
    "what have I missed" — which means this channel works with no tunnel, no
    public hostname and no TLS certificate. Of the two channels this repository
    can be talked to from outside, Telegram is the one that can be set up in a
    minute, and it is the one to try first.

    The allowlist is what makes it safe to have on at all. A bot token is not a
    secret to Telegram's users: anybody who learns the bot's username can write
    to it, and without a filter that is a stranger talking to an agent that has
    a shell. So an empty allowlist admits nobody, and the bot says so rather than
    staying mute — a person who has just added the bot and been ignored has no
    way to tell "no" from "broken", and the refusal carries the id they need to
    put in the config.

    `accept_from_anyone` is the deliberate opposite, and it is a separate switch
    rather than an empty-list convention for the reason stated above: refusing
    everybody because nobody is configured yet and admitting everybody because
    nobody is configured yet are not the same sentence, and only one of them is
    what somebody who installed an agent to talk to the public meant.
    """

    name = "telegram"

    def __init__(
        self,
        token: str,
        allowed_chat_ids: list[int] | None = None,
        allow_groups: bool = False,
        accept_from_anyone: bool = False,
        on_inbound: Any = None,
    ):
        self.token = token
        self.allowed_chat_ids = {int(i) for i in (allowed_chat_ids or [])}
        self.allow_groups = allow_groups
        self.accept_from_anyone = accept_from_anyone
        self.on_inbound = on_inbound
        self._app: Any = None
        self._polling = False
        # Update ids, kept so a replay cannot become a second message.
        #
        # Telegram acknowledges an update when this process acknowledges the
        # `getUpdates` call, not when the handler returns, so anything read but
        # not finished — a restart mid-turn, a crash — comes back after the next
        # poll. Without this, one "good morning" becomes two inbound rows and the
        # agent deliberates on both. Bounded because this bridges a restart, it
        # is not a log.
        self._seen_updates: set[int] = set()

    def mark_seen_update(self, update_id: int | None) -> bool:
        if not update_id:
            return True
        if update_id in self._seen_updates:
            logger.info("comms.telegram.duplicate_update", update_id=update_id)
            return False
        self._seen_updates.add(update_id)
        if len(self._seen_updates) > 4096:
            for stale in sorted(self._seen_updates)[:1024]:
                self._seen_updates.discard(stale)
        return True

    async def start(self) -> None:
        # All three from `telegram.ext`, and that is not tidiness. `Application`,
        # `MessageHandler` and `filters` have never been top-level names in
        # python-telegram-bot — they live in the `ext` package, which is where the
        # framework half of the library is. An earlier version of this line asked
        # `telegram` for all three, and nothing caught it: the module imports
        # cleanly, every test that touches this adapter exercises `accepts` or
        # `mark_seen_update` rather than the library, and the only thing that
        # actually calls this method is `ethos-comms` on a machine with the library
        # installed. So it failed as an `ImportError` inside `host.start`, was caught
        # by the one-adapter-fails rule beside the other channels, and was logged as
        # a channel that would not start — which is indistinguishable, to somebody
        # holding a phone, from a channel nobody has spoken to.
        from telegram.ext import Application, CommandHandler, MessageHandler, filters

        # Every timeout bounded, and the reason is the door-closing path rather than
        # the running one: the poller checks for "please stop" only *between* reads,
        # so closing a door waits out the read already in flight. Left at the
        # library's defaults those are ten seconds of somebody watching a settings
        # screen wonder whether they pressed the right thing, and a socket that stops
        # answering waits forever rather than long.
        self._app = (
            Application.builder()
            .token(self.token)
            .connect_timeout(10.0)
            .read_timeout(POLL_READ_TIMEOUT_S)
            .write_timeout(10.0)
            .pool_timeout(5.0)
            .build()
        )

        async def handle(update: Any, _context: Any) -> None:
            if not self.mark_seen_update(getattr(update, "update_id", None)):
                return
            tg_message = update.effective_message
            if tg_message is None or not (tg_message.text or "").strip():
                return
            chat = update.effective_chat
            chat_id = chat.id if chat is not None else 0
            text = (tg_message.text or "").strip()

            # Commands (`/id`, `/start`) never reach here: the handler is
            # registered for text that is not a command, and the command handler
            # below is where a sender discovers the id the allowlist wants —
            # in the refusal, when the allowlist does not know them.

            if not self.accepts(chat):
                await self._refuse(tg_message, chat_id)
                return

            person_id = f"tg:{chat_id}"
            message = Message(
                channel=self.name,
                person_id=person_id,
                text=text,
                conversation_key=f"telegram:{chat_id}",
                # `update.effective_user` is on every update Telegram sends and is
                # the only reason a message from Telegram reads as coming from a
                # person rather than from a number. In a group chat it is the
                # author of the message rather than the chat, which is the whole
                # reason this is read per message and not per chat.
                sender_name=_display_name(update.effective_user),
                sender_username=_handle(update.effective_user),
            )
            try:
                await self.on_inbound(message)
            except Exception:
                logger.exception("comms.telegram.inbound_failed")

        async def command(update: Any, _context: Any) -> None:
            tg_message = update.effective_message
            chat = update.effective_chat
            if tg_message is None or chat is None:
                return
            chat_id = chat.id
            if not self.allowed_chat_ids and not self.accept_from_anyone:
                await tg_message.reply_text(
                    f"your Telegram chat id is {chat_id} — add it to "
                    "channels.telegram.allowed_chat_ids to talk to this agent",
                )
                return
            if not self.accepts(chat):
                await self._refuse(tg_message, chat_id)
                return
            await tg_message.reply_text(f"your Telegram chat id is {chat_id}")

        self._app.add_handler(CommandHandler("start", command))
        self._app.add_handler(CommandHandler("id", command))
        self._app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle))
        await self._app.initialize()
        await self._app.start()
        # Deliberately not `start_polling`. Initializing the application is what
        # makes `self._app.bot` usable, which is all `send` needs; opening the
        # `getUpdates` long-poll is `listen`'s work, and it is the one part of this
        # channel that cannot be done twice — Telegram answers a second poller with
        # a conflict rather than with a second copy of the conversation. `ethos-core`
        # builds this adapter to reply and never polls it.
        logger.info("comms.telegram.ready_to_send", allowed=len(self.allowed_chat_ids),
                    allow_groups=self.allow_groups, open_to_all=self.accept_from_anyone)

    async def listen(self) -> None:
        if self._app is None or self._polling:
            return
        self._polling = True
        # Not `drop_pending_updates`: a message that arrived while this process
        # was down is a message somebody actually sent, and Telegram's own
        # suggestion to discard it would delete the only copy of an instruction
        # the agent never got to see. The dedupe in `mark_seen_update` is what
        # stops the replay that this makes possible.
        await self._app.updater.start_polling(
            drop_pending_updates=False, timeout=POLL_TIMEOUT_S)
        logger.info("comms.telegram.listening", allowed=len(self.allowed_chat_ids),
                    allow_groups=self.allow_groups, open_to_all=self.accept_from_anyone)

    async def stop(self) -> None:
        """Close the door, and be unable to prevent the process from shutting down.

        Bounded, and that is the whole change. A poller's shutdown makes one more
        network call — the library's own note that this is best-effort and is
        suppressed "to ensure graceful shutdown" — and that call waits on an HTTP
        connection the long-poll it is stopping is still holding. It can therefore
        block for as long as the pool's timeout, which used to be survivable: this
        ran once, at process exit, where the supervisor's SIGKILL was the backstop.

        It is no longer once. A door is now closed when a settings screen says so,
        so this sits between somebody pressing Save and the agent answering them,
        and an unbounded wait here is a way to wedge the whole comms process — taking
        every other channel down with a door that was never open — while the person
        watches a spinner. So the wait is capped, and a door that will not close
        politely is closed impolitely: the caller drops its handle either way, and
        the worst case is one replayed update, which `mark_seen_update` is already
        there to absorb.
        """
        if self._app is None:
            return
        app = self._app
        try:
            if self._polling:
                await self._stop_updater()
            for shutdown_step in (app.stop, app.shutdown):
                try:
                    await shutdown_step()
                except Exception:
                    logger.exception("comms.telegram.stop_failed")
        except asyncio.CancelledError:
            # The caller went away mid-close — a reload interrupted by a shutdown, or
            # by the next change arriving. Re-raised rather than swallowed, because a
            # cancelled close is the caller's business; but the `finally` below is
            # what stops it becoming ours.
            raise
        except Exception:
            logger.exception("comms.telegram.stop_failed")
        finally:
            # Synchronous, and in a `finally`, and that is the whole point. It has to
            # happen on every path out of this method *including* the one where the
            # close was cancelled half-done, because a poller that outlives its door
            # is worse than a door that will not shut: it keeps a `getUpdates`
            # long-poll open on a token the settings screen says is closed, and the
            # next person to open that door gets a conflict from a poller they cannot
            # see. An `await` here would not do — a task being cancelled cannot be
            # relied on to reach it — while `task.cancel()` always can, and is a
            # no-op once the cooperative stop has already finished the job.
            self._cancel_poller()
            self._app = None
            self._polling = False

    async def _stop_updater(self) -> None:
        """Ask the library to stop polling, and stop waiting for it if it will not.

        Not `wait_for` around the library's coroutine. `wait_for` cancels what it is
        waiting on and then *waits for the cancellation to finish*, so a coroutine
        that does not respond to being cancelled makes the `wait_for` uncancellable
        too — and this runs on the path between somebody pressing Save and the agent
        answering them. `asyncio.wait` on a task of its own gives up on schedule and
        leaves that task to be dealt with by `_cancel_poller`, which is the thing that
        can actually stop a poller.
        """
        stopping = asyncio.ensure_future(self._app.updater.stop())
        # Nothing is ever going to await `stopping` again, and a task whose exception
        # is never retrieved is logged as a warning nobody can act on. Retrieving it
        # here keeps the failure where it belongs — in the log line below — instead of
        # in an orphan.
        stopping.add_done_callback(_swallow)
        done, _pending = await asyncio.wait({stopping}, timeout=STOP_TIMEOUT_S)
        if not done:
            logger.warning("comms.telegram.stop_timed_out", timeout=STOP_TIMEOUT_S)

    def _cancel_poller(self) -> None:
        """Cancel a poller that would not stop, rather than leaving it running.

        A poller that survives its own door is the worst outcome here, and worse than
        the hang that gets us here: it keeps a `getUpdates` long-poll open on a token
        the settings screen says is closed, so the next person to open that door gets
        a conflict from a poller they cannot see. The cooperative stop sets an event
        the poller checks between reads, so a poller still alive after the cap is one
        that is not going to notice.

        Cancelled by task name rather than by reaching into the library's private
        attributes: the name is set by the library deliberately, it is the only handle
        a caller has on this task, and a `getattr` for a name-mangled field is a
        silent `AttributeError` the day the library changes it.
        """
        cancelled = 0
        for task in asyncio.all_tasks():
            if task is asyncio.current_task() or task.done():
                continue
            if (task.get_name() or "").startswith("Updater:"):
                task.cancel()
                cancelled += 1
        if cancelled:
            logger.warning("comms.telegram.poller_cancelled", tasks=cancelled)

    def accepts(self, chat: Any) -> bool:
        """Whether this chat is one the agent will answer.

        Two gates, and they answer different questions. `accept_from_anyone` is
        the first: it is an explicit choice by whoever installed the agent that
        anybody who finds this bot may write to it, and it exists because the
        default -- an empty allowlist admitting nobody -- makes the channel
        useless for an agent whose whole purpose is to talk to the public. It is
        not implied by an empty list, because "nobody is configured yet" and
        "everybody is welcome" are not the same statement and reading one as the
        other is how an agent holding a shell ends up answering a stranger.

        `allow_groups` is the second and is independent of it: in a group every
        member's words arrive as one sender to the agent and one reply goes back
        to all of them, which is not a conversation -- it is a broadcast with a
        reply-to in it. So a group stays refused under `accept_from_anyone`
        until it is separately asked for.
        """
        if chat is None:
            return False
        if chat.type in ("group", "supergroup", "channel"):
            return self.allow_groups
        return self.accept_from_anyone or chat.id in self.allowed_chat_ids

    async def _refuse(self, tg_message: Any, chat_id: int) -> None:
        """
        Says no, rather than saying nothing.

        Silence from a bot a person just added is indistinguishable from a broken
        one, so the refusal names itself and gives the id to allow. It is the
        whole of what this channel tells an unlisted sender.
        """
        logger.info("comms.telegram.sender_not_allowed", chat_id=chat_id)
        try:
            await tg_message.reply_text(
                f"chat {chat_id} is not on this agent's list; add it to "
                "channels.telegram.allowed_chat_ids to be answered",
            )
        except Exception:
            logger.exception("comms.telegram.refusal_failed", chat_id=chat_id)

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        if self._app is None or not person_id or not person_id.startswith("tg:"):
            return {"status": "failed", "error": "telegram adapter not started or bad person_id"}
        chat_id = int(person_id.split(":", 1)[1])
        try:
            sent = await self._app.bot.send_message(chat_id=chat_id, text=text)
            return {"status": "sent", "chat_id": chat_id,
                    "provider_message_id": getattr(sent, "message_id", None)}
        except Exception as exc:
            logger.exception("comms.telegram.send_failed")
            return {"status": "failed", "error": str(exc)}


def telegram_token_from_env(env_name: str) -> str | None:
    import os

    return os.environ.get(env_name)


def _swallow(task: asyncio.Future[Any]) -> None:
    """Take an abandoned task's exception, so it is not reported as an orphan."""
    if not task.cancelled():
        task.exception()
