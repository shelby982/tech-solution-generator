"""标包名称提取（2026-10-16，用户反馈"标的名称没有展示"）。

背景：抽取阶段刻意留空名称（不编造），确认标包时只写了编号标签（如「标的 1」），
而名称其实就在 evidence 引文里（「标的1：2026年…采购项目」）。
这里测两件事：从引文取名称的规则，以及启动时的回填。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.package_analysis import name_from_quotes

QUOTE = ("2.1项目概述：标的1：2026年南网数字运营软件科技（广东）有限公司应用中台客户 "
         "档案管理认证系统开发实施服务采购项目 本项目基于公司数智营")


def test_extracts_name_across_pdf_line_break():
    """PDF 抽文把长名称断在空格处 —— 要去空格拼回完整名称（实测场景）。"""
    assert name_from_quotes([QUOTE]) == (
        "2026年南网数字运营软件科技（广东）有限公司应用中台客户档案管理认证系统开发实施服务采购项目")


def test_cuts_trailing_clause():
    """名称后面粘上从句时要截断（实测「…采购项目为提升客户服务体验」）。"""
    q = "标的2：智能客服应用开发实施服务采购项目为提升客户服务体验，需建设智能客服系统"
    assert name_from_quotes([q]) == "智能客服应用开发实施服务采购项目"


def test_cuts_at_sentence_end():
    q = "标的2：2026年南方电网数字平台科技（广东）有限公司应用中台档案管理认证系统开发实施服务。"
    assert name_from_quotes([q]) == "2026年南方电网数字平台科技（广东）有限公司应用中台档案管理认证系统开发实施服务"


def test_returns_empty_without_name():
    """取不到名称就留空 —— 不编造。"""
    assert name_from_quotes(["标的1"]) == ""
    assert name_from_quotes(["标包2："]) == ""
    assert name_from_quotes([]) == ""


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "pkg.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


async def test_init_db_backfills_bare_label_from_evidence(db):
    """回填：只补"为空或只是编号标签"的行，且从已有引文取，不重跑解析。"""
    await db.execute("INSERT INTO projects (name) VALUES ('p')")
    evidence = f'[{{"material_id": "79", "page": 3, "quote": "{QUOTE}"}}]'
    await db.execute(
        "INSERT INTO packages (project_id, target_code, package_code, display_name, status, confidence, evidence) "
        "VALUES (1, '1', '', '标的 1', 'confirmed', 'high', ?)", (evidence,))
    await db.execute(
        "INSERT INTO packages (project_id, target_code, package_code, display_name, status, confidence, evidence) "
        "VALUES (1, '2', '', '某个人工填的名称', 'confirmed', 'manual', ?)", (evidence,))
    await db.commit()

    await init_db(db)                       # 迁移在启动时运行

    rows = {r["target_code"]: r["display_name"] for r in await (
        await db.execute("SELECT target_code, display_name FROM packages WHERE project_id = 1")).fetchall()}
    assert rows["1"].startswith("2026年南网数字运营软件科技"), "编号标签应被回填成真名"
    assert rows["1"] != "标的 1"
    assert rows["2"] == "某个人工填的名称", "人工填写的名称不能被覆盖"
