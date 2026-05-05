"""FastAPI entry point.

Composes: middleware → routes → background tasks (MQTT bridge, retention sweep).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.types import Scope

from .auth import ensure_admin_user
from .db import init_db
from .mqtt_bridge import bridge as mqtt_bridge
from .retention import retention_sweeper
from .routes.auth import router as auth_router
from .routes.calls import router as calls_router
from .routes.setup import router as setup_router
from .routes.status import router as status_router
from .routes.talkgroups import router as talkgroups_router
from .routes.tr_ingest import router as tr_ingest_router
from .security import (
    BodySizeLimitMiddleware,
    IPAllowlistMiddleware,
    SecureHeadersMiddleware,
    require_csrf,
)
from .settings import get_settings
from .ws_hub import hub

logger = logging.getLogger("police_scanner")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("starting police-scanner v0.1.0")
    settings.scanner_data_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    await ensure_admin_user()
    await mqtt_bridge.start()
    sweeper_task = asyncio.create_task(retention_sweeper())
    try:
        yield
    finally:
        sweeper_task.cancel()
        await mqtt_bridge.stop()


app = FastAPI(
    title="Police Scanner",
    version="0.1.0",
    docs_url=None,           # Disable /docs and /redoc on public deploy
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Middleware order: outermost first (executed last on response).
app.add_middleware(SecureHeadersMiddleware)
app.add_middleware(IPAllowlistMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    # Pre-route CSRF check for state-changing methods (login is exempt — see require_csrf logic).
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.url.path == "/api/auth/login":
            pass  # login itself doesn't have a session yet
        else:
            try:
                await require_csrf(request)
            except Exception:
                return JSONResponse(status_code=403, content={"detail": "CSRF check failed"})
    return await call_next(request)


class RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that disables caching for first-party assets.

    Browsers (and Cloudflare) cache /static/*.js and *.css aggressively.
    After a deploy that lands at the same URL, users would otherwise keep
    running stale JS until they hard-refreshed. Setting no-cache forces a
    cheap ETag revalidation on every request — the browser sends If-None-Match
    and the server returns 304 if unchanged. Vendored libs under
    /static/js/vendor/ are content-addressable enough to stay cacheable.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if not path.startswith("js/vendor/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# Static + templates
app.mount("/static", RevalidatingStaticFiles(directory=str(HERE / "static")), name="static")


@app.get("/")
async def root(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "app.html", {})


@app.get("/login")
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {})


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


# WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.serve(ws)


# REST routers
app.include_router(auth_router)
app.include_router(setup_router)
app.include_router(calls_router)
app.include_router(talkgroups_router)
app.include_router(status_router)
app.include_router(tr_ingest_router)


def cli() -> None:  # entry point used by pyproject scripts
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "police_scanner.main:app",
        host=s.scanner_host,
        port=s.scanner_port,
        proxy_headers=s.scanner_trust_proxy,
        forwarded_allow_ips="127.0.0.1" if s.scanner_trust_proxy else None,
        server_header=False,
    )
