from __future__ import annotations

import asyncio
import errno
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.comms.adapters.cli import CliAdapter
from ethos.comms.adapters.discord import DiscordAdapter
from ethos.comms.adapters.email import EmailAdapter
from ethos.comms.adapters.slack import SlackAdapter, slack_signature
from ethos.comms.adapters.telegram import TelegramAdapter
from ethos.comms.adapters.web import WebAdapter
from ethos.comms.adapters.whatsapp import WhatsappAdapter
from ethos.comms.webhook import WebhookReceiver
from ethos.config import ChannelsConfig, EthosConfig, channels_path, config_dir, load_config
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.host")

InboundHandler = Callable[[Message], Awaitable[None]]

# How often a running process re-reads the channel configuration.
#
# Short enough that a person who has just pressed Save has not yet reached for the
# terminal to restart anything, and long enough that re-reading a YAML file twice a
# second is not a cost worth optimising. The file is written by a rename, so this
# never reads a half-written one.
WATCH_INTERVAL_S = 2.0

# How many polls may pass on unchanged file stamps before the configuration is read
# anyway. One stamp is a cached inode's modification time and length, and there is no
# reason to trust it with the last word about a file it did not read; this bounds how
# long a door could be shut on a stamp that was wrong. Sixteen seconds at the default
# interval, for one parse — cheap enough to be worth the certainty.
RESCAN_EVERY_POLLS = 8


def _channel_files_stamp(config: EthosConfig) -> tuple:
    """Cheap "could either of these have changed?" for the two files that hold doors.

    Identity as well as time and length, because both files are written by rename:
    the committed one when somebody edits it, the agent's own when a settings screen
    or a terminal command saves a door. A rename makes a new inode, so a stamp that
    missed a save would still notice the file is not the one it last looked at.

    A file that is not there is part of the answer rather than an absence from it —
    "no overlay yet" and "the overlay was deleted" are different states and both are
    represented, because the second one has to close the doors the first one opened.
    """
    stamp: list[tuple] = []
    for path in (config_dir() / "channels.yaml", channels_path(config.paths.home)):
        try:
            info = path.stat()
            stamp.append((info.st_mtime_ns, info.st_size, info.st_ino))
        except OSError:
            stamp.append((0, 0, 0))
    return tuple(stamp)


@dataclass(frozen=True)
class _Route:
    """One path on the shared receiver, as data rather than as a call.

    `signature` is what decides whether the path has to be registered again, and
    `kwargs` is what registers it. They are separate because the handler is a
    closure over the adapter it belongs to, so it cannot go into a comparison, while
    everything that would make a re-registration a different route can.
    """

    signature: tuple
    kwargs: dict[str, Any] = field(compare=False, repr=False)


@dataclass
class _Plan:
    """What a configuration asks for, worked out without touching anything.

    Planning and applying are separate because applying is not reversible in one
    step: opening a door can mean a Telegram long-poll, an IMAP loop, or a bind on a
    loopback port, and a door that has been opened has to be closed again before the
    changed version of it can be opened. So the plan is built first, in full, and
    only then compared against what is running.
    """

    adapters: dict[str, ChannelAdapter] = field(default_factory=dict)
    signatures: dict[str, tuple] = field(default_factory=dict)
    routes: list[_Route] = field(default_factory=list)
    wants_receiver: bool = False
    receiver: tuple[str, int, int] | None = None

    def fingerprint(self) -> str:
        """A value that changes exactly when this plan would build something else.

        Over the signatures only, never over the adapters: an adapter's `repr` is its
        address, so hashing the objects themselves would report a change on every
        single poll and rebuild the doors continuously.
        """
        material = repr((sorted(self.signatures.items()), self.wants_receiver, self.receiver,
                         sorted(route.signature for route in self.routes)))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CommsHost:
    """Channel adapter manager. Inbound flows onto the bus; outbound flows
    through the adapters. The core has no request/response coupling [Section 12].

    A running host follows the channel configuration rather than being a snapshot of
    it. Both files that can change it — the committed one and the agent's own — are
    read again every couple of seconds and the doors are reconciled against what is
    actually running, so a person who opens a door or pastes a token watches it take
    effect instead of being told to restart an agent that is doing something else at
    the time. The two rules that make a door safe to re-open are kept here rather
    than left to the adapters: only the process that was told to listen ever listens,
    and a door that is already open is always closed before it is opened again.
    """

    def __init__(
        self,
        config: EthosConfig,
        on_inbound: InboundHandler,
    ):
        self.config = config
        # The channel half of the configuration, held on its own because it is the
        # only half that is re-read. Everything else in `config` — the model, the
        # database, the paths — belongs to a process that outlives a settings save,
        # and replacing it wholesale would throw away state that save has no opinion
        # about.
        self.channels: ChannelsConfig = config.channels
        self.on_inbound = on_inbound
        self.adapters: dict[str, ChannelAdapter] = {}
        # What each running adapter was built from, and what the current plan is
        # worth, so a poll that finds nothing different costs two comparisons and a
        # poll that finds something different knows exactly which door it is about.
        self._signatures: dict[str, tuple] = {}
        self._fingerprint: str | None = None
        # Whether this process holds the doors open. Set by `start` and never
        # inferred, because it is the one fact the whole start/listen split exists
        # to get right and a reload must not quietly change the answer.
        self._listen = True
        self._started = False
        # Present only when a push channel asked for it. One listener for all of
        # them, because a tunnel exposes one URL and one port is one thing to
        # forward.
        self.receiver: WebhookReceiver | None = None
        self._receiver_settings: tuple[str, int, int] | None = None

    def build_adapters(self) -> None:
        """Which adapters this configuration calls for, installed but not opened.

        The "what would be built" question on its own, and kept as a thing that can
        be asked because it is a real one: a channel that is on in the file and
        absent from the process is a channel an operator has every reason to believe
        is working, and the way to tell the two apart is to build the set and look at
        it rather than to start a poller and watch nothing arrive.

        `start` does this and then opens the doors. A reload does not call it — it
        reconciles, so a door that has not changed is left alone.
        """
        plan = self.plan(self.channels)
        self.adapters = dict(plan.adapters)
        self._signatures = dict(plan.signatures)
        self._install_receiver(plan)

    def _install_receiver(self, plan: _Plan) -> None:
        """Make the shared listener exist, if the plan wants one, and route to it.

        Separate from starting it because a receiver is a real object with a real
        port and creating one is not the same act as binding it: the process that
        only sends registers routes on a receiver it never opens, and must be able
        to do that without taking a port away from the process that holds it.
        """
        if plan.receiver is None:
            return
        if self.receiver is not None and self._receiver_settings != plan.receiver:
            # The address moved. The async path closes the old listener first, because
            # a bound port cannot be rebound; reaching here directly means a plan is
            # being inspected rather than applied, so there is nothing listening yet.
            self.receiver = None
        if self.receiver is None:
            host, port, max_body = plan.receiver
            self.receiver = WebhookReceiver(host, port, self._handle_inbound, max_body)
            self._receiver_settings = plan.receiver
        for route in plan.routes:
            self.receiver.route(**route.kwargs)

    def plan(self, channels: ChannelsConfig) -> _Plan:
        """What this configuration asks for, as adapters and nothing else.

        No mutation: the plan is built for a configuration that may turn out to be
        worse than the running one, and a door that is built before it is known to be
        wanted has to be closed again — which for a Telegram bot means a poller that
        briefly conflicts with its own replacement. Deciding first is what makes the
        swap below a single clean step.

        Every door that is switched off is absent from the plan, and that is the only
        reason a door can be closed: there is nothing to unwind for a channel
        somebody turned off, because turning it off removed the thing that was
        listening.
        """
        # Resolved once, here, for every adapter below. `ChannelsConfig.credentials`
        # is where the order between an environment variable and a value in the
        # agent's own home is decided, and an adapter that reached into
        # `os.environ` itself would be a second copy of that decision — and the
        # one place the two halves of a channel are allowed to disagree is the
        # place that decides which half answers.
        #
        # A resolved credential is part of a door's signature, so pasting a corrected
        # token rebuilds that door rather than leaving the old one holding a token
        # that was revoked an hour ago.
        creds = channels.credentials()
        plan = _Plan()

        def add(name: str, adapter: ChannelAdapter, signature: tuple) -> None:
            plan.adapters[name] = adapter
            plan.signatures[name] = signature

        if channels.cli.enabled:
            add("cli", CliAdapter(channels.cli.socket, self._handle_inbound),
                ("cli", channels.cli.socket))
        if channels.web.enabled:
            add("web", WebAdapter(channels.web.ws_port, creds.get("web.auth_token"), self._handle_inbound),
                ("web", channels.web.ws_port, creds.get("web.auth_token", "")))

        if channels.telegram.enabled:
            token = creds.get("telegram.token")
            if token:
                telegram = channels.telegram
                add("telegram", TelegramAdapter(
                    token,
                    allowed_chat_ids=telegram.allowed_chat_ids,
                    allow_groups=telegram.allow_groups,
                    accept_from_anyone=telegram.accept_from_anyone,
                    on_inbound=self._handle_inbound,
                ), ("telegram", token, tuple(telegram.allowed_chat_ids), telegram.allow_groups,
                    telegram.accept_from_anyone))
                if not telegram.allowed_chat_ids and not telegram.accept_from_anyone:
                    # Refusing everybody is correct but silently looks broken, so
                    # it is said out loud at the level that gets read in a log.
                    logger.warning(
                        "comms.telegram_no_allowlist",
                        hint="message the bot, send /id, add the id to "
                             "channels.telegram.allowed_chat_ids",
                    )
            else:
                logger.warning(
                    "comms.telegram_disabled_no_token", missing=channels.missing_credentials("telegram"),
                )

        if channels.whatsapp.enabled:
            self._plan_whatsapp(plan, channels, creds, add)
        if channels.slack.enabled:
            self._plan_slack(plan, channels, creds, add)
        if channels.discord.enabled:
            self._plan_discord(plan, channels, creds, add)

        if channels.email.enabled:
            address = creds.get("email.address")
            password = creds.get("email.password")
            imap_host = creds.get("email.imap_host")
            smtp_host = creds.get("email.smtp_host")
            if address and password and imap_host and smtp_host:
                email = channels.email
                add("email", EmailAdapter(
                    address, password, imap_host, smtp_host,
                    email.poll_interval_s, self._handle_inbound,
                ), ("email", address, imap_host, smtp_host, email.poll_interval_s))
            else:
                logger.warning(
                    "comms.email_disabled_missing_env", missing=channels.missing_credentials("email"),
                )

        # The listener, but only if a push door actually needs one. Keyed on that
        # rather than on the webhook being switched on, because the old behaviour was
        # a receiver made by the first channel that asked for it: a deployment with
        # `webhook.enabled` and no WhatsApp and no Slack has nothing to deliver, and
        # a listener bound for it is a port taken for nothing.
        plan.receiver = self._plan_receiver(channels) if plan.wants_receiver else None
        return plan

    def _plan_whatsapp(self, plan: _Plan, channels: ChannelsConfig,
                       creds: dict[str, str], add: Any) -> None:
        """
        WhatsApp, which needs two halves rather than one adapter.

        The Cloud API has no polling, so inbound exists only where Meta can
        POST — a receiver on a loopback port that a tunnel forwards to. The
        adapter owns parsing and sending; the receiver owns the door. Both are
        skipped, loudly, if any one of the four credentials is absent: a webhook
        configured with a token but no secret would answer 401 to every delivery
        and look exactly like a channel that is not being spoken to.

        The receiver is asked for rather than created, and the route is returned as
        data, so that a plan for a configuration that is not going to be applied never
        binds a port.
        """
        cfg = channels.whatsapp
        missing = channels.missing_credentials("whatsapp")
        if missing:
            logger.warning("comms.whatsapp_disabled_missing_env", missing=missing)
            return
        if not self._receiver_wanted(channels):
            logger.warning(
                "comms.whatsapp_disabled_no_webhook",
                hint="set channels.webhook.enabled so Meta has somewhere to POST",
            )
            return
        adapter = WhatsappAdapter(
            phone_number_id=creds["whatsapp.phone_number_id"],
            access_token=creds["whatsapp.access_token"],
            allowed_phone_numbers=cfg.allowed_phone_numbers,
            graph_version=cfg.graph_version,
            accept_from_anyone=cfg.accept_from_anyone,
        )
        add("whatsapp", adapter,
            ("whatsapp", creds["whatsapp.phone_number_id"], creds["whatsapp.access_token"],
             creds["whatsapp.app_secret"], creds["whatsapp.verify_token"], cfg.path,
             cfg.graph_version, tuple(cfg.allowed_phone_numbers), cfg.accept_from_anyone))
        plan.wants_receiver = True
        plan.routes.append(_Route(
            signature=(cfg.path, creds["whatsapp.app_secret"], creds["whatsapp.verify_token"]),
            kwargs={
                "path": cfg.path,
                "secret": creds["whatsapp.app_secret"],
                "verify_token": creds["whatsapp.verify_token"],
                "handler": lambda payload: adapter.handle_webhook(payload, self._handle_inbound),
            },
        ))
        if not cfg.allowed_phone_numbers and not cfg.accept_from_anyone:
            logger.warning(
                "comms.whatsapp_no_allowlist",
                hint="message the business number, read contacts[0].wa_id, add it to "
                     "channels.whatsapp.allowed_phone_numbers",
            )

    def _plan_slack(self, plan: _Plan, channels: ChannelsConfig,
                    creds: dict[str, str], add: Any) -> None:
        """
        Slack, which is a signed webhook on a path rather than a socket.

        The same shape as WhatsApp and the same receiver: a tunnel exposes one
        URL, so a second channel that pushes to one must land on the same listener
        and be told apart by path. Which is also why there is one receiver rather
        than making a second one — two listeners on one port is not two doors, it is
        one door and a failure.

        Skipped, loudly, on a missing credential, for the same reason WhatsApp is:
        a route with a token and no signing secret answers 401 to every delivery,
        and that looks exactly like a channel nobody is speaking to.
        """
        cfg = channels.slack
        missing = channels.missing_credentials("slack")
        if missing:
            logger.warning("comms.slack_disabled_missing_env", missing=missing)
            return
        if not self._receiver_wanted(channels):
            logger.warning(
                "comms.slack_disabled_no_webhook",
                hint="set channels.webhook.enabled so Slack has somewhere to POST",
            )
            return
        adapter = SlackAdapter(
            creds["slack.bot_token"],
            allowed_ids=cfg.allowed_ids,
            accept_from_anyone=cfg.accept_from_anyone,
        )
        add("slack", adapter,
            ("slack", creds["slack.bot_token"], creds["slack.signing_secret"], cfg.path,
             tuple(cfg.allowed_ids), cfg.accept_from_anyone))
        plan.wants_receiver = True
        plan.routes.append(_Route(
            signature=(cfg.path, creds["slack.signing_secret"]),
            kwargs={
                "path": cfg.path,
                "secret": creds["slack.signing_secret"],
                "authentic": slack_signature(creds["slack.signing_secret"]),
                "challenge": adapter.challenge,
                "handler": lambda payload: adapter.handle_webhook(payload, self._handle_inbound),
            },
        ))
        if not cfg.allowed_ids and not cfg.accept_from_anyone:
            logger.warning(
                "comms.slack_no_allowlist",
                hint="message the bot, read event.user or event.channel, add it to "
                     "channels.slack.allowed_ids",
            )

    def _plan_discord(self, plan: _Plan, channels: ChannelsConfig,
                      creds: dict[str, str], add: Any) -> None:
        """
        Discord, which is a socket and not a door.

        The only channel here with no callback URL, because Discord has no
        callback: `listen` opens the gateway and only one process may do that,
        for the same reason only one process may hold a Telegram poller. A core
        that answers alone is enough to reply, which is all `ethos-core` asks of it.

        A missing token is the only thing that stops this, and it stops it out
        loud — a Discord bot with no token is a channel that is off, and an
        operator who was told nothing has no way to tell that from a channel
        nobody has spoken to yet.
        """
        cfg = channels.discord
        token = creds.get("discord.token")
        if not token:
            logger.warning(
                "comms.discord_disabled_missing_env", missing=channels.missing_credentials("discord"),
            )
            return
        add("discord", DiscordAdapter(
            token,
            allowed_ids=cfg.allowed_ids,
            intents=cfg.intents,
            accept_from_anyone=cfg.accept_from_anyone,
            on_inbound=self._handle_inbound,
        ), ("discord", token, cfg.intents, tuple(cfg.allowed_ids), cfg.accept_from_anyone))
        if not cfg.allowed_ids and not cfg.accept_from_anyone:
            logger.warning(
                "comms.discord_no_allowlist",
                hint="message the bot, read author.id, add it to channels.discord.allowed_ids",
            )

    def _receiver_wanted(self, channels: ChannelsConfig) -> bool:
        """Whether a push door has a listener to arrive on.

        The one loopback listener, wanted rather than made, because a tunnel exposes
        one URL and one port is one thing to forward: WhatsApp and Slack are told
        apart by path rather than by which listener they arrived on. A second
        `WebhookReceiver` on the same port would race the first for it and the loser
        would be logged as a webhook that failed to start.

        False when the webhook listener is switched off, which is the one thing a
        push channel cannot work without — there is nowhere for a POST to land.
        """
        return channels.webhook.enabled

    def _plan_receiver(self, channels: ChannelsConfig) -> tuple[str, int, int] | None:
        """The address the shared listener binds, or `None` when nothing needs one.

        `None` when the webhook listener is switched off, which is the one thing a
        push channel cannot work without — there is nowhere for a POST to land.
        """
        if not channels.webhook.enabled:
            return None
        webhook = channels.webhook
        return (webhook.host, webhook.port, webhook.max_body_bytes)

    async def _handle_inbound(self, message: Message) -> None:
        try:
            await self.on_inbound(message)
        except Exception:
            logger.exception("comms.inbound_handler_failed")

    async def send(
        self,
        *,
        person_id: str | None,
        channel: str,
        text: str,
        urgency: str = "normal",
        conversation_key: str | None = None,
        in_reply_to: Any = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(channel)
        if adapter is None:
            logger.warning("comms.no_adapter_for_channel", channel=channel)
            return {"status": "failed", "error": f"no adapter for channel '{channel}'"}
        return await adapter.send(
            person_id=person_id, text=text, urgency=urgency,
            conversation_key=conversation_key, in_reply_to=in_reply_to,
        )

    async def start(self, *, listen: bool = True) -> None:
        """
        Build every open channel, and open the doors that can only be open once.

        `listen=False` is what `ethos-core` uses. The core builds a `CommsHost` for
        one reason: `OutboundPipeline` needs something to send through, and the
        agent has to be able to answer on the door a question arrived on. So it
        starts every adapter — a Telegram bot client, an SMTP address, an HTTP
        client — and opens none of the doors, because `ethos-comms` is already
        holding them.

        That is the arrangement the channel feature needs and could not have
        before. Running both processes with every adapter polling put two
        `getUpdates` long-polls on one bot, which Telegram answers with a conflict
        for as long as the other is alive; two IMAP loops on one mailbox, where the
        second sees nothing and neither says why; and two webhook receivers racing
        for one loopback port, with the loser logged as an adapter that failed to
        start. Which is not a subtle degradation: it is the channel silently not
        receiving.

        The same split holds on every later reload, which is why `_listen` is a field
        rather than an argument to `reconcile`: a door opened by a save must open in
        the process that was already holding doors, and in no other.
        """
        self._listen = listen
        plan = self.plan(self.channels)
        self.adapters = dict(plan.adapters)
        self._signatures = dict(plan.signatures)
        self._fingerprint = plan.fingerprint()
        self._started = True
        for name in list(self.adapters):
            await self._start_one(name)
        if listen:
            for name in list(self.adapters):
                await self._listen_one(name)
        await self._apply_receiver(plan)

    async def reconcile(self, channels: ChannelsConfig) -> bool:
        """Move the running doors onto a new configuration. True if anything moved.

        The order is the whole of it: **every door that has to change is closed
        before any door is opened.** Telegram answers a second `getUpdates` on one
        token with a conflict for as long as the first is alive, and an IMAP loop
        and a bind on the shared port are the same argument in a different key. So
        this closes first and opens second rather than opening a changed door beside
        its own replacement and hoping the old one notices.

        A door that has not changed is not touched at all. Restarting a Telegram
        poller that is already answering would drop the reply to a message somebody
        sent a second ago, and the person watching would have no way to tell that
        from the channel being broken.
        """
        if not self._started:
            return False
        plan = self.plan(channels)
        fingerprint = plan.fingerprint()
        if fingerprint == self._fingerprint:
            return False

        closing: list[str] = []
        for name in list(self.adapters):
            if name in plan.adapters and self._signatures.get(name) == plan.signatures.get(name):
                continue
            await self._close_one(name)
            closing.append(name)

        opening: list[str] = []
        for name, adapter in plan.adapters.items():
            if name in self.adapters:
                continue
            self.adapters[name] = adapter
            self._signatures[name] = plan.signatures[name]
            await self._start_one(name)
            opening.append(name)

        if self._listen:
            for name in opening:
                await self._listen_one(name)
        await self._apply_receiver(plan)

        # Only now, once the doors are the ones that were asked for. Until this line
        # a failed reload is retried on the next poll rather than being mistaken for
        # a configuration that has already been applied.
        self.channels = channels
        self._fingerprint = fingerprint
        logger.info(
            "comms.channels_reloaded",
            opened=sorted(opening), closed=sorted(closing),
            listening=self._listen, receiver=bool(plan.wants_receiver),
        )
        return True

    async def _start_one(self, name: str) -> None:
        """Start one adapter, and never let its failure cost another door its start.

        Telegram refusing to send because its token was revoked is not a reason for
        WhatsApp to stop listening, so every exception ends here rather than
        unwinding the loop around it.
        """
        adapter = self.adapters.get(name)
        if adapter is None:
            return
        try:
            await adapter.start()
            logger.info("comms.adapter_started", adapter=name)
        except OSError as exc:
            # The one failure the start/listen split is *for*. `_listen=False` says
            # this process sends and does not hold the doors, and `ethos-comms` is
            # already holding them — so a port that is taken is the design working,
            # not a fault. It was logged as an error with a traceback on every start,
            # which is how a wall of red in the log teaches somebody to stop reading
            # the log, and the log is where the start-up failures worth reading are.
            if not self._listen and exc.errno == errno.EADDRINUSE:
                logger.info("comms.adapter_door_held_elsewhere", adapter=name)
            else:
                logger.exception("comms.adapter_start_failed", adapter=name)
        except Exception:
            logger.exception("comms.adapter_start_failed", adapter=name)

    async def _listen_one(self, name: str) -> None:
        adapter = self.adapters.get(name)
        if adapter is None:
            return
        try:
            await adapter.listen()
        except Exception:
            logger.exception("comms.adapter_listen_failed", adapter=name)

    async def _close_one(self, name: str) -> None:
        """Close one door, and forget it either way.

        Forgetting a door whose close failed is deliberate: a poller that could not be
        stopped is not one this process may keep a handle on, and leaving it in the
        dict would mean a later reload believed it was still there and left it alone
        forever.
        """
        adapter = self.adapters.pop(name, None)
        self._signatures.pop(name, None)
        if adapter is None:
            return
        try:
            await adapter.stop()
            logger.info("comms.adapter_stopped", adapter=name)
        except Exception:
            logger.exception("comms.adapter_stop_failed", adapter=name)

    async def _apply_receiver(self, plan: _Plan) -> None:
        """Bring the shared listener in line with the plan.

        Recreated when the address moved, because a listener that has already bound a
        port cannot bind another one, and a tunnel that was forwarded to the old port
        is the operator's problem to notice rather than something to do quietly here.
        Routes are re-registered on every apply rather than only when the signature
        changes, because a route is a dictionary entry and a stale one is a delivery
        going to a closed adapter's handler.
        """
        if plan.receiver is None:
            if self.receiver is not None:
                await self._close_receiver()
            return
        if self.receiver is not None and self._receiver_settings != plan.receiver:
            await self._close_receiver()
        self._install_receiver(plan)
        if self._listen and self.receiver is not None and not self.receiver.started:
            try:
                await self.receiver.start()
            except Exception:
                logger.exception("comms.webhook_start_failed")

    async def _close_receiver(self) -> None:
        if self.receiver is None:
            return
        try:
            await self.receiver.stop()
        except Exception:
            logger.exception("comms.webhook_stop_failed")
        self.receiver = None
        self._receiver_settings = None

    async def watch_channels(self, *, interval: float = WATCH_INTERVAL_S) -> None:
        """Follow the channel configuration until cancelled.

        This is the loop that makes a settings save enough on its own. It watches the
        two files that can hold a door rather than waiting to be told about a write,
        because the person saving is not the only writer: `ethos channels set` writes
        the same file from a terminal, and both the browser and the CLI go through the
        bridge. A watcher that had to be summoned would work for the browser and
        quietly not for the terminal.

        It looks before it reads. Re-reading the configuration is not free — it parses
        every file in it and rebuilds every model — and this loop runs for as long as
        the agent does, so the common poll has to be cheap or it is a tax on a process
        that is otherwise idle. Two `stat` calls answer "could anything have changed?"
        for about a hundredth of that.

        Which is not quite the same question as "has anything changed?", so the full
        read still happens periodically whether the stamps agree or not. A stamp is a
        cached inode's modification time and length, and there is no reason to trust it
        with the last word about a file it did not read; this bounds how long a door
        could be shut on a stamp that was wrong, and costs one parse a minute.
        """
        stamp = _channel_files_stamp(self.config)
        # Started one short of the backstop so the very first poll always reads. The
        # stamp above is taken before this task has run even once, which is exactly
        # the window a save can slip through: a door opened or closed in the first
        # moments of the agent's life would be stamped as already-known and then
        # waited on until the periodic read, which is a door that is shut for a
        # quarter of a minute after somebody asked for it to be open.
        polls = RESCAN_EVERY_POLLS - 1
        while True:
            await asyncio.sleep(interval)
            polls += 1
            current = _channel_files_stamp(self.config)
            if current == stamp and polls < RESCAN_EVERY_POLLS:
                continue
            stamp, polls = current, 0
            try:
                fresh = load_config().channels
            except Exception:
                logger.warning("comms.channels_reload_unreadable")
                continue
            if self._fingerprint is None or fresh is None:
                continue
            try:
                await self.reconcile(fresh)
            except Exception:
                # Said rather than swallowed. A reload that quietly fails leaves the
                # previous answer standing and the next poll trying again, which is
                # the right recovery — but a person watching a settings screen has no
                # way to tell that from a save that worked, and a door that never
                # opens is the failure this whole mechanism exists to prevent. The
                # log is the only place the difference is visible.
                logger.exception("comms.channels_reload_failed")

    async def stop(self) -> None:
        self._started = False
        await self._close_receiver()
        for name in list(self.adapters):
            await self._close_one(name)
        self.adapters.clear()
        self._signatures.clear()
        self._fingerprint = None


def inbound_to_bus(bus: Any, social: Any) -> InboundHandler:
    """The canonical inbound pipeline: record, then publish as a percept event."""

    async def handler(message: Message) -> None:
        await social.record_inbound(message)
        await bus.publish(
            EventKind.INBOUND_MSG,
            {
                "message_id": str(message.id),
                "channel": message.channel,
                "person_id": message.person_id,
                "text": message.text[:4000],
                "conversation_key": message.conversation_key,
                "is_untrusted": message.person_id is None,
                "urgency": 0.3 if message.person_id else 0.2,
                # The sender's own name, so a percept that says who is speaking
                # says it the way the channel spells it rather than as the digits
                # of a chat id. Carried because `percept_from_event` has one field
                # for a sender and this is what goes in it — the summary line below
                # is derived from the same string, so attention and the log cannot
                # disagree about who wrote.
                "sender_name": message.sender_name,
                "sender_username": message.sender_username,
                "summary": f"Message from {message.sender_who()}: {message.text[:200]}",
                "sender_person": message.person_id,
                "sender_class": "known" if message.person_id else "unknown",
            },
            trust="known" if message.person_id else "untrusted",
        )

    return handler
