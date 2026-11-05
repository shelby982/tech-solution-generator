"""POST /api/leads：入库、必填校验、蜜罐、限流。

隔离方式沿用 tests/conftest.py 的 _isolate_db（每个用例一套临时库），
这里只额外保证 leads 表已建好并挂上路由。
"""
from __future__ import annotations

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import db as db_module
from db import init_db
from routes import leads as leads_routes


@pytest.fixture
async def client():
    # 清空进程内限流计数，避免用例之间互相影响
    leads_routes._hits.clear()

    conn = await aiosqlite.connect(db_module.DB_PATH)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.commit()
    await conn.close()

    app = FastAPI()
    app.include_router(leads_routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


VALID = {"name": "张三", "company": "某某科技", "contact": "13800000000"}


@pytest.mark.asyncio
async def test_create_lead_persists_with_utm(client):
    payload = {**VALID, "topic": "素材引用与举证",
               "utm_source": "video", "utm_medium": "short_video",
               "utm_campaign": "launch_2026H2", "utm_content": "45s",
               "referer": "https://example.com/lp"}
    res = await client.post("/api/leads", json=payload)
    assert res.status_code == 201
    body = res.json()
    assert body["ok"] is True and isinstance(body["id"], int)

    async with aiosqlite.connect(db_module.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM leads WHERE id=?", (body["id"],))).fetchone()
    assert row["name"] == "张三"
    assert row["utm_source"] == "video"
    assert row["utm_content"] == "45s"
    assert row["topic"] == "素材引用与举证"
    assert row["created_at"]


@pytest.mark.asyncio
async def test_missing_fields_rejected(client):
    res = await client.post("/api/leads", json={"name": "张三", "company": "", "contact": "13800000000"})
    assert res.status_code == 422
    res = await client.post("/api/leads", json={**VALID, "contact": "123"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_honeypot_silently_ignored(client):
    res = await client.post("/api/leads", json={**VALID, "website": "http://spam.example"})
    assert res.status_code == 201
    assert res.json()["id"] is None
    async with aiosqlite.connect(db_module.DB_PATH) as db:
        count = await (await db.execute("SELECT COUNT(*) FROM leads")).fetchone()
    assert count[0] == 0


@pytest.mark.asyncio
async def test_rate_limited_after_three(client):
    for _ in range(3):
        assert (await client.post("/api/leads", json=VALID)).status_code == 201
    assert (await client.post("/api/leads", json=VALID)).status_code == 429
