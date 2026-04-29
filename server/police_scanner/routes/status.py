"""System status: SDR detection, trunk-recorder service state, decode rates."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends

from ..auth import require_user
from ..rate_limit import api_limiter
from ..settings import get_settings

router = APIRouter(
    prefix="/api/status",
    tags=["status"],
    dependencies=[Depends(require_user), Depends(api_limiter)],
)


@router.get("")
async def status() -> dict:
    settings = get_settings()
    return {
        "rr_configured": settings.rr_configured,
        "tr_config_exists": Path(settings.scanner_tr_config_path).is_file(),
        "tr_service_active": await _systemctl_active(settings.scanner_tr_systemd_unit),
        "sdrs": await _detect_sdrs(),
        "disk_free_gb": _disk_free_gb(settings.scanner_recordings_dir),
        "recordings_dir": str(settings.scanner_recordings_dir),
    }


async def _systemctl_active(unit: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/systemctl", "is-active", unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode().strip()
    except (TimeoutError, FileNotFoundError):
        return None


async def _detect_sdrs() -> list[dict]:
    """Run rtl_test -t with a 1-second timeout to enumerate dongles by serial."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/local/bin/rtl_test", "-t",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return []
    except FileNotFoundError:
        return []
    text = out.decode(errors="replace")
    sdrs: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("0:", "1:", "2:", "3:")):
            if current:
                sdrs.append(current)
            current = {"index": int(line.split(":", 1)[0]), "name": line.split(":", 1)[1].strip()}
        elif current and line.startswith("Serial number:"):
            current["serial"] = line.split(":", 1)[1].strip()
    if current:
        sdrs.append(current)
    return sdrs


def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        return round(usage.free / 1e9, 1)
    except OSError:
        return -1
