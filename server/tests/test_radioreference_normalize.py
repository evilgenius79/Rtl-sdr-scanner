"""Test the RadioReference response → dataclass normalization, without hitting the network."""

from __future__ import annotations

from police_scanner.radioreference import _freq_row, _hz, _site_row


def test_hz_conversion_handles_strings_and_floats():
    assert _hz("154.355") == 154_355_000
    assert _hz(154.355) == 154_355_000
    assert _hz("769.81875") == 769_818_750
    assert _hz(None) == 0
    assert _hz("garbage") == 0


def test_site_row_extracts_control_and_voice():
    raw = {
        "siteRfss": 1,
        "siteNumber": 94,
        "siteDescr": "Knightstown",
        "siteFreqs": [
            {"freq": 774.68125, "use": "Control"},
            {"freq": 769.81875, "use": "Voice"},
            {"freq": 770.50625, "use": ""},
        ],
    }
    site = _site_row(raw)
    assert site.name == "Knightstown"
    assert 774_681_250 in site.control_channels_hz
    assert 769_818_750 in site.voice_channels_hz
    assert 770_506_250 in site.voice_channels_hz


def test_freq_row_roundtrip():
    raw = {
        "freq": 155.625,
        "alpha": "RushSO",
        "descr": "Rush Sheriff",
        "tone": "179.9 PL",
        "mode": "FMN",
        "license": "WZX813",
    }
    fr = _freq_row(raw)
    assert fr.frequency_hz == 155_625_000
    assert fr.alpha == "RushSO"
    assert fr.tone == "179.9 PL"
    assert fr.license == "WZX813"
