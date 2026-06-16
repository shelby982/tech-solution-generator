"""SpecRepository 集成测试 — 用内存 SQLite 验证 toc + outline_matrix 往返。"""

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from domain.spec import OutlineMatrixRow, Section, SpecRepository


# ─────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    """每个测试一个内存 SQLite 连接 + 一个预建项目。"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute(
        "INSERT INTO projects (name) VALUES (?)", ("测试项目",)
    )
    await conn.commit()
    proj_id = cursor.lastrowid
    yield conn, proj_id
    await conn.close()


# ─────────────────────────────────────────────────────────
# doc_summary
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doc_summary_round_trip(db):
    conn, proj_id = db
    repo = SpecRepository(conn)
    summary = "本项目为某地铁站施工规范，重点关注 BIM 与安全文明施工。"
    await repo.save_doc_summary(proj_id, summary)
    got = await repo.get_doc_summary(proj_id)
    assert got == summary


@pytest.mark.asyncio
async def test_get_doc_summary_returns_empty_when_unset(db):
    conn, proj_id = db
    repo = SpecRepository(conn)
    got = await repo.get_doc_summary(proj_id)
    assert got == ""


# ─────────────────────────────────────────────────────────
# outline_matrix
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_outline_matrix_round_trip(db):
    conn, proj_id = db
    repo = SpecRepository(conn)

    sections = [
        Section(
            id="s1",
            level=1,
            title="技术方案",
            raw_content="原文 A",          # 不持久化
            special_marks=["★"],            # 不持久化
        ),
        Section(id="s2", level=2, title="施工组织"),
    ]
    rows = [
        OutlineMatrixRow(
            block_id="s1",
            title="技术方案",
            requirement="须满足国家规范 GB50300",
            key_points="设计 / 施工 / 验收",
            veto_items="资质不全则废标",
            bonus_items="提供 BIM 模型加 2 分",
            score_items="技术评分 30 分",
            evidence_required="提供项目业绩 3 项",
            constraint_level="mandatory",
            indicators="工期 ≤180 天",
        ),
        OutlineMatrixRow(
            block_id="s2",
            title="施工组织",
            requirement="须配置项目经理",
            constraint_level="recommended",
        ),
    ]

    await repo.save_outline_matrix(proj_id, sections, rows)
    got_sections, got_rows = await repo.get_outline_matrix(proj_id)

    # sections 还原（按 order_idx 排序）
    assert [s.id for s in got_sections] == ["s1", "s2"]
    assert got_sections[0].level == 1
    assert got_sections[0].title == "技术方案"
    # raw_content 与 special_marks 不持久化
    assert got_sections[0].raw_content == ""
    assert got_sections[0].special_marks == []

    # rows 8 字段全部还原
    assert [r.block_id for r in got_rows] == ["s1", "s2"]
    r0 = got_rows[0]
    assert r0.requirement == "须满足国家规范 GB50300"
    assert r0.key_points == "设计 / 施工 / 验收"
    assert r0.veto_items == "资质不全则废标"
    assert r0.bonus_items == "提供 BIM 模型加 2 分"
    assert r0.score_items == "技术评分 30 分"
    assert r0.evidence_required == "提供项目业绩 3 项"
    assert r0.constraint_level == "mandatory"
    assert r0.indicators == "工期 ≤180 天"

    r1 = got_rows[1]
    assert r1.requirement == "须配置项目经理"
    assert r1.constraint_level == "recommended"
    assert r1.key_points == ""


@pytest.mark.asyncio
async def test_outline_matrix_overwrites_on_resave(db):
    conn, proj_id = db
    repo = SpecRepository(conn)

    sections_v1 = [
        Section(id="s1", level=1, title="A"),
        Section(id="s2", level=1, title="B"),
        Section(id="s3", level=1, title="C"),
    ]
    rows_v1 = [OutlineMatrixRow(block_id=s.id, title=s.title) for s in sections_v1]
    await repo.save_outline_matrix(proj_id, sections_v1, rows_v1)

    sections_v2 = [
        Section(id="s10", level=1, title="X"),
        Section(id="s20", level=2, title="Y"),
    ]
    rows_v2 = [
        OutlineMatrixRow(block_id="s10", title="X", requirement="R-X"),
        OutlineMatrixRow(block_id="s20", title="Y", requirement="R-Y"),
    ]
    await repo.save_outline_matrix(proj_id, sections_v2, rows_v2)

    got_sections, got_rows = await repo.get_outline_matrix(proj_id)
    assert [s.id for s in got_sections] == ["s10", "s20"]
    assert [r.requirement for r in got_rows] == ["R-X", "R-Y"]


# ─────────────────────────────────────────────────────────
# dataclass 自检
# ─────────────────────────────────────────────────────────

def test_outline_matrix_row_to_dict_from_dict_inverse():
    row = OutlineMatrixRow(
        block_id="s1",
        title="技术方案",
        requirement="R",
        key_points="K",
        veto_items="V",
        bonus_items="B",
        score_items="S",
        evidence_required="E",
        constraint_level="mandatory",
        indicators="I",
        error="oops",
    )
    restored = OutlineMatrixRow.from_dict(row.to_dict())
    assert restored == row


def test_section_to_dict_from_dict_inverse():
    s = Section(id="s1", level=2, title="T", raw_content="C", special_marks=["★"])
    assert Section.from_dict(s.to_dict()) == s
