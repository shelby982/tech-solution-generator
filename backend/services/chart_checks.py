"""图表校验（v3 §7 M4）：依据可追溯 / 结构 / 图文不矛盾 / 表格列。

只产出"能定位到具体项"的问题；不静默删除任何内容。
"""
from __future__ import annotations

import re

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}
_COUNT_RE = re.compile(r"([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:个)?\s*(?:层|级|类|项|维度|阶段)")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万|个|项|人|台|套|笔|条|次|分钟|小时|%|年)")
_DIAGRAM_RE = re.compile(r"(?ms)^:::diagram([^\n]*)\n(.*?)\n:::")
_ROW_RE = re.compile(r"^\s*\|(.+?)\|\s*$")


def _to_int(token: str) -> int:
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token, 0)


def _diagram_nodes(markdown: str) -> tuple[str, list[str]]:
    match = _DIAGRAM_RE.search(markdown or "")
    if not match:
        return "", []
    head = match.group(1) or ""
    dtype = "flow"
    for kind in ("layered", "timeline", "flow"):
        if f"type={kind}" in head:
            dtype = kind
            break
    nodes = []
    for line in (match.group(2) or "").splitlines():
        text = line.strip().lstrip("-").strip()
        if text:
            nodes.append(text)
    return dtype, nodes


def chart_items(role: str, markdown: str, deliverable: dict, section_text: str,
                refs: list[str]) -> list[dict]:
    """返回该图表的问题项（空 = 通过）。"""
    items: list[dict] = []
    title = str((deliverable or {}).get("title") or "")
    # 设计版图表是图片资产，几何/内容校验在生成时做；这里不按文本图解析。
    if str(markdown or "").lstrip().startswith(":::figure"):
        return items
    if not refs:
        items.append({"group": "proof", "severity": "block",
                      "title": f"{'图' if role == 'diagram' else '表'}「{title}」缺依据",
                      "evidence": "该图表没有关联的条款/素材依据，无法核验",
                      "source": "依据可追溯", "action": "locate"})
    if role == "diagram":
        dtype, nodes = _diagram_nodes(markdown)
        if len(nodes) < 2:
            items.append({"group": "auto", "severity": "block", "title": f"图「{title}」节点不足",
                          "evidence": f"当前 {len(nodes)} 个节点（要求 2~12）",
                          "source": "结构校验", "action": "autofix"})
        # 图文矛盾：正文明确写了层/级/维度数量，而图层数不同
        text_counts = [_to_int(m.group(1)) for m in _COUNT_RE.finditer(section_text or "")]
        text_counts = [c for c in text_counts if c >= 2]
        if dtype == "layered" and text_counts and len(nodes):
            expected = max(set(text_counts), key=text_counts.count)
            if expected != len(nodes):
                items.append({"group": "proof", "severity": "suggest",
                              "title": f"图文层数不一致：图「{title}」",
                              "evidence": f"正文提到 {expected} 层/级，图里有 {len(nodes)} 个节点",
                              "source": "图文一致性", "action": "locate"})
        # 图里出现正文没有的数字
        chart_tokens = {m.group(0).replace(" ", "") for m in _NUM_RE.finditer(markdown or "")}
        text_tokens = {m.group(0).replace(" ", "") for m in _NUM_RE.finditer(section_text or "")}
        absent = sorted(chart_tokens - text_tokens)
        if absent:
            items.append({"group": "proof", "severity": "suggest",
                          "title": f"图里出现正文没有的数字：「{title}」",
                          "evidence": "、".join(absent[:5]),
                          "source": "数字一致性", "action": "locate"})
    elif role == "table":
        cols = [str(c) for c in (deliverable.get("cols") or []) if str(c).strip()]
        header: list[str] = []
        for line in (markdown or "").splitlines():
            row = _ROW_RE.match(line)
            if row and "---" not in line:
                header = [cell.strip() for cell in row.group(1).split("|")]
                break
        if cols and header and len(header) != len(cols):
            items.append({"group": "proof", "severity": "suggest",
                          "title": f"表格列数与计划不一致：「{title}」",
                          "evidence": f"计划 {len(cols)} 列（{'/'.join(cols)}），实际 {len(header)} 列",
                          "source": "交付物校验", "action": "locate"})
    return items
