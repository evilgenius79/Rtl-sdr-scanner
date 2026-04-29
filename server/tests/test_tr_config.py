"""Tests for trunk-recorder config generator: SDR planning + atomic write + CSV."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from police_scanner.radioreference import SiteRow, TalkgroupRow, TrsInfo
from police_scanner.tr_config import (
    DongleSpec,
    plan_source_for_conventional,
    plan_sources_for_p25_site,
    render_config,
    write_config_atomically,
    write_talkgroups_csv,
)

D1 = DongleSpec(serial="00000101", role="control_low")
D2 = DongleSpec(serial="00000102", role="voice_high")
D3 = DongleSpec(serial="00000103", role="conventional")


def _site(span_mhz: float, control_offset_hz: int = 0) -> SiteRow:
    """Build a SiteRow whose voice freqs span exactly span_mhz."""
    base = 769_800_000
    voice = [base, base + int(span_mhz * 1e6)]
    return SiteRow(
        rfss=1,
        site_number=94,
        name="test",
        control_channels_hz=[base + control_offset_hz],
        voice_channels_hz=voice,
    )


def test_one_sdr_for_narrow_site():
    site = _site(span_mhz=1.5)
    sources = plan_sources_for_p25_site(site, dongles=(D1, D2))
    assert len(sources) == 1
    # Center should sit between the extremes.
    c = sources[0]["center"]
    assert 769_800_000 <= c <= 769_800_000 + 1_500_000


def test_two_sdrs_for_wide_p25_site_rushville_realistic():
    # Mirrors the Knightstown SAFE-T site span (~4.86 MHz)
    site = _site(span_mhz=4.86)
    sources = plan_sources_for_p25_site(site, dongles=(D1, D2))
    assert len(sources) == 2
    # Verify every original frequency is within at least one window
    USABLE = 1_800_000
    freqs = sorted(set(site.control_channels_hz + site.voice_channels_hz))
    centers = [s["center"] for s in sources]
    for f in freqs:
        assert any(abs(f - c) <= USABLE // 2 for c in centers), f"Freq {f} not covered"


def test_too_wide_raises_when_freqs_actually_dense():
    # 10 MHz of frequencies every 1 MHz → 11 freqs needing ~5 windows of 2 MHz
    # — exceeds 2-dongle budget.
    base = 769_800_000
    voice = [base + i * 1_000_000 for i in range(11)]
    dense_site = SiteRow(
        rfss=1, site_number=99, name="dense",
        control_channels_hz=[base], voice_channels_hz=voice,
    )
    with pytest.raises(ValueError):
        plan_sources_for_p25_site(dense_site, dongles=(D1, D2))


def test_two_freqs_far_apart_succeeds_with_two_dongles():
    # 10 MHz span with only 2 freqs — 2 SDRs trivially cover them, gap in middle
    # has no signals so no loss.
    site = _site(span_mhz=10.0)
    sources = plan_sources_for_p25_site(site, dongles=(D1, D2))
    assert len(sources) == 2


def test_conventional_max_coverage():
    # A cluster of VHF dispatch frequencies — typical Rush County mix.
    freqs = [154_355_000, 154_415_000, 155_190_000, 155_625_000, 156_195_000, 159_360_000, 159_420_000]
    src, covered = plan_source_for_conventional(freqs, D3)
    # Should cover at least the dense low cluster.
    assert len(covered) >= 5
    assert src["analogRecorders"] >= 1
    assert src["digitalRecorders"] == 0
    assert src["device"] == "rtl=00000103"


def test_render_full_config_writes_valid_json(tmp_path: Path):
    site = _site(span_mhz=4.86)
    trs = TrsInfo(
        sid=8084,
        name="Hoosier SAFE-T",
        type="P25",
        flavor="P25 Phase 1",
        voice="APCO-25 CAI",
        wacn="BEE00",
        sysid="6BD",
        sites=[site],
        talkgroups=[],
    )
    cfg = render_config(
        p25_systems=[(trs, site)],
        conventional_freqs_hz=[154_355_000, 155_625_000, 156_195_000],
        talkgroups_files={"hoosier_safe_t_8084": tmp_path / "tg.csv"},
        channels_files={"conv": tmp_path / "ch.csv"},
        dongles=[D1, D2, D3],
        capture_dir=tmp_path / "captures",
        mqtt_host="127.0.0.1",
        mqtt_port=1883,
        mqtt_username="",
        mqtt_password="",
        mqtt_topic_base="trunk-recorder",
        upload_script_path=tmp_path / "uploadhook",
        hide_encrypted=True,
    )
    out = tmp_path / "config.json"
    write_config_atomically(out, cfg)
    parsed = json.loads(out.read_text())
    assert parsed["ver"] == 2
    # Must have at least 3 sources (2 P25 + 1 conventional).
    assert len(parsed["sources"]) == 3
    assert any(s["device"] == "rtl=00000103" for s in parsed["sources"])
    assert parsed["systems"][0]["type"] == "p25"
    assert parsed["systems"][0]["hideEncrypted"] is True
    assert parsed["plugins"][0]["library"] == "libmqtt_status_plugin.so"


def test_atomic_write_does_not_leave_temp_on_success(tmp_path: Path):
    out = tmp_path / "x.json"
    write_config_atomically(out, {"ver": 2})
    leftovers = list(tmp_path.glob(".config-*.tmp"))
    assert leftovers == []


def test_talkgroups_csv_escapes_commas_and_quotes(tmp_path: Path):
    rows = [
        TalkgroupRow(
            tgid=1234,
            alpha='Sheriff "A"',
            description="Patrol, Precinct 1",
            mode="D",
            encrypted=False,
            tag="Law",
            category="Sheriff",
        )
    ]
    p = tmp_path / "tg.csv"
    write_talkgroups_csv(p, rows)
    text = p.read_text()
    assert '"Sheriff ""A"""' in text
    assert '"Patrol, Precinct 1"' in text
