"""Async SQLite engine + session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from . import models  # noqa: F401  (import for side effects so tables register)
from .settings import get_settings


def _make_engine():
    settings = get_settings()
    settings.scanner_db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{settings.scanner_db_path}"
    return create_async_engine(
        url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 15},
        pool_pre_ping=True,
        poolclass=StaticPool,  # SQLite: single connection works best for low-volume async use
    )


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = _make_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


async def init_db() -> None:
    eng = engine()
    async with eng.begin() as conn:
        # Enable WAL + foreign keys at the SQLite level for resilience.
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conn.run_sync(SQLModel.metadata.create_all)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
