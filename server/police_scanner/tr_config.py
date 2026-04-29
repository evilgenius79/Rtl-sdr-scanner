"""Generate trunk-recorder ``config.json`` from imported RadioReference data
plus per-system user choices stored in our DB.

The generated config is written atomically (write-temp-then-rename) so a crash
mid-write can never leave trunk-recorder reading a corrupt file.

Schema reference: docs/CONFIGURE.md from robotastic/trunk-recorder.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .radioreference import SiteRow, TrsInfo
from .settings import get_settings

# Default sample rate for RTL-SDR Blog v3/v4. trunk-recorder community standard is
# 2.4 Msps which yields ~2.0 MHz of usable spectrum after filter roll-off. Some Pi USB
# hub combinations have trouble with this; if you see "lost samples" in the log, drop
# to 2_048_000 and let plan_sources_for_p25_site() add a 3rd SDR for wide sites.
DEFAULT_RATE = 2_400_000
USABLE_BW = 2_000_000  # Hz


@dataclass(frozen=True)
class DongleSpec:
    """Physical RTL-SDR description; flashed serial selects which dongle."""

    serial: str
    role: str  # "control_low", "voice_high", "conventional"
    digital_recorders: int = 4
    analog_recorders: int = 0
    gain: int = 36
    ppm: int = 0


def plan_sources_for_p25_site(
    site: SiteRow,
    *,
    dongles: tuple[DongleSpec, ...],
) -> list[dict]:
    """Greedy interval-cover: place SDR windows of width USABLE_BW so that every
    listed frequency falls inside at least one window. Uses as few SDRs as possible.

    Raises ValueError if more dongles are needed than supplied. Note that this
    can succeed on a wide site even when (max - min) > N * USABLE_BW, as long
    as the actual frequencies cluster — the gaps don't matter if no freq lives there.
    """
    freqs = sorted(set(site.control_channels_hz + site.voice_channels_hz))
    if not freqs:
        raise ValueError(f"Site {site.name!r} has no frequencies")

    centers: list[int] = []
    i = 0
    while i < len(freqs):
        cluster_start = freqs[i]
        # Walk forward as long as freqs[j] - cluster_start <= USABLE_BW
        j = i
        while j < len(freqs) and freqs[j] - cluster_start <= USABLE_BW:
            j += 1
        # Cluster covers freqs[i..j-1]. Center the window on its midpoint.
        cluster_end = freqs[j - 1]
        centers.append((cluster_start + cluster_end) // 2)
        i = j

    if len(centers) > len(dongles):
        raise ValueError(
            f"Site {site.name!r} needs {len(centers)} SDRs to cover "
            f"all {len(freqs)} frequencies (span {(freqs[-1] - freqs[0]) / 1e6:.2f} MHz "
            f"with USABLE_BW {USABLE_BW / 1e6:.2f} MHz), but only {len(dongles)} are "
            f"allocated to this system."
        )

    # Sanity check: every freq is in exactly one window.
    half = USABLE_BW // 2
    for f in freqs:
        if not any(abs(f - c) <= half for c in centers):
            raise AssertionError(f"Internal: frequency {f} not covered by any window")

    return [_source_dict(dongles[idx], c) for idx, c in enumerate(centers)]


def plan_source_for_conventional(
    frequencies_hz: list[int], dongle: DongleSpec
) -> tuple[dict, list[int]]:
    """Center a single SDR's window of width USABLE_BW to maximize the count
    of conventional channels covered. Sliding-window O(n) on sorted frequencies.

    Returns (source_dict, list_of_covered_frequencies). Any freq outside the
    chosen window is dropped — caller decides how to surface that to the user.
    """
    if not frequencies_hz:
        return _source_dict(dongle, 155_500_000, analog=True), []
    sorted_freqs = sorted(set(frequencies_hz))

    best_count = 0
    best_left = 0
    best_right = 0
    left = 0
    for right in range(len(sorted_freqs)):
        while sorted_freqs[right] - sorted_freqs[left] > USABLE_BW:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_count = count
            best_left = left
            best_right = right

    covered = sorted_freqs[best_left : best_right + 1]
    center = (covered[0] + covered[-1]) // 2 if covered else sorted_freqs[0]
    return _source_dict(dongle, center, analog=True), covered


def _source_dict(dongle: DongleSpec, center_hz: int, *, analog: bool = False) -> dict:
    return {
        "center": int(center_hz),
        "rate": DEFAULT_RATE,
        "ppm": dongle.ppm,
        "gain": dongle.gain,
        "agc": False,
        "digitalRecorders": 0 if analog else dongle.digital_recorders,
        "analogRecorders": 4 if analog else dongle.analog_recorders,
        "driver": "osmosdr",
        "device": "rtl=" + dongle.serial,
    }


def render_config(
    *,
    p25_systems: list[tuple[TrsInfo, SiteRow]],
    conventional_freqs_hz: list[int],
    talkgroups_files: dict[str, Path],
    channels_files: dict[str, Path],
    dongles: list[DongleSpec],
    capture_dir: Path,
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: str,
    mqtt_password: str,
    mqtt_topic_base: str,
    upload_script_path: Path,
    hide_encrypted: bool = True,
) -> dict:
    """Build the config.json object."""
    if len(dongles) < 1:
        raise ValueError("Need at least 1 dongle.")

    sources: list[dict] = []
    systems: list[dict] = []

    p25_dongles = [d for d in dongles if d.role.startswith("control") or d.role.startswith("voice")]
    conv_dongle = next((d for d in dongles if d.role == "conventional"), None)

    if p25_systems:
        if len(p25_dongles) < 2:
            raise ValueError("Need 2 dongles for the wide-span P25 plan.")
        for trs, site in p25_systems:
            site_sources = plan_sources_for_p25_site(
                site,
                dongles=(p25_dongles[0], p25_dongles[1]),
            )
            sources.extend(site_sources)
            systems.append(
                {
                    "shortName": _short_name(trs.name, trs.sid),
                    "type": "p25",
                    "modulation": "qpsk",
                    "control_channels": site.control_channels_hz,
                    "talkgroupsFile": str(talkgroups_files[_short_name(trs.name, trs.sid)]),
                    "talkgroupDisplayFormat": "tag_id",
                    "hideEncrypted": hide_encrypted,
                    "audioArchive": True,
                    "transmissionArchive": False,
                    "callLog": True,
                    "compressWav": True,
                    "compressBitrate": "32k",
                    "uploadScript": str(upload_script_path),
                    "minDuration": 0,
                    "minTransmissionDuration": 0,
                }
            )

    if conventional_freqs_hz and conv_dongle is not None:
        src, _covered = plan_source_for_conventional(conventional_freqs_hz, conv_dongle)
        sources.append(src)
        systems.append(
            {
                "shortName": "conv",
                "type": "conventional",
                "channelFile": str(channels_files["conv"]),
                "audioArchive": True,
                "callLog": True,
                "uploadScript": str(upload_script_path),
                "squelch": -55,
                "deemphasisTau": 0.000750,
            }
        )

    plugins = [
        {
            "name": "MQTT Status",
            "library": "libmqtt_status_plugin.so",
            "broker": f"tcp://{mqtt_host}:{mqtt_port}",
            "topic": mqtt_topic_base,
            "unit_topic": f"{mqtt_topic_base}/units",
            "message_topic": f"{mqtt_topic_base}/messages",
            "username": mqtt_username,
            "password": mqtt_password,
            "console_logs": False,
            "mqtt_audio": False,  # we read audio off disk via uploadScript
            "mqtt_qos": 0,
        }
    ]

    return {
        "ver": 2,
        "captureDir": str(capture_dir),
        "logFile": True,
        "logDir": str(Path(get_settings().scanner_data_dir) / "trunk-recorder" / "logs"),
        "consoleLog": True,
        "logLevel": "info",
        "callTimeout": 3,
        "controlWarnRate": 5,
        "controlRetuneLimit": 0,
        "frequencyFormat": "mhz",
        "statusAsString": True,
        "instanceId": "police-scanner",
        "sources": sources,
        "systems": systems,
        "plugins": plugins,
    }


def write_talkgroups_csv(path: Path, talkgroups) -> None:
    """trunk-recorder reads talkgroups as CSV (no header).

    Columns: Decimal,Hex,Mode,Alpha,Description,Tag,Group,Priority
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("Decimal,Hex,Mode,Alpha Tag,Description,Tag,Group,Priority\n")
        for tg in talkgroups:
            mode = tg.mode if tg.mode else "D"
            f.write(
                ",".join(
                    [
                        str(tg.tgid),
                        f"{tg.tgid:X}",
                        mode,
                        _csv_escape(tg.alpha),
                        _csv_escape(tg.description),
                        _csv_escape(tg.tag),
                        _csv_escape(tg.category),
                        str(tg.priority or 0),
                    ]
                )
                + "\n"
            )


def write_channels_csv(path: Path, channels) -> None:
    """trunk-recorder conventional channelFile CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("Frequency,Alpha,Description,Tone,Mode\n")
        for c in channels:
            f.write(
                ",".join(
                    [
                        f"{c.frequency_hz/1e6:.6f}",
                        _csv_escape(c.alpha),
                        _csv_escape(c.description),
                        _csv_escape(c.tone),
                        _csv_escape(c.mode),
                    ]
                )
                + "\n"
            )


def write_config_atomically(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _short_name(system_name: str, sid: int) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in system_name.lower())[:24]
    return f"{safe}_{sid}" if safe else f"sys_{sid}"


def _csv_escape(value: str) -> str:
    if not value:
        return ""
    if "," in value or '"' in value or "\n" in value:
        return '"' + value.replace('"', '""') + '"'
    return value
