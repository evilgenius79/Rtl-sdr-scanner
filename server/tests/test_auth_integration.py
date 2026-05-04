"""End-to-end auth flow against an in-process FastAPI app.

Catches the bugs that unit tests missed:
  * SQLite returns naive datetimes; comparison with aware now() crashed sessions.
  * AsyncSession.merge is a coroutine — old code added the coroutine to the session.
  * CSRF middleware blocking login.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANNER_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("SCANNER_ADMIN_PASSWORD", "test-password-1234")
    monkeypatch.setenv("SCANNER_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    # Force settings to reload with our overrides.
    from police_scanner import settings as settings_mod

    settings_mod._settings = None
    # And the engine, since SCANNER_DB_PATH changed.
    from police_scanner import db as db_mod

    db_mod._engine = None
    db_mod._sessionmaker = None

    from police_scanner.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Bring the app up via lifespan so ensure_admin_user runs.
        async with app.router.lifespan_context(app):
            yield ac


@pytest.mark.asyncio
async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_login_then_me(client):
    r = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "test-password-1234"},
    )
    assert r.status_code == 200, r.text
    assert "scanner_session" in r.cookies
    me = await client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    r = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong-on-purpose"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_returns_401(client):
    r = await client.post(
        "/api/auth/login",
        data={"username": "ghost", "password": "anything"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_lockout_after_repeated_failures(client):
    # Five wrong attempts → next attempt must be 429 (locked) even with right password.
    for _ in range(5):
        r = await client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "test-password-1234"},
    )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_protected_route_requires_login(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_security_headers_present_on_html(client):
    r = await client.get("/login")
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
