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


def normalize_title(title: str) -> str:
    """规范化标题：去掉站点后缀（"_最新政策_中国政府网"这类）与空白，用于"是否同一篇原文"的判断。"""
    text = str(title or "").split("_")[0]
    return re.sub(r"\s+", "", text)


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
    # 摘录优先取**最长的正文段落**：直接截 HTML 会把"首页 | 简 | 繁 | 登录…"这类导航带进来
    paragraphs = [_strip_html(match.group(1)) for match in
                  re.finditer(r"(?is)<p[^>]*>(.*?)</p>", html or "")]
    body = max((p for p in paragraphs if len(p) >= 24), key=len, default="")
    if not body:
        body = _strip_html(html or "")
        if title and body.startswith(title):
            body = body[len(title):]
    excerpt = re.sub(r"\s+", " ", body).strip()[:EXCERPT_LIMIT]
    return {"url": url, "domain": domain_of(url), "title": title or "(无标题)",
            "publisher": publisher, "published_at": date, "excerpt": excerpt}


# 官方入口目录：没有检索 API 时的兜底"先找来源"——只列官方站点，用户可点开、也可让系统扫描
OFFICIAL_PORTALS = [
    {"name": "中国政府网 · 政策文件库", "url": "https://www.gov.cn/zhengce/", "domain": "www.gov.cn",
     "hint": "国务院/部委政策、指导意见、规划"},
    {"name": "国家标准全文公开系统", "url": "https://openstd.samr.gov.cn/bzgk/gb/index", "domain": "openstd.samr.gov.cn",
     "hint": "GB/GB-T 标准原文与状态"},
    {"name": "中国南方电网", "url": "https://www.csg.cn/", "domain": "www.csg.cn",
     "hint": "集团战略、部署、新闻稿"},
    {"name": "广东电网", "url": "https://www.gd.csg.cn/", "domain": "gd.csg.cn",
     "hint": "省公司实践、招标与公告"},
    {"name": "全国公共资源交易平台", "url": "https://www.ggzy.gov.cn/", "domain": "www.ggzy.gov.cn",
     "hint": "招标公告、中标结果原文"},
    {"name": "中国招标投标公共服务平台", "url": "http://www.cebpubservice.com/", "domain": "www.cebpubservice.com",
     "hint": "公告与公示原文"},
]
_PORTAL_SCAN_LIMIT = 8


DOC_TITLE_HINTS = ("政策", "意见", "通知", "办法", "规划", "标准", "公告", "公示", "指南", "规定", "纲要")


def _link_candidates(html: str, base_url: str, keywords: list[str], limit: int = _PORTAL_SCAN_LIMIT) -> list[dict]:
    """从官方页面里挑链接。

    两档：① 标题命中检索词（最相关）；② 标题像"文档"（政策/意见/通知/标准/公告…）的兜底，
    因为官方入口首页的链接标题通常不含检索词 —— 只给导航，不给候选等于没帮上忙。
    """
    from urllib.parse import urljoin

    matched, docs, seen = [], [], set()
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"'>]+)[\"'][^>]*>(.*?)</a>", html or "", re.I | re.S):
        href = match.group(1).strip()
        title = _strip_html(match.group(2))
        if not title or len(title) < 6:
            continue
        url = urljoin(base_url, href)
        if not is_official(url) or (url, title) in seen:
            continue
        seen.add((url, title))
        hit = bool(keywords) and any(keyword in title for keyword in keywords)
        item = {"url": url, "title": title[:80], "domain": domain_of(url), "matched": hit}
        if hit:
            matched.append(item)
        elif any(hint in title for hint in DOC_TITLE_HINTS):
            docs.append(item)
        if len(matched) >= limit:
            break
    return (matched + docs)[:limit]


async def discover_candidates(query: str) -> dict:
    """先找官方来源：配置了检索 API 就真检索；否则扫描官方入口页，按关键词挑链接。

    返回的候选都标 ``source``：``search``（检索 API）/ ``portal``（官方入口扫描）/ ``portal-list``（仅入口）。
    用户仍可自己粘贴链接补充来源。
    """
    keywords = [part for part in re.split(r"[\s，,、；;]+", str(query or "")) if len(part) >= 2][:4]
    if search_configured():
        data = await search_official(query)
        for item in data.get("results") or []:
            item["source"] = "search"
        return {**data, "portals": OFFICIAL_PORTALS}
    candidates: list[dict] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for portal in OFFICIAL_PORTALS:
            try:
                resp = await client.get(portal["url"], headers={"User-Agent": "Mozilla/5.0 (compatible; bid-assistant)"})
                if resp.status_code >= 400:
                    continue
                candidates.extend(_link_candidates(resp.text, portal["url"], keywords))
            except Exception:
                continue
            if len(candidates) >= _PORTAL_SCAN_LIMIT:
                break
    return {"configured": False, "results": candidates[: _PORTAL_SCAN_LIMIT],
            "portals": OFFICIAL_PORTALS,
            "message": ("未配置检索源（SERPER_API_KEY）：已扫描官方入口页并挑出候选（标题命中检索词优先，"
                        "其次是有文档特征的链接），点一个用它「抓取核验」，或直接粘贴官方原文链接。"
                        if candidates else
                        "未配置检索源（SERPER_API_KEY），官方入口页也没有可推荐链接；"
                        "请点开官方入口自己查找，或粘贴官方原文链接。配置检索 API 后可自动按关键词找原文。")}


def _html_table_to_markdown(table_html: str) -> str:
    """HTML 表格 → Markdown 表格（来源里的表格常常就是可直接沿用的素材）。"""
    rows = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table_html or "")
    parsed = []
    for row in rows:
        cells = [_strip_html(cell) for cell in re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)]
        cells = [cell.replace("|", "／").strip() for cell in cells]
        if any(cells):
            parsed.append(cells)
    if len(parsed) < 2:
        return ""
    width = max(len(row) for row in parsed)
    lines = []
    for index, row in enumerate(parsed):
        cells = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


NAV_HINTS = ("ICP备", "公网安备", "网站标识码", "无障碍", "返回顶部", "版权所有", "关注我们", "扫码")
SKIP_IMAGE_HINTS = ("logo", "icon", "qrcode", "banner", "btn", "arrow", "weixin", "wx_", "avatar",
                    "newsearch", "shouqi", "footer", "header", "bg", "sprite", "spacer")


def extract_segments(html: str, url: str, limit: int = 40) -> list[dict]:
    """把来源页面拆成**可用片段**：段落 / 表格 / 图片（带所在章节标题）。

    用户的需求不是"有条来源"，而是"这篇来源里哪一段、哪张表、哪张图能直接用"。
    """
    segments: list[dict] = []
    heading = ""
    # 按文档顺序扫标题/段落/表格/图片
    pattern = re.compile(r"(?is)<h([1-3])[^>]*>(.*?)</h\1>|<p[^>]*>(.*?)</p>|<table[^>]*>(.*?)</table>|<img[^>]+src=[\"']([^\"'>]+)[\"'][^>]*>")
    for match in pattern.finditer(html or ""):
        if len(segments) >= limit:
            break
        level, heading_text, paragraph, table, image = match.groups()
        if heading_text:
            heading = _strip_html(heading_text)[:60]
            continue
        if table:
            markdown = _html_table_to_markdown(table)
            if markdown:
                segments.append({"kind": "table", "heading": heading, "content": markdown[:2000]})
            continue
        if image:
            from urllib.parse import urljoin
            src = urljoin(url, image.strip())
            low = src.lower()
            if any(hint in low for hint in SKIP_IMAGE_HINTS):
                continue                     # 站点 logo / 图标 / 二维码不是可用素材
            if is_official(src) or domain_of(src) == domain_of(url):
                segments.append({"kind": "image", "heading": heading, "content": src})
            continue
        text = _strip_html(paragraph or "")
        if len(text) < 30 or text.count("|") >= 3:
            continue
        if any(hint in text for hint in NAV_HINTS) and len(text) < 160:
            continue                          # 页脚/导航文字
        segments.append({"kind": "text", "heading": heading, "content": text[:1200]})
    return segments


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


async def remember_search(db, project_id: int, thread_id: str, block_id: str, query: str, results: list[dict]) -> None:
    """记一次检索（供同一章下次打开时复用候选，避免重复搜）。"""
    if not block_id:
        return
    await db.execute(
        "INSERT INTO citation_searches (id, project_id, thread_id, block_id, query, results) "
        "VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, project_id, thread_id, block_id, str(query)[:200],
         json.dumps(results[:8], ensure_ascii=False)),
    )
    await db.commit()


async def search_history(db, project_id: int, thread_id: str, block_id: str, limit: int = 5) -> list[dict]:
    rows = await (await db.execute(
        "SELECT query, results, created_at FROM citation_searches "
        "WHERE project_id=? AND block_id=? ORDER BY created_at DESC LIMIT ?",
        (project_id, str(block_id), limit),
    )).fetchall()
    history, seen = [], set()
    for row in rows:
        query = row["query"]
        if query in seen:            # 同一个检索词只留最近一次，避免历史里全是重复项
            continue
        seen.add(query)
        try:
            results = json.loads(row["results"] or "[]")
        except ValueError:
            results = []
        history.append({"query": query, "results": results, "created_at": row["created_at"]})
    return history


async def fetch_candidate(url: str, whitelist_additions: list[str] | None = None) -> dict:
    """抓取并核验一个链接，返回待接受的引用候选（不落库）。

    产品口径：**官方原文最优，非官方真实来源也允许**（否则素材不够、写不出来）——
    但非官方必须带来源标注、由用户确认。trust: official / unverified。
    """
    if not domain_of(url):
        raise ValueError("请粘贴完整链接（https://…）")
    official = is_official(url, whitelist_additions)
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; bid-assistant)"})
    resp.raise_for_status()
    if len(resp.content) > MAX_BYTES:
        raise ValueError("页面过大，请换更具体的原文页")
    candidate = extract_page(resp.text, url)
    excerpt = candidate["excerpt"] or ""
    if len(excerpt) < 30:
        raise ValueError("页面没有可读正文（可能是登录页或脚本渲染）")
    # 404/重定向页常被抓成站点页脚：ICP/备案号出现即视为无效原文
    if re.search(r"(ICP备|公网安备|网站标识码)", excerpt):
        raise ValueError("抓到的是站点页脚（可能是 404 或重定向页），请换原文链接")
    candidate["status"] = "pending"
    candidate["segments"] = extract_segments(resp.text, url)
    candidate["trust"] = "official" if official else "unverified"
    if not official:
        candidate["warning"] = ("非官方渠道：内容可能真实，但引用时必须标注来源并由你确认；"
                                "涉及政策/标准/指标等硬信息建议换官方原文")
    return candidate


async def suggest_query(db, project_id: int, thread_id: str, block_id: str) -> dict:
    """按**当前章节**推导检索词 —— 用户不需要盲搜。

    依据：章节标题 + 该章 evidence_required（素材缺口）+ 配方骨架里的第一项。
    同时给出"本章已有多少来源/材料"，让用户知道够不够。
    """
    from services.block_store import list_blocks

    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    block = next((b for b in blocks if str(b.get("block_id")) == str(block_id)), None)
    if block is None:
        raise ValueError("找不到该章节")
    title = str(block.get("title") or "")
    gaps = [item.strip() for item in re.split(r"[；;、，,/]", str(block.get("evidence_required") or "")) if len(item.strip()) >= 2]
    # 章节类型（与质检/计划同一口径）
    from services.quality_check import infer_layout
    stored = {}
    for row in await (await db.execute(
        "SELECT b.block_id, cs.layout FROM chapter_studio cs JOIN blocks b ON b.id=cs.block_id "
        "WHERE b.project_id=? AND b.run_thread_id=?", (project_id, thread_id),
    )).fetchall():
        stored[row["block_id"]] = row["layout"] or ""
    layout = infer_layout(block, blocks, stored)
    from services.writing_specs import list_specs
    recipe = ((await list_specs(db)).get("specs") or {}).get(layout, {}).get("structured") or {}
    skeleton = (recipe.get("skeleton") or [])[:1]
    keywords = []
    for source in [title, *(gaps[:2]), *skeleton]:
        for part in re.split(r"[\s、，,；;/]+", str(source or "")):
            part = part.strip()
            if len(part) >= 2 and part not in keywords:
                keywords.append(part)
    query = " ".join(keywords[:3])[:60]
    # 缺口逐条：每条给一个可检索的 query + 已补数量（引用时记录 gap）
    need_rows = []
    for gap_text in gaps[:5]:
        covered_row = await (await db.execute(
            "SELECT COUNT(*) AS n FROM citations WHERE project_id=? AND status='accepted' AND gap=? AND ("
            "  block_id=? OR EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = citations.id AND l.block_id = ?))",
            (project_id, gap_text, str(block_id), str(block_id)),
        )).fetchone()
        need_rows.append({"text": gap_text, "query": f"{title} {gap_text}"[:60],
                          "covered": int(covered_row["n"])})
    if not need_rows:
        need_rows.append({"text": "本章依据", "query": query, "covered": 0})
    have_citations = await (await db.execute(
        "SELECT COUNT(*) AS n FROM citations WHERE project_id=? AND status='accepted' AND ("
        "  block_id=? OR EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = citations.id AND l.block_id = ?))",
        (project_id, str(block_id), str(block_id)),
    )).fetchone()
    have_materials = await (await db.execute(
        "SELECT COUNT(*) AS n FROM blocks b JOIN chapter_materials cm ON cm.block_id = b.id "
        "WHERE b.project_id=? AND b.run_thread_id=? AND b.block_id=?", (project_id, thread_id, str(block_id)),
    )).fetchone()
    return {
        "query": query,
        "keywords": keywords[:6],
        "chapter": {"block_id": str(block_id), "title": title, "layout": layout},
        "gaps": gaps[:5],
        "needs": need_rows,
        "have": {"citations": int(have_citations["n"]), "materials": int(have_materials["n"])},
    }


async def save_citation(db, project_id: int, payload: dict, thread_id: str = "", block_id: str = "") -> dict:
    """接受一条引用（落库，带溯源字段）。非官方来源必须用户确认（confirmed=True）。"""
    url = str(payload.get("url") or "")
    trust = str(payload.get("trust") or ("official" if is_official(url) else "unverified"))
    if trust != "official" and not payload.get("confirmed"):
        raise ValueError("非官方来源需要你确认内容真实可靠后才能引用")
    if trust != "official":
        trust = "confirmed"
    # 同一章不重复接受同一篇原文：URL 相同，或**域名 + 规范化标题**相同
    # （同一篇原文常有两种路径顺序，只比 URL 会漏，实测漏过一次）
    title_key = normalize_title(payload.get("title") or "")
    existing = await (await db.execute(
        "SELECT id, url, domain, title FROM citations WHERE project_id=? AND status='accepted' AND ("
        "  block_id=? OR EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = citations.id AND l.block_id = ?))",
        (project_id, str(block_id), str(block_id)),
    )).fetchall()
    for row in existing:
        same_url = row["url"] == url
        same_doc = (title_key and normalize_title(row["title"]) == title_key
                    and (row["domain"] or domain_of(row["url"])) == (domain_of(url) or ""))
        if same_url or same_doc:
            raise ValueError("该来源已关联本章，无需重复接受")
    citation_id = uuid.uuid4().hex
    segments = payload.get("segments") or []
    await db.execute(
        "INSERT INTO citations (id, project_id, thread_id, block_id, query, url, domain, title, "
        "publisher, published_at, excerpt, status, trust, gap, segments, selected) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (citation_id, project_id, thread_id, block_id, str(payload.get("query") or ""),
         url, str(payload.get("domain") or domain_of(url)),
         str(payload.get("title") or ""), str(payload.get("publisher") or ""),
         str(payload.get("published_at") or ""), str(payload.get("excerpt") or ""), "accepted", trust,
         str(payload.get("gap") or ""), json.dumps(segments[:40], ensure_ascii=False),
         json.dumps(list(payload.get("selected") or []))),
    )
    if block_id:
        await db.execute("INSERT OR IGNORE INTO citation_links (citation_id, block_id) VALUES (?, ?)",
                         (citation_id, block_id))
    await db.commit()
    return {"id": citation_id, **{k: payload.get(k) for k in
                                  ("url", "domain", "title", "publisher", "published_at", "excerpt")},
            "status": "accepted", "trust": trust, "links": [block_id] if block_id else []}


async def refresh_segments(db, project_id: int, citation_id: str) -> dict:
    """重新抓取该来源并更新可用片段（老引用接受时还没有片段，需要补一次）。"""
    row = await (await db.execute(
        "SELECT url FROM citations WHERE id=? AND project_id=?", (citation_id, project_id),
    )).fetchone()
    if not row:
        raise ValueError("引用不存在")
    try:
        candidate = await fetch_candidate(row["url"])
    except ValueError as exc:
        raise ValueError(str(exc))
    segments = candidate.get("segments") or []
    await db.execute("UPDATE citations SET segments=?, selected='[]' WHERE id=?",
                     (json.dumps(segments, ensure_ascii=False), citation_id))
    await db.commit()
    return {"citation_id": citation_id, "segments": segments, "selected": []}


async def select_segments(db, project_id: int, citation_id: str, indices: list[int]) -> dict:
    """选用/取消选用片段：只有选中的片段会进入本章编写上下文。"""
    row = await (await db.execute(
        "SELECT segments FROM citations WHERE id=? AND project_id=?", (citation_id, project_id),
    )).fetchone()
    if not row:
        raise ValueError("引用不存在")
    try:
        segments = json.loads(row["segments"] or "[]")
    except ValueError:
        segments = []
    valid = sorted({int(index) for index in indices if 0 <= int(index) < len(segments)})
    await db.execute("UPDATE citations SET selected=? WHERE id=?", (json.dumps(valid), citation_id))
    await db.commit()
    return {"citation_id": citation_id, "selected": valid}


async def list_citations(db, project_id: int, status: str | None = None,
                          block_id: str | None = None) -> list[dict]:
    """引用列表（含多章关联）。block_id 过滤 = 关联到该章的引用。"""
    where, args = "project_id = ?", [project_id]
    if status:
        where += " AND status = ?"
        args.append(status)
    if block_id:
        where += (" AND (block_id = ? OR id IN (SELECT citation_id FROM citation_links WHERE block_id = ?))"
                  if False else " AND EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = citations.id AND l.block_id = ?)")
        args.append(block_id)
    rows = await (await db.execute(
        f"SELECT * FROM citations WHERE {where} ORDER BY created_at DESC LIMIT 200", tuple(args),
    )).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("segments", "selected"):
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except ValueError:
                item[key] = []
        links = await (await db.execute(
            "SELECT block_id FROM citation_links WHERE citation_id=?", (item["id"],),
        )).fetchall()
        item["links"] = [link["block_id"] for link in links] or ([item["block_id"]] if item.get("block_id") else [])
        result.append(item)
    return result


async def link_citation(db, project_id: int, citation_id: str, block_id: str) -> dict:
    """把一条已接受的依据关联到某章（多对多；可复用）。"""
    row = await (await db.execute(
        "SELECT id, status FROM citations WHERE id=? AND project_id=?", (citation_id, project_id),
    )).fetchone()
    if not row:
        raise ValueError("引用不存在")
    if row["status"] != "accepted":
        raise ValueError("只有已接受的引用才能关联章节")
    await db.execute("INSERT OR IGNORE INTO citation_links (citation_id, block_id) VALUES (?, ?)",
                     (citation_id, block_id))
    await db.commit()
    return {"citation_id": citation_id, "block_id": block_id, "linked": True}


async def unlink_citation(db, project_id: int, citation_id: str, block_id: str) -> dict:
    cursor = await db.execute(
        "DELETE FROM citation_links WHERE citation_id=? AND block_id=? "
        "AND citation_id IN (SELECT id FROM citations WHERE project_id=?)",
        (citation_id, block_id, project_id),
    )
    await db.commit()
    return {"removed": cursor.rowcount > 0}


async def reject_citation(db, project_id: int, citation_id: str) -> bool:
    cursor = await db.execute(
        "UPDATE citations SET status='rejected' WHERE id=? AND project_id=?", (citation_id, project_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def citations_for_prompt(db, project_id: int, block_id: str, limit: int = 12) -> list[dict]:
    """编写上下文用的引用（只要 accepted；章级优先，含项目级）。"""
    rows = await (await db.execute(
        "SELECT url, domain, title, publisher, published_at, excerpt, trust, segments, selected FROM citations "
        "WHERE project_id=? AND status='accepted' AND ("
        "  block_id='' OR block_id=? OR EXISTS ("
        "    SELECT 1 FROM citation_links l WHERE l.citation_id = citations.id AND l.block_id = ?)) "
        "ORDER BY created_at DESC LIMIT ?",
        (project_id, block_id, block_id, limit),
    )).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            segments = json.loads(item.pop("segments", None) or "[]")
            selected = json.loads(item.pop("selected", None) or "[]")
        except ValueError:
            segments, selected = [], []
        item["snippets"] = [segments[index] for index in selected
                            if isinstance(index, int) and 0 <= index < len(segments)]
        result.append(item)
    return result


def citation_line(citation: dict) -> str:
    trust = citation.get("trust") or "official"
    label = "【来源】" if trust == "official" else "【来源·非官方，用户已确认】"
    line = "{} {} · {} · {} · {} · {}".format(
        label,
        citation.get("publisher") or citation.get("domain") or "",
        citation.get("published_at") or "日期待补", citation.get("title") or "",
        citation.get("url") or "", (citation.get("excerpt") or "")[:EXCERPT_LIMIT],
    )
    snippets = citation.get("snippets") or []
    if snippets:
        # 用户选中的片段才是"可沿用的素材"：段落可改写、表格可整表沿用、图需另行下载上传
        parts = []
        for index, snippet in enumerate(snippets[:4], 1):
            kind = {"table": "表格", "image": "图片", "text": "段落"}.get(snippet.get("kind"), "内容")
            heading = snippet.get("heading") or ""
            content = str(snippet.get("content") or "")[:700]
            parts.append(f"  [{index}] {kind}{('《' + heading + '》') if heading else ''}：{content}")
        line += "\n  可参考片段（改写须保留来源标注）：\n" + "\n".join(parts)
    return line


__all__ = ["is_official", "extract_page", "fetch_candidate", "search_official", "search_configured",
           "OFFICIAL_PORTALS", "discover_candidates",
           "save_citation", "list_citations", "reject_citation", "citations_for_prompt",
           "citation_line", "domain_of", "link_citation", "unlink_citation", "suggest_query",
           "remember_search", "search_history", "normalize_title", "extract_segments", "select_segments", "refresh_segments"]
