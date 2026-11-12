"""运营后台接口（受 ADMIN_TOKEN 保护）。

用途：看注册账号与用量、手动延期试用、调整套餐/额度、停用账号。
安全：`ADMIN_TOKEN` 环境变量未设置时**整体关闭**（404），避免生产误开放。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services import accounts as acc

router = APIRouter(prefix="/admin", tags=["admin"])


def _token() -> str:
    return os.environ.get("ADMIN_TOKEN", "").strip()


def _check(token: str | None) -> None:
    expected = _token()
    if not expected:
        raise HTTPException(404, "后台未开启")
    if not token or not hmac_compare(token, expected):
        raise HTTPException(401, "管理口令不正确")


def hmac_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(str(a), str(b))


class ExtendBody(BaseModel):
    days: int = Field(default=14, ge=1, le=3650)


class PlanBody(BaseModel):
    plan: str = Field(default="pro", max_length=20)
    token_limit: int | None = Field(default=None, ge=0)


class StatusBody(BaseModel):
    status: str = Field(default="active", max_length=20)


@router.get('/enabled')
async def admin_enabled():
    return {"enabled": bool(_token())}


@router.get('/accounts')
async def list_accounts(x_admin_token: str | None = Header(default=None)):
    _check(x_admin_token)
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, email, company, phone, plan, trial_ends_at, status, created_at, token_limit "
            "FROM accounts ORDER BY id DESC LIMIT 200")).fetchall()
        out = []
        for row in rows:
            item = acc._public_account(row)
            usage = await acc.usage_summary(db, int(item["id"]))
            projects = await (await db.execute(
                "SELECT COUNT(*) FROM projects WHERE owner_account_id = ?", (int(item["id"]),))).fetchone()
            item["usage"] = usage
            item["project_count"] = int(projects[0] or 0) if projects else 0
            out.append(item)
        total = await (await db.execute("SELECT COUNT(*) FROM accounts")).fetchone()
    return {"accounts": out, "total": int(total[0] or 0) if total else 0}


@router.post('/accounts/{account_id}/extend')
async def extend_trial(account_id: int, body: ExtendBody,
                       x_admin_token: str | None = Header(default=None)):
    """手动延期试用（从"现在"或原到期时间中较晚者起算）。"""
    _check(x_admin_token)
    async with get_db() as db:
        row = await acc.get_account(db, account_id)
        if not row:
            raise HTTPException(404, "账号不存在")
        from datetime import datetime, timedelta
        base = datetime.now()
        try:
            current = datetime.strptime(str(row["trial_ends_at"] or ""), "%Y-%m-%d %H:%M:%S")
            if current > base:
                base = current
        except Exception:
            pass
        new_end = (base + timedelta(days=body.days)).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute("UPDATE accounts SET trial_ends_at = ? WHERE id = ?", (new_end, account_id))
        await db.commit()
        row = await acc.get_account(db, account_id)
    return {"account": acc._public_account(row)}


@router.post('/accounts/{account_id}/plan')
async def set_plan(account_id: int, body: PlanBody,
                   x_admin_token: str | None = Header(default=None)):
    _check(x_admin_token)
    async with get_db() as db:
        row = await acc.get_account(db, account_id)
        if not row:
            raise HTTPException(404, "账号不存在")
        if body.token_limit is not None:
            await db.execute("UPDATE accounts SET plan = ?, token_limit = ? WHERE id = ?",
                             (body.plan, int(body.token_limit), account_id))
        else:
            await db.execute("UPDATE accounts SET plan = ? WHERE id = ?", (body.plan, account_id))
        await db.commit()
        row = await acc.get_account(db, account_id)
    return {"account": acc._public_account(row)}


@router.post('/accounts/{account_id}/status')
async def set_status(account_id: int, body: StatusBody,
                     x_admin_token: str | None = Header(default=None)):
    _check(x_admin_token)
    if body.status not in ("active", "disabled"):
        raise HTTPException(422, "status 只能是 active / disabled")
    async with get_db() as db:
        row = await acc.get_account(db, account_id)
        if not row:
            raise HTTPException(404, "账号不存在")
        await db.execute("UPDATE accounts SET status = ? WHERE id = ?", (body.status, account_id))
        await db.commit()
        row = await acc.get_account(db, account_id)
    return {"account": acc._public_account(row)}


@router.get('/orders')
async def list_orders(x_admin_token: str | None = Header(default=None), limit: int = 100):
    _check(x_admin_token)
    from services import billing

    async with get_db() as db:
        orders = await billing.list_orders(db, None, limit)
        emails = {int(r["id"]): r["email"] for r in await (await db.execute(
            "SELECT id, email FROM accounts")).fetchall()}
    for order in orders:
        order["account_email"] = emails.get(int(order["account_id"]), "")
    return {"orders": orders, "stripe": billing.stripe_enabled()}


@router.post('/orders/{order_id}/mark-paid')
async def mark_order_paid(order_id: int, x_admin_token: str | None = Header(default=None)):
    _check(x_admin_token)
    from services import billing

    async with get_db() as db:
        try:
            order = await billing.mark_paid(db, order_id, note="后台手动开通")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        account = await acc.account_public(db, int(order["account_id"]))
    return {"order": order, "account": account}


@router.get('/outbox')
async def outbox(x_admin_token: str | None = Header(default=None), limit: int = 50):
    """未配置邮件服务时的"发件箱"：能直接看到验证/重置/邀请链接。"""
    _check(x_admin_token)
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, to_email, subject, body, provider, ok, error, created_at "
            "FROM mail_outbox ORDER BY id DESC LIMIT ?", (limit,))).fetchall()
    return {"items": [dict(r) for r in rows]}
