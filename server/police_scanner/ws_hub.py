"""WebSocket fanout hub.

A single asyncio.Queue per connected client. Producers (MQTT bridge, upload
receiver, transcribe worker) call ``broadcast(msg)`` and we push to every
client; slow clients are dropped after their queue fills, never blocking
the producer.

Authorization is enforced at connect time via the same session cookie used
for HTTP routes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .auth import SESSION_COOKIE, session_user

logger = logging.getLogger("police_scanner.ws")
QUEUE_SIZE = 64  # per-client


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, asyncio.Queue[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> bool:
        token = ws.cookies.get(SESSION_COOKIE)
        user = await session_user(token)
        if user is None:
            await ws.close(code=4401)
            return False
        await ws.accept()
        async with self._lock:
            self._clients[ws] = asyncio.Queue(maxsize=QUEUE_SIZE)
        return True

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)
        try:
            await ws.close()
        except Exception:  # noqa: S110 — already disconnected; nothing actionable to log
            pass

    async def serve(self, ws: WebSocket) -> None:
        if not await self.connect(ws):
            return
        queue = self._clients[ws]
        try:
            while True:
                # Concurrently wait for either a server-side message to send,
                # or an incoming message (which we mostly ignore — UI is read-only).
                send_task = asyncio.create_task(queue.get())
                recv_task = asyncio.create_task(ws.receive_text())
                done, pending = await asyncio.wait(
                    {send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for p in pending:
                    p.cancel()
                if recv_task in done:
                    try:
                        recv_task.result()
                    except WebSocketDisconnect:
                        return
                if send_task in done:
                    msg = send_task.result()
                    await ws.send_text(msg)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("ws connection ended with %r", exc)
        finally:
            await self.disconnect(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message, separators=(",", ":"), default=str)
        async with self._lock:
            stale: list[WebSocket] = []
            for ws, q in self._clients.items():
                try:
                    q.put_nowait(text)
                except asyncio.QueueFull:
                    logger.info("dropping slow ws client")
                    stale.append(ws)
            for ws in stale:
                self._clients.pop(ws, None)
                try:
                    await ws.close(code=1008)
                except Exception:  # noqa: S110 — slow client may already be gone
                    pass


hub = WebSocketHub()
