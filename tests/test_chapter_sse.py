"""M2-a：章节级 SSE 作业引擎（事件序列 + 落库）。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@pytest.fixture()
def seeded_block():
    """在测试库里造一个"有正文 + 有大纲"的章节。"""
    import db as db_module
    from services.section_outline import normalize_outline

    async def main():
        async with db_module.get_db() as db:
            await db.execute(
                "INSERT INTO blocks (project_id, block_id, title, content, level, status) "
                "VALUES (?,?,?,?,?,?)",
                (29, "s1.1", "测试章", "## 背景\n\n（本节待写）\n", 2, "draft"))
            await db.commit()
            row = await (await db.execute("SELECT * FROM blocks WHERE block_id='s1.1'")).fetchone()
            outline = normalize_outline({"sections": [
                {"title": "背景", "points": ["说明背景"], "words": 800},
            ]}, {"title": "测试章", "requirements": []})
            await db.execute(
                "INSERT INTO writing_plans (id, project_id, thread_id, root_block_id, plan, status) VALUES (?,?,?,?,?,?)",
                ("plan-t", 29, "t" * 10, "s1.1", __import__("json").dumps({"chapters": [
                    {"block_id": "s1.1", "outline": outline}]}, ensure_ascii=False), "draft"))
            await db.commit()
            return dict(row)

    return asyncio.run(main())


def test_runner_emits_events_and_saves(monkeypatch, seeded_block):
    from services import chapter_sse

    events: list[tuple[str, dict]] = []

    async def fake_emit(job_id, event, payload):
        events.append((event, payload))

    async def fake_stream(block, section, thread_id, model):
        yield "本节正文内容，用于验证 SSE 引擎的流式与落库行为。" * 4

    monkeypatch.setattr(chapter_sse, "emit", fake_emit)
    monkeypatch.setattr(chapter_sse, "_stream_section", fake_stream)

    async def main():
        job_id = await chapter_sse.create_job(29, "t" * 10, int(seeded_block["id"]))
        await chapter_sse.run_job(job_id)
        return job_id

    job_id = asyncio.run(main())
    names = [name for name, _ in events]
    assert names[0] == "job"
    assert "plan" in names
    assert any(n == "role" and p.get("state") == "working" for n, p in events)
    assert any(n == "delta" for n, _ in events)
    assert any(n == "section" and p.get("state") == "done" for n, p in events)
    assert any(n == "done" for n, _ in events)


def test_merge_charts_replaces_placeholder_and_reports_missing():
    from services.chapter_sse import merge_charts

    text = "## 一\n\n如图。\n\n[[FIGURE:对比图]]\n\n如表。\n\n[[TABLE:映射表]]\n"
    charts = [
        {"ok": True, "role": "diagram", "title": "对比图", "markdown": ":::diagram type=layered 对比图\n- A\n- B\n:::"},
        {"ok": True, "role": "table", "title": "另一个表", "markdown": "**表：另一个表**\n\n| A |\n| --- |\n| 1 |"},
    ]
    merged, missing = merge_charts(text, charts)
    assert ":::diagram" in merged                     # 占位符被替换
    assert "[[TABLE:映射表]]" not in merged            # 无对应图表的占位符被清掉
    assert any("映射表" in note for note in missing)    # 并记为待补
    assert any("另一个表" in note for note in missing)  # 无占位的图表附在节末


def test_chart_checks_flag_missing_ref_and_layer_conflict():
    from services.chart_checks import chart_items

    markdown = ":::diagram type=layered 对比图\n合规落实\n数据管理\n权限控制\n安全技术措施\n:::"
    items = chart_items("diagram", markdown, {"title": "对比图", "type": "layered"},
                        "本节从三个层面说明。", [])
    titles = [item["title"] for item in items]
    assert any("缺依据" in t for t in titles)                 # 无 ref
    assert any("图文层数不一致" in t for t in titles)          # 正文三层 vs 图 4 节点


def test_chart_checks_pass_when_consistent():
    from services.chart_checks import chart_items

    markdown = ":::diagram type=layered 对比图\n合规落实\n数据管理\n权限控制\n:::"
    items = chart_items("diagram", markdown, {"title": "对比图"}, "本节从三个层面说明。", ["招标文件-2.2.5"])
    assert [i for i in items if i["title"].endswith("缺依据")] == []
    assert [i for i in items if "层数不一致" in i["title"]] == []


def test_job_artifacts_carry_section_records(monkeypatch, seeded_block):
    """M6：留痕 —— 作业结束后 artifacts 里有节级记录（角色/模型/字数/耗时）。"""
    import json as _json

    from services import chapter_sse

    async def fake_stream(block, section, thread_id, model):
        yield "本节正文内容，用于验证留痕。" * 6

    monkeypatch.setattr(chapter_sse, "_stream_section", fake_stream)
    monkeypatch.setattr(chapter_sse, "emit", lambda *a, **k: asyncio.sleep(0))

    async def main():
        job_id = await chapter_sse.create_job(29, "t" * 10, int(seeded_block["id"]))
        await chapter_sse.run_job(job_id)
        return job_id

    job_id = asyncio.run(main())

    async def read():
        row = await chapter_sse._job_row(job_id)
        return _json.loads(row["artifacts"] or "{}")

    artifacts = asyncio.run(read())
    sections = artifacts.get("sections") or []
    assert sections and sections[0]["role"] == "text"
    assert sections[0]["words"] > 0
    assert "ms" in sections[0]
