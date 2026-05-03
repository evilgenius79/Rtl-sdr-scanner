"""ZIP-driven first-run wizard: lookup → preview systems → write trunk-recorder config."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from ..auth import require_user
from ..db import session
from ..models import (
    AuditEvent,
    ConventionalChannel,
    Site,
    System,
    SystemType,
    Talkgroup,
    utcnow,
)
from ..radioreference import (
    CountyDataset,
    RRError,
    SiteRow,
    TrsInfo,
    get_client,
)
from ..rate_limit import api_limiter
from ..security import client_ip, require_csrf
from ..settings import get_settings
from ..tr_config import (
    DongleSpec,
    render_config,
    write_channels_csv,
    write_config_atomically,
    write_talkgroups_csv,
)

logger = logging.getLogger("police_scanner.setup")
router = APIRouter(
    prefix="/api/setup",
    tags=["setup"],
    dependencies=[Depends(require_user), Depends(api_limiter)],
)

ZIP_RE = re.compile(r"^\d{5}$")


class ZipLookupRequest(BaseModel):
    zip: str = Field(..., min_length=5, max_length=5)


class TalkgroupPreview(BaseModel):
    tgid: int
    alpha: str
    description: str
    category: str
    encrypted: bool


class SitePreview(BaseModel):
    name: str
    rfss: int
    site_number: int
    control_channels: list[int]
    voice_channels: list[int]
    span_hz: int


class TrsPreview(BaseModel):
    sid: int
    name: str
    flavor: str
    voice: str
    sites: list[SitePreview]
    talkgroups: list[TalkgroupPreview]
    talkgroup_count: int
    encrypted_count: int


class FreqPreview(BaseModel):
    frequency_hz: int
    alpha: str
    description: str
    tone: str
    mode: str


class SetupPreview(BaseModel):
    zip: str
    county: str
    state: str
    trs_systems: list[TrsPreview]
    conventional: list[FreqPreview]


class SetupApply(BaseModel):
    zip: str = Field(..., min_length=5, max_length=5)
    selected_trs_sid: int | None = None
    selected_site_index: int = 0
    selected_categories: list[str] = []
    include_conventional: bool = True
    hide_encrypted: bool = True
    # 2 dongles = trunked-only (no conventional). 3 = trunked + conventional.
    # 4+ = additional conventional or sidecar use. Empty strings filtered server-side.
    dongle_serials: list[str] = Field(
        default=["00000101", "00000102", "00000103"], min_length=1, max_length=8
    )


@router.post("/lookup", response_model=SetupPreview)
async def lookup(payload: ZipLookupRequest) -> SetupPreview:
    if not ZIP_RE.match(payload.zip):
        raise HTTPException(status_code=400, detail="ZIP must be exactly 5 digits.")
    rr = get_client()
    try:
        zinfo = await rr.lookup_zip(payload.zip)
        county = await rr.county_dataset(zinfo.county_id)
    except RRError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _county_to_preview(payload.zip, county)


@router.post("/apply", dependencies=[Depends(require_csrf)])
async def apply(
    payload: SetupApply,
    user=Depends(require_user),
    request=None,
) -> dict:
    rr = get_client()
    try:
        zinfo = await rr.lookup_zip(payload.zip)
        county = await rr.county_dataset(zinfo.county_id)
    except RRError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    selected_trs: TrsInfo | None = None
    selected_site: SiteRow | None = None
    if payload.selected_trs_sid is not None:
        for trs in county.trs_systems:
            if trs.sid == payload.selected_trs_sid:
                selected_trs = trs
                break
        if selected_trs is None:
            raise HTTPException(status_code=400, detail="Selected TRS not in this county.")
        if not selected_trs.sites:
            raise HTTPException(status_code=400, detail="Selected TRS has no sites.")
        idx = max(0, min(payload.selected_site_index, len(selected_trs.sites) - 1))
        selected_site = selected_trs.sites[idx]

    settings = get_settings()
    settings.scanner_data_dir.mkdir(parents=True, exist_ok=True)
    tr_dir = Path(settings.scanner_tr_config_path).parent
    tr_dir.mkdir(parents=True, exist_ok=True)
    talkgroups_dir = tr_dir / "talkgroups"
    channels_dir = tr_dir / "channels"

    # ── Persist to DB
    async with session() as s:
        s.add(
            AuditEvent(
                action="setup_apply",
                actor=user.username,
                ip=client_ip(request) if request else None,
                detail=f"zip={payload.zip} trs={payload.selected_trs_sid}",
            )
        )

        if selected_trs is not None and selected_site is not None:
            sysrow = (
                await s.execute(select(System).where(System.rr_sid == selected_trs.sid))
            ).scalar_one_or_none()
            if sysrow is None:
                sysrow = System(
                    rr_sid=selected_trs.sid,
                    short_name=_short_name(selected_trs),
                    description=selected_trs.name,
                    type=SystemType.P25,
                    wacn=selected_trs.wacn,
                    sysid=selected_trs.sysid,
                )
                s.add(sysrow)
                await s.flush()
            sysrow.updated_at = utcnow()
            await s.flush()

            site = Site(
                system_id=sysrow.id,  # type: ignore[arg-type]
                rr_site_id=None,
                name=selected_site.name,
                rfss=selected_site.rfss,
                site_number=selected_site.site_number,
                control_channels=",".join(str(x) for x in selected_site.control_channels_hz),
                voice_channels=",".join(str(x) for x in selected_site.voice_channels_hz),
                span_hz=_span_hz(selected_site),
            )
            s.add(site)

            await _replace_talkgroups(
                s, sysrow.id, selected_trs, payload.selected_categories, payload.hide_encrypted
            )

        if payload.include_conventional and county.conventional:
            conv_sys = (
                await s.execute(select(System).where(System.short_name == "conv"))
            ).scalar_one_or_none()
            if conv_sys is None:
                conv_sys = System(
                    rr_sid=None,
                    short_name="conv",
                    description=f"{county.county_name} conventional",
                    type=SystemType.CONVENTIONAL,
                )
                s.add(conv_sys)
                await s.flush()
            await _replace_conventional(s, conv_sys.id, county)

    # ── Render trunk-recorder config + CSVs (atomic)
    talkgroups_files: dict[str, Path] = {}
    channels_files: dict[str, Path] = {}
    p25_for_config: list[tuple[TrsInfo, SiteRow]] = []
    if selected_trs is not None and selected_site is not None:
        short = _short_name(selected_trs)
        tg_path = talkgroups_dir / f"{short}.csv"
        # Filter talkgroups to selected categories (or include all if none specified).
        selected_tgs = [
            tg
            for tg in selected_trs.talkgroups
            if (not payload.selected_categories or tg.category in payload.selected_categories)
            and (not payload.hide_encrypted or not tg.encrypted)
        ]
        write_talkgroups_csv(tg_path, selected_tgs)
        talkgroups_files[f"{short}_{selected_trs.sid}"] = tg_path  # match short used in render_config
        p25_for_config.append((selected_trs, selected_site))

    conventional_freqs_hz: list[int] = []
    if payload.include_conventional and county.conventional:
        ch_path = channels_dir / "conv.csv"
        write_channels_csv(ch_path, county.conventional)
        channels_files["conv"] = ch_path
        conventional_freqs_hz = [c.frequency_hz for c in county.conventional if c.frequency_hz]

    # Drop blank entries from the UI, then validate the rest are 8 hex/digits.
    serials = [s.strip() for s in payload.dongle_serials if s and s.strip()]
    for serial in serials:
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", serial):
            raise HTTPException(status_code=400, detail=f"Invalid dongle serial: {serial!r}")

    if not serials:
        raise HTTPException(status_code=400, detail="At least one dongle serial is required.")
    if len(serials) != len(set(serials)):
        raise HTTPException(status_code=400, detail="Dongle serials must be unique.")

    # Role assignment by count:
    #   1 dongle  → control_low (narrow trunked sites only, no conventional)
    #   2 dongles → control_low + voice_high (full trunked, no conventional)
    #   3 dongles → + conventional (the original layout)
    #   4+        → extra dongles tagged 'conventional2', 'conventional3'… for future use
    role_for = ["control_low", "voice_high", "conventional"]
    dongles = []
    for i, serial in enumerate(serials):
        if i < len(role_for):
            role = role_for[i]
        else:
            role = f"conventional{i - 1}"
        dongles.append(DongleSpec(serial=serial, role=role))

    # Refuse a config that asks for conventional but doesn't have a dongle for it.
    if conventional_freqs_hz and not any(d.role == "conventional" for d in dongles):
        # Quietly drop the conventional system instead of failing — user will see
        # "0 conventional channels" in the response and can re-run with more dongles.
        conventional_freqs_hz = []
        channels_files = {}

    config = render_config(
        p25_systems=p25_for_config,
        conventional_freqs_hz=conventional_freqs_hz,
        talkgroups_files={
            f"{_short_name(trs)}_{trs.sid}": talkgroups_files[f"{_short_name(trs)}_{trs.sid}"]
            for trs, _ in p25_for_config
        },
        channels_files=channels_files,
        dongles=dongles,
        capture_dir=settings.scanner_tr_capture_dir,
        mqtt_host=settings.mqtt_host,
        mqtt_port=settings.mqtt_port,
        mqtt_username=settings.mqtt_username,
        mqtt_password=settings.mqtt_password.get_secret_value() if settings.mqtt_password else "",
        mqtt_topic_base=settings.mqtt_topic_base,
        upload_script_path=Path(settings.scanner_data_dir) / "trunk-recorder" / "uploadhook",
        hide_encrypted=payload.hide_encrypted,
    )

    write_config_atomically(settings.scanner_tr_config_path, config)
    return {
        "ok": True,
        "config_path": str(settings.scanner_tr_config_path),
        "p25": bool(p25_for_config),
        "conventional_count": len(conventional_freqs_hz),
        "next": "Restart trunk-recorder.service to pick up the new config.",
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _county_to_preview(zip_code: str, county: CountyDataset) -> SetupPreview:
    trs_previews: list[TrsPreview] = []
    for trs in county.trs_systems:
        sites = [
            SitePreview(
                name=s.name,
                rfss=s.rfss,
                site_number=s.site_number,
                control_channels=s.control_channels_hz,
                voice_channels=s.voice_channels_hz,
                span_hz=_span_hz(s),
            )
            for s in trs.sites
        ]
        tgs = [
            TalkgroupPreview(
                tgid=tg.tgid,
                alpha=tg.alpha,
                description=tg.description,
                category=tg.category,
                encrypted=tg.encrypted,
            )
            for tg in trs.talkgroups
        ]
        trs_previews.append(
            TrsPreview(
                sid=trs.sid,
                name=trs.name,
                flavor=trs.flavor,
                voice=trs.voice,
                sites=sites,
                talkgroups=tgs,
                talkgroup_count=len(tgs),
                encrypted_count=sum(1 for t in tgs if t.encrypted),
            )
        )

    conv = [
        FreqPreview(
            frequency_hz=c.frequency_hz,
            alpha=c.alpha,
            description=c.description,
            tone=c.tone,
            mode=c.mode,
        )
        for c in county.conventional
    ]
    return SetupPreview(
        zip=zip_code,
        county=county.county_name,
        state=county.state,
        trs_systems=trs_previews,
        conventional=conv,
    )


def _short_name(trs: TrsInfo) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in trs.name.lower())[:24]
    return safe or f"sys_{trs.sid}"


def _span_hz(site: SiteRow) -> int:
    freqs = sorted(set(site.control_channels_hz + site.voice_channels_hz))
    if not freqs:
        return 0
    return freqs[-1] - freqs[0]


async def _replace_talkgroups(s, system_id: int, trs: TrsInfo, categories: list[str], hide_enc: bool):
    # Delete existing talkgroups for this system, then re-insert.
    existing = (await s.execute(select(Talkgroup).where(Talkgroup.system_id == system_id))).scalars()
    for row in existing:
        await s.delete(row)
    await s.flush()
    for tg in trs.talkgroups:
        if categories and tg.category not in categories:
            continue
        s.add(
            Talkgroup(
                system_id=system_id,
                tgid=tg.tgid,
                alpha=tg.alpha,
                description=tg.description,
                category=tg.category,
                tag=tg.tag,
                mode=tg.mode,
                encrypted=tg.encrypted,
                priority=tg.priority,
                hidden=tg.encrypted and hide_enc,
            )
        )


async def _replace_conventional(s, system_id: int, county: CountyDataset):
    existing = (
        await s.execute(select(ConventionalChannel).where(ConventionalChannel.system_id == system_id))
    ).scalars()
    for row in existing:
        await s.delete(row)
    await s.flush()
    for c in county.conventional:
        s.add(
            ConventionalChannel(
                system_id=system_id,
                frequency_hz=c.frequency_hz,
                alpha=c.alpha,
                description=c.description,
                tone=c.tone,
                mode=c.mode,
                license=c.license,
            )
        )
