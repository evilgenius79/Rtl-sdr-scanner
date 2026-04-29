"""Talkgroup list, hold/avoid, hidden flag toggles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..auth import require_user
from ..db import session
from ..models import Talkgroup, TalkgroupState
from ..rate_limit import api_limiter
from ..security import require_csrf

router = APIRouter(
    prefix="/api/talkgroups",
    tags=["talkgroups"],
    dependencies=[Depends(require_user), Depends(api_limiter)],
)


class TgUpdate(BaseModel):
    held: bool | None = None
    avoided: bool | None = None
    hidden: bool | None = None
    notes: str | None = None


@router.get("")
async def list_talkgroups(category: str | None = None) -> dict:
    async with session() as s:
        stmt = select(Talkgroup)
        if category:
            stmt = stmt.where(Talkgroup.category == category)
        rows = (await s.execute(stmt)).scalars().all()
        states = {
            ts.talkgroup_id: ts
            for ts in (await s.execute(select(TalkgroupState))).scalars().all()
        }
        out = []
        for tg in rows:
            ts = states.get(tg.id)
            out.append(
                {
                    "id": tg.id,
                    "tgid": tg.tgid,
                    "alpha": tg.alpha,
                    "description": tg.description,
                    "category": tg.category,
                    "tag": tg.tag,
                    "mode": tg.mode,
                    "encrypted": tg.encrypted,
                    "hidden": tg.hidden,
                    "held": bool(ts and ts.held),
                    "avoided": bool(ts and ts.avoided),
                }
            )
        return {"talkgroups": out}


@router.post("/{tg_id}", dependencies=[Depends(require_csrf)])
async def update_talkgroup(tg_id: int, payload: TgUpdate) -> dict:
    async with session() as s:
        tg = await s.get(Talkgroup, tg_id)
        if not tg:
            raise HTTPException(status_code=404, detail="Talkgroup not found")
        if payload.hidden is not None:
            tg.hidden = payload.hidden
            s.add(tg)
        if payload.held is not None or payload.avoided is not None or payload.notes is not None:
            ts = (
                await s.execute(select(TalkgroupState).where(TalkgroupState.talkgroup_id == tg_id))
            ).scalar_one_or_none()
            if ts is None:
                ts = TalkgroupState(talkgroup_id=tg_id)
            if payload.held is not None:
                ts.held = payload.held
            if payload.avoided is not None:
                ts.avoided = payload.avoided
            if payload.notes is not None:
                ts.notes = payload.notes[:255]
            s.add(ts)
    return {"ok": True}
