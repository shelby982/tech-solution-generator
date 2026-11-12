"""套餐与订单：试用 → 付费升级。

设计：**没有支付凭据也能闭环** ——
  1. 用户点「升级」→ 建订单（pending）
  2. 若配置了 `STRIPE_SECRET_KEY` → 生成 Stripe Checkout 链接（真实支付）
     否则 → 提示"销售会联系你"，运营后台可手动标记已付款 → 自动升级
  3. 付款完成（webhook 或后台标记）→ `activate()` 升级 plan、延长到期、提升额度

价格集中在 PLANS，方便改价。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

PLANS: dict[str, dict] = {
    "trial": {"label": "试用版", "price_yuan": 0, "months": 0,
              "token_limit": 2_000_000, "seats": 1,
              "features": ["全部编辑/评审功能", "1 个项目", "近 30 天 200 万 token"]},
    "pro": {"label": "专业版", "price_yuan": 9800, "months": 12,
            "token_limit": 50_000_000, "seats": 1,
            "features": ["不限项目数", "全部评审能力", "年 5000 万 token", "Word 原生交付"]},
    "team": {"label": "团队版", "price_yuan": 29800, "months": 12,
             "token_limit": 200_000_000, "seats": 10,
             "features": ["专业版全部能力", "10 个成员席位", "项目共享协作", "年 2 亿 token"]},
}


def plan_price_cents(plan: str) -> int:
    item = PLANS.get(plan) or {}
    return int(item.get("price_yuan") or 0) * 100


def plan_token_limit(plan: str) -> int:
    item = PLANS.get(plan) or {}
    return int(item.get("token_limit") or 0)


def plan_months(plan: str) -> int:
    item = PLANS.get(plan) or {}
    return int(item.get("months") or 12)


def public_plans() -> list[dict]:
    out = []
    for key, item in PLANS.items():
        out.append({"id": key, **item})
    return out


async def create_order(db, account_id: int, plan: str, channel: str = "manual") -> dict:
    if plan not in PLANS or plan == "trial":
        raise ValueError("套餐不存在")
    amount = plan_price_cents(plan)
    months = plan_months(plan)
    cur = await db.execute(
        "INSERT INTO billing_orders (account_id, plan, amount_cents, months, status, channel) "
        "VALUES (?,?,?,?,'pending',?)", (account_id, plan, amount, months, channel))
    await db.commit()
    row = await (await db.execute("SELECT * FROM billing_orders WHERE id = ?",
                                  (cur.lastrowid,))).fetchone()
    return dict(row)


async def list_orders(db, account_id: int | None = None, limit: int = 100) -> list[dict]:
    if account_id:
        rows = await (await db.execute(
            "SELECT * FROM billing_orders WHERE account_id = ? ORDER BY id DESC LIMIT ?",
            (account_id, limit))).fetchall()
    else:
        rows = await (await db.execute(
            "SELECT * FROM billing_orders ORDER BY id DESC LIMIT ?", (limit,))).fetchall()
    return [dict(r) for r in rows]


async def mark_paid(db, order_id: int, *, external_id: str = "", note: str = "") -> dict:
    """标记订单已付款并升级账号（幂等：已 paid 直接返回）。"""
    order = await (await db.execute("SELECT * FROM billing_orders WHERE id = ?", (order_id,))).fetchone()
    if not order:
        raise ValueError("订单不存在")
    order = dict(order)
    if str(order["status"]) == "paid":
        return order
    await db.execute(
        "UPDATE billing_orders SET status='paid', paid_at=CURRENT_TIMESTAMP, external_id=?, note=? "
        "WHERE id = ?", (str(external_id)[:120], str(note)[:300], order_id))
    await activate(db, int(order["account_id"]), str(order["plan"]), commit=False)
    await db.commit()
    row = await (await db.execute("SELECT * FROM billing_orders WHERE id = ?", (order_id,))).fetchone()
    return dict(row)


async def activate(db, account_id: int, plan: str, *, commit: bool = True) -> dict:
    """把账号切到某付费套餐：延长试用到期 + 提升 token 上限。"""
    if plan not in PLANS:
        raise ValueError("套餐不存在")
    months = plan_months(plan) or 12
    row = await (await db.execute("SELECT trial_ends_at FROM accounts WHERE id = ?",
                                  (account_id,))).fetchone()
    base = datetime.now()
    try:
        current = datetime.strptime(str(row["trial_ends_at"] or ""), "%Y-%m-%d %H:%M:%S")
        if current > base:
            base = current
    except Exception:
        pass
    new_end = (base + timedelta(days=30 * months)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "UPDATE accounts SET plan = ?, token_limit = ?, trial_ends_at = ?, status = 'active' WHERE id = ?",
        (plan, plan_token_limit(plan), new_end, account_id))
    if commit:
        await db.commit()
    return {"account_id": account_id, "plan": plan, "trial_ends_at": new_end,
            "token_limit": plan_token_limit(plan)}


def stripe_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())


async def stripe_checkout_url(order: dict, success_url: str, cancel_url: str) -> str | None:
    """有 Stripe 凭据则创建 Checkout Session 返回链接；没有返回 None（走人工开通）。"""
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        return None
    import json
    import urllib.parse
    import urllib.request

    form = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "cny",
        "line_items[0][price_data][unit_amount]": str(int(order["amount_cents"])),
        "line_items[0][price_data][product_data][name]": f"应标文件生成系统 · {order['plan']}",
        "client_reference_id": str(order["id"]),
        "metadata[order_id]": str(order["id"]),
    }
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions", data=data, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
            payload = json.loads(resp.read() or b"{}")
        return str(payload.get("url") or "") or None
    except Exception:
        return None
