"""Security middleware: secure headers, IP allowlist, body size cap, CSRF for state changes."""

from __future__ import annotations

import hmac
import ipaddress
import logging
import secrets
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .settings import Settings, get_settings

logger = logging.getLogger("police_scanner.security")

CSRF_COOKIE = "scanner_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB; uploadScript hits a separate larger-body route


def client_ip(request: Request) -> str:
    """Extract the client IP, honoring X-Forwarded-For only when explicitly trusted."""
    settings = get_settings()
    if settings.scanner_trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SecureHeadersMiddleware(BaseHTTPMiddleware):
    """Restrictive headers safe for a same-origin SPA."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # Vue 3 from CDN — explicitly allow jsDelivr; everything else self-hosted.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "media-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        # Suppress server fingerprinting.
        response.headers.pop("server", None)
        return response


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Enforce SCANNER_IP_ALLOWLIST CIDRs. Empty allowlist = pass through."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        if not settings.scanner_ip_allowlist:
            return await call_next(request)
        ip_str = client_ip(request)
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise HTTPException(status_code=403, detail="Forbidden") from None
        for net in settings.allowed_networks:
            if ip in net:
                return await call_next(request)
        logger.warning("ip_allowlist_block ip=%s path=%s", ip_str, request.url.path)
        raise HTTPException(status_code=403, detail="Forbidden")


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject overlarge request bodies before they touch the app.

    The /api/tr/upload route (audio upload from trunk-recorder) sets a request
    state attribute that bypasses this — it has its own size cap.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/api/tr/"):
            return await call_next(request)
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        return await call_next(request)


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_cookie_kwargs(settings: Settings) -> dict:
    secure = settings.scanner_public_url.startswith("https://")
    return {
        "httponly": False,  # JS must read it
        "secure": secure,
        "samesite": "strict",
        "path": "/",
    }


async def require_csrf(request: Request) -> None:
    """Double-submit-cookie CSRF: header value must match cookie value."""
    if request.method in SAFE_METHODS:
        return
    # uploadScript / trunk-recorder talks to /api/tr/* over loopback only and uses
    # a shared secret instead of CSRF (see routes/tr_ingest.py).
    if request.url.path.startswith("/api/tr/"):
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")
