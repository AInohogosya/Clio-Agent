from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.platform import SUPPORTS_UNIX_SOCKETS
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.cli")


class CliAdapter(ChannelAdapter):
    """Local CLI over a unix socket: JSON-lines in, JSON-lines out."""

    name = "cli"

    def __init__(self, socket_path: str, on_inbound: Any):
        self.socket_path = os.path.expanduser(socket_path)
        self.on_inbound = on_inbound
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        """Bind the socket, where there is a socket to bind.

        **Windows cannot serve this channel, and is told so rather than served
        nothing.** `asyncio/__init__.py` branches on `sys.platform == 'win32'` and
        imports `windows_events`, which does not export `start_unix_server` at all
        — so the attribute is simply absent there, whatever Winsock happens to
        support. Calling it raised `AttributeError`; `CommsHost.start()` catches
        `Exception` per adapter, so that exception cost the *other* channels
        nothing, and cost this one everything while saying only
        `comms.adapter_start_failed`.

        A loopback TCP listener was considered and not taken. This channel is
        unauthenticated by construction — it trusts whoever can open the file — and
        the file is protected by the mode bits on its directory, where a port is
        reachable by every account on the machine. Trading a filesystem-permission
        boundary for a network one, silently, on the platform where a human is
        least likely to notice, is not a degradation worth making. The window in
        `comms.web` is the surface that *is* meant to be a port, and it binds on
        Windows today.
        """
        if not SUPPORTS_UNIX_SOCKETS:
            logger.warning(
                "comms.cli.unsupported",
                reason=(
                    "the cli channel is a unix socket, and asyncio on Windows has no "
                    "start_unix_server to serve one with"
                ),
                hint="use the window channel (comms.web), which binds a loopback port",
            )
            return
        # `os.path.dirname` of a bare filename is `""`, and `makedirs("")` raises.
        # The configured default is `~/.ethos/run/cli.sock` so it has a directory,
        # but a deployment that wrote `cli.sock` into a config file did not, and the
        # failure arrived as a bare `FileNotFoundError` with no channel named in it.
        parent = os.path.dirname(self.socket_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)
        logger.info("comms.cli.listening", socket=self.socket_path)

    async def listen(self) -> None:
        """Nothing separate to open: binding the socket is `start`'s work.

        The two halves are the same act for this channel, because the clients a
        socket holds are also how a reply reaches them — there is no other path
        out. So the listener cannot be split from the sender, and the race for the
        socket between two processes costs a log line rather than the channel: one
        process binds it, and a surface connected to it is answered from there.
        The channels that cannot be shared this way are the ones with a real
        `listen`.
        """

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._clients):
            writer.close()
        # Removed only when it was created. On a host that skipped `start` there
        # is no socket, and a shutdown that raises on its way out is a shutdown
        # that does not finish the rest of its work.
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                    text = str(payload.get("text", "")).strip()
                    person_id = str(payload.get("person_id", "cli-user"))
                except (ValueError, UnicodeDecodeError):
                    text = line.decode("utf-8", errors="replace").strip()
                    person_id = "cli-user"
                if not text:
                    continue
                message = Message(channel=self.name, person_id=person_id, text=text,
                                  conversation_key=f"cli:{person_id}")
                try:
                    await self.on_inbound(message)
                except Exception:
                    logger.exception("comms.cli.inbound_failed")
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        payload = json.dumps({"direction": "outbound", "person_id": person_id,
                              "text": text, "urgency": urgency})
        for writer in list(self._clients):
            try:
                writer.write((payload + "\n").encode("utf-8"))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                self._clients.discard(writer)
        return {"status": "sent", "clients": len(self._clients)}
