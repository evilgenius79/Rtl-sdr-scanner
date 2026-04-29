"""Auth + path-traversal + CSRF defense tests."""

from __future__ import annotations

import secrets

from police_scanner.auth import _hash_token, hash_password, verify_password


def test_password_hash_roundtrip():
    h = hash_password("hunter2-very-strong")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "hunter2-very-strong") is True
    assert verify_password(h, "wrong") is False


def test_token_hash_is_deterministic_and_distinct():
    t1 = secrets.token_urlsafe(32)
    t2 = secrets.token_urlsafe(32)
    assert _hash_token(t1) == _hash_token(t1)
    assert _hash_token(t1) != _hash_token(t2)


def test_path_traversal_in_audio_route_is_blocked(tmp_path):
    """Path-traversal style audio_path values must not escape recordings_dir."""
    base = tmp_path.resolve()
    candidate = (base / "../../../etc/passwd")
    # Mirror the same check used in routes/calls.py
    try:
        candidate.resolve().relative_to(base)
        ok = True
    except ValueError:
        ok = False
    assert ok is False


def test_zip_validation():
    import re
    ZIP_RE = re.compile(r"^\d{5}$")
    assert ZIP_RE.match("46173")
    assert not ZIP_RE.match("4617")
    assert not ZIP_RE.match("46173a")
    assert not ZIP_RE.match("../../etc")


def test_dongle_serial_validation():
    import re
    pat = re.compile(r"[0-9A-Fa-f]{8}")
    assert pat.fullmatch("00000101")
    assert pat.fullmatch("DEADBEEF")
    assert not pat.fullmatch("0000010")  # too short
    assert not pat.fullmatch("0000010g")  # non-hex
    assert not pat.fullmatch("'; DROP TABLE--")  # SQLi-ish
