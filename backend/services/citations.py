"""官方源引用（P2）：不进素材库，只作外部依据的溯源记录。

产品约束（用户口径）：外部检索的信息**必须有来源、是可核验的官方原文**，
不能是二手转发或无发布机构的内容。因此本模块只做三件事：

1. **抓取核验**：给定 URL → 抓取页面 → 抽标题/机构/日期/摘录 → 域名必须在白名单内；
2. **检索适配器**：配置了搜索 API 才真检索（`SERPER_API_KEY`），否则明确返回
   "未配置检索源"，引导用户粘贴官方链接由系统抓取核验 —— 绝不假装检索过；
3. **接受/拒绝留痕**：只有 accepted 的条目会进入编写上下文（package.citations），
   正文引用写【来源】机构 · 文号/日期 · 标题 · URL · 摘录。

白名单（官方源）：
- 政府：``.gov.cn``
- 南方电网 / 广东电网：``csg.cn``、``gd.csg.cn``
- 标准发布：``openstd.samr.gov.cn``、``.org.cn``（国家标准平台）
- 招标方与公共资源交易平台公告原文：需显式加入 whitelist_additions
"""

from __future__ import annotations

import json
import os
import re
import uuid

import httpx

OFFICIAL_SUFFIXES = (
    ".gov.cn",
    "csg.cn",
    "gd.csg.cn",
    "openstd.samr.gov.cn",
    "ggzy.gov.cn",
    "cebpubservice.com",
)
MAX_BYTES = 2_000_000
EXCERPT_LIMIT = 200


def domain_of(url: str) -> str:
    match = re.match(r"^https?://([^/]+)", str(url or "").strip(), re.I)
    return (match.group(1) if match else "").lower()


def is_official(url: str, whitelist_additions: list[str] | None = None) -> bool:
    domain = domain_of(url)
    if not domain:
        return False
    allowed = tuple(OFFICIAL_SUFFIXES) + tuple(whitelist_additions or ())
    return any(domain == item or domain.endswith("." + item) or domain.endswith(item)
               for item in allowed)


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def extract_page(html: str, url: str) -> dict:
    """从页面 HTML 里抽：标题 / 发布机构 / 日期 / 摘录（摘录截到 200 字）。"""
    title = ""
    for pattern in (r"(?is)<meta[^>]+property=[\"']og:title[\"'][^>]*content=[\"']([^\"']+)",
                    r"(?is)<title[^>]*>(.*?)</title>"):
        match = re.search(pattern, html or "")
        if match and match.group(1).strip():
            title = re.sub(r"\s+", " ", _strip_html(match.group(1))).strip()
            break
    publisher = ""
    for pattern in (r"(?is)<meta[^>]+name=[\"'](?:author|publisher|SiteName)[\"'][^>]*content=[\"']([^\"']+)",
                    r"(?is)<meta[^>]+property=[\"']og:site_name[\"'][^>]*content=[\"']([^\"']+)"):
        match = re.search(pattern, html or "")
        if match and match.group(1).strip():
            publisher = _strip_html(match.group(1))[:60]
            break
    date = ""
    match = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", html or "")
    if match:
        date = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    text = _strip_html(html or "")
    if title and text.startswith(title):
        text = text[len(title):]
    excerpt = text[:EXCERPT_LIMIT].strip()
    return {"url": url, "domain": domain_of(url), "title": title or "(无标题)",
            "publisher": publisher, "published_at": date, "excerpt": excerpt}


def search_configured() -> bool:
    return bool(os.environ.get("SERPER_API_KEY"))


async def search_official(query: str, limit: int = 5) -> dict:
    """检索官方源。未配置检索 API 时**明确返回未配置**，不假装检索过。"""
    if not search_configured():
        return {"configured": False, "results": [],
                "message": "未配置检索源（SERPER_API_KEY）。可粘贴官方链接，由系统抓取核验。"}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
            content=json.dumps({"q": query, "num": max(limit, 5), "gl": "cn", "hl": "zh-cn"}),
        )
        resp.raise_for_status()
        organic = (resp.json() or {}).get("organic") or []
    results = []
    for item in organic:
        url = str(item.get("link") or "")
        if not is_official(url):
            continue
        results.append({"url": url, "domain": domain_of(url), "title": str(item.get("title") or ""),
                        "snippet": str(item.get("snippet") or "")[:EXCERPT_LIMIT]})
        if len(results) >= limit:
            break
    return {"configured": True, "results": results}


async def fetch_candidate(url: str, whitelist_additions: list[str] | None = None) -> dict:
    """抓取并核验一个官方链接，返回待接受的引用候选（不落库）。"""
    if not is_official(url, whitelist_additions):
        raise ValueError("不是官方来源域名，请改用政府/南方电网/标准发布/招标平台原文")
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; bid-assistant)"})
    resp.raise_for_status()
    if len(resp.content) > MAX_BYTES:
        raise ValueError("页面过大，请换更具体的原文页")
    candidate = extract_page(resp.text, url)
    if not candidate["excerpt"]:
        raise ValueError("页面没有可读正文（可能是登录页或脚本渲染）")
    candidate["status"] = "pending"
    return candidate


async def save_citation(db, project_id: int, payload: dict, thread_id: str = "", block_id: str = "") -> dict:
    """接受一条引用（落库，带溯源字段）。"""
    citation_id = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO citations (id, project_id, thread_id, block_id, query, url, domain, title, "
        "publisher, published_at, excerpt, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (citation_id, project_id, thread_id, block_id, str(payload.get("query") or ""),
         str(payload.get("url") or ""), str(payload.get("domain") or domain_of(payload.get("url"))),
         str(payload.get("title") or ""), str(payload.get("publisher") or ""),
         str(payload.get("published_at") or ""), str(payload.get("excerpt") or ""), "accepted"),
    )
    await db.commit()
    return {"id": citation_id, **{k: payload.get(k) for k in
                                  ("url", "domain", "title", "publisher", "published_at", "excerpt")},
            "status": "accepted"}


async def list_citations(db, project_id: int, status: str | None = None) -> list[dict]:
    where, args = "project_id = ?", [project_id]
    if status:
        where += " AND status = ?"
        args.append(status)
    rows = await (await db.execute(
        f"SELECT * FROM citations WHERE {where} ORDER BY created_at DESC LIMIT 200", tuple(args),
    )).fetchall()
    return [dict(row) for row in rows]


async def reject_citation(db, project_id: int, citation_id: str) -> bool:
    cursor = await db.execute(
        "UPDATE citations SET status='rejected' WHERE id=? AND project_id=?", (citation_id, project_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def citations_for_prompt(db, project_id: int, block_id: str, limit: int = 12) -> list[dict]:
    """编写上下文用的引用（只要 accepted；章级优先，含项目级）。"""
    rows = await (await db.execute(
        "SELECT url, domain, title, publisher, published_at, excerpt FROM citations "
        "WHERE project_id=? AND status='accepted' AND (block_id=? OR block_id='') "
        "ORDER BY created_at DESC LIMIT ?",
        (project_id, block_id, limit),
    )).fetchall()
    return [dict(row) for row in rows]


def citation_line(citation: dict) -> str:
    return "【来源】{} · {} · {} · {} · {}".format(
        citation.get("publisher") or citation.get("domain") or "",
        citation.get("published_at") or "日期待补", citation.get("title") or "",
        citation.get("url") or "", (citation.get("excerpt") or "")[:EXCERPT_LIMIT],
    )


__all__ = ["is_official", "extract_page", "fetch_candidate", "search_official", "search_configured",
           "save_citation", "list_citations", "reject_citation", "citations_for_prompt",
           "citation_line", "domain_of"]
