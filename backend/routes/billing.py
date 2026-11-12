"""套餐与订单接口（SaaS 付费）。"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, HTTPException, Request
from pydantic import BaseModel, Field

from db import get_db
from services import accounts as acc
from services import billing

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutBody(BaseModel):
    plan: str = Field(default="pro", max_length=20)


@router.get('/plans')
async def plans():
    return {"plans": billing.public_plans(), "stripe": billing.stripe_enabled()}


@router.post('/checkout')
async def checkout(body: CheckoutBody, request: Request,
                   ge_session: str | None = Cookie(default=None)):
    """下单：有 Stripe 给支付链接；没有则建人工订单（销售开通）。"""
    async with get_db() as db:
        if not acc.saas_enabled():
            raise HTTPException(422, "当前为内网版，无需付费")
        row = await acc.session_account(db, ge_session or "")
        if not row:
            raise HTTPException(401, "请先登录")
        account = acc._public_account(row)
        try:
            order = await billing.create_order(db, int(account["id"]), body.plan,
                                               channel="stripe" if billing.stripe_enabled() else "manual")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    pay_url = None
    if billing.stripe_enabled():
        base = str(request.base_url).rstrip("/")
        pay_url = await billing.stripe_checkout_url(
            order, success_url=f"{base}/?paid=1", cancel_url=f"{base}/upgrade?cancel=1")
    return {"order": order, "pay_url": pay_url,
            "manual": pay_url is None,
            "message": ("请完成支付，支付成功后账号会自动升级。" if pay_url
                        else "订单已创建，我们会尽快联系你开通（也可在后台手动标记已付款）。")}


@router.get('/orders')
async def orders(ge_session: str | None = Cookie(default=None)):
    async with get_db() as db:
        row = await acc.session_account(db, ge_session or "")
        if not row:
            raise HTTPException(401, "请先登录")
        account = acc._public_account(row)
        return {"orders": await billing.list_orders(db, int(account["id"]))}
