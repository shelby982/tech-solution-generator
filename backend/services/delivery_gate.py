"""交付门（P0）：把"一眼看出没做完"的问题挡在导出之前。

三类硬门 + 一类可自动修：
  ① 内部语法残留  —— `:::diagram` / `type=layered` / `[[FIGURE:` / `[[TABLE:` / `"task_id"`
  ② 内部术语外泄  —— 任务包 / 评分点 / 条款包 …（可自动替换为标准说法）
  ③ 占位符未清零  —— 【待补充：…】 / 【待补事实：…】
纯规则，不调模型。每条都带 locator（节 + 原句），便于前端定位。
"""
from __future__ import annotations

import re

# ── ① 内部语法残留 ────────────────────────────────────────────────────────
_SYNTAX_PATTERNS = (
    (re.compile(r"\btype=(?:layered|flow|timeline)\b"), "图类型语法残留"),
    (re.compile(r"\[\[FIGURE:[^\]]*\]\]"), "图片占位符未替换"),
    (re.compile(r"\[\[TABLE:[^\]]*\]\]"), "表格占位符未替换"),
    (re.compile(r'"(?:task_id|job_id|block_id|section_index)"\s*:'), "内部字段泄漏"),
    (re.compile(r"^\s*:::\s*$", re.M), "图块结束标记残留"),
)

# ── ② 内部术语外泄（自动替换为标准说法）──────────────────────────────────
_TERM_REPLACEMENTS = (
    ("任务包", "招标文件"),
    ("评分点", "评分要求"),
    ("条款包", "招标文件"),
    ("章节包", "章节"),
    ("工作项包", "工作项"),
    ("生成任务", "编写任务"),
)
# 这些是"允许出现"的上下文（避免误替换，例如已经写成"评分要求"的）
_TERM_ALLOW = ("评分要求",)

# ── ③ 占位符 ──────────────────────────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(r"【(待补充|待补事实|待确认)[:：]?([^】]{0,60})】")
_SECTION_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.M)


def _section_of(content: str, offset: int) -> str:
    """offset 所在的小节标题。"""
    section = ""
    for match in _SECTION_RE.finditer(str(content or "")):
        if match.start() > offset:
            break
        section = match.group(1).strip()
    return section


def _line_of(content: str, offset: int) -> str:
    text = str(content or "")
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:end if end >= 0 else len(text)].strip()


def _item(kind: str, blocking: bool, title: str, content: str, offset: int,
          fixable: str = "", extra: dict | None = None) -> dict:
    line = _line_of(content, offset)
    return {
        "kind": kind,
        "blocking": blocking,
        "fixable": fixable,                     # syntax | term | ""
        "title": title,
        "section": _section_of(content, offset),
        "quote": line[:80],
        "offset": offset,
        **(extra or {}),
    }


_DIAGRAM_BLOCK = re.compile(r"(?ms)^:::diagram([^\n]*)\n(.*?)\n:::[ \t]*$")
_FIGURE_BLOCK = re.compile(r"(?ms)^:::figure([^\n]*)\n(.*?)\n:::[ \t]*$")
_UNCLOSED_BLOCK = re.compile(r"(?m)^:::[a-z]+[^\n]*$")


def _legal_ranges(content: str) -> list[tuple[int, int]]:
    """能正常渲染的图块/素材块区间（这些区间内的 ::: / type= 都是合法的）。"""
    from services.diagrams import parse_diagram

    text = str(content or "")
    ranges: list[tuple[int, int]] = []
    for match in _DIAGRAM_BLOCK.finditer(text):
        head = match.group(0).splitlines()[0].strip()
        try:
            nodes = parse_diagram(head, match.group(2).splitlines()).get("nodes") or []
        except Exception:
            nodes = []
        if len(nodes) >= 2:
            ranges.append((match.start(), match.end()))
    for match in _FIGURE_BLOCK.finditer(text):
        if "![" in match.group(2):
            ranges.append((match.start(), match.end()))
    return ranges


def _in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def _diagram_block_items(content: str) -> list[dict]:
    """合法图块（能解析出 ≥2 节点）不算问题；不合法/未闭合才算。

    导出时"图没渲染、只剩一行文字"的真实原因是块本身不合法，
    所以这里按"能不能解析成图"判断，而不是一律拦 `:::`。
    """
    from services.diagrams import parse_diagram

    text = str(content or "")
    items: list[dict] = []
    legal_ranges: list[tuple[int, int]] = []
    for match in _DIAGRAM_BLOCK.finditer(text):
        legal_ranges.append((match.start(), match.end()))
        head = match.group(0).splitlines()[0].strip()
        body = match.group(2).splitlines()
        try:
            parsed = parse_diagram(head, body)
            nodes = parsed.get("nodes") or []
        except Exception:
            nodes = []
        if len(nodes) < 2:
            items.append(_item("syntax", True, f"图块无法渲染（{len(nodes)} 个节点，要求 2~12）",
                               text, match.start(), fixable="syntax"))
    for match in _FIGURE_BLOCK.finditer(text):
        legal_ranges.append((match.start(), match.end()))
        if "![" not in match.group(2):
            items.append(_item("syntax", True, "素材图块缺少图片地址（导出会是空框）",
                               text, match.start(), fixable="syntax"))
    # 未闭合的 ::: 行（不在合法块内）
    for match in _UNCLOSED_BLOCK.finditer(text):
        if any(start <= match.start() < end for start, end in legal_ranges):
            continue
        if re.match(r"^:::\s*$", match.group(0)):
            continue
        items.append(_item("syntax", True, "图块未闭合（缺少结尾 :::）", text, match.start(), fixable="syntax"))
    return items


def scan(content: str) -> dict:
    """返回 {blocking: [...], auto_fixable: [...], summary}。"""
    text = str(content or "")
    blocking: list[dict] = []
    auto_fixable: list[dict] = []

    # ① 内部语法（图块按"能否解析"判断，合法的 :: diagram 不拦）
    for item in _diagram_block_items(text):
        blocking.append(item)
        auto_fixable.append(item)
    legal = _legal_ranges(text)
    for pattern, label in _SYNTAX_PATTERNS:
        for match in pattern.finditer(text):
            if _in_ranges(match.start(), legal):
                continue                      # 合法图块里的 type= / ::: 不算问题
            item = _item("syntax", True, f"内部语法残留：{label}", text, match.start(),
                         fixable="syntax", extra={"match": match.group(0)[:80]})
            blocking.append(item)
            auto_fixable.append(item)

    # ② 内部术语
    for term, replacement in _TERM_REPLACEMENTS:
        for match in re.finditer(re.escape(term), text):
            start = match.start()
            window = text[max(0, start - 6): start + len(term) + 6]
            if any(allowed in window for allowed in _TERM_ALLOW if allowed != term):
                continue
            item = _item("term", True, f"内部术语外泄：「{term}」→「{replacement}」", text, start,
                         fixable="term", extra={"term": term, "replacement": replacement})
            blocking.append(item)
            auto_fixable.append(item)

    # ③ 占位符
    for match in _PLACEHOLDER_RE.finditer(text):
        blocking.append(_item("placeholder", True,
                              f"占位符未清零：「{match.group(0)[:24]}」", text, match.start(),
                              fixable="", extra={"placeholder": match.group(0)}))

    summary = {
        "blocking": len(blocking),
        "auto_fixable": len(auto_fixable),
        "manual": len(blocking) - len(auto_fixable),
        "by_kind": {
            "syntax": sum(1 for i in blocking if i["kind"] == "syntax"),
            "term": sum(1 for i in blocking if i["kind"] == "term"),
            "placeholder": sum(1 for i in blocking if i["kind"] == "placeholder"),
        },
    }
    return {"blocking": blocking, "auto_fixable": auto_fixable, "summary": summary}


def auto_fix(content: str, kinds: tuple[str, ...] = ("syntax", "term")) -> tuple[str, int, list[str]]:
    """自动清理：删除内部语法残留、替换内部术语。返回 (新正文, 改动数, 说明)。"""
    text = str(content or "")
    changes: list[str] = []
    if "syntax" in kinds:
        legal = _legal_ranges(text)
        # 只删"未闭合的 ::: 行"和游离的 type= 片段，不动合法图块
        unclosed = [m for m in _UNCLOSED_BLOCK.finditer(text)
                    if not re.match(r"^:::\s*$", m.group(0))
                    and not _in_ranges(m.start(), legal)]
        for match in reversed(unclosed):
            text = text[:match.start()] + text[match.end():]
        if unclosed:
            changes.append(f"清理未闭合图块标记 {len(unclosed)} 处")
        for pattern, label in _SYNTAX_PATTERNS:
            matches = [m for m in pattern.finditer(text) if not _in_ranges(m.start(), legal)]
            if matches:
                for match in reversed(matches):
                    text = text[:match.start()] + text[match.end():]
                changes.append(f"清理{label} {len(matches)} 处")
    if "term" in kinds:
        for term, replacement in _TERM_REPLACEMENTS:
            count = text.count(term)
            if count:
                text = text.replace(term, replacement)
                changes.append(f"「{term}」→「{replacement}」 {count} 处")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, len(changes), changes
