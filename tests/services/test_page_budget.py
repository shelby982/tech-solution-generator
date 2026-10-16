"""篇幅预算：总额一次填 → 自动分配到各章 → 人工改过的固定 → 合计精确。

对应原型（docs/design/mockups/prod-prototype.html）在「应答策划」阶段的行为：
- 页数落在目录每一行（不是第二张表）
- 改总页数就重算，人工改过的章节保持不动
- 「清除手工调整」全部重算
- 目录未确认时不允许确认篇幅
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.page_budget import (
    allocate_pages,
    clear_page_budget,
    get_page_budget,
    is_allocatable,
    kind_factor,
    save_page_budget,
    summarize,
)


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "budget.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.commit()
    yield conn
    await conn.close()


async def _seed(db, thread_id="t1", project_id=1):
    """目录：一个一级章（技术方案，分值 30）+ 两个二级章 + 一个三级章（不参与分配）。"""
    rows = [
        ("1", "技术方案", 1, 30.0, 0),
        ("1.1", "系统架构设计", 2, 12.0, 1),
        ("1.2", "关键技术实现", 2, 18.0, 2),
        ("1.2.1", "容灾细节", 3, 5.0, 3),
    ]
    for block_id, title, level, weight, idx in rows:
        await db.execute(
            "INSERT INTO blocks (project_id, block_id, title, level, score_weight, run_thread_id, order_idx) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, block_id, title, level, weight, thread_id, idx),
        )
    await db.commit()


# ── 纯函数：分配 ─────────────────────────────────────────────

def test_allocate_sums_exactly_to_total():
    chapters = [
        {"block_id": "1", "title": "技术方案", "level": 1, "score_weight": 30},
        {"block_id": "1.1", "title": "系统架构设计", "level": 2, "score_weight": 12},
        {"block_id": "1.2", "title": "关键技术实现", "level": 2, "score_weight": 18},
    ]
    pages = allocate_pages(120, chapters)
    assert sum(pages.values()) == 120, "合计必须精确等于总页数，不差页"
    assert all(v >= 0 for v in pages.values())


def test_allocate_only_top_two_levels():
    chapters = [
        {"block_id": "1", "title": "技术方案", "level": 1, "score_weight": 30},
        {"block_id": "1.1.1", "title": "很深的叶子", "level": 3, "score_weight": 10},
    ]
    pages = allocate_pages(10, chapters)
    assert "1.1.1" not in pages, "三层及以下不参与分配（计入上级）"
    assert sum(pages.values()) == 10


def test_allocate_respects_pinned_and_absorbs_diff():
    chapters = [
        {"block_id": "1", "title": "技术方案", "level": 1, "score_weight": 30, "pages": 20, "pinned": True},
        {"block_id": "1.1", "title": "系统架构设计", "level": 2, "score_weight": 12},
        {"block_id": "1.2", "title": "关键技术实现", "level": 2, "score_weight": 18},
    ]
    pages = allocate_pages(120, chapters)
    assert pages["1"] == 20, "人工改过的章节保持不动"
    assert sum(pages.values()) == 120, "其余章节吸收差额"


def test_allocate_handles_zero_total_and_oversized_pins():
    chapters = [{"block_id": "1", "title": "技术方案", "level": 1, "score_weight": 30}]
    assert allocate_pages(0, chapters)["1"] == 0
    pinned = [
        {"block_id": "1", "title": "技术方案", "level": 1, "score_weight": 30, "pages": 50, "pinned": True},
        {"block_id": "1.1", "title": "系统架构设计", "level": 2, "score_weight": 12},
    ]
    pages = allocate_pages(10, pinned)
    assert pages["1"] == 50 and pages["1.1"] == 0, "固定值超总额时不出现负数"


def test_kind_factor_and_allocatable():
    assert kind_factor("技术方案") > kind_factor("项目背景"), "方案类正文权重高于背景"
    assert kind_factor("没见过的章节") == 1.0
    assert is_allocatable({"level": 2}) and not is_allocatable({"level": 3})
    assert is_allocatable({}), "缺 level 时按一级处理"


def test_summarize_reports_diff():
    chapters = [
        {"block_id": "1", "title": "技术方案", "level": 1, "pages": 100},
        {"block_id": "1.1", "title": "系统架构设计", "level": 2, "pages": 12},
    ]
    out = summarize(120, chapters)
    assert out == {"total": 120, "allocated": 112, "chapter_count": 2, "diff": -8, "ok": False}


# ── 落库往返 ─────────────────────────────────────────────────

async def test_save_then_read_roundtrip(db):
    await _seed(db)
    payload = await save_page_budget(db, 1, "t1", total=120, source="技术部分规定", allocate=True)
    assert payload["ok"] and payload["total"] == 120
    assert payload["allocated"] == 120
    assert payload["chapter_count"] == 3, "三级章不参与分配"

    again = await get_page_budget(db, 1, "t1")
    assert again["total"] == 120 and again["source"] == "技术部分规定"
    assert again["ok"] and again["allocated"] == 120


async def test_manual_override_pins_and_survives_reallocate(db):
    await _seed(db)
    await save_page_budget(db, 1, "t1", total=120, allocate=True)
    await save_page_budget(db, 1, "t1", overrides=[{"block_id": "1", "pages": 20, "pinned": True}])
    after = await save_page_budget(db, 1, "t1", total=150, allocate=True)
    first = next(c for c in after["chapters"] if c["block_id"] == "1")
    assert first["pages"] == 20 and first["pinned"], "手工改过的章节在重算后保持不动"
    assert after["ok"] and after["allocated"] == 150


async def test_clear_manual_resets_everything(db):
    await _seed(db)
    await save_page_budget(db, 1, "t1", total=120, allocate=True)
    await save_page_budget(db, 1, "t1", overrides=[{"block_id": "1", "pages": 99, "pinned": True}])
    cleared = await clear_page_budget(db, 1, "t1")
    first = next(c for c in cleared["chapters"] if c["block_id"] == "1")
    assert first["pages"] != 99 and not first["pinned"]
    assert cleared["ok"] and cleared["allocated"] == 120


async def test_confirm_flag_and_unknown_block_ignored(db):
    await _seed(db)
    await save_page_budget(db, 1, "t1", total=120, allocate=True,
                           overrides=[{"block_id": "不存在的章", "pages": 5}])
    payload = await save_page_budget(db, 1, "t1", confirm=True)
    assert payload["confirmed"] is True
    assert payload["ok"], "不存在的章节不影响合计"


async def test_two_response_types_are_isolated(db):
    """商务与技术是两份独立应答：篇幅各自一份，互不影响。"""
    await _seed(db, thread_id="tech")
    await _seed(db, thread_id="biz")
    await save_page_budget(db, 1, "tech", total=120, allocate=True)
    await save_page_budget(db, 1, "biz", total=30, allocate=True)

    tech = await get_page_budget(db, 1, "tech")
    biz = await get_page_budget(db, 1, "biz")
    assert tech["total"] == 120 and tech["allocated"] == 120
    assert biz["total"] == 30 and biz["allocated"] == 30


def test_allocate_uses_subtree_scores_when_parent_has_none():
    """真实目录：分值挂在叶子，父节点没有分值 —— 应按子树汇总，不能退化成均分。"""
    chapters = [
        {"block_id": "s1", "title": "技术评分项应答", "level": 1},
        {"block_id": "s1.1", "title": "项目理解", "level": 2},
        {"block_id": "s1.1.1", "title": "项目需求理解完整性", "level": 3, "score_weight": 4},
        {"block_id": "s1.2", "title": "技术方案", "level": 2},
        {"block_id": "s1.2.1", "title": "技术实现方案", "level": 3, "score_weight": 24},
    ]
    pages = allocate_pages(120, chapters)
    assert sum(pages.values()) == 120
    assert pages["s1.2"] > pages["s1.1"], "24 分的子树应比 4 分的子树分到更多页"
    assert pages["s1"] > 0


async def test_clear_manual_can_set_new_total(db):
    """清除手工调整时可以同时改总额（否则会按旧总额重算回去）。"""
    await _seed(db)
    await save_page_budget(db, 1, "t1", total=120, allocate=True)
    cleared = await clear_page_budget(db, 1, "t1", total=0)
    assert cleared["total"] == 0 and cleared["allocated"] == 0


async def test_clear_manual_also_updates_source(db):
    """清除手工调整时，来源字段也要跟着更新（只透传 total 会静默丢 source）。"""
    await _seed(db)
    await save_page_budget(db, 1, "t1", total=120, source="技术部分规定", allocate=True)
    cleared = await clear_page_budget(db, 1, "t1", total=0, source="")
    assert cleared["total"] == 0 and cleared["source"] == ""
