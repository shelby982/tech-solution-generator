"""SaaS：套餐/订单、找回密码令牌、团队邀请与项目共享。"""
from __future__ import annotations

import asyncio

import aiosqlite
import pytest


async def _db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript('''
        CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE,
          password_hash TEXT DEFAULT '', company TEXT DEFAULT '', phone TEXT DEFAULT '',
          plan TEXT DEFAULT 'trial', trial_ends_at DATETIME, token_limit INTEGER DEFAULT 2000000,
          status TEXT DEFAULT 'active', created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE billing_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
          plan TEXT, amount_cents INTEGER DEFAULT 0, months INTEGER DEFAULT 12,
          status TEXT DEFAULT 'pending', channel TEXT DEFAULT 'manual', external_id TEXT DEFAULT '',
          note TEXT DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, paid_at DATETIME);
        CREATE TABLE email_tokens (token TEXT PRIMARY KEY, account_id INTEGER, kind TEXT,
          email TEXT DEFAULT '', expires_at DATETIME, used_at DATETIME,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE mail_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, to_email TEXT, subject TEXT DEFAULT '',
          body TEXT DEFAULT '', provider TEXT DEFAULT '', ok INTEGER DEFAULT 1, error TEXT DEFAULT '',
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE team_members (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER,
          member_id INTEGER, member_email TEXT DEFAULT '', role TEXT DEFAULT 'member',
          status TEXT DEFAULT 'invited', invite_token TEXT DEFAULT '',
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    ''')
    await conn.commit()
    return conn


def test_billing_plans_and_mark_paid():
    from services import billing

    async def run():
        db = await _db()
        await db.execute("INSERT INTO accounts (email) VALUES ('a@b.co')")
        await db.commit()
        assert billing.plan_price_cents('pro') == 9800 * 100
        order = await billing.create_order(db, 1, 'pro')
        assert order['status'] == 'pending' and order['amount_cents'] == 980000
        paid = await billing.mark_paid(db, int(order['id']), note='测试开通')
        assert paid['status'] == 'paid'
        acc_row = await (await db.execute("SELECT plan, token_limit, trial_ends_at FROM accounts WHERE id=1")).fetchone()
        assert acc_row['plan'] == 'pro'
        assert int(acc_row['token_limit']) == billing.plan_token_limit('pro')
        # 幂等
        again = await billing.mark_paid(db, int(order['id']))
        assert again['status'] == 'paid'
        await db.close()

    asyncio.run(run())


def test_team_invite_existing_and_new_member():
    from services import team

    async def run():
        db = await _db()
        await db.execute("INSERT INTO accounts (email, password_hash) VALUES ('owner@b.co','x'), ('mate@b.co','x')")
        await db.commit()
        # 已有账号 → 直接 active
        out = await team.invite(db, 1, 'MATE@b.co')
        assert out['status'] == 'active' and out['linked'] is True
        ids = await team.accessible_account_ids(db, {'id': 2})
        assert ids == [1, 2]      # 成员能看到 owner 的项目
        # 未注册 → 生成邀请
        out2 = await team.invite(db, 1, 'new@b.co')
        assert out2['status'] == 'invited' and out2['invite_token']
        # 新账号接受邀请
        await db.execute("INSERT INTO accounts (email, password_hash) VALUES ('new@b.co','x')")
        await db.commit()
        token_row = await (await db.execute("SELECT id FROM accounts WHERE email='new@b.co'")).fetchone()
        res = await team.accept_invite(db, out2['invite_token'], int(token_row['id']))
        assert res['ok'] and res['owner_id'] == 1
        ids2 = await team.accessible_account_ids(db, {'id': int(token_row['id'])})
        assert ids2 == [1, int(token_row['id'])]
        # 邀请不能用两次
        with pytest.raises(ValueError):
            await team.accept_invite(db, out2['invite_token'], int(token_row['id']))
        await db.close()

    asyncio.run(run())


def test_team_seat_limit_metadata():
    from services import billing

    assert billing.PLANS['team']['seats'] == 10
    assert billing.PLANS['trial']['seats'] == 1


def test_mailer_falls_back_to_outbox(monkeypatch):
    from services import mailer

    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert mailer.provider_name() == "outbox"
