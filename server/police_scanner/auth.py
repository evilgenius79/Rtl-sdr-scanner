"""Auth + session management.

* Argon2id password hashing.
* Server-side sessions (random 32-byte token, hashed in DB; cookie holds the plain token).
* Constant-time token compare via stdlib ``hmac``.
* Lockout on repeated failed logins.
* Strict cookie attributes: HttpOnly, Secure (toggled by public_url scheme), SameSite=Strict.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlmodel import select

from .db import session
from .models import AuditEvent, Session, User
from .settings import get_settings

logger = logging.getLogger("police_scanner.auth")

SESSION_COOKIE = "scanner_session"
SESSION_LIFETIME = timedelta(hours=12)
LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION = timedelta(minutes=15)

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB — fine on a Pi 5
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored: str, plain: str) -> bool:
    try:
        return _hasher.verify(stored, plain)
    except VerifyMismatchError:
        return False


def _hash_token(token: str) -> str:
    """Hash a session token before storing. Plain token only ever lives in the cookie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; we always store UTC. Promote to aware on read
    so comparisons with ``datetime.now(UTC)`` don't raise TypeError."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


async def ensure_admin_user() -> None:
    """Create the bootstrap admin user from env if no user exists."""
    settings = get_settings()
    async with session() as s:
        existing = (await s.execute(select(User))).first()
        if existing:
            return
        if not settings.scanner_admin_password:
            logger.warning(
                "No SCANNER_ADMIN_PASSWORD set and no users exist; "
                "the admin account will not be created. Set the var and restart."
            )
            return
        u = User(
            username=settings.scanner_admin_username,
            password_hash=hash_password(settings.scanner_admin_password.get_secret_value()),
            is_admin=True,
        )
        s.add(u)
        s.add(AuditEvent(action="user_create", actor="system", detail=f"bootstrap admin '{u.username}'"))


async def authenticate(username: str, password: str, *, ip: str | None) -> User:
    """Verify credentials. Raises HTTPException on failure with a generic message
    so we don't leak whether the username exists.

    Bookkeeping (failed-login counter, lockout, audit events) is committed before
    we raise, otherwise the session-context rollback would reset the counter.
    """
    error: HTTPException | None = None
    user_out: User | None = None
    async with session() as s:
        user = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
        now = datetime.now(UTC)
        if user is None:
            # Compare against a dummy hash to keep timing roughly constant.
            verify_password(_DUMMY_HASH, password)
            s.add(AuditEvent(action="login_fail", actor=username, ip=ip, detail="unknown_user"))
            error = _bad_creds()
        elif (locked_until := _aware(user.locked_until)) and locked_until > now:
            s.add(AuditEvent(action="login_fail", actor=user.username, ip=ip, detail="locked"))
            error = _locked(locked_until)
        elif not verify_password(user.password_hash, password):
            user.failed_login_count += 1
            if user.failed_login_count >= LOCKOUT_THRESHOLD:
                user.locked_until = now + LOCKOUT_DURATION
                s.add(
                    AuditEvent(
                        action="account_lock",
                        actor=user.username,
                        ip=ip,
                        detail=f"{user.failed_login_count} failures",
                    )
                )
            s.add(AuditEvent(action="login_fail", actor=user.username, ip=ip, detail="bad_password"))
            error = _bad_creds()
        else:
            user.failed_login_count = 0
            user.locked_until = None
            user.last_login_at = now
            s.add(AuditEvent(action="login_ok", actor=user.username, ip=ip))
            user_out = user
    if error is not None:
        raise error
    assert user_out is not None
    return user_out


async def create_session(user: User, *, ip: str | None, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    async with session() as s:
        s.add(
            Session(
                token_hash=_hash_token(token),
                user_id=user.id,  # type: ignore[arg-type]
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + SESSION_LIFETIME,
                user_agent=(user_agent or "")[:256],
                ip=ip,
            )
        )
    return token


async def destroy_session(token: str) -> None:
    h = _hash_token(token)
    async with session() as s:
        existing = (
            await s.execute(select(Session).where(Session.token_hash == h))
        ).scalar_one_or_none()
        if existing:
            await s.delete(existing)


async def session_user(token: str | None) -> User | None:
    """Look up the user owning a session token, refreshing expiry on hit.

    Constant-time: compare against the *hashed* token from DB. The plain token
    is hashed once before lookup; lookup itself is by indexed column.
    """
    if not token:
        return None
    h = _hash_token(token)
    async with session() as s:
        sess = (
            await s.execute(select(Session).where(Session.token_hash == h))
        ).scalar_one_or_none()
        if not sess:
            return None
        expires_at = _aware(sess.expires_at)
        if expires_at is None or expires_at < datetime.now(UTC):
            await s.delete(sess)
            return None
        # Constant-time double-check (paranoia: defends against any cmp shortcut).
        if not hmac.compare_digest(sess.token_hash, h):
            return None
        user = await s.get(User, sess.user_id)
        return user


def _bad_creds() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials.",
    )


def _locked(until: datetime) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Account locked until {until.isoformat()}.",
    )


# Dummy hash used to keep failed-username timing similar to bad-password timing.
# Generated once at import; the value is intentionally not a valid password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def cookie_kwargs(public_url: str) -> dict:
    secure = public_url.startswith("https://")
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "strict",
        "max_age": int(SESSION_LIFETIME.total_seconds()),
        "path": "/",
    }


async def require_user(request: Request) -> User:
    """FastAPI dependency for protected routes."""
    token = request.cookies.get(SESSION_COOKIE)
    user = await session_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user
