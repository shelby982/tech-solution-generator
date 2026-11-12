"""SaaS 账号：注册 / 登录 / 会话 / 试用限制 / 项目隔离。"""
from __future__ import annotations

import asyncio
import os

import aiosqlite
import pytest


@pytest.fixture()
def db():
    async def make():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.executescript('''
            CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '', plan TEXT NOT NULL DEFAULT 'trial',
              trial_ends_at DATETIME, status TEXT NOT NULL DEFAULT 'active',
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE account_sessions (token TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
              expires_at DATETIME NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        ''')
        await conn.commit()
        return conn
    return asyncio.run(make())


def test_password_hash_and_verify():
    from services.accounts import hash_password, verify_password

    stored = hash_password("abc12345")
    assert stored.startswith("scrypt$")
    assert verify_password("abc12345", stored) is True
    assert verify_password("abc12346", stored) is False
    assert verify_password("abc12345", "broken") is False
    # 同密码两次哈希不同（不同 salt）
    assert hash_password("abc12345") != stored


def test_password_and_email_rules():
    from services.accounts import valid_email, valid_password

    assert valid_email("a@b.co") and not valid_email("a@b")
    ok, why = valid_password("12345678")
    assert not ok and "字母" in why
    ok, why = valid_password("abcdefgh")
    assert not ok
    ok, why = valid_password("abc12345")
    assert ok and why == ""


def test_register_login_session_and_trial(db):
    from services import accounts as acc

    async def run():
        account = await acc.register(db, email="Boss@Example.COM", password="abc12345",
                                     company="XX 科技", phone="138")
        assert account["email"] == "boss@example.com"      # 归一化
        assert account["plan"] == "trial"
        assert account["trial_expired"] is False
        assert account["trial_days_left"] >= 13
        # 重复注册
        with pytest.raises(ValueError):
            await acc.register(db, email="boss@example.com", password="abc12345")
        # 登录
        logged = await acc.login(db, email="boss@example.com", password="abc12345")
        assert logged["id"] == account["id"]
        with pytest.raises(ValueError):
            await acc.login(db, email="boss@example.com", password="wrong123")
        # 会话
        token = await acc.create_session(db, int(account["id"]))
        row = await acc.session_account(db, token)
        assert row and row["email"] == "boss@example.com"
        await acc.destroy_session(db, token)
        assert await acc.session_account(db, token) is None

    asyncio.run(run())


def test_trial_expired_blocks_create_project():
    from services.accounts import can_create_project

    assert can_create_project(None)[0] is False
    assert can_create_project({"trial_expired": False}) == (True, "")
    ok, why = can_create_project({"trial_expired": True})
    assert ok is False and "试用期已结束" in why


def test_saas_mode_flag(monkeypatch):
    from services import accounts as acc

    monkeypatch.setenv("SAAS_MODE", "1")
    assert acc.saas_enabled() is True
    monkeypatch.setenv("SAAS_MODE", "0")
    assert acc.saas_enabled() is False
    monkeypatch.delenv("SAAS_MODE", raising=False)
    assert acc.saas_enabled() is False
