"""Pytest configuration: provide an env file before settings is imported."""

from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture(autouse=True, scope="session")
def _bootstrap_env(tmp_path_factory):
    """Set required env vars so settings.Settings() can construct."""
    base = tmp_path_factory.mktemp("scanner_env")
    os.environ.setdefault("SCANNER_SECRET_KEY", secrets.token_urlsafe(64))
    os.environ.setdefault("SCANNER_DATA_DIR", str(base))
    os.environ.setdefault("SCANNER_RECORDINGS_DIR", str(base / "recordings"))
    os.environ.setdefault("SCANNER_DB_PATH", str(base / "db.sqlite"))
    os.environ.setdefault("SCANNER_TR_CONFIG_PATH", str(base / "trunk-recorder/config.json"))
    os.environ.setdefault("SCANNER_TR_CAPTURE_DIR", str(base / "trunk-recorder/captures"))
    os.environ.setdefault("MQTT_HOST", "127.0.0.1")
    os.environ.setdefault("MQTT_TOPIC_BASE", "trunk-recorder-test")
    os.environ.setdefault("SCANNER_PUBLIC_URL", "http://localhost:8080")
    yield base
