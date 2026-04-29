"""trunk-recorder uploadScript receiver.

Loopback-only HTTP endpoint protected by a shared secret. Saves the audio
file under recordings/ and writes the call's metadata to the DB.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from sqlmodel import select

from ..db import session
from ..models import Call
from ..security import client_ip
from ..settings import get_settings
from ..ws_hub import hub

logger = logging.getLogger("police_scanner.ingest")
router = APIRouter(prefix="/api/tr", tags=["ingest"])

MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MiB hard cap per call
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def _ingest_secret() -> str:
    return get_settings().scanner_secret_key.get_secret_value()


@router.post("/upload")
async def upload(
    request: Request,
    audio: UploadFile = File(...),
    meta: str = Form(...),
    x_scanner_secret: str = Header(default=""),
):
    if not _is_loopback(client_ip(request)):
        raise HTTPException(status_code=403, detail="Forbidden (must originate from loopback)")
    if not hmac.compare_digest(x_scanner_secret, _ingest_secret()):
        raise HTTPException(status_code=403, detail="Invalid ingest secret")

    try:
        meta_obj = json.loads(meta)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid meta JSON") from exc

    settings = get_settings()
    settings.scanner_recordings_dir.mkdir(parents=True, exist_ok=True)

    short = SAFE_NAME.sub("_", str(meta_obj.get("short_name") or "unknown"))[:32]
    call_num = SAFE_NAME.sub("_", str(meta_obj.get("call_num") or meta_obj.get("id") or "unknown"))[:32]
    start_ts = meta_obj.get("start_time") or 0
    try:
        start_dt = datetime.fromtimestamp(int(start_ts), tz=UTC)
    except (TypeError, ValueError, OSError):
        start_dt = datetime.now(UTC)

    # Lay out files: recordings/<system>/<YYYY>/<MM>/<DD>/<call>.<ext>
    ext = Path(audio.filename or "").suffix.lower() or ".wav"
    if ext not in {".wav", ".m4a", ".mp3"}:
        ext = ".wav"
    rel = Path(short) / start_dt.strftime("%Y/%m/%d") / f"{start_dt.strftime('%H%M%S')}_{call_num}{ext}"
    target = settings.scanner_recordings_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)

    bytes_written = 0
    with target.open("wb") as out:
        while chunk := await audio.read(64 * 1024):
            bytes_written += len(chunk)
            if bytes_written > MAX_AUDIO_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Audio too large")
            out.write(chunk)

    talkgroup = meta_obj.get("talkgroup") or 0
    alpha = (meta_obj.get("talkgroup_tag") or meta_obj.get("talkgroup_alpha_tag") or "")[:64]
    description = (meta_obj.get("talkgroup_description") or "")[:255]
    category = (meta_obj.get("talkgroup_group") or meta_obj.get("talkgroup_category") or "")[:128]
    encrypted = bool(meta_obj.get("encrypted"))
    freq = meta_obj.get("freq")
    try:
        freq_hz = int(float(freq) * 1_000_000) if freq and float(freq) < 1e6 else int(freq or 0)
    except (TypeError, ValueError):
        freq_hz = 0
    src_list = meta_obj.get("srcList") or []
    src_csv = ",".join(str(s.get("src", s)) for s in src_list)[:512] if isinstance(src_list, list) else ""

    duration = float(meta_obj.get("call_length") or meta_obj.get("length") or 0.0)
    end_dt = datetime.fromtimestamp(start_dt.timestamp() + duration, tz=UTC)

    async with session() as s:
        existing = (
            await s.execute(
                select(Call).where(
                    Call.system_short_name == short,
                    Call.call_id == call_num,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = Call(system_short_name=short, call_id=call_num, start_time=start_dt)
        existing.talkgroup_tgid = int(talkgroup or 0)
        existing.talkgroup_alpha = alpha
        existing.talkgroup_description = description
        existing.talkgroup_category = category
        existing.encrypted = encrypted
        existing.frequency_hz = freq_hz
        existing.start_time = start_dt
        existing.end_time = end_dt
        existing.duration_seconds = duration
        existing.source_unit_ids = src_csv
        existing.audio_path = str(rel)
        existing.audio_size_bytes = bytes_written
        existing.audio_duration_seconds = duration
        s.add(existing)
        # Flush so existing.id is populated for the WS broadcast below.
        await s.flush()
        call_id_for_ws = existing.id

    await hub.broadcast(
        {
            "type": "call_recorded",
            "call": {
                "id": call_id_for_ws,
                "system": short,
                "tgid": existing.talkgroup_tgid,
                "alpha": existing.talkgroup_alpha,
                "description": existing.talkgroup_description,
                "category": existing.talkgroup_category,
                "encrypted": existing.encrypted,
                "frequency_hz": existing.frequency_hz,
                "start_time": existing.start_time.isoformat(),
                "duration_seconds": existing.duration_seconds,
                "audio_url": f"/api/calls/{call_id_for_ws}/audio",
            },
        }
    )
    return {"ok": True, "bytes": bytes_written}


def _is_loopback(ip: str) -> bool:
    return ip in {"127.0.0.1", "::1", "localhost"}
