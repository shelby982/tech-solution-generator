"""block_store 占位 upsert 的用例。

背景：``block_id`` 是**位置派生**的（parse 给 s1..sN 流水号，派生目录给 s1/s1.1/…），
换个文件顺序或换一组文件，同一个 id 就指向另一节。占位 upsert 原先只盖
title/level/order_idx，旧行的 8 个提炼字段原样留着 —— 实测表现为目录里的「总则」
顶着上一份 PDF 某章的 requirement 显示。
"""

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from services.block_store import (
    current_run_thread_id,
    get_block,
    list_blocks,
    sync_outline_placeholders,
    upsert_outline_block,
)


class _Sec:
    """Section-like：sync_outline_placeholders 只要求 .id / .level / .title。"""

    def __init__(self, id, level, title):
        self.id = id
        self.level = level
        self.title = title


@pytest_asyncio.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("测试项目",))
    await conn.commit()
    yield conn, cursor.lastrowid
    await conn.close()


async def _extract(conn, pid, block_id, title, level, order_idx, **matrix):
    """模拟一次提炼：把 8 字段写进去，status 升为 outline_done。"""
    await upsert_outline_block(
        conn, pid, block_id, level=level, title=title, order_idx=order_idx,
        matrix=matrix,
    )


@pytest.mark.asyncio
async def test_same_block_id_new_section_clears_extracted_fields(db):
    """同一个 block_id 换了节：旧 8 字段必须清空，不能顶着显示。"""
    conn, pid = db
    # 第一次解析：文件 A 在前，s1 = 「（3）CCRC信息安全服务资质认证证书」
    await upsert_outline_block(
        conn, pid, "s1", level=3, title="（3）CCRC信息安全服务资质认证证书", order_idx=0,
    )
    await _extract(
        conn, pid, "s1", "（3）CCRC信息安全服务资质认证证书", 3, 0,
        requirement="1. 基本理解背景、现状、目标、建设范围…",
        key_points="资质证书齐全",
        score_items="★",
        indicators="3 份",
    )

    # 第二次解析：换了文件顺序，s1 变成 docx 的「总则」
    await upsert_outline_block(
        conn, pid, "s1", level=2, title="总则", order_idx=0,
    )

    got = await get_block(conn, (await list_blocks(conn, pid))[0]["id"])
    assert got["title"] == "总则"
    assert got["level"] == 2
    assert got["requirement"] == ""
    assert got["key_points"] == ""
    assert got["score_items"] == ""
    assert got["indicators"] == ""
    assert got["status"] == "empty", "清了字段就必须退回未提炼，否则前端显示已完成"


@pytest.mark.asyncio
async def test_same_section_rerun_keeps_extracted_fields(db):
    """坐标三者全同 = 同一节重跑：不能白清已提炼内容。"""
    conn, pid = db
    await upsert_outline_block(conn, pid, "s1", level=2, title="总则", order_idx=0)
    await _extract(conn, pid, "s1", "总则", 2, 0, requirement="应标要求正文")

    await upsert_outline_block(conn, pid, "s1", level=2, title="总则", order_idx=0)

    got = await get_block(conn, (await list_blocks(conn, pid))[0]["id"])
    assert got["requirement"] == "应标要求正文"
    assert got["status"] == "outline_done"


@pytest.mark.asyncio
async def test_same_title_but_shifted_position_clears(db):
    """标题相同但位置变了不算同一节 —— docx 里「工作项1：梳理分析现状」重名多次，
    只比 title 会把它们当成同一节。"""
    conn, pid = db
    await upsert_outline_block(
        conn, pid, "s28", level=6, title="工作项1：梳理分析现状", order_idx=27,
    )
    await _extract(
        conn, pid, "s28", "工作项1：梳理分析现状", 6, 27, requirement="上一节的正文",
    )

    await upsert_outline_block(
        conn, pid, "s28", level=6, title="工作项1：梳理分析现状", order_idx=33,
    )

    got = await get_block(conn, (await list_blocks(conn, pid))[0]["id"])
    assert got["requirement"] == ""
    assert got["status"] == "empty"


@pytest.mark.asyncio
async def test_placeholder_keeps_generated_content(db):
    """换了节也不动 content（正文）：沿用 sync_outline_placeholders 的约定，
    宁可留一条脏数据也不删用户内容。"""
    conn, pid = db
    await upsert_outline_block(conn, pid, "s1", level=2, title="旧标题", order_idx=0)
    await conn.execute(
        "UPDATE blocks SET content = ?, status = 'done' WHERE project_id = ? AND block_id = 's1'",
        ("已经写好的正文", pid),
    )
    await conn.commit()

    await upsert_outline_block(conn, pid, "s1", level=2, title="新标题", order_idx=0)

    got = await get_block(conn, (await list_blocks(conn, pid))[0]["id"])
    assert got["content"] == "已经写好的正文"


@pytest.mark.asyncio
async def test_sync_outline_placeholders_clears_reslotted_rows(db):
    """整轮同步走一遍：目录换版后，还留在 toc 里但换了节的 id 同样要清。"""
    conn, pid = db
    old = [_Sec("s1", 3, "（3）CCRC信息安全服务资质认证证书")]
    await sync_outline_placeholders(conn, pid, old)
    await _extract(
        conn, pid, "s1", "（3）CCRC信息安全服务资质认证证书", 3, 0,
        requirement="旧文档的要求",
    )

    new = [_Sec("s1", 2, "总则")]
    await sync_outline_placeholders(conn, pid, new)

    blocks = await list_blocks(conn, pid)
    assert len(blocks) == 1
    assert blocks[0]["title"] == "总则"
    assert blocks[0]["requirement"] == ""
    assert blocks[0]["status"] == "empty"


@pytest.mark.asyncio
async def test_sync_outline_placeholders_counts_kept_orphans(db):
    """不在 toc 但有正文的行不删，且在 stats 里报出来（只做观测，不改删除规则）。"""
    conn, pid = db
    await sync_outline_placeholders(conn, pid, [_Sec("s1", 2, "旧节")])
    await conn.execute(
        "UPDATE blocks SET content = ?, status = 'done' WHERE project_id = ? AND block_id = 's1'",
        ("已经写好的正文", pid),
    )
    await conn.commit()

    stats = await sync_outline_placeholders(conn, pid, [_Sec("s2", 2, "新节")])

    assert stats["kept"] == 1
    assert stats["removed"] == 0, "有正文的孤儿不能算进 removed"
    assert stats["created"] == 1
    blocks = {b["block_id"]: b for b in await list_blocks(conn, pid)}
    assert sorted(blocks) == ["s1", "s2"], "孤儿行要留着"
    assert blocks["s1"]["content"] == "已经写好的正文"


@pytest.mark.asyncio
async def test_sync_outline_placeholders_kept_zero_when_all_orphans_empty(db):
    """常规情形：目录换版后孤儿行 content 为空，全删，kept 为 0。"""
    conn, pid = db
    await sync_outline_placeholders(conn, pid, [_Sec("s1", 2, "旧节")])

    stats = await sync_outline_placeholders(conn, pid, [_Sec("s2", 2, "新节")])

    assert stats == {"removed": 1, "created": 1, "kept": 0}
    blocks = await list_blocks(conn, pid)
    assert [b["block_id"] for b in blocks] == ["s2"]


# ── run 归属：两次 run 的顶层 block_id 会撞车 ───────────────


@pytest.mark.asyncio
async def test_two_runs_same_block_id_do_not_overwrite(db):
    """block_id 是位置派生的，两次 run 的 s1 是不同章节 —— 各写各的行。"""
    conn, pid = db
    await upsert_outline_block(
        conn, pid, "s1", level=2, title="上一版目录", order_idx=0,
        run_thread_id="run-a",
    )
    await upsert_outline_block(
        conn, pid, "s1", level=2, title="这一版目录", order_idx=0,
        run_thread_id="run-b",
    )

    assert [r["title"] for r in await list_blocks(conn, pid, "run-a")] == ["上一版目录"]
    assert [r["title"] for r in await list_blocks(conn, pid, "run-b")] == ["这一版目录"]
    assert len(await list_blocks(conn, pid)) == 2, "不过滤时两行都在（历史行不删）"


@pytest.mark.asyncio
async def test_sync_only_clears_own_run_orphans(db):
    """新 run 的 sync 不碰别的 run 的行：旧 run 的空占位留着，不参与清理。"""
    conn, pid = db
    await sync_outline_placeholders(
        conn, pid, [_Sec("old-1", 2, "上一版")], run_thread_id="run-a",
    )

    stats = await sync_outline_placeholders(
        conn, pid, [_Sec("s1", 2, "新节")], run_thread_id="run-b",
    )

    assert stats == {"removed": 0, "created": 1, "kept": 0}
    blocks = {b["block_id"]: b for b in await list_blocks(conn, pid)}
    assert sorted(blocks) == ["old-1", "s1"]
    assert blocks["old-1"]["run_thread_id"] == "run-a"


@pytest.mark.asyncio
async def test_list_blocks_fallback_all_covers_legacy_rows(db):
    """老项目的行没有 run 归属：该 run 一行都没有时要退回显示全部，页面不能全空。"""
    conn, pid = db
    await upsert_outline_block(conn, pid, "s1", level=2, title="老行", order_idx=0)

    rows = await list_blocks(conn, pid, "run-new", fallback_all=True)
    assert [r["title"] for r in rows] == ["老行"], "该 run 无行 → 退回全部"

    await upsert_outline_block(
        conn, pid, "s1", level=2, title="新行", order_idx=0, run_thread_id="run-new",
    )
    rows = await list_blocks(conn, pid, "run-new", fallback_all=True)
    assert [r["title"] for r in rows] == ["新行"], "该 run 有自己的行 → 不再退回"
    assert [r["title"] for r in await list_blocks(conn, pid, "run-new")] == ["新行"]


@pytest.mark.asyncio
async def test_current_run_thread_id_picks_latest(db):
    """当前 run = 本项目最近创建的一条 workflow_runs。"""
    conn, pid = db
    for tid, created_at in [("run-old", "2026-01-01 00:00:00"),
                            ("run-new", "2026-02-01 00:00:00")]:
        await conn.execute(
            "INSERT INTO workflow_runs (thread_id, project_id, created_at)"
            " VALUES (?, ?, ?)",
            (tid, pid, created_at),
        )
    await conn.commit()

    assert await current_run_thread_id(conn, pid) == "run-new"
    assert await current_run_thread_id(conn, 999) is None
