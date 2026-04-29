"""Application settings loaded from environment.

All settings are validated at process start; an invalid env file will fail loud
rather than degrading silently. Secrets are never logged.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Web app
    scanner_host: str = "127.0.0.1"
    scanner_port: int = Field(8080, ge=1, le=65535)
    scanner_public_url: str = "http://localhost:8080"
    scanner_secret_key: SecretStr
    scanner_admin_username: str = "admin"
    scanner_admin_password: SecretStr | None = None
    scanner_trust_proxy: bool = False
    scanner_ip_allowlist: str = ""

    # ── RadioReference
    rr_app_key: SecretStr | None = None
    rr_username: str | None = None
    rr_password: SecretStr | None = None

    # ── Storage
    scanner_data_dir: Path = Path("/var/lib/police-scanner")
    scanner_recordings_dir: Path = Path("/var/lib/police-scanner/recordings")
    scanner_db_path: Path = Path("/var/lib/police-scanner/db.sqlite")
    scanner_tr_config_path: Path = Path("/var/lib/police-scanner/trunk-recorder/config.json")
    scanner_tr_capture_dir: Path = Path("/var/lib/police-scanner/trunk-recorder/captures")

    # ── trunk-recorder
    scanner_tr_binary: Path = Path("/usr/local/bin/trunk-recorder")
    scanner_tr_systemd_unit: str = "trunk-recorder.service"

    # ── MQTT
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = Field(1883, ge=1, le=65535)
    mqtt_username: str = ""
    mqtt_password: SecretStr | None = None
    mqtt_topic_base: str = "trunk-recorder"

    # ── Optional
    whisper_model_path: Path | None = None
    recording_retention_days: int = Field(30, ge=0)

    @field_validator("scanner_secret_key")
    @classmethod
    def _strong_secret(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError(
                "SCANNER_SECRET_KEY must be at least 32 characters. "
                "Generate one: python -c 'import secrets; print(secrets.token_urlsafe(64))'"
            )
        return v

    @field_validator("scanner_ip_allowlist")
    @classmethod
    def _validate_cidrs(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        for cidr in (c.strip() for c in v.split(",")):
            ipaddress.ip_network(cidr, strict=False)
        return v

    @property
    def allowed_networks(self) -> list[ipaddress._BaseNetwork]:
        if not self.scanner_ip_allowlist:
            return []
        return [
            ipaddress.ip_network(c.strip(), strict=False)
            for c in self.scanner_ip_allowlist.split(",")
            if c.strip()
        ]

    @property
    def rr_configured(self) -> bool:
        return all([self.rr_app_key, self.rr_username, self.rr_password])


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton accessor — first call validates the env."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
