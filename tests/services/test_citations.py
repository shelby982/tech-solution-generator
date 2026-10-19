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
    assert line.startswith("【来源】国家发改委 · 2024-03-15 · 指导意见 · https://www.gov.cn/a.html")
