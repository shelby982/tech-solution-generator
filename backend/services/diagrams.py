"""信息图：结构模型（图型 / 节点 / 层级）的解析与生成。

支持三种 P0 图型：
- flow      横向流程（A -> B -> C）
- layered   分层结构（- / -- / --- 表示层级）
- timeline  时间线（年份 | 事件）

正文里用 `:::diagram type=xxx 标题` + 结构行 + `:::`，纯文本、可编辑、可 diff；
不放图型时按 flow 处理（兼容历史 `:::diagram A -> B -> C`）。
"""
from __future__ import annotations

import json
import re

DIAGRAM_TYPES = ("flow", "layered", "timeline")
TYPE_LABELS = {"flow": "流程图", "layered": "层次图", "timeline": "时间线"}
_OPEN_RE = re.compile(r"^:::diagram(?:\s+type=(\w+))?(?:\s+(.*))?$")
_YEAR_RE = re.compile(r"(\d{4})\s*[|:：、]\s*(.+)")


def parse_diagram(header: str, body_lines: list[str]) -> dict:
    """解析 `:::diagram …` 头与正文行 → {type, title, nodes[], edges[]}。"""
    match = _OPEN_RE.match(str(header or "").strip())
    dtype = (match.group(1) if match and match.group(1) in DIAGRAM_TYPES else "flow")
    title = (match.group(2) if match and match.group(2) else "结构关系图").strip()[:80]
    lines = [str(line).strip() for line in body_lines if str(line).strip()]
    nodes: list[dict] = []
    edges: list[dict] = []
    if dtype == "layered":
        for line in lines:
            m = re.match(r"^(-{1,6})\s*(.+)$", line)
            if m:
                nodes.append({"id": f"n{len(nodes) + 1}", "label": m.group(2).strip()[:60], "level": len(m.group(1)) - 1})
            elif nodes:
                nodes[-1]["label"] = (nodes[-1]["label"] + " " + line)[:60]
        if not nodes:                      # 没有层级前缀 → 当作单层
            nodes = [{"id": f"n{i + 1}", "label": t[:60], "level": 0} for i, t in enumerate(lines)]
    elif dtype == "timeline":
        for line in lines:
            m = _YEAR_RE.search(line)
            if m:
                nodes.append({"id": f"n{len(nodes) + 1}", "label": m.group(2).strip()[:60], "level": 0, "when": m.group(1)})
            elif nodes:
                nodes[-1]["label"] = (nodes[-1]["label"] + " " + line)[:60]
        if len(nodes) < 2:                 # 没有年份 → 退化为流程
            return parse_diagram(":::diagram type=flow " + title, lines)
    else:
        text = " ".join(lines).replace("→", "->")
        for item in [n.strip() for n in text.split("->") if n.strip()]:
            nodes.append({"id": f"n{len(nodes) + 1}", "label": item[:60], "level": 0})
        edges = [{"from": f"n{i + 1}", "to": f"n{i + 2}"} for i in range(len(nodes) - 1)]
    return {"type": dtype, "title": title, "nodes": nodes, "edges": edges}


def render_diagram_markdown(structure: dict) -> str:
    """结构 → 正文文本（`: ::diagram type=… 标题` + 结构行 + `:::`）。"""
    dtype = structure.get("type") or "flow"
    title = (structure.get("title") or "结构关系图").strip()[:80]
    nodes = structure.get("nodes") or []
    if dtype == "layered":
        body = "\n".join("-" * (int(n.get("level") or 0) + 1) + " " + str(n.get("label") or "") for n in nodes)
    elif dtype == "timeline":
        body = "\n".join(f"{n.get('when') or '—'} | {n.get('label') or ''}" for n in nodes)
    else:
        body = " -> ".join(str(n.get("label") or "") for n in nodes)
    return f":::diagram type={dtype} {title}\n{body}\n:::"


def normalize_model_output(raw, fallback_title: str = "") -> dict | None:
    """把模型输出规整成结构；兼容旧格式 {"nodes": ["A","B"]}。"""
    data = raw if isinstance(raw, dict) else None
    if data is None:
        try:
            data = json.loads(str(raw))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    dtype = data.get("type") if data.get("type") in DIAGRAM_TYPES else "flow"
    title = str(data.get("title") or fallback_title or "结构关系图")[:80]
    nodes: list[dict] = []
    for i, item in enumerate(data.get("nodes") or []):
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("text") or "").strip()
            level = int(item.get("level") or 0)
            when = str(item.get("when") or "").strip()
        else:
            label, level, when = str(item or "").strip(), 0, ""
        label = label.replace("->", " ").replace("→", " ").strip()[:60]
        if not label:
            continue
        node = {"id": f"n{len(nodes) + 1}", "label": label, "level": max(0, min(3, level))}
        if when:
            node["when"] = when
        nodes.append(node)
    if dtype == "timeline" and not any(n.get("when") for n in nodes):
        dtype = "flow"
    if dtype == "layered" and len({n["level"] for n in nodes}) == 1 and len(nodes) > 2:
        pass                               # 单层也允许（渲染成一行）
    nodes = nodes[:12]
    if len(nodes) < 2:
        return None
    edges = [{"from": f"n{i + 1}", "to": f"n{i + 2}"} for i in range(len(nodes) - 1)] if dtype == "flow" else []
    return {"type": dtype, "title": title, "nodes": nodes, "edges": edges}
