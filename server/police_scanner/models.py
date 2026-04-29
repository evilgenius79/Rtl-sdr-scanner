"""SQLModel database schema.

Indexes are intentional — every column used in WHERE/ORDER BY in routes is indexed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class SystemType(StrEnum):
    P25 = "p25"
    SMARTNET = "smartnet"
    CONVENTIONAL = "conventional"
    CONVENTIONAL_P25 = "conventionalP25"
    CONVENTIONAL_DMR = "conventionalDMR"


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str  # argon2id
    is_admin: bool = True
    failed_login_count: int = 0
    locked_until: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime | None = None


class Session(SQLModel, table=True):
    """Server-side session store. Tokens are random 32-byte URL-safe strings."""

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True, max_length=64)
    user_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)
    user_agent: str | None = Field(default=None, max_length=256)
    ip: str | None = Field(default=None, max_length=64)


class System(SQLModel, table=True):
    """A trunked or conventional radio system imported from RadioReference."""

    id: int | None = Field(default=None, primary_key=True)
    rr_sid: int | None = Field(default=None, index=True)  # RadioReference system ID
    short_name: str = Field(index=True, max_length=32)
    description: str = Field(max_length=255)
    type: SystemType
    wacn: str | None = None
    sysid: str | None = None
    nac: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Site(SQLModel, table=True):
    """A trunked-system site (control + voice frequencies)."""

    id: int | None = Field(default=None, primary_key=True)
    system_id: int = Field(foreign_key="system.id", index=True)
    rr_site_id: int | None = Field(default=None, index=True)
    name: str = Field(max_length=128)
    rfss: int | None = None
    site_number: int | None = None
    control_channels: str  # CSV of Hz integers
    voice_channels: str  # CSV of Hz integers
    span_hz: int = 0


class Talkgroup(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("system_id", "tgid"),)

    id: int | None = Field(default=None, primary_key=True)
    system_id: int = Field(foreign_key="system.id", index=True)
    tgid: int = Field(index=True)
    alpha: str = Field(max_length=64)
    description: str = Field(default="", max_length=255)
    category: str = Field(default="", index=True, max_length=128)
    tag: str = Field(default="", max_length=64)
    mode: str = Field(default="D", max_length=4)  # D, A, T, DE, AE …
    encrypted: bool = False
    priority: int = 0
    hidden: bool = False  # encrypted are hidden by default per UX choice


class TalkgroupState(SQLModel, table=True):
    """Per-talkgroup user state — hold/avoid/priority — separate so reimports don't wipe it."""

    id: int | None = Field(default=None, primary_key=True)
    talkgroup_id: int = Field(foreign_key="talkgroup.id", unique=True)
    held: bool = False
    avoided: bool = False
    notes: str = Field(default="", max_length=255)


class ConventionalChannel(SQLModel, table=True):
    """Discrete conventional channel imported from RadioReference."""

    id: int | None = Field(default=None, primary_key=True)
    system_id: int = Field(foreign_key="system.id", index=True)
    frequency_hz: int = Field(index=True)
    alpha: str = Field(max_length=64)
    description: str = Field(default="", max_length=255)
    tone: str = Field(default="", max_length=32)  # PL, DPL, NAC, RAN
    mode: str = Field(default="FMN", max_length=8)  # FMN, P25, DMR, NXDN48
    license: str = Field(default="", max_length=32)


class Call(SQLModel, table=True):
    """A captured call. Audio file is on disk, referenced by `audio_path`."""

    __table_args__ = (UniqueConstraint("system_short_name", "call_id"),)

    id: int | None = Field(default=None, primary_key=True)
    system_short_name: str = Field(index=True, max_length=32)
    call_id: str = Field(index=True, max_length=64)  # trunk-recorder's call number
    talkgroup_tgid: int = Field(index=True)
    talkgroup_alpha: str = Field(default="", max_length=64)
    talkgroup_description: str = Field(default="", max_length=255)
    talkgroup_category: str = Field(default="", index=True, max_length=128)
    encrypted: bool = False
    frequency_hz: int = 0
    start_time: datetime = Field(index=True)
    end_time: datetime | None = None
    duration_seconds: float = 0.0
    source_unit_ids: str = Field(default="", max_length=512)  # CSV of unit IDs
    audio_path: str = Field(default="", max_length=512)
    audio_duration_seconds: float = 0.0
    audio_size_bytes: int = 0
    transcript: str | None = None
    transcript_status: str = Field(default="pending", max_length=16)


class AuditEvent(SQLModel, table=True):
    """Immutable audit trail for security-relevant actions."""

    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    actor: str = Field(default="system", max_length=64)
    ip: str | None = Field(default=None, max_length=64)
    action: str = Field(index=True, max_length=64)
    detail: str = Field(default="", max_length=512)
