"""团队协作接口。"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services import accounts as acc
from services import billing
from services import team as team_service

router = APIRouter(prefix="/team", tags=["team"])


class InviteBody(BaseModel):
    email: str = Field(..., max_length=200)
    role: str = Field(default="member", max_length=20)


class AcceptBody(BaseModel):
    token: str = Field(..., max_length=200)


async def _me(db, ge_session: str | None) -> dict:
    row = await acc.session_account(db, ge_session or "")
    if not row:
        raise HTTPException(401, "请先登录")
    return acc._public_account(row)


@router.get('/members')
async def members(ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        me = await _me(db, ge_session)
        items = await team_service.list_members(db, int(me["id"]))
        # 我作为成员加入的团队（看 owner 是谁）
        joined = await (await db.execute(
            "SELECT tm.owner_id, a.email AS owner_email FROM team_members tm "
            "JOIN accounts a ON a.id = tm.owner_id "
            "WHERE tm.member_id = ? AND tm.status = 'active'", (int(me["id"]),))).fetchall()
        account = await acc.get_account(db, int(me["id"]))
        limit = billing.PLANS.get(str(account["plan"]), {}).get("seats", 1) if account else 1
    return {"members": items, "joined": [dict(r) for r in joined],
            "seats": limit, "used": len(items)}


@router.post('/invite')
async def invite(body: InviteBody, ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        me = await _me(db, ge_session)
        account = await acc.get_account(db, int(me["id"]))
        limit = billing.PLANS.get(str(account["plan"]), {}).get("seats", 1)
        current = await team_service.list_members(db, int(me["id"]))
        if len(current) >= limit:
            raise HTTPException(403, f"当前套餐最多 {limit} 个成员席位，请升级团队版")
        try:
            result = await team_service.invite(db, int(me["id"]), body.email, body.role)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    return result


@router.post('/accept')
async def accept(body: AcceptBody, ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        me = await _me(db, ge_session)
        try:
            return await team_service.accept_invite(db, body.token, int(me["id"]))
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@router.delete('/members/{member_row_id}')
async def remove(member_row_id: int, ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        me = await _me(db, ge_session)
        await team_service.remove_member(db, int(me["id"]), member_row_id)
    return {"ok": True}
