"""团队与多人协作：一个账号（owner）邀请成员共享项目。

简化模型（MVP）：
- `team_members(owner_id, member_id, member_email, role, status, invite_token)`
- 项目仍归属 **owner**；成员通过 `accessible_account_ids()` 拿到 owner 的项目
- 邀请流程：owner 填成员邮箱 → 已有账号直接 active；没有账号发邀请链接，注册后接受
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

INVITE_DAYS = 14


async def accessible_account_ids(db, account: dict | None) -> list[int]:
    """当前账号能看到的项目归属集合：自己 + 所属团队 owner。"""
    if not account:
        return []
    me = int(account["id"])
    ids = {me}
    try:
        owner_row = await (await db.execute(
            "SELECT owner_id FROM team_members WHERE member_id = ? AND status = 'active'",
            (me,))).fetchone()
        if owner_row:
            ids.add(int(owner_row["owner_id"]))
    except Exception:
        pass
    return sorted(ids)


async def list_members(db, owner_id: int) -> list[dict]:
    rows = await (await db.execute(
        "SELECT id, owner_id, member_id, member_email, role, status, created_at "
        "FROM team_members WHERE owner_id = ? ORDER BY id", (owner_id,))).fetchall()
    return [dict(r) for r in rows]


async def invite(db, owner_id: int, email: str, role: str = "member") -> dict:
    """邀请成员：有账号直接激活；没有账号则发邀请链接。"""
    from services import accounts as acc
    from services import mailer

    email_n = acc.normalize_email(email)
    if not acc.valid_email(email_n):
        raise ValueError("邮箱格式不正确")
    existing = await acc.get_account_by_email(db, email_n)
    if existing and int(existing["id"]) == int(owner_id):
        raise ValueError("不能邀请自己")
    row = await (await db.execute(
        "SELECT id, status FROM team_members WHERE owner_id = ? AND member_email = ?",
        (owner_id, email_n))).fetchone()
    token = secrets.token_urlsafe(24)
    expires = (datetime.now() + timedelta(days=INVITE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    if existing:
        if row:
            await db.execute(
                "UPDATE team_members SET member_id = ?, status = 'active', role = ?, invite_token = '' "
                "WHERE id = ?", (int(existing["id"]), role, int(row["id"])))
        else:
            await db.execute(
                "INSERT INTO team_members (owner_id, member_id, member_email, role, status) "
                "VALUES (?,?,?,?,'active')", (owner_id, int(existing["id"]), email_n, role))
        await db.commit()
        return {"status": "active", "email": email_n, "linked": True}
    # 未注册：发邀请链接
    await db.execute("INSERT INTO email_tokens (token, account_id, kind, email, expires_at) "
                     "VALUES (?,?,?,?,?)", (token, owner_id, "invite", email_n, expires))
    if row:
        await db.execute("UPDATE team_members SET status='invited', invite_token=? WHERE id=?",
                         (token, int(row["id"])))
    else:
        await db.execute(
            "INSERT INTO team_members (owner_id, member_email, role, status, invite_token) "
            "VALUES (?,?,?,? ,?)", (owner_id, email_n, role, "invited", token))
    await db.commit()
    link = f"/register?invite={token}"
    await mailer.send(email_n, "邀请你协作 · 应标文件生成系统",
                      f"你的同事邀请你加入团队协作。\n\n注册/登录后打开：{link}\n\n{INVITE_DAYS} 天内有效。")
    return {"status": "invited", "email": email_n, "invite_token": token, "link": link}


async def accept_invite(db, token: str, account_id: int) -> dict:
    """接受邀请（新注册账号调用）。"""
    from services import accounts as acc

    row = await (await db.execute(
        "SELECT * FROM email_tokens WHERE token = ? AND kind = 'invite'", (token,))).fetchone()
    if not row:
        raise ValueError("邀请链接无效或已过期")
    if row["used_at"]:
        raise ValueError("邀请已被使用")
    if str(row["expires_at"] or "") < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        raise ValueError("邀请已过期，请让同事重新邀请")
    owner_id = int(row["account_id"])
    email_n = str(row["email"] or "")
    me = await acc.get_account(db, account_id)
    if not me:
        raise ValueError("账号不存在")
    await db.execute(
        "UPDATE team_members SET member_id = ?, status = 'active', invite_token = '' "
        "WHERE owner_id = ? AND member_email = ?", (account_id, owner_id, email_n))
    await db.execute("UPDATE email_tokens SET used_at = CURRENT_TIMESTAMP WHERE token = ?", (token,))
    await db.commit()
    return {"ok": True, "owner_id": owner_id}


async def remove_member(db, owner_id: int, member_id: int) -> None:
    await db.execute("UPDATE team_members SET status = 'removed', member_id = NULL "
                     "WHERE owner_id = ? AND id = ?", (owner_id, member_id))
    await db.commit()
