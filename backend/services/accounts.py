"""SaaS 账号与会话：注册 / 登录 / 试用期。

设计要点（MVP）：
- **密码**：`hashlib.scrypt`（标准库，无新依赖），每账号独立 salt；校验用 `hmac.compare_digest`。
- **会话**：随机 32 字节 token 存 `account_sessions`，前端只拿 httpOnly Cookie。
- **试用**：注册即开通 `plan=trial`，`trial_ends_at = 现在 + 14 天`；到期后仍可登录看数据，
  但不能新建项目（`can_create_project()` 返回 False）。
- **开关**：`SAAS_MODE=1` 时启用鉴权；本地开发/测试默认关闭，不影响既有接口。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

TRIAL_DAYS = int(os.environ.get("SAAS_TRIAL_DAYS", "14"))
SESSION_COOKIE = "ge_session"
SESSION_DAYS = int(os.environ.get("SAAS_SESSION_DAYS", "30"))
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def saas_enabled() -> bool:
    return os.environ.get("SAAS_MODE", "").strip() in ("1", "true", "True", "on")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt 哈希 → `scrypt$<salt_b64>$<hash_b64>`。"""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(str(password).encode("utf-8"), salt=salt,
                            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}".format(base64.b64encode(salt).decode(),
                                 base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, hash_b64 = str(stored).split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(str(password).encode("utf-8"), salt=salt,
                                n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected))
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def valid_password(password: str) -> tuple[bool, str]:
    text = str(password or "")
    if len(text) < 8:
        return False, "密码至少 8 位"
    if len(text) > 128:
        return False, "密码过长（最多 128 位）"
    if text.isdigit() or text.isalpha():
        return False, "密码需同时包含字母和数字"
    return True, ""


def _public_account(row) -> dict:
    """对外返回的账号信息（绝不带 password_hash）。"""
    data = dict(row)
    data.pop("password_hash", None)
    trial_ends = str(data.get("trial_ends_at") or "")
    days_left = None
    expired = False
    if trial_ends:
        try:
            end = datetime.strptime(trial_ends, "%Y-%m-%d %H:%M:%S")
            days_left = (end - _utc_now()).days
            expired = days_left < 0
        except Exception:
            pass
    data["trial_days_left"] = days_left
    data["trial_expired"] = expired
    data["plan"] = data.get("plan") or "trial"
    return data


async def get_account_by_email(db, email: str):
    return await (await db.execute("SELECT * FROM accounts WHERE email = ?",
                                   (normalize_email(email),))).fetchone()


async def get_account(db, account_id: int):
    return await (await db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))).fetchone()


async def register(db, *, email: str, password: str, company: str = "", phone: str = "") -> dict:
    """注册账号：校验 → 建号 → 开通试用。返回公开账号信息。"""
    email_n = normalize_email(email)
    if not valid_email(email_n):
        raise ValueError("邮箱格式不正确")
    ok, reason = valid_password(password)
    if not ok:
        raise ValueError(reason)
    if await get_account_by_email(db, email_n):
        raise ValueError("该邮箱已注册，请直接登录")
    trial_ends = (_utc_now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO accounts (email, password_hash, company, phone, plan, trial_ends_at, "
        "status, created_at) VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        (email_n, hash_password(password), str(company or "")[:120], str(phone or "")[:40],
         "trial", trial_ends, "active"))
    await db.commit()
    row = await get_account_by_email(db, email_n)
    return _public_account(row)


async def login(db, *, email: str, password: str) -> dict:
    row = await get_account_by_email(db, email)
    if not row or not verify_password(password, row["password_hash"] or ""):
        raise ValueError("邮箱或密码不正确")
    if str(row["status"] or "") != "active":
        raise ValueError("账号已停用，请联系管理员")
    return _public_account(row)


async def create_session(db, account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (_utc_now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO account_sessions (token, account_id, expires_at, created_at) "
        "VALUES (?,?,?,CURRENT_TIMESTAMP)", (token, account_id, expires))
    await db.commit()
    return token


async def session_account(db, token: str):
    """按 Cookie token 取账号；过期/无效返回 None。"""
    token = str(token or "").strip()
    if not token:
        return None
    row = await (await db.execute(
        "SELECT s.expires_at, a.* FROM account_sessions s JOIN accounts a ON a.id = s.account_id "
        "WHERE s.token = ?", (token,))).fetchone()
    if not row:
        return None
    expires = str(row["expires_at"] or "")
    if expires and expires < _now():
        try:
            await db.execute("DELETE FROM account_sessions WHERE token = ?", (token,))
            await db.commit()
        except Exception:
            pass
        return None
    return row


async def destroy_session(db, token: str) -> None:
    if not str(token or "").strip():
        return
    await db.execute("DELETE FROM account_sessions WHERE token = ?", (token,))
    await db.commit()


async def account_public(db, account_id: int) -> dict | None:
    row = await get_account(db, account_id)
    return _public_account(row) if row else None


def can_create_project(account: dict | None) -> tuple[bool, str]:
    """试用到期后不允许新建项目（数据仍可读、可导出）。"""
    if not account:
        return False, "请先登录"
    if account.get("trial_expired"):
        return False, "试用期已结束，请升级后继续新建项目"
    return True, ""
