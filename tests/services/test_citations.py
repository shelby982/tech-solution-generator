"""官方源引用：只采可核验的官方原文；未配置检索源时明确告知。"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.citations import (citation_line, extract_page, is_official, list_citations,
                                reject_citation, save_citation, search_official)


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "citations.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (name) VALUES ('p')")
    await conn.commit()
    yield conn
    await conn.close()


def test_is_official_whitelist():
    assert is_official("https://www.gov.cn/zhengce/content/2024-01/01/content_1.htm")
    assert is_official("https://www.csg.cn/xwzx/2024/xx.html")
    assert is_official("https://openstd.samr.gov.cn/bzgk/gb/")
    assert not is_official("https://weibo.com/123")
    assert not is_official("https://mp.weixin.qq.com/s/abc")
    assert not is_official("随便粘贴的文字")


def test_extract_page_pulls_title_publisher_date_excerpt():
    html = """<html><head>
      <meta property="og:title" content="关于加快推进数字化转型的指导意见">
      <meta name="author" content="国家发展和改革委员会">
      <title>ignored</title></head>
      <body><p>2024年3月15日，国家发展改革委发布指导意见，要求到2026年…</p></body></html>"""
    page = extract_page(html, "https://www.gov.cn/a.html")
    assert page["title"] == "关于加快推进数字化转型的指导意见"
    assert page["publisher"] == "国家发展和改革委员会"
    assert page["published_at"] == "2024-03-15"
    assert page["excerpt"] and "国家发展改革委发布指导意见" in page["excerpt"]
    assert page["domain"] == "www.gov.cn"


async def test_save_list_and_reject_citation(db):
    saved = await save_citation(db, 1, {
        "url": "https://www.gov.cn/a.html", "domain": "www.gov.cn",
        "title": "指导意见", "publisher": "发改委", "published_at": "2024-03-15",
        "excerpt": "摘录", "query": "数字化转型",
    }, block_id="s1.2.1.1")
    assert saved["status"] == "accepted"
    listed = await list_citations(db, 1, "accepted")
    assert len(listed) == 1 and listed[0]["title"] == "指导意见"
    assert await reject_citation(db, 1, saved["id"]) is True
    assert await list_citations(db, 1, "accepted") == []


async def test_search_reports_not_configured(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    data = await search_official("数字化转型 政策")
    assert data["configured"] is False and data["results"] == []
    assert "未配置检索源" in data["message"]


def test_citation_line_format():
    line = citation_line({"publisher": "国家发改委", "published_at": "2024-03-15",
                          "title": "指导意见", "url": "https://www.gov.cn/a.html", "excerpt": "摘录"})
    assert line.startswith("【来源】")
    assert "国家发改委 · 2024-03-15 · 指导意见 · https://www.gov.cn/a.html" in line
    # 非官方来源要显式标注，供人工确认
    assert citation_line({"trust": "confirmed", "domain": "某行业网", "title": "行业报告",
                          "url": "https://example.org/a", "excerpt": "x"}).startswith("【来源·非官方，用户已确认】")


def test_link_candidates_only_official_and_keyword_matched():
    """官方入口扫描：只保留官方域名内、标题命中关键词的链接。"""
    from services.citations import _link_candidates
    html = """
    <a href="/zhengce/content/2024-03/15/content_1.htm">关于加快推进数字化转型的指导意见</a>
    <a href="https://weibo.com/123">数字化转型 微博</a>
    <a href="/about.html">关于本站</a>
    """
    items = _link_candidates(html, "https://www.gov.cn/zhengce/", ["数字化"])
    assert len(items) == 1
    assert items[0]["url"].startswith("https://www.gov.cn/")
    assert "数字化" in items[0]["title"]


def test_official_portals_shape():
    from services.citations import OFFICIAL_PORTALS
    assert len(OFFICIAL_PORTALS) >= 4
    for portal in OFFICIAL_PORTALS:
        assert portal["url"].startswith("http")
        assert portal["domain"] in portal["url"]
        assert portal["name"] and portal["hint"]


def test_404_footer_page_rejected_by_fetch_guard():
    """抓取校验：页脚（ICP/备案）不当成候选 —— 404/重定向页容易抓到这种内容。"""
    import asyncio
    from unittest.mock import patch

    from services import citations as module

    class FakeResponse:
        status_code = 200
        text = "<html><head><title>站点</title></head><body><p>网站标识码bm01000001 京ICP备05070218号</p></body></html>"
        content = text.encode("utf-8")

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    with patch.object(module.httpx, "AsyncClient", FakeClient):
        try:
            asyncio.get_event_loop().run_until_complete(module.fetch_candidate("https://www.gov.cn/zhengce/"))
        except ValueError as exc:
            assert "页脚" in str(exc)
        else:
            raise AssertionError("页脚页应被拒绝")


async def test_suggest_query_derives_from_chapter_and_reports_coverage(db):
    """按章节推导检索词：标题 + 素材缺口 + 配方骨架，并报告本章已有来源/材料数。"""
    from services.citations import suggest_query

    await db.execute("INSERT INTO workflow_runs (thread_id, project_id, stage) VALUES ('t1', 1, 'outline_review')")
    await db.execute(
        "INSERT INTO blocks (project_id, block_id, title, level, content, order_idx, run_thread_id, status, evidence_required) "
        "VALUES (1, 's1.1', '项目背景现状目标', 3, '正文', 0, 't1', 'draft', '近三年行业数据；政策依据')")
    await db.commit()
    data = await suggest_query(db, 1, "t1", "s1.1")
    assert data["chapter"]["title"] == "项目背景现状目标"
    assert data["gaps"] == ["近三年行业数据", "政策依据"]
    assert data["query"] and "项目背景现状目标" in data["query"]
    assert data["have"] == {"citations": 0, "materials": 0}


async def test_needs_per_gap_with_covered_count(db):
    """缺口逐条：每个 evidence_required 一条检索建议；接受过引用的缺口标"已补 N 条"。"""
    from services.citations import save_citation, suggest_query

    await db.execute("INSERT INTO workflow_runs (thread_id, project_id, stage) VALUES ('t1', 1, 'outline_review')")
    await db.execute(
        "INSERT INTO blocks (project_id, block_id, title, level, content, order_idx, run_thread_id, status, evidence_required) "
        "VALUES (1, 's1.1', '建设范围', 3, '正文', 0, 't1', 'draft', '边界说明；同类项目业绩')")
    await db.commit()
    await save_citation(db, 1, {"url": "https://www.gov.cn/a.html", "title": "边界说明依据",
                                "excerpt": "x", "gap": "边界说明"}, block_id="s1.1")
    data = await suggest_query(db, 1, "t1", "s1.1")
    needs = {need["text"]: need["covered"] for need in data["needs"]}
    assert needs["边界说明"] == 1
    assert needs["同类项目业绩"] == 0
    assert all(need["query"] for need in data["needs"])


async def test_search_history_roundtrip(db):
    from services.citations import remember_search, search_history

    await remember_search(db, 1, "t1", "s1.1", "政策依据", [{"url": "https://www.gov.cn/a", "title": "A"}])
    await remember_search(db, 1, "t1", "s1.1", "行业数据", [{"url": "https://www.gov.cn/b", "title": "B"}])
    history = await search_history(db, 1, "t1", "s1.1")
    assert [item["query"] for item in history] == ["行业数据", "政策依据"]
    assert history[0]["results"][0]["title"] == "B"
