from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import websockets

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.web")


class WebAdapter(ChannelAdapter):
    """WebSocket bridge for the web dashboard: shared timeline both ways."""

    name = "web"

    def __init__(self, ws_port: int, auth_token: str | None, on_inbound: Any):
        self.ws_port = ws_port
        self.auth_token = auth_token
        self.on_inbound = on_inbound
        self._server: Any = None
        self._clients: set[Any] = set()

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle, "127.0.0.1", self.ws_port,
        )
        logger.info("comms.web.listening", port=self.ws_port)

    async def listen(self) -> None:
        """Nothing separate to open, for the same reason as the CLI socket.

        The connections this channel holds are how a reply reaches a surface, so
        binding is part of sending rather than a separate door. Both sockets are
        loopback, and a race for one costs a log line rather than a channel — the
        Telegram poller and the webhook port are the ones that cannot be shared.
        """

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for client in list(self._clients):
            await client.close()
        self._clients.clear()

    async def _handle(self, websocket: Any) -> None:
        try:
            hello_raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            hello = json.loads(hello_raw)
            if self.auth_token and hello.get("token") != self.auth_token:
                await websocket.close(code=4401, reason="unauthorized")
                return
            self._clients.add(websocket)
            while True:
                raw = await websocket.recv()
                payload = json.loads(raw)
                if payload.get("type") == "message":
                    message = Message(
                        channel=self.name,
                        person_id=str(payload.get("person_id", "owner")),
                        text=str(payload.get("text", "")),
                        conversation_key=str(payload.get("conversation_key", "web:owner")),
                    )
                    if message.text.strip():
                        await self.on_inbound(message)
        except (TimeoutError, json.JSONDecodeError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.exception("comms.web.handler_failed")
        finally:
            self._clients.discard(websocket)

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        payload = json.dumps({
            "type": "message", "direction": "outbound", "person_id": person_id,
            "text": text, "urgency": urgency,
        })
        dead: list[Any] = []
        for client in list(self._clients):
            try:
                await client.send(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self._clients.discard(client)
        return {"status": "sent", "clients": len(self._clients)}


def web_auth_token(env_name: str) -> str | None:
    return os.environ.get(env_name) or None
