"""章节大纲计划（P0-1）+ 分节写稿（P0-2）。

产品口径（对照真实手稿）：
- 手稿是「节 → 子主题 → 要素」层层展开，一段 176 字、首句结论、①②③ 分点；
- 所以先把一章拆成 6~10 个**节**（每个带要点、字数、是否需要表/图），
  再逐节写（600~1200 字），而不是一次生成整章。
"""
from __future__ import annotations

import json
import re

SECTION_WORDS_MIN, SECTION_WORDS_MAX = 500, 1400
MAX_SECTIONS = 12


def _clean(text, limit: int = 60) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def fallback_outline(chapter: dict) -> list[dict]:
    """没有模型时按交付物/任务确定性地给出大纲（保证流程可用）。"""
    sections: list[dict] = []
    chapter_basis = []
    for req in (chapter.get("requirements") or [])[:5]:
        text = req.get("text") if isinstance(req, dict) else str(req)
        if _clean(text, 80):
            chapter_basis.append({"type": "score_point", "ref": _clean(text, 80), "use": "本节需回应"})
    for task in chapter.get("tasks") or []:
        kind = task.get("kind") or "text"
        need = "table" if kind == "table" else ("figure" if kind == "diagram" else "")
        sections.append({
            "title": _clean(task.get("title") or "本节", 40),
            "points": [_clean(task.get("purpose") or "说明本节要点", 80)],
            "words": 900,
            "need": need,
            "basis": list(chapter_basis),
            "how": {"structure": "", "style": _clean(task.get("purpose"), 120), "pitfalls": []},
            "deliverables": _normalize_deliverables([], need),
            "source": "task",
        })
    if not sections:
        sections.append({"title": "现状与问题", "points": ["说明现状事实与差距"], "words": 900, "need": "", "source": "fallback"})
        sections.append({"title": "思路与措施", "points": ["给出解决思路与措施"], "words": 900, "need": "", "source": "fallback"})
    return sections[:MAX_SECTIONS]


def normalize_outline(raw, chapter: dict) -> list[dict]:
    """模型输出 → 规范大纲；不合格则退回 fallback。"""
    data = raw if isinstance(raw, dict) else None
    if data is None:
        try:
            data = json.loads(str(raw))
        except Exception:
            data = None
    items = (data or {}).get("sections") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) < 2:
        return fallback_outline(chapter)
    sections: list[dict] = []
    for item in items[:MAX_SECTIONS]:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 40)
        if not title:
            continue
        points = [_clean(p, 80) for p in (item.get("points") or []) if _clean(p, 80)][:5]
        try:
            words = int(item.get("words") or 900)
        except Exception:
            words = 900
        need = str(item.get("need") or "").strip().lower()
        if need not in ("table", "figure"):
            need = ""
        sections.append({
            "title": title,
            "points": points or ["说明本节要点"],
            "words": max(SECTION_WORDS_MIN, min(SECTION_WORDS_MAX, words)),
            "need": need,
            "basis": _normalize_basis(item.get("basis")),
            "how": _normalize_how(item.get("how")),
            "deliverables": _normalize_deliverables(item.get("deliverables"), need),
            "source": "model",
        })
    return sections if len(sections) >= 2 else fallback_outline(chapter)


_BASIS_TYPES = ("score_point", "clause", "policy", "material")


def _clean_list(value, limit: int = 8) -> list:
    return value if isinstance(value, list) else []


def _normalize_basis(raw) -> list[dict]:
    """依据：评分点 / 招标条款 / 政策 / 素材。"""
    out: list[dict] = []
    for item in _clean_list(raw)[:8]:
        if isinstance(item, str):
            text = _clean(item, 80)
            if text:
                out.append({"type": "policy", "ref": text, "use": ""})
            continue
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip()
        if kind not in _BASIS_TYPES:
            kind = "policy"
        ref = _clean(item.get("ref") or item.get("text"), 100)
        if not ref:
            continue
        out.append({"type": kind, "ref": ref, "use": _clean(item.get("use"), 60)})
    return out


def _normalize_how(raw) -> dict:
    """写法：结构 / 句式 / 禁止项。"""
    if isinstance(raw, str):
        return {"structure": _clean(raw, 160), "style": "", "pitfalls": []}
    if not isinstance(raw, dict):
        return {"structure": "", "style": "", "pitfalls": []}
    pitfalls = [_clean(x, 60) for x in _clean_list(raw.get("pitfalls")) if _clean(x, 60)][:5]
    return {"structure": _clean(raw.get("structure"), 200), "style": _clean(raw.get("style"), 200), "pitfalls": pitfalls}


def _normalize_deliverables(raw, need: str = "") -> list[dict]:
    """交付物：文字（字数）/ 表格（列名）/ 图（图型）。"""
    out: list[dict] = []
    for item in _clean_list(raw)[:5]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in ("text", "table", "figure"):
            continue
        entry = {"kind": kind, "title": _clean(item.get("title"), 40) or {"text": "正文", "table": "表格", "figure": "图示"}[kind]}
        if kind == "text":
            try:
                entry["words"] = max(150, min(2000, int(item.get("words") or 600)))
            except Exception:
                entry["words"] = 600
        elif kind == "table":
            entry["cols"] = [_clean(c, 20) for c in _clean_list(item.get("cols")) if _clean(c, 20)][:6]
        else:
            dtype = str(item.get("type") or "").strip()
            entry["type"] = dtype if dtype in ("layered", "flow", "timeline") else "layered"
        out.append(entry)
    if not out:
        out = [{"kind": "text", "title": "正文", "words": 700}]
        if need == "table":
            out.append({"kind": "table", "title": "对比表", "cols": []})
        if need == "figure":
            out.append({"kind": "figure", "title": "结构图", "type": "layered"})
    return out


OUTLINE_SYSTEM = (
    "你是标书主编。根据章节标题、编写要求（评分点/招标条款）、交付物与素材情况，"
    "把这一章拆成 6~10 个**节**（每节 600~1200 字），并为**每一节**写清三件事：\n"
    "（a）依据 basis：本节凭什么写——从评分点/招标条款/政策文件/素材中挑选，给出名称与用途；\n"
    "（b）写法 how：本节怎么写——结构（先写什么再写什么）、句式要求、禁止项；\n"
    "（c）交付物 deliverables：本节要交什么——文字（多少字）、表格（表名与列名）、图（图名与图型）；\n"
    "要求：节标题是名词性短语（≤20 字）且互不重叠；每节 2~5 条要点（≤40 字/条）；覆盖评分点。\n"
    "只输出 JSON：{\"sections\":[{\"title\":\"…\",\"points\":[\"…\"],\"words\":900,"
    "\"basis\":[{\"type\":\"score_point|clause|policy|material\",\"ref\":\"…\",\"use\":\"…\"}],"
    "\"how\":{\"structure\":\"…\",\"style\":\"…\",\"pitfalls\":[\"…\"]},"
    "\"deliverables\":[{\"kind\":\"text|table|figure\",\"title\":\"…\",\"words\":600,\"cols\":[\"…\"],\"type\":\"layered|flow|timeline\"}]}]}"
)

SECTION_SYSTEM = (
    "你是标书编写专家。撰写**指定的一节**（不是整章）。\n"
    "用户消息里会给出本节的：依据 basis（评分点/条款/政策/素材）、写法 how（结构/句式/禁止项）、"
    "交付物 deliverables（文字字数、表格列名、图示类型）与【本次额外要求】——必须按这些约束写。\n"
    "基本要求：\n"
    "1) 开头一句结论（本节要说明什么结论），不要写「本节将…」；\n"
    "2) 段落 3~6 段，每段 ≤200 字，段内用 ①②③ 分点；\n"
    "3) 能用数字/指标/标准号的地方必须用（来自给定素材与要求，禁止编造具体数字）；\n"
    "4) 引用政策/标准必须带《文件名》，并在句尾标注【来源：文件名】；\n"
    "5) 不使用「综上所述/赋能/闭环/抓手/生态位」等空话；\n"
    "6) 只输出这一节的正文（Markdown，无需标题），不要解释。"
)


def build_section_payload(chapter: dict, section: dict, evidence: str = "") -> dict:
    return {
        "chapter": chapter.get("title") or "",
        "section": section.get("title") or "",
        "points": section.get("points") or [],
        "words": section.get("words") or 900,
        "need": section.get("need") or "",
        "requirements": [r.get("text") if isinstance(r, dict) else str(r) for r in (chapter.get("requirements") or [])][:8],
        "basis": section.get("basis") or [],
        "how": section.get("how") or {},
        "deliverables": section.get("deliverables") or [],
        "evidence": evidence[:6000],
    }


_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")


def section_ranges(content: str) -> list[tuple[str, int, int]]:
    """正文里的 `## 标题` 分段 → [(title, start, end)]（end 为下一标题起点）。"""
    text = str(content or "")
    marks = [(m.group(1).strip(), m.start()) for m in _HEADING_RE.finditer(text)]
    ranges = []
    for i, (title, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        ranges.append((title, start, end))
    return ranges


def replace_section(content: str, title: str, body: str) -> tuple[str, bool]:
    """把 `## title` 这一节替换为 body；不存在则追加到末尾。返回 (新正文, 是否替换)。"""
    text = str(content or "")
    target = str(title or "").strip()
    for name, start, end in section_ranges(text):
        if name == target:
            return (text[:start] + f"## {target}\n\n{body.strip()}\n\n" + text[end:]).strip() + "\n", True
    addition = f"## {target}\n\n{body.strip()}"
    return ((text.rstrip() + "\n\n" + addition).strip() + "\n"), False
