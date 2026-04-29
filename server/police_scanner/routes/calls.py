"""Recording library: list / search / play / delete."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc
from sqlmodel import select

from ..auth import require_user
from ..db import session
from ..models import Call
from ..rate_limit import api_limiter
from ..settings import get_settings

router = APIRouter(
    prefix="/api/calls",
    tags=["calls"],
    dependencies=[Depends(require_user), Depends(api_limiter)],
)


@router.get("")
async def list_calls(
    q: str = Query("", max_length=128),
    talkgroup: int | None = Query(None, ge=0),
    system: str | None = Query(None, max_length=32),
    category: str | None = Query(None, max_length=128),
    encrypted: bool | None = None,
    since: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    async with session() as s:
        stmt = select(Call)
        if talkgroup is not None:
            stmt = stmt.where(Call.talkgroup_tgid == talkgroup)
        if system:
            stmt = stmt.where(Call.system_short_name == system)
        if category:
            stmt = stmt.where(Call.talkgroup_category == category)
        if encrypted is not None:
            stmt = stmt.where(Call.encrypted == encrypted)
        if since is not None:
            stmt = stmt.where(Call.start_time >= since)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                (Call.talkgroup_alpha.ilike(like))  # type: ignore[union-attr]
                | (Call.talkgroup_description.ilike(like))  # type: ignore[union-attr]
                | (Call.transcript.ilike(like))  # type: ignore[union-attr]
            )
        stmt = stmt.order_by(desc(Call.start_time)).limit(limit).offset(offset)
        rows = (await s.execute(stmt)).scalars().all()
        return {
            "calls": [_call_to_dict(c) for c in rows],
            "limit": limit,
            "offset": offset,
        }


@router.get("/{call_id}/audio")
async def get_audio(call_id: int) -> FileResponse:
    async with session() as s:
        call = await s.get(Call, call_id)
    if not call or not call.audio_path:
        raise HTTPException(status_code=404, detail="Not found")

    settings = get_settings()
    base = settings.scanner_recordings_dir.resolve()
    candidate = (base / call.audio_path).resolve() if not Path(call.audio_path).is_absolute() else Path(
        call.audio_path
    ).resolve()
    # Defense-in-depth path-traversal check.
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Audio file missing")

    media_type = "audio/mp4" if candidate.suffix.lower() == ".m4a" else (
        "audio/mpeg" if candidate.suffix.lower() == ".mp3" else "audio/wav"
    )
    return FileResponse(
        path=str(candidate),
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


def _call_to_dict(c: Call) -> dict:
    return {
        "id": c.id,
        "system": c.system_short_name,
        "call_id": c.call_id,
        "tgid": c.talkgroup_tgid,
        "alpha": c.talkgroup_alpha,
        "description": c.talkgroup_description,
        "category": c.talkgroup_category,
        "encrypted": c.encrypted,
        "frequency_hz": c.frequency_hz,
        "start_time": c.start_time.astimezone(UTC).isoformat() if c.start_time else None,
        "end_time": c.end_time.astimezone(UTC).isoformat() if c.end_time else None,
        "duration_seconds": c.duration_seconds,
        "audio_url": f"/api/calls/{c.id}/audio" if c.audio_path else None,
        "transcript": c.transcript,
        "transcript_status": c.transcript_status,
    }
