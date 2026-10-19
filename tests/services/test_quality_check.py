"""章节质检：结构完整性 / 图表数量 / 占位与空词 / 篇幅偏差（确定性检查）。"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.quality_check import check_chapter, check_thread

RECIPE = {"skeleton": ["政策依据", "现状差距", "目标指标"],
          "tables": ["现状维度表"], "figures": ["趋势图"]}


def test_clean_chapter_has_no_high_issues():
    content = """## 政策依据
国家与集团政策均要求推进数字化转型。

| 维度 | 现状 | 挑战 |
| --- | --- | --- |
| 架构 | 单体 | 扩展性不足 |

:::diagram 趋势
单体 -> 微服务
:::

## 现状差距
现状仍有差距。

## 目标指标
目标明确。
"""
    row = check_chapter(content, RECIPE, budget_pages=1)
    assert row["stats"]["tables"] == 1
    assert row["stats"]["figures"] == 1
    assert row["ok"] is True


def test_missing_table_is_high_and_missing_skeleton_is_medium():
    row = check_chapter("只有一段文字，没有任何结构。", RECIPE)
    codes = {issue["code"] for issue in row["issues"]}
    assert "table" in codes and "figure" in codes
    assert "skeleton" in codes
    assert row["ok"] is False


def test_pending_placeholders_and_budget_deviation():
    content = "## 政策依据\n【待补充：官方来源】\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    row = check_chapter(content, {"skeleton": ["政策依据"]}, budget_pages=10)
    codes = {issue["code"] for issue in row["issues"]}
    assert "pending" in codes and "budget" in codes


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "quality.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.execute("INSERT INTO workflow_runs (thread_id, project_id, stage) VALUES ('t1', 1, 'outline_review')")
    rows = [("s1", 1, "技术评分项应答", ""),
            ("s1.1", 2, "项目理解", ""),
            ("s1.1.1", 3, "项目背景现状目标", "| a | b |\n| --- | --- |\n| 1 | 2 |\n"),
            ("s1.2", 2, "技术方案", ""),
            ("s1.2.1", 3, "方案总体架构设计", "只有文字")]
    for index, (block_id, level, title, content) in enumerate(rows):
        await conn.execute(
            "INSERT INTO blocks (project_id, block_id, title, level, content, order_idx, run_thread_id, status) "
            "VALUES (1, ?, ?, ?, ?, ?, 't1', 'draft')",
            (block_id, title, level, content, index))
    await conn.commit()
    yield conn
    await conn.close()


async def test_check_thread_reports_only_writable_chapters(db):
    data = await check_thread(db, 1, "t1")
    titles = [row["title"] for row in data["chapters"]]
    # 父目录（无正文）不检查，只检查有正文/叶子的编写单元
    assert "项目背景现状目标" in titles
    assert data["summary"]["chapters"] == len(data["chapters"])
    assert data["summary"]["high"] >= 1      # 有个章节没有表格
