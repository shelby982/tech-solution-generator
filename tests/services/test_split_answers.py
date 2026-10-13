"""拆分确认项的**作答**与**项目级投标范围**。

用户要求：拆分时需要确认的内容可以与用户沟通。
上一版只做到"看得到"，这一版要能**回答**，且回答在下次拆分与报告中生效。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.split_answers import (
    get_answers,
    get_project_scope,
    resolve_split_context,
    set_answer,
    set_project_scope,
)


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "answers.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_answer_roundtrip_and_upsert(db):
    await set_answer(db, 1, "tid-1", "scope", {"table_no": 2})
    assert (await get_answers(db, 1, "tid-1"))["scope"] == {"table_no": 2}
    # 再答一次是覆盖，不是追加
    await set_answer(db, 1, "tid-1", "scope", {"table_no": 1})
    assert (await get_answers(db, 1, "tid-1"))["scope"] == {"table_no": 1}


@pytest.mark.asyncio
async def test_unknown_answer_key_is_rejected(db):
    """写错的 key 要报错，否则用户以为存上了、其实永远不生效。"""
    with pytest.raises(ValueError):
        await set_answer(db, 1, "tid-1", "随便写的", {"x": 1})


@pytest.mark.asyncio
async def test_run_level_answer_overrides_project_level(db):
    """run 级作答优先：想"这一次换个范围试试"不该改坏项目设置。"""
    await set_answer(db, 1, None, "scope", {"table_no": 1})
    await set_answer(db, 1, "tid-1", "scope", {"table_no": 3})
    ctx = await resolve_split_context(db, 1, "tid-1")
    assert ctx["answers"]["scope"] == {"table_no": 3}
    # 未作答的 run 回落到项目级
    ctx2 = await resolve_split_context(db, 1, "tid-2")
    assert ctx2["answers"]["scope"] == {"table_no": 1}


@pytest.mark.asyncio
async def test_project_scope_feeds_split_context(db):
    """项目级投标范围 → 等价于 scope 作答（投标人视角：一次设定、贯穿全程）。"""
    await set_project_scope(db, 1, target="标的2", package_no="2",
                            package_name="智能客服应用开发实施服务采购项目")
    assert (await get_project_scope(db, 1))["target"] == "标的2"
    ctx = await resolve_split_context(db, 1, "tid-1")
    assert ctx["answers"]["scope"]["target"] == "标的2"
    assert ctx["answers"]["scope"]["package_no"] == "2"


@pytest.mark.asyncio
async def test_topic_placement_answer_is_stored(db):
    await set_answer(db, 1, "tid-1", "topic_placement",
                     {"3.2.1. 客户档案管理模块": "功能要求"})
    got = (await get_answers(db, 1, "tid-1"))["topic_placement"]
    assert got["3.2.1. 客户档案管理模块"] == "功能要求"


def test_report_marks_answered_items_as_identified():
    """已作答的确认项在报告里不能还显示"待确认"。"""
    from services.split_report import _apply_answers

    items = [{"key": "scope", "question": "按哪个标包拆分？", "needs_confirm": True},
             {"key": "topic_placement", "question": "主题归属？", "needs_confirm": True},
             {"key": "structure", "question": "结构？", "needs_confirm": True}]
    out = _apply_answers(items, {"scope": {"table_no": 1}})
    by_key = {x["key"]: x for x in out}
    assert by_key["scope"]["answered"] and not by_key["scope"]["needs_confirm"]
    assert by_key["scope"]["answer"] == {"table_no": 1}
    assert by_key["topic_placement"]["answered"] is False
    assert by_key["topic_placement"]["answer_key"] == "topic_placement"
    assert by_key["structure"]["answer_key"] == "structure"


@pytest.mark.asyncio
async def test_review_points_confirmation_is_a_valid_answer(db):
    """两阶段流程第①步：确认评分点（先确认要拿哪些分，再确认目录怎么拆）。"""
    await set_answer(db, 1, "tid-1", "review_points",
                     {"confirmed": True, "revision": 3, "count": 13, "score": 100})
    got = (await get_answers(db, 1, "tid-1"))["review_points"]
    assert got["confirmed"] is True and got["revision"] == 3


def test_report_offers_review_points_confirmation_and_answers_it():
    """报告里要有「评分点确认」项，且作答后不再是待确认。"""
    from services.split_report import _apply_answers, _confirmations

    items = _confirmations([], [], 100.0, [], [], [], {})
    rp = next(i for i in items if i["key"] == "review_points")
    assert rp["needs_confirm"] is True

    answered = _apply_answers(items, {"review_points": {"confirmed": True, "revision": 3}})
    rp2 = next(i for i in answered if i["key"] == "review_points")
    assert rp2["answered"] is True and rp2["needs_confirm"] is False
    assert rp2["answer"] == {"confirmed": True, "revision": 3}
