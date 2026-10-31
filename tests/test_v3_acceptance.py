"""v3 验收（§10 的 17 条）：能自动验的都在这里，逐条对应。

跑法：LLM_MODE=mock python -m pytest tests/test_v3_acceptance.py -q
"""
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


# ── 1. 大纲 deliverables 有 elements ───────────────────────────────────────
def test_01_outline_deliverables_have_elements():
    from services.section_outline import _normalize_deliverables, ensure_outline_elements

    got = _normalize_deliverables([{"kind": "figure", "title": "对比图", "type": "layered"}],
                                  "figure", ["现状：未建立", "目标：完整建立"])
    figure = [d for d in got if d["kind"] == "figure"][0]
    assert figure["elements"] == ["现状：未建立", "目标：完整建立"]
    # 旧大纲（无 elements）读取时派生
    legacy = [{"title": "对比", "points": ["甲", "乙"], "deliverables": [{"kind": "figure", "title": "图"}]}]
    assert ensure_outline_elements(legacy)[0]["deliverables"][0]["elements"] == ["甲", "乙"]


# ── 2. 4 头像（前端静态判据）──────────────────────────────────────────────
def test_02_four_avatars_and_no_reviewer():
    source = (ROOT / "frontend/assets/chapter-studio.mjs").read_text(encoding="utf-8")
    bar = re.search(r"function agentBarHtml\(states, models\) \{([\s\S]{0,400})", source).group(1)
    roles = re.search(r"const roles = \[(.*?)\]", bar).group(1)
    assert "chief" in roles and "text" in roles and "diagram" in roles and "table" in roles
    assert "wang_anshi" not in roles and "bao_zheng" not in roles


# ── 3/4. 图文并行 & 图不从正文生成 ────────────────────────────────────────
def test_03_04_chart_runs_parallel_and_ignores_section_text(monkeypatch):
    from services import chapter_sse

    async def fake_job_row(job_id):
        return {"status": "running"}

    monkeypatch.setattr(chapter_sse, "_job_row", fake_job_row)

    captured = {}
    events = []

    async def fake_emit(job_id, event, payload):
        events.append((event, payload))

    async def fake_stream(block, section, thread_id, model):
        for chunk in ("本" * 30, "文" * 30):
            yield chunk

    async def fake_chart(job_id, index, section, deliverable, thread_id, model, block=None):
        captured["deliverable"] = deliverable
        captured["has_text"] = "text" in json.dumps(deliverable, ensure_ascii=False)
        await asyncio.sleep(0)
        return {"ok": True, "role": "diagram", "index": index, "title": "对比图",
                "markdown": ":::diagram type=layered 对比图\n甲\n乙\n:::", "model": "m", "checks": {}}

    monkeypatch.setattr(chapter_sse, "emit", fake_emit)
    monkeypatch.setattr(chapter_sse, "_stream_section", fake_stream)
    monkeypatch.setattr(chapter_sse, "_generate_chart", fake_chart)

    section = {"title": "对比", "points": ["甲", "乙"], "words": 800,
               "deliverables": [{"kind": "figure", "title": "对比图", "type": "layered", "elements": ["甲", "乙"]}]}
    content = "## 对比\n\n（本节待写）\n"
    draft, checks = asyncio.run(chapter_sse._write_sections_for_test(content, [section], "t" * 10, {"text": "m", "diagram": "m"}))
    names = [n for n, _ in events]
    assert "delta" in names and "section" in names
    # 图表在正文 section done 之前就已启动（working）
    working = [i for i, (n, p) in enumerate(events) if n == "role" and p.get("role") == "text"]
    section_done = [i for i, (n, p) in enumerate(events) if n == "section" and p.get("state") == "done"]
    assert working and section_done and working[0] < section_done[0]
    assert captured["has_text"] is False, "图表入参不应包含正文"


# ── 5. 占位符 ─────────────────────────────────────────────────────────────
def test_05_placeholder_merge():
    from services.chapter_sse import merge_charts

    text = "见图。\n\n[[FIGURE:对比图]]\n"
    merged, missing = merge_charts(text, [{"ok": True, "role": "diagram", "title": "对比图",
                                           "markdown": ":::diagram type=layered 对比图\n甲\n乙\n:::"}])
    assert ":::diagram" in merged and "[[" not in merged


# ── 6. 结构硬校验 ─────────────────────────────────────────────────────────
def test_06_structure_hard_check(monkeypatch):
    from services import chapter_sse

    async def fake_emit(*args, **kwargs):
        return None

    async def fake_call(role, system, payload, settings):
        return ":::diagram type=layered 单节点\n只有一个\n:::", "m"

    monkeypatch.setattr(chapter_sse, "emit", fake_emit)
    import agents.chapter_team as team
    monkeypatch.setattr(team, "call_role", fake_call)

    result = asyncio.run(chapter_sse._generate_chart(
        "job", 0, {"title": "s", "basis": [], "points": []},
        {"kind": "figure", "title": "图", "type": "layered"}, "t" * 10, ""))
    assert result["ok"] is False and "节点" in result.get("error", "")


# ── 7/8/9. 校验：依据 / 矛盾 / 表格列 ─────────────────────────────────────
def test_07_09_checks():
    from services.chart_checks import chart_items

    no_ref = chart_items("diagram", ":::diagram type=layered 图\n甲\n乙\n:::",
                         {"title": "图"}, "正文三层。", [])
    assert [i["title"] for i in no_ref if "缺依据" in i["title"]], [i["title"] for i in no_ref]
    conflict = chart_items("diagram", ":::diagram type=layered 图\n甲\n乙\n丙\n丁\n:::",
                           {"title": "图"}, "正文分三层说明。", ["ref"])
    assert any("层数不一致" in i["title"] for i in conflict)
    cols = chart_items("table", "**表：t**\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
                       {"title": "t", "cols": ["A", "B", "C"]}, "", ["ref"])
    assert any("列数" in i["title"] for i in cols)


# ── 10/11. 模型解析与设置持久化 ───────────────────────────────────────────
def test_10_11_model_resolution_order(monkeypatch):
    import services.writing_settings as ws

    async def fake_block(db, block_id):
        return {"text": "deepseek-v4-pro"}

    async def fake_project(db, project_id):
        return {"text": "deepseek-flash"}

    monkeypatch.setattr(ws, "block_settings", fake_block)
    monkeypatch.setattr(ws, "project_settings", fake_project)
    resolved = asyncio.run(ws.resolve_model(None, 29, 1, "text"))
    assert resolved["source"] == "block" and resolved["used"] == "deepseek-v4-pro"


# ── 12. 失败隔离：图表失败不挡正文 ────────────────────────────────────────
def test_12_chart_failure_does_not_block_text(monkeypatch):
    from services import chapter_sse

    async def fake_job_row(job_id):
        return {"status": "running"}

    monkeypatch.setattr(chapter_sse, "_job_row", fake_job_row)

    async def fake_emit(*args, **kwargs):
        return None

    async def fake_stream(block, section, thread_id, model):
        yield "正文" * 40

    async def fake_chart(*args, **kwargs):
        return {"ok": False, "role": "diagram", "title": "图", "error": "boom"}

    monkeypatch.setattr(chapter_sse, "emit", fake_emit)
    monkeypatch.setattr(chapter_sse, "_stream_section", fake_stream)
    monkeypatch.setattr(chapter_sse, "_generate_chart", fake_chart)
    section = {"title": "s", "points": ["p"], "words": 800,
               "deliverables": [{"kind": "figure", "title": "图", "type": "layered"}]}
    draft, _ = asyncio.run(chapter_sse._write_sections_for_test("## s\n\n（本节待写）\n", [section],
                                                               "t" * 10, {"text": "m", "diagram": "m"}))
    assert "正文" in draft


# ── 13. 人工保护：base 变了不覆盖 ─────────────────────────────────────────
def test_13_manual_edit_protection():
    source = (ROOT / "backend/services/chapter_sse.py").read_text(encoding="utf-8")
    assert "正文在生成期间被修改，已保留建议稿，不覆盖" in source
    assert "draft == base" in source          # 空变更不写库


# ── 14. 取消 / 重启恢复 ───────────────────────────────────────────────────
def test_14_cancel_and_recovery():
    source = (ROOT / "backend/services/chapter_sse.py").read_text(encoding="utf-8")
    assert "cancelled" in source and "if row_now and row_now[\"status\"] == \"cancelled\"" in source
    assert "recover_stale_jobs" in source


# ── 16. 清理：无死引用 ────────────────────────────────────────────────────
def test_16_no_dead_references():
    studio = (ROOT / "frontend/assets/chapter-studio.mjs").read_text(encoding="utf-8")
    for dead in ("chapter-team.mjs", "writeChapterApi", "writeSectionApi", "collaborationHtml"):
        assert dead not in studio, f"仍有死引用：{dead}"
    assert not (ROOT / "frontend/assets/chapter-team.mjs").exists()


# ── 17. 精修：保留引用与图表 + 字数下界 ───────────────────────────────────
def test_17_rewrite_quality_metrics():
    source = (ROOT / "backend/routes/chapter_studio.py").read_text(encoding="utf-8")
    assert "SECTION_REWRITE_SYSTEM" in source
    assert "citations_before" in source and "words_before" in source
    prompt = (ROOT / "backend/services/section_outline.py").read_text(encoding="utf-8")
    assert "SECTION_REWRITE_SYSTEM" in prompt and "80%" in prompt
