"""Subscribe to trunk-recorder's MQTT topics and translate them into
WebSocket events for the UI plus DB updates for the recordings library.

Topics (per the trunk-recorder MQTT plugin README):
    {base}/calls_active        — list of currently active call objects
    {base}/call_start          — single call begun
    {base}/call_end            — single call finished
    {base}/recorders           — recorder array
    {base}/rates               — control-channel decode rates
    {base}/systems  (retained) — configured systems
    {base}/config   (retained) — current trunk-recorder config
    {base}/trunk_recorder/status (retained)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import paho.mqtt.client as mqtt
from sqlmodel import select

from .db import session
from .models import Call, utcnow
from .settings import get_settings
from .ws_hub import hub

logger = logging.getLogger("police_scanner.mqtt")


class MQTTBridge:
    def __init__(self) -> None:
        self._client: mqtt.Client | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        settings = get_settings()
        client = mqtt.Client(client_id=f"police-scanner-{id(self):x}", clean_session=True)
        if settings.mqtt_username:
            client.username_pw_set(
                settings.mqtt_username,
                settings.mqtt_password.get_secret_value() if settings.mqtt_password else None,
            )
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        try:
            client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        except Exception as exc:
            logger.warning("mqtt initial connect failed: %s", exc)
        client.loop_start()
        self._client = client

    async def stop(self) -> None:
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    # ── MQTT callbacks (run in paho's thread; bounce back to asyncio loop) ─

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc: int) -> None:
        if rc != 0:
            logger.error("mqtt connect rc=%s", rc)
            return
        base = get_settings().mqtt_topic_base
        for sub in (
            f"{base}/calls_active",
            f"{base}/call_start",
            f"{base}/call_end",
            f"{base}/recorders",
            f"{base}/rates",
            f"{base}/systems",
            f"{base}/config",
            f"{base}/trunk_recorder/status",
            f"{base}/trunk_recorder/console",
        ):
            client.subscribe(sub, qos=0)
        logger.info("mqtt connected, subscribed under %s/#", base)

    def _on_disconnect(self, client, userdata, rc) -> None:
        if rc != 0:
            logger.warning("mqtt disconnected unexpectedly rc=%s", rc)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        if not self._loop:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {"raw": msg.payload.decode("utf-8", errors="replace")[:200]}
        # Schedule the async handler back on the event loop.
        asyncio.run_coroutine_threadsafe(self._dispatch(msg.topic, payload), self._loop)

    async def _dispatch(self, topic: str, payload: Any) -> None:
        base = get_settings().mqtt_topic_base
        suffix = topic[len(base) + 1 :] if topic.startswith(base + "/") else topic
        try:
            if suffix == "call_start":
                await self._on_call_start(payload)
            elif suffix == "call_end":
                await self._on_call_end(payload)
            elif suffix == "calls_active":
                await hub.broadcast({"type": "calls_active", "calls": payload})
            elif suffix == "recorders":
                await hub.broadcast({"type": "recorders", "recorders": payload})
            elif suffix == "rates":
                await hub.broadcast({"type": "rates", "rates": payload})
            elif suffix in ("systems", "config", "trunk_recorder/status"):
                await hub.broadcast({"type": suffix.replace("/", "_"), "data": payload})
        except Exception as exc:
            logger.exception("dispatch error for %s: %s", topic, exc)

    async def _on_call_start(self, payload: Any) -> None:
        call = _extract_call(payload)
        if call is None:
            return
        await hub.broadcast({"type": "call_start", "call": call})

    async def _on_call_end(self, payload: Any) -> None:
        call = _extract_call(payload)
        if call is None:
            return
        await hub.broadcast({"type": "call_end", "call": call})
        # Persist a stub row; the audio file's uploadScript hook will fill in the rest.
        async with session() as s:
            existing = (
                await s.execute(
                    select(Call).where(
                        Call.system_short_name == call["system"],
                        Call.call_id == call["call_id"],
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.end_time = utcnow()
                existing.duration_seconds = float(call.get("duration") or 0.0)
                s.add(existing)
            else:
                s.add(
                    Call(
                        system_short_name=call["system"],
                        call_id=str(call["call_id"]),
                        talkgroup_tgid=int(call.get("tgid") or 0),
                        talkgroup_alpha=str(call.get("alpha") or ""),
                        talkgroup_description=str(call.get("description") or ""),
                        encrypted=bool(call.get("encrypted")),
                        frequency_hz=int(call.get("frequency_hz") or 0),
                        start_time=utcnow(),
                        end_time=utcnow(),
                        duration_seconds=float(call.get("duration") or 0.0),
                    )
                )


def _extract_call(payload: Any) -> dict | None:
    """Normalize the various trunk-recorder MQTT call payload shapes."""
    if not isinstance(payload, dict):
        return None
    p = payload.get("call") if "call" in payload else payload
    if not isinstance(p, dict):
        return None
    sys_short = p.get("short_name") or p.get("sys_name") or p.get("shortName") or ""
    call_id = p.get("id") or p.get("call_num") or p.get("callNum") or ""
    if not sys_short or not call_id:
        return None
    return {
        "system": str(sys_short),
        "call_id": str(call_id),
        "tgid": p.get("talkgroup") or p.get("talkgroup_tag") or p.get("tg_num"),
        "alpha": p.get("talkgroup_alpha_tag") or p.get("alphaTag") or "",
        "description": p.get("talkgroup_description") or p.get("description") or "",
        "encrypted": p.get("encrypted") or False,
        "frequency_hz": int(float(p.get("freq") or 0) * (1 if (p.get("freq") or 0) > 1e6 else 1e6)),
        "duration": p.get("length") or p.get("duration") or 0,
        "src_list": p.get("srcList") or p.get("source_list") or [],
        "ts": p.get("start_time") or p.get("startTime"),
    }


bridge = MQTTBridge()
