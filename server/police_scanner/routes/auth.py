"""Login / logout / current-user endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response

from ..auth import (
    SESSION_COOKIE,
    authenticate,
    cookie_kwargs,
    create_session,
    destroy_session,
    require_user,
)
from ..rate_limit import login_limiter
from ..security import (
    CSRF_COOKIE,
    client_ip,
    csrf_cookie_kwargs,
    issue_csrf_token,
    require_csrf,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def me(user=Depends(require_user)) -> dict:
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "last_login": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.post("/login", dependencies=[Depends(login_limiter)])
async def login(
    request: Request,
    response: Response,
    username: str = Form(..., min_length=1, max_length=64),
    password: str = Form(..., min_length=1, max_length=256),
) -> dict:
    user = await authenticate(username, password, ip=client_ip(request))
    token = await create_session(
        user, ip=client_ip(request), user_agent=request.headers.get("user-agent")
    )
    response.set_cookie(SESSION_COOKIE, token, **cookie_kwargs(request))
    csrf = issue_csrf_token()
    response.set_cookie(CSRF_COOKIE, csrf, **csrf_cookie_kwargs(request))
    return {"ok": True, "username": user.username, "csrf": csrf}


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await destroy_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}
