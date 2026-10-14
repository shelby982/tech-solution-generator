"""素材**按需检索**：用本章的举证要求去素材库找片段（确定性，不调模型）。"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.material_search import extract_terms, score_chunk, search_block_materials


def test_extract_terms_keeps_long_phrases_and_drops_stopwords():
    terms = extract_terms("投标偏差表（技术部分）及偏差说明对应的投标文件页码")
    assert "投标偏差表" in terms            # 长短语保留
    assert "偏差说明" in terms
    assert "的" not in terms and "投标" not in terms   # 停用词/被更长词包含的短词被去掉


def test_score_prefers_more_hits_and_penalises_bloat():
    terms = ["偏差表", "技术部分"]
    hit = "投标偏差表（技术部分）：完全响应" 
    assert score_chunk(hit, terms) > score_chunk("与本章无关的一段很长的文字" * 20, terms)
    assert score_chunk("", terms) == 0.0


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "ms.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    await conn.execute(
        "INSERT INTO materials (id, project_id, filename, role, parse_status) "
        "VALUES (10, 1, 'XX项目合同.pdf', 'source', 'done')")
    for i, text in enumerate([
        "乙方提供统一客户档案管理模块的设计、开发与实施服务，服务期 12 个月。",
        "本表为投标偏差表（技术部分），逐条列明偏差说明与支撑页。",
        "公司简介与办公地址。",
    ]):
        await conn.execute(
            "INSERT INTO material_chunks (material_id, chunk_index, content, page) "
            "VALUES (10, ?, ?, ?)", (i, text, i + 1))
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_search_uses_evidence_required_and_ranks_hits(db):
    block = {"block_id": "s1", "project_id": 1, "title": "技术条款差异性",
             "evidence_required": "投标偏差表（技术部分）；偏差说明对应的页码",
             "key_points": ""}
    out = await search_block_materials(db, block)
    assert out["query_source"] == "evidence_required"
    assert out["terms"], "应当能抽出关键词"
    assert out["results"], "应当命中偏差表那段"
    assert "偏差表" in out["results"][0]["content"]
    assert out["results"][0]["filename"] == "XX项目合同.pdf"
    assert out["results"][0]["page"] == 2


@pytest.mark.asyncio
async def test_search_falls_back_to_title_when_no_evidence(db):
    """没有举证要求的章节（"不需要素材"）也能按标题检索，只是结果可能为空。"""
    block = {"block_id": "s2", "project_id": 1, "title": "公司简介",
             "evidence_required": "", "key_points": ""}
    out = await search_block_materials(db, block)
    assert out["query_source"] == "title"
    assert all("results" in out for _ in [0])


@pytest.mark.asyncio
async def test_search_is_read_only(db):
    """按需检索**不写库**：跑两次结果一致，chunks 数量不变（不会污染预匹配的结果）。"""
    before = (await (await db.execute("SELECT COUNT(*) FROM material_chunks")).fetchone())[0]
    block = {"block_id": "s1", "project_id": 1, "title": "x",
             "evidence_required": "客户档案管理 实施服务", "key_points": ""}
    a = await search_block_materials(db, block)
    b = await search_block_materials(db, block)
    after = (await (await db.execute("SELECT COUNT(*) FROM material_chunks")).fetchone())[0]
    assert before == after
    assert [r["content"] for r in a["results"]] == [r["content"] for r in b["results"]]
