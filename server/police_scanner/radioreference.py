"""RadioReference SOAP client.

Talks to https://api.radioreference.com/soap2/?wsdl&v=latest using zeep.
authInfo is sent on every call as documented at:
    https://wiki.radioreference.com/index.php/RadioReference.com_Web_Service3.1

Defensive notes:
  * Network errors are wrapped in ``RRError`` so callers don't see raw zeep stack traces.
  * Credentials never appear in log output.
  * Calls are run in a thread because zeep is synchronous.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from zeep import Client
from zeep.exceptions import Error as ZeepError
from zeep.helpers import serialize_object

from .settings import get_settings

logger = logging.getLogger("police_scanner.rr")

WSDL_URL = "https://api.radioreference.com/soap2/?wsdl&v=latest"
ZIP_RE = re.compile(r"^\d{5}$")


class RRError(RuntimeError):
    """Public-facing RadioReference error (safe to surface to UI)."""


@dataclass
class ZipInfo:
    zip: str
    county_id: int
    state_id: int
    city: str
    latitude: float | None
    longitude: float | None


@dataclass
class TalkgroupRow:
    tgid: int
    alpha: str
    description: str
    mode: str
    encrypted: bool
    tag: str
    category: str
    priority: int = 0


@dataclass
class SiteRow:
    rfss: int
    site_number: int
    name: str
    control_channels_hz: list[int] = field(default_factory=list)
    voice_channels_hz: list[int] = field(default_factory=list)


@dataclass
class TrsInfo:
    sid: int
    name: str
    type: str  # "P25", "Motorola", etc.
    flavor: str  # "P25 Phase 1", etc.
    voice: str
    wacn: str | None
    sysid: str | None
    sites: list[SiteRow] = field(default_factory=list)
    talkgroups: list[TalkgroupRow] = field(default_factory=list)


@dataclass
class FreqRow:
    frequency_hz: int
    alpha: str
    description: str
    tone: str
    mode: str
    license: str


@dataclass
class CountyDataset:
    county_id: int
    county_name: str
    state: str
    trs_systems: list[TrsInfo] = field(default_factory=list)
    conventional: list[FreqRow] = field(default_factory=list)


def _hz(value: Any) -> int:
    """RR returns frequencies as MHz strings or floats. Normalize to integer Hz."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return round(f * 1_000_000)


class RadioReferenceClient:
    """Thin wrapper around the SOAP client. Construct lazily; reuse the instance."""

    def __init__(self) -> None:
        self._client: Client | None = None
        self._auth: dict[str, str] | None = None

    def _ensure(self) -> tuple[Client, dict[str, str]]:
        if self._client is not None and self._auth is not None:
            return self._client, self._auth
        settings = get_settings()
        if not settings.rr_configured:
            raise RRError(
                "RadioReference credentials not configured. "
                "Set RR_APP_KEY, RR_USERNAME, RR_PASSWORD."
            )
        assert settings.rr_app_key and settings.rr_password and settings.rr_username
        try:
            self._client = Client(WSDL_URL)
        except Exception as exc:
            raise RRError(f"Could not load RadioReference WSDL: {exc}") from exc
        self._auth = {
            "appKey": settings.rr_app_key.get_secret_value(),
            "username": settings.rr_username,
            "password": settings.rr_password.get_secret_value(),
            "version": "latest",
            "style": "rpc",
        }
        return self._client, self._auth

    # ── Low-level call helpers ─────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(ZeepError),
        reraise=True,
    )
    def _call_sync(self, method: str, *args: Any) -> Any:
        client, auth = self._ensure()
        try:
            fn = getattr(client.service, method)
        except AttributeError as exc:
            raise RRError(f"RadioReference API has no method '{method}'") from exc
        try:
            return fn(*args, auth)
        except ZeepError:
            raise
        except Exception as exc:
            raise RRError(f"RadioReference call '{method}' failed: {exc}") from exc

    async def _call(self, method: str, *args: Any) -> Any:
        try:
            return await asyncio.to_thread(self._call_sync, method, *args)
        except ZeepError as exc:
            raise RRError(f"RadioReference call '{method}' failed: {exc}") from exc

    # ── High-level convenience methods ──────────────────────────────────────

    async def lookup_zip(self, zip_code: str) -> ZipInfo:
        if not ZIP_RE.match(zip_code):
            raise RRError("ZIP code must be exactly 5 digits.")
        raw = serialize_object(await self._call("getZipcodeInfo", zip_code))
        if not raw:
            raise RRError(f"ZIP {zip_code} not found.")
        return ZipInfo(
            zip=zip_code,
            county_id=int(raw.get("ctid") or raw.get("countyId") or 0),
            state_id=int(raw.get("stid") or raw.get("stateId") or 0),
            city=str(raw.get("city") or raw.get("name") or ""),
            latitude=float(raw["lat"]) if raw.get("lat") else None,
            longitude=float(raw["lon"]) if raw.get("lon") else None,
        )

    async def county_dataset(self, county_id: int) -> CountyDataset:
        county_raw = serialize_object(await self._call("getCountyInfo", county_id)) or {}
        county_name = str(county_raw.get("countyName") or county_raw.get("name") or "")
        state = str(county_raw.get("stateAbbreviation") or county_raw.get("state") or "")

        trs_systems: list[TrsInfo] = []
        for trs in county_raw.get("trsSystems", []) or []:
            sid = int(trs.get("sid") or trs.get("trsId") or 0)
            if not sid:
                continue
            try:
                trs_systems.append(await self.trs_info(sid))
            except RRError as exc:
                logger.warning("Skipping TRS sid=%s due to error: %s", sid, exc)

        conventional: list[FreqRow] = []
        for sub in county_raw.get("subcats", []) or []:
            for f in sub.get("freqs", []) or []:
                conventional.append(_freq_row(f))
        for agency in county_raw.get("agencies", []) or []:
            for cat in agency.get("cats", []) or []:
                for f in cat.get("freqs", []) or []:
                    conventional.append(_freq_row(f))

        return CountyDataset(
            county_id=county_id,
            county_name=county_name,
            state=state,
            trs_systems=trs_systems,
            conventional=_dedupe_freqs(conventional),
        )

    async def trs_info(self, sid: int) -> TrsInfo:
        info = serialize_object(await self._call("getTrsInfo", sid)) or {}
        sites_raw = serialize_object(await self._call("getTrsSites", sid)) or []
        cats_raw = serialize_object(await self._call("getTrsTalkgroupCats", sid)) or []
        tg_raw = serialize_object(await self._call("getTrsTalkgroups", sid, 0, 0, 0, 0)) or []

        sites = [_site_row(s) for s in sites_raw if s]
        cat_lookup: dict[int, str] = {}
        for c in cats_raw:
            cat_id = int(c.get("tgCid") or c.get("tgcid") or 0)
            cat_name = str(c.get("tgCname") or c.get("name") or "")
            cat_lookup[cat_id] = cat_name

        talkgroups: list[TalkgroupRow] = []
        for tg in tg_raw:
            mode = str(tg.get("tgMode") or tg.get("mode") or "D").upper()
            cat_id = int(tg.get("tgCid") or tg.get("tgcid") or 0)
            talkgroups.append(
                TalkgroupRow(
                    tgid=int(tg.get("tgDec") or tg.get("dec") or 0),
                    alpha=str(tg.get("tgAlpha") or tg.get("alpha") or ""),
                    description=str(tg.get("tgDescr") or tg.get("descr") or ""),
                    mode=mode,
                    encrypted="E" in mode,
                    tag=str(tg.get("tgTag") or tg.get("tag") or ""),
                    category=cat_lookup.get(cat_id, ""),
                    priority=int(tg.get("tgPriority") or 0),
                )
            )

        return TrsInfo(
            sid=sid,
            name=str(info.get("sName") or info.get("name") or ""),
            type=str(info.get("sType") or ""),
            flavor=str(info.get("sFlavor") or info.get("flavor") or ""),
            voice=str(info.get("sVoice") or info.get("voice") or ""),
            wacn=info.get("sWacn") or info.get("wacn"),
            sysid=info.get("sSysid") or info.get("sysid"),
            sites=sites,
            talkgroups=talkgroups,
        )


def _site_row(s: dict[str, Any]) -> SiteRow:
    cc = []
    voice = []
    for f in s.get("siteFreqs", []) or []:
        hz = _hz(f.get("freq") or f.get("frequency"))
        if not hz:
            continue
        use = str(f.get("use") or f.get("type") or "").lower()
        if "control" in use or "c" == str(f.get("controlChannel") or "").lower():
            cc.append(hz)
        else:
            voice.append(hz)
    return SiteRow(
        rfss=int(s.get("siteRfss") or 0),
        site_number=int(s.get("siteNumber") or 0),
        name=str(s.get("siteDescr") or s.get("name") or ""),
        control_channels_hz=cc,
        voice_channels_hz=voice,
    )


def _freq_row(f: dict[str, Any]) -> FreqRow:
    return FreqRow(
        frequency_hz=_hz(f.get("freq") or f.get("frequency")),
        alpha=str(f.get("alpha") or f.get("alphaTag") or ""),
        description=str(f.get("descr") or f.get("description") or ""),
        tone=str(f.get("tone") or ""),
        mode=str(f.get("mode") or "FMN"),
        license=str(f.get("license") or f.get("callsign") or ""),
    )


def _dedupe_freqs(rows: list[FreqRow]) -> list[FreqRow]:
    seen: set[tuple[int, str]] = set()
    out: list[FreqRow] = []
    for r in rows:
        key = (r.frequency_hz, r.alpha)
        if key in seen or not r.frequency_hz:
            continue
        seen.add(key)
        out.append(r)
    return out


_singleton: RadioReferenceClient | None = None


def get_client() -> RadioReferenceClient:
    global _singleton
    if _singleton is None:
        _singleton = RadioReferenceClient()
    return _singleton
