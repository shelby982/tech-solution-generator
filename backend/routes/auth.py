"""SaaS 账号接口：注册 / 登录 / 登出 / 当前账号。

除 `/api/auth/*` 外的业务接口在 `SAAS_MODE=1` 时通过 `current_account` 依赖取账号，
未登录直接 401（前端会跳到 /login）。
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from db import get_db
from services import accounts as acc

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    email: str = Field(..., max_length=200)
    password: str = Field(..., max_length=128)
    company: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)


class LoginBody(BaseModel):
    email: str = Field(..., max_length=200)
    password: str = Field(..., max_length=128)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=acc.SESSION_COOKIE, value=token, max_age=acc.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=bool(os_secure()),
    )


def os_secure() -> bool:
    """Cookie 的 Secure 标记：默认关闭。

    本地 http 调试时必须关闭（Secure cookie 在 http 下不会保存/回传）；
    线上 https 可显式 `SAAS_COOKIE_SECURE=1` 打开。
    """
    import os
    return os.environ.get("SAAS_COOKIE_SECURE", "0").strip() in ("1", "true", "True", "on")


@router.get('/enabled')
async def auth_enabled():
    """前端用它判断是否需要走登录（本地未开启 SaaS 时直接进应用）。"""
    return {"saas": acc.saas_enabled()}


@router.post('/register', status_code=201)
async def register(body: RegisterBody, response: Response):
    async with get_db() as db:
        try:
            account = await acc.register(db, email=body.email, password=body.password,
                                         company=body.company, phone=body.phone)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        token = await acc.create_session(db, int(account["id"]))
    _set_cookie(response, token)
    return {"account": account}


@router.post('/login')
async def login(body: LoginBody, response: Response):
    async with get_db() as db:
        try:
            account = await acc.login(db, email=body.email, password=body.password)
        except ValueError as exc:
            raise HTTPException(401, str(exc))
        token = await acc.create_session(db, int(account["id"]))
    _set_cookie(response, token)
    return {"account": account}


@router.post('/logout')
async def logout(response: Response, ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        await acc.destroy_session(db, ge_session or "")
    response.delete_cookie(acc.SESSION_COOKIE)
    return {"ok": True}


@router.get('/me')
async def me(ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        if not acc.saas_enabled():
            return {"saas": False, "account": None}
        row = await acc.session_account(db, ge_session or "")
        if not row:
            raise HTTPException(401, "未登录")
        account = acc._public_account(row)
        can, why = acc.can_create_project(account)
        usage = await acc.usage_summary(db, int(account["id"]))
        return {"saas": True, "account": account, "can_create_project": can, "reason": why,
                "usage": usage}


# ── 业务接口用的账号依赖 ──────────────────────────
async def maybe_account(ge_session: str | None = Cookie(default=None)):
    """未开启 SaaS 时返回 None（不需要登录）；开启时取会话账号，未登录返回 None。"""
    if not acc.saas_enabled():
        return None
    async with get_db() as db:
        row = await acc.session_account(db, ge_session or "")
        return acc._public_account(row) if row else None


async def require_account(ge_session: str | None = Cookie(default=None)):
    """开启 SaaS 时必须登录，否则 401。"""
    if not acc.saas_enabled():
        return None
    async with get_db() as db:
        row = await acc.session_account(db, ge_session or "")
        if not row:
            raise HTTPException(401, "未登录")
        return acc._public_account(row)
