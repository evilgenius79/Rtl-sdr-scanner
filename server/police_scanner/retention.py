"""Sweep old recordings off disk + DB based on RECORDING_RETENTION_DAYS."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import select

from .db import session
from .models import Call
from .settings import get_settings

logger = logging.getLogger("police_scanner.retention")


async def retention_sweeper() -> None:
    """Run forever; sweep once per hour. Skips if RECORDING_RETENTION_DAYS=0."""
    while True:
        try:
            await _sweep_once()
        except Exception as exc:  # pragma: no cover (defensive)
            logger.exception("retention sweep failed: %s", exc)
        await asyncio.sleep(3600)


async def _sweep_once() -> None:
    settings = get_settings()
    if settings.recording_retention_days <= 0:
        return
    cutoff = datetime.now(UTC) - timedelta(days=settings.recording_retention_days)
    rec_dir = settings.scanner_recordings_dir.resolve()
    deleted_files = 0
    deleted_rows = 0
    async with session() as s:
        old = (await s.execute(select(Call).where(Call.start_time < cutoff))).scalars().all()
        for call in old:
            if call.audio_path:
                p = (rec_dir / call.audio_path) if not Path(call.audio_path).is_absolute() else Path(
                    call.audio_path
                )
                try:
                    p_resolved = p.resolve()
                    p_resolved.relative_to(rec_dir)  # guard against ../ leak
                    if p_resolved.is_file():
                        p_resolved.unlink()
                        deleted_files += 1
                except (ValueError, OSError):
                    pass
            await s.delete(call)
            deleted_rows += 1
    if deleted_files or deleted_rows:
        logger.info("retention sweep: removed %d files / %d rows", deleted_files, deleted_rows)
