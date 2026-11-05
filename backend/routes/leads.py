"""预约演示线索：落地页表单提交入口。

设计要点：
- 蜜罐字段 `website`（页面上不可见）：机器人会填，直接静默成功、不落库；
- 同一 IP 60 秒内最多 3 次，超出返回 429（进程内计数，量级与投放量吻合）；
- 只做宽松校验（姓名/公司必填、联系方式 ≥5 字符），不拦截真实用户；
- 落库到 `leads` 表，UTM 与 referer 一并保留，便于区分投放来源。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from db import get_db

router = APIRouter(tags=["leads"])

_WINDOW_SECONDS = 60.0
_MAX_PER_WINDOW = 3
_hits: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    """同一 IP 窗口内计数；先清理过期记录再判断。"""
    now = time.time()
    bucket = _hits[ip]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _MAX_PER_WINDOW:
        return True
    bucket.append(now)
    return False


class LeadIn(BaseModel):
    name: str = Field(default="", max_length=64)
    company: str = Field(default="", max_length=64)
    contact: str = Field(default="", max_length=64)
    topic: str = Field(default="", max_length=64)
    utm_source: str = Field(default="", max_length=64)
    utm_medium: str = Field(default="", max_length=64)
    utm_campaign: str = Field(default="", max_length=64)
    utm_content: str = Field(default="", max_length=64)
    referer: str = Field(default="", max_length=256)
    website: str = Field(default="", max_length=256)


@router.post("/leads", status_code=201)
async def create_lead(payload: LeadIn, request: Request):
    # 蜜罐命中：假装成功，避免给机器人反馈
    if payload.website.strip():
        return {"ok": True, "id": None}

    fields = {
        k: (getattr(payload, k) or "").strip()
        for k in ("name", "company", "contact", "topic", "utm_source",
                  "utm_medium", "utm_campaign", "utm_content", "referer")
    }
    if not fields["name"] or not fields["company"] or len(fields["contact"]) < 5:
        return JSONResponse(
            status_code=422,
            content={"detail": "姓名、公司、联系方式必填（联系方式至少 5 个字符）"},
        )

    ip = request.client.host if request.client else ""
    if _rate_limited(ip):
        return JSONResponse(status_code=429, content={"detail": "提交过于频繁，请稍后再试"})

    user_agent = request.headers.get("user-agent", "")[:256]
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO leads (name, company, contact, topic, utm_source, utm_medium,
                   utm_campaign, utm_content, referer, user_agent, ip)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fields["name"], fields["company"], fields["contact"], fields["topic"],
             fields["utm_source"], fields["utm_medium"], fields["utm_campaign"],
             fields["utm_content"], fields["referer"], user_agent, ip),
        )
        await db.commit()
        lead_id = cur.lastrowid

    return {"ok": True, "id": lead_id}
