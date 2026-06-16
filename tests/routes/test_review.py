"""routes/review.py 评审快照端点测试。

覆盖：
- 200 正常路径：thread_id 有 findings 时返回完整字段
- 200 空数组：thread_id 没有 findings 时返回空 list
"""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import db as db_module
from db import init_db
from domain.review import Finding, Issue, ReviewRepository, WorkflowRunRepository
from routes import review as review_routes


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def isolated_db(tmp_path, monkeypatch):
    """把 DB_PATH 指向 tmp_path，建表 + 预置一个 project，返回 (db_path, proj_id)。"""
    db_path = str(tmp_path / "test_review.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        cursor = await conn.execute(
            "INSERT INTO projects (name) VALUES (?)", ("评审快照测试项目",)
        )
        await conn.commit()
        proj_id = cursor.lastrowid
    return db_path, proj_id


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(review_routes.router, prefix="/api")
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_findings(db_path: str, proj_id: int, thread_id: str) -> None:
    """往 reviews 表预置 2 条 finding（同一 block，两位 agent）。"""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        run_repo = WorkflowRunRepository(conn)
        await run_repo.create(thread_id, proj_id, stage="reviewing")
        review_repo = ReviewRepository(conn)
        await review_repo.add_finding(
            thread_id,
            Finding(
                block_id="1.1",
                agent="wang_anshi",
                score=85,
                issues=[
                    Issue(
                        severity="high",
                        point="缺少性能指标",
                        suggestion="补充 SLA",
                    ),
                ],
                strengths=["架构清晰"],
                error="",
            ),
        )
        await review_repo.add_finding(
            thread_id,
            Finding(
                block_id="1.1",
                agent="bao_zheng",
                score=90,
                issues=[],
                strengths=["合规完整"],
                error="",
            ),
        )


# ─────────────────────────────────────────────
# 正常路径
# ─────────────────────────────────────────────

async def test_get_review_returns_findings(client, isolated_db):
    db_path, proj_id = isolated_db
    tid = "t-review-1"
    await _seed_findings(db_path, proj_id, tid)

    r = await client.get(f"/api/review/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"] == tid
    assert isinstance(body["findings"], list)
    assert len(body["findings"]) == 2

    # 字段完整 + 顺序：(block_id, agent) 升序 → bao_zheng, wang_anshi
    bao = body["findings"][0]
    wang = body["findings"][1]
    assert bao["agent"] == "bao_zheng"
    assert wang["agent"] == "wang_anshi"

    for item in (bao, wang):
        assert set(item.keys()) >= {
            "block_id", "agent", "score", "issues", "strengths", "error",
        }
        assert item["block_id"] == "1.1"
        assert isinstance(item["issues"], list)
        assert isinstance(item["strengths"], list)

    assert wang["score"] == 85
    assert wang["issues"][0]["severity"] == "high"
    assert wang["issues"][0]["point"] == "缺少性能指标"
    assert wang["issues"][0]["suggestion"] == "补充 SLA"
    assert wang["strengths"] == ["架构清晰"]

    assert bao["score"] == 90
    assert bao["strengths"] == ["合规完整"]


async def test_get_review_returns_empty_findings_for_unknown_thread(client, isolated_db):
    # 即使 thread_id 没数据，也返回 200 + 空 list（快照端点对未知 tid 也容忍）
    r = await client.get("/api/review/no-such-thread")
    assert r.status_code == 200
    body = r.json()
    assert body == {"thread_id": "no-such-thread", "findings": []}
