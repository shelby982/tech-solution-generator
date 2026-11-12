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


# ── 找回密码 / 邮箱验证 ───────────────────────────

import secrets as _secrets
from datetime import datetime as _dt, timedelta as _td


class ForgotBody(BaseModel):
    email: str = Field(..., max_length=200)


class ResetBody(BaseModel):
    token: str = Field(..., max_length=200)
    password: str = Field(..., max_length=128)


async def _issue_token(db, account_id: int, kind: str, email: str) -> str:
    token = _secrets.token_urlsafe(24)
    expires = (_dt.now() + _td(hours=2 if kind == "reset" else 24)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute("INSERT INTO email_tokens (token, account_id, kind, email, expires_at) "
                     "VALUES (?,?,?,?,?)", (token, account_id, kind, email, expires))
    await db.commit()
    return token


@router.post('/forgot')
async def forgot(body: ForgotBody):
    """申请重置密码：无论邮箱是否存在都返回成功（不泄露账号是否存在）。"""
    from services import mailer

    email_n = acc.normalize_email(body.email)
    async with get_db() as db:
        row = await acc.get_account_by_email(db, email_n)
        link_sent = False
        if row:
            token = await _issue_token(db, int(row["id"]), "reset", email_n)
            link = f"/reset?token={token}"
            await mailer.send(email_n, "重置密码 · 应标文件生成系统",
                              f"点此重置密码（2 小时内有效）：{link}\n\n如非本人操作请忽略。")
            link_sent = True
    return {"ok": True, "sent": link_sent,
            "message": "如果该邮箱已注册，重置链接已发送（也可能在运营后台 outbox 里）。"}


@router.post('/reset')
async def reset(body: ResetBody):
    ok, reason = acc.valid_password(body.password)
    if not ok:
        raise HTTPException(422, reason)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM email_tokens WHERE token = ? AND kind = 'reset'", (body.token,))).fetchone()
        if not row or row["used_at"]:
            raise HTTPException(422, "链接无效或已使用")
        if str(row["expires_at"] or "") < _dt.now().strftime("%Y-%m-%d %H:%M:%S"):
            raise HTTPException(422, "链接已过期，请重新申请")
        await db.execute("UPDATE accounts SET password_hash = ? WHERE id = ?",
                         (acc.hash_password(body.password), int(row["account_id"])))
        await db.execute("UPDATE email_tokens SET used_at = CURRENT_TIMESTAMP WHERE token = ?",
                         (body.token,))
        await db.execute("DELETE FROM account_sessions WHERE account_id = ?", (int(row["account_id"]),))
        await db.commit()
    return {"ok": True}


@router.post('/verify/send')
async def send_verify(ge_session: str | None = Cookie(default=None)):
    from services import mailer

    async with get_db() as db:
        row = await acc.session_account(db, ge_session or "")
        if not row:
            raise HTTPException(401, "请先登录")
        account = acc._public_account(row)
        token = await _issue_token(db, int(account["id"]), "verify", account["email"])
    await mailer.send(account["email"], "验证邮箱 · 应标文件生成系统",
                      f"验证链接（24 小时内有效）：/api/auth/verify?token={token}")
    return {"ok": True}


@router.get('/verify')
async def verify(token: str = ""):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM email_tokens WHERE token = ? AND kind = 'verify'", (str(token),))).fetchone()
        if not row or row["used_at"]:
            raise HTTPException(422, "链接无效或已使用")
        await db.execute("UPDATE email_tokens SET used_at = CURRENT_TIMESTAMP WHERE token = ?",
                         (str(token),))
        await db.commit()
    return {"ok": True, "message": "邮箱已验证"}
