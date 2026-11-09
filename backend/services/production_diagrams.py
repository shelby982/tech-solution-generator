"""生产级图表模板：结构化 spec → 确定性 SVG/PNG。

这里的模板不是上游 Diagram Design 的 HTML 模板，而是产品自己的"图型模板"：
- 每类图有自己的 JSON schema（由 LLM 只抽事实）；
- 布局/坐标/图例/箭头由代码计算；
- 统一 header（图号/评分点/结论）、footer（依据/图例）、A4 横向画布。
"""
from __future__ import annotations

import html
import json
import math
import re

from db import get_db
from services import diagram_assets

W = 1280
MARGIN = 28
HEADER_H = 96
FOOTER_H = 54
INK = "#0f172a"
MUTED = "#64748b"
SOFT = "#94a3b8"
ACCENT = "#1d4ed8"
ACCENT_2 = "#0f766e"
LINE = "#cbd5e1"
PAPER = "#ffffff"
BAND = "#f7f8fa"

TEMPLATES = {
    "layered": "分层架构图",
    "data_flow": "数据流图",
    "sequence": "时序图",
    "gantt": "甘特/阶段图",
    "matrix": "矩阵对照图",
    "fishbone": "鱼骨图",
    "pyramid": "金字塔/目标分层",
    "radar": "能力雷达",
    "org": "组织/职责图",
}

TEMPLATE_ELEMENTS = {
    "layered": ["图号/标题", "结论", "层带", "层内组件", "层间箭头", "图例", "依据"],
    "data_flow": ["阶段列", "节点", "有向数据流", "关系标签", "图例", "依据"],
    "sequence": ["参与角色", "生命线", "消息顺序", "请求/响应", "标题/结论", "依据"],
    "gantt": ["时间轴/日期", "任务/阶段", "起止时间", "负责人", "里程碑", "依据"],
    "matrix": ["行头", "列头", "单元格", "状态", "标题/结论", "依据"],
    "fishbone": ["结果/问题", "原因类", "原因", "主骨/分支", "标题/结论", "依据"],
    "pyramid": ["分层目标", "层级说明", "支撑要素", "标题/结论", "依据"],
    "radar": ["维度轴", "现状多边形", "目标多边形", "量程/图例", "依据"],
    "org": ["组织根节点", "下级角色/组", "汇报/职责连线", "角色说明", "依据"],
}

SCHEMAS = {
    "layered": '{"bands":[{"label":"层名","description":"一句说明","items":["组件1","组件2"]}],"edges":[{"from":"组件1","to":"组件2","label":"调用"}]}',
    "data_flow": '{"stages":[{"label":"数据源","items":["节点1","节点2"]}],"edges":[{"from":"节点1","to":"节点2","label":"采集"}]}',
    "sequence": '{"actors":[{"id":"user","label":"用户"},{"id":"app","label":"应用"}],"messages":[{"from":"user","to":"app","label":"请求","order":1}]}',
    "gantt": '{"tasks":[{"name":"任务名","start":0,"end":4,"owner":"负责方","phase":"阶段","milestone":false}]}',
    "matrix": '{"rows":[{"id":"r1","label":"政策"}],"cols":[{"id":"c1","label":"要求"}],"cells":[{"row":"r1","col":"c1","text":"响应","status":"满足"}]}',
    "fishbone": '{"effect":"问题/结果","categories":[{"name":"原因类","causes":["原因1","原因2"]}]}',
    "pyramid": '{"levels":[{"label":"顶层目标","items":["目标1"]},{"label":"支撑层","items":["能力1"]}]}',
    "radar": '{"metrics":[{"label":"维度","current":3,"target":5,"max":5}]}',
    "org": '{"root":{"label":"项目组织","children":[{"label":"技术组","children":[]},{"label":"实施组","children":[]}]}}',
}


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _clip(value, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def _text(x, y, value, size=12, fill=INK, weight=400, anchor="start", mono=False):
    family = "'SFMono-Regular',Consolas,monospace" if mono else "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}" font-family="{family}">{_esc(value)}</text>'


def _line(x1, y1, x2, y2, color=LINE, width=1, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"{d}/>'


def _rect(x, y, w, h, fill=PAPER, stroke=LINE, rx=6, width=1):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'


def _header(meta: dict, h: int) -> str:
    title = meta.get("title") or "图表"
    score = "、".join((meta.get("score_refs") or [])[:2])
    claim = _clip(meta.get("claim") or "", 64)
    parts = [_text(MARGIN, 42, _clip(title, 34), 25, INK, 700)]
    if score:
        parts.append(_text(MARGIN, 64, "对应评分点：" + score, 11, ACCENT))
    if claim:
        parts.append(_text(MARGIN, 84, "结论：" + claim, 11, "#334155"))
    if meta.get("section"):
        parts.append(_text(W - MARGIN, 64, "对应章节：" + _clip(meta["section"], 24), 9, SOFT, anchor="end"))
    return "".join(parts)


def _footer(meta: dict, h: int, legend: list[tuple[str, str, str]] | None = None) -> str:
    y = h - 28
    parts = []
    if legend:
        lx = MARGIN
        parts.append(_text(lx, y, "图例", 9, MUTED))
        lx += 34
        for label, color, dash in legend:
            parts.append(_line(lx, y - 3, lx + 20, y - 3, color, 1.6, dash))
            parts.append(_text(lx + 25, y, label, 9, "#475569"))
            lx += 78
    evidence = meta.get("evidence") or []
    if evidence:
        parts.append(_text(W - MARGIN, y, "依据：" + _clip("；".join(evidence[:3]), 72), 8.5, SOFT, anchor="end"))
    return "".join(parts)


def _doc(body: str, height: int, meta: dict, legend=None) -> str:
    title = _esc(meta.get("title") or "图表")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img" aria-labelledby="prod-title prod-desc">
<title id="prod-title">{title}</title>
<desc id="prod-desc">{_esc(meta.get("claim") or '生产级技术方案图')}</desc>
<rect width="{W}" height="{height}" fill="{PAPER}"/>
{_header(meta, height)}
{body}
{_footer(meta, height, legend)}
</svg>'''
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#fff}text{font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}</style></head><body>' + svg + '</body></html>'


def _arrow_defs() -> str:
    return '''<defs>
<marker id="p-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="#1d4ed8"/></marker>
<marker id="p-arrow-soft" markerWidth="9" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 1 L8 4 L0 7" fill="none" stroke="#64748b" stroke-width="1.4"/></marker>
</defs>'''


# ── 分层架构 ─────────────────────────────────────────────────────────────
def _layer_arrow(x, y1, y2, label):
    out = [f'<path d="M {x:.1f} {y1:.1f} V {y2:.1f}" fill="none" stroke="{ACCENT}" stroke-width="1.5" marker-end="url(#p-arrow)"/>']
    mid = (y1 + y2) / 2
    out.append(f'<rect x="{x - 36:.1f}" y="{mid - 9:.1f}" width="32" height="17" rx="3" fill="{PAPER}"/>')
    out.append(_text(x - 20, mid + 3, label, 8, "#475569", anchor="middle"))
    return "".join(out)


def render_layered(spec: dict, meta: dict) -> str:
    bands = [b for b in (spec.get("bands") or []) if isinstance(b, dict) and b.get("label")][:7]
    if not bands:
        bands = [{"label": "业务层", "items": ["业务能力"]}, {"label": "应用层", "items": ["应用服务"]},
                 {"label": "数据层", "items": ["数据服务"]}]
    label_w = 180
    row_gap = 14
    top = HEADER_H + 16
    heights = []
    for band in bands:
        items = [str(x) for x in (band.get("items") or []) if str(x).strip()][:6]
        heights.append(max(58, 34 + (len(items) > 0) * 46))
    total = sum(heights) + row_gap * (len(bands) - 1)
    height = int(top + total + FOOTER_H)
    parts = [_arrow_defs()]
    y = top
    for index, (band, bh) in enumerate(zip(bands, heights)):
        x = MARGIN
        parts.append(_rect(x, y, W - MARGIN * 2, bh, BAND, "#e2e8f0", 8))
        parts.append(_rect(x + 12, y + 10, label_w - 24, 24, PAPER, "#dbe3ef", 4))
        parts.append(_text(x + 22, y + 27, _clip(band.get("label"), 10), 12, INK, 600))
        desc = _clip(band.get("description") or "", 30)
        if desc:
            parts.append(_text(x + 22, y + 45, desc, 9, MUTED))
        items = [str(v) for v in (band.get("items") or []) if str(v).strip()][:6]
        available = W - MARGIN * 2 - label_w - 24
        card_w = min(230, max(150, available / max(1, len(items))))
        total_w = len(items) * card_w + max(0, len(items) - 1) * 10
        start_x = MARGIN + label_w + 12 + max(0, (available - total_w) / 2)
        for i, item in enumerate(items):
            cx = start_x + i * (card_w + 10)
            parts.append(_rect(cx, y + 18, card_w, 34, PAPER, "#dbe3ef", 5))
            parts.append(_text(cx + 12, y + 39, _clip(item, max(6, int(card_w / 13))), 11, "#172033", 500))
        y += bh + row_gap
    # 层间依赖：在右侧留一条独立竖向轨道，避免压住组件
    track_x = W - MARGIN - 20
    rail = []
    for i in range(len(bands) - 1):
        upper = top + sum(heights[:i + 1]) + row_gap * i
        lower = top + sum(heights[:i + 1]) + row_gap * (i + 1)
        rail.append(_layer_arrow(track_x, upper - 4, lower + 4, "调用"))
    return _doc("".join(parts) + "".join(rail), height, meta,
                legend=[("层带", "#e2e8f0", ""), ("组件", "#cbd5e1", ""), ("层间依赖", ACCENT, "")])


# ── 数据流 ───────────────────────────────────────────────────────────────
def render_data_flow(spec: dict, meta: dict) -> str:
    stages = [s for s in (spec.get("stages") or []) if isinstance(s, dict) and s.get("label")][:6]
    if not stages:
        stages = [{"label": "数据源", "items": ["源系统"]}, {"label": "数据层", "items": ["数据仓库"]},
                  {"label": "应用", "items": ["分析服务"]}]
    gap = 34
    stage_w = (W - MARGIN * 2 - gap * (len(stages) - 1)) / len(stages)
    max_items = max(len([x for x in (s.get("items") or []) if str(x).strip()]) for s in stages) or 1
    top = HEADER_H + 34
    height = int(top + max(260, max_items * 76 + 70) + FOOTER_H)
    positions = {}
    parts = [_arrow_defs()]
    for si, stage in enumerate(stages):
        x = MARGIN + si * (stage_w + gap)
        parts.append(_rect(x, top, stage_w, height - top - FOOTER_H - 8, BAND, "#e2e8f0", 8))
        parts.append(_rect(x + 12, top + 10, min(stage_w - 24, 120), 22, PAPER, "#dbe3ef", 4))
        parts.append(_text(x + 22, top + 25, _clip(stage.get("label"), 12), 11, INK, 600))
        items = [str(v) for v in (stage.get("items") or []) if str(v).strip()][:8]
        for i, item in enumerate(items):
            iy = top + 52 + i * 76
            parts.append(_rect(x + 16, iy, stage_w - 32, 48, PAPER, "#dbe3ef", 5))
            parts.append(_text(x + 28, iy + 20, _clip(item, 14), 11, INK, 600))
            parts.append(_text(x + 28, iy + 36, "环节 " + str(i + 1), 8.5, MUTED))
            positions[item] = {"x1": x + 16, "x2": x + stage_w - 16, "y": iy + 24, "stage": si}
    edges = [e for e in (spec.get("edges") or []) if isinstance(e, dict)]
    for edge_index, edge in enumerate(edges):
        a = positions.get(str(edge.get("from")))
        b = positions.get(str(edge.get("to")))
        if not a or not b:
            continue
        label = _clip(edge.get("label") or "", 14)
        if b["stage"] < a["stage"]:
            # 回流边统一走底部回程线，避免和正向数据流/标签打架
            lane_y = height - FOOTER_H - 18 - (edge_index % 3) * 14
            sx, tx = a["x2"], b["x2"]
            path = f"M {sx:.1f} {a['y'] + 24:.1f} V {lane_y:.1f} H {tx:.1f} V {b['y'] + 24:.1f}"
            parts.append(f'<path d="{path}" fill="none" stroke="{MUTED}" stroke-width="1.4" stroke-dasharray="5,4" marker-end="url(#p-arrow-soft)"/>')
            if label:
                width = min(140, max(38, len(label) * 10 + 12))
                lx = (sx + tx) / 2
                parts.append(f'<rect x="{lx - width / 2:.1f}" y="{lane_y - 9:.1f}" width="{width}" height="17" rx="3" fill="{PAPER}"/>')
                parts.append(_text(lx, lane_y + 3, label, 9, "#475569", anchor="middle"))
            continue
        if b["stage"] > a["stage"]:
            sx, tx = a["x2"], b["x1"]
        else:
            sx, tx = a["x2"], b["x2"]
        mid = (sx + tx) / 2
        path = f"M {sx:.1f} {a['y']:.1f} H {mid:.1f} V {b['y']:.1f} H {tx:.1f}"
        parts.append(f'<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="1.5" marker-end="url(#p-arrow)"/>')
        if label:
            ly = (a["y"] + b["y"]) / 2 + (edge_index % 3 - 1) * 12
            width = min(140, max(38, len(label) * 10 + 12))
            parts.append(f'<rect x="{mid - width / 2:.1f}" y="{ly - 9:.1f}" width="{width}" height="17" rx="3" fill="{PAPER}"/>')
            parts.append(_text(mid, ly + 3, label, 9, "#475569", anchor="middle"))
    return _doc("".join(parts), height, meta,
                legend=[("阶段", "#e2e8f0", ""), ("数据流", ACCENT, "")])


# ── 时序图 ───────────────────────────────────────────────────────────────
def render_sequence(spec: dict, meta: dict) -> str:
    actors = [a for a in (spec.get("actors") or []) if isinstance(a, dict) and a.get("label")][:6]
    if len(actors) < 2:
        actors = [{"id": "user", "label": "用户"}, {"id": "app", "label": "应用"}]
    messages = [m for m in (spec.get("messages") or []) if isinstance(m, dict)][:12]
    top = HEADER_H + 34
    height = int(top + 70 + max(1, len(messages)) * 42 + FOOTER_H)
    col_w = (W - MARGIN * 2) / len(actors)
    xs = [MARGIN + col_w * i + col_w / 2 for i in range(len(actors))]
    ids = {str(a.get("id") or a.get("label")): i for i, a in enumerate(actors)}
    parts = [_arrow_defs()]
    for i, actor in enumerate(actors):
        parts.append(_rect(xs[i] - 80, top, 160, 40, PAPER, ACCENT if i == 0 else LINE, 6))
        parts.append(_text(xs[i], top + 25, _clip(actor.get("label"), 12), 11, INK, 600, "middle"))
        parts.append(_line(xs[i], top + 40, xs[i], height - FOOTER_H - 16, "#cbd5e1", 1, "4,4"))
    for row, message in enumerate(messages):
        y = top + 78 + row * 42
        parts.append(_text(MARGIN - 2, y + 3, str(row + 1), 8, SOFT, anchor="end"))
        a = ids.get(str(message.get("from") or message.get("source") or ""))
        b = ids.get(str(message.get("to") or message.get("target") or ""))
        if a is None or b is None:
            continue
        label = _clip(message.get("label") or "", 18)
        parts.append(_line(xs[a], y, xs[b], y, ACCENT if a < b else MUTED, 1.5))
        target = 1 if b > a else -1
        parts.append(f'<polygon points="{xs[b]:.1f},{y:.1f} {xs[b] - target * 8:.1f},{y - 4:.1f} {xs[b] - target * 8:.1f},{y + 4:.1f}" fill="{ACCENT if a < b else MUTED}"/>')
        parts.append(_text((xs[a] + xs[b]) / 2, y - 8, label, 9, "#475569", anchor="middle"))
    return _doc("".join(parts), height, meta)


# ── 甘特/阶段 ─────────────────────────────────────────────────────────────
def _month_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?", text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    day = int(match.group(3) or 1)
    return year * 12 + (month - 1) + (day - 1) / 31


def _month_label(value: float) -> str:
    index = int(round(value))
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _time_axis(tasks: list[dict], timeline: dict) -> tuple[bool, float, float, list[tuple[float, str]], str]:
    """返回：是否日期模式、起点、终点、刻度、说明。"""
    start_raw = timeline.get("start") or timeline.get("start_date")
    end_raw = timeline.get("end") or timeline.get("end_date")
    task_starts = [_month_value(t.get("start", t.get("start_date"))) for t in tasks]
    task_ends = [_month_value(t.get("end", t.get("end_date"))) for t in tasks]
    has_date_task = any(
        isinstance(t.get("start", t.get("start_date")), str) or isinstance(t.get("end", t.get("end_date")), str)
        for t in tasks
    )
    date_mode = bool(start_raw or end_raw or has_date_task)
    if date_mode:
        values = [v for v in ([_month_value(start_raw), _month_value(end_raw)] + task_starts + task_ends) if v is not None]
        start = min(values) if values else 0
        end = max(values) if values else start + 6
        if end <= start:
            end = start + 1
        span = end - start
        tick_count = max(2, min(8, int(math.ceil(span)) + 1))
        ticks = [(start + span * i / (tick_count - 1), _month_label(start + span * i / (tick_count - 1))) for i in range(tick_count)]
        return True, start, end, ticks, f"时间：{_month_label(start)} ~ {_month_label(end)}"
    unit = str(timeline.get("unit") or "阶段")
    values = [v for v in task_ends if v is not None] or [1]
    start, end = 0, max(1, max(values))
    ticks = [(i, f"第{int(i) + 1}{unit}") for i in range(0, int(end) + 1, max(1, int(math.ceil(end / 6))))]
    return False, start, end, ticks, f"相对周期（{unit}）"


def render_gantt(spec: dict, meta: dict) -> str:
    tasks = [t for t in (spec.get("tasks") or []) if isinstance(t, dict) and t.get("name")][:14]
    if not tasks:
        tasks = [{"name": "阶段一", "start": 0, "end": 2}, {"name": "阶段二", "start": 2, "end": 4}]
    label_w = 250
    top = HEADER_H + 48
    row_h = 30
    height = int(top + max(1, len(tasks)) * row_h + 66 + FOOTER_H)
    date_mode, start_v, end_v, ticks, axis_note = _time_axis(tasks, spec.get("timeline") or {})
    chart_x = MARGIN + label_w
    chart_w = W - MARGIN - chart_x
    parts = [_arrow_defs()]
    parts.append(_text(MARGIN, top - 18, "任务 / 阶段", 10, MUTED))
    parts.append(_text(chart_x, top - 18, axis_note, 9, MUTED))
    for tick, label in ticks:
        x = chart_x + chart_w * (tick - start_v) / max(0.0001, end_v - start_v)
        parts.append(_line(x, top - 10, x, top + len(tasks) * row_h + 4, "#e2e8f0", 1))
        parts.append(_text(x, top - 8, label, 8.5, SOFT, anchor="middle"))
    for i, task in enumerate(tasks):
        y = top + i * row_h
        parts.append(_text(MARGIN, y + 18, _clip(task.get("name"), 18), 10.5, INK, 600))
        owner = _clip(task.get("owner") or task.get("phase") or "", 10)
        if owner:
            parts.append(_text(MARGIN + label_w - 8, y + 18, owner, 8.5, MUTED, anchor="end"))
        start_raw = _month_value(task.get("start", task.get("start_date")))
        end_raw = _month_value(task.get("end", task.get("end_date")))
        if start_raw is None:
            start_raw = start_v
        if end_raw is None or end_raw <= start_raw:
            end_raw = start_raw + 0.6
        x1 = chart_x + chart_w * (start_raw - start_v) / max(0.0001, end_v - start_v)
        x2 = chart_x + chart_w * (end_raw - start_v) / max(0.0001, end_v - start_v)
        if task.get("milestone"):
            cx = x1
            parts.append(f'<path d="M {cx:.1f} {y + 8} L {cx + 7:.1f} {y + 15} L {cx:.1f} {y + 22} L {cx - 7:.1f} {y + 15} z" fill="{ACCENT}"/>')
        else:
            parts.append(_rect(x1, y + 8, max(5, x2 - x1), 14, "#dbeafe", ACCENT, 3))
            parts.append(_text(min(x2 + 6, W - MARGIN - 8), y + 19, _clip(task.get("phase") or "", 8), 8, MUTED))
    return _doc("".join(parts), height, meta,
                legend=[("任务", "#dbeafe", ""), ("里程碑", ACCENT, "")])


# ── 矩阵对照 ─────────────────────────────────────────────────────────────
def render_matrix(spec: dict, meta: dict) -> str:
    rows = [r for r in (spec.get("rows") or []) if isinstance(r, dict) and r.get("label")][:8]
    cols = [c for c in (spec.get("cols") or []) if isinstance(c, dict) and c.get("label")][:5]
    if not rows or not cols:
        rows = [{"id": "r1", "label": "政策"}, {"id": "r2", "label": "要求"}]
        cols = [{"id": "c1", "label": "工作"}, {"id": "c2", "label": "证据"}]
    label_w = 180
    col_w = (W - MARGIN * 2 - label_w) / len(cols)
    top = HEADER_H + 30
    row_h = 46
    height = int(top + 34 + len(rows) * row_h + FOOTER_H)
    parts = []
    parts.append(_rect(MARGIN, top, label_w, 34, "#eff6ff", "#bfdbfe", 4))
    parts.append(_text(MARGIN + 12, top + 22, "对象", 10, ACCENT, 600))
    for j, col in enumerate(cols):
        x = MARGIN + label_w + j * col_w
        parts.append(_rect(x, top, col_w, 34, "#eff6ff", "#bfdbfe", 4))
        parts.append(_text(x + 12, top + 22, _clip(col.get("label"), 14), 10, ACCENT, 600))
    cells = spec.get("cells") or []
    lookup = {(str(c.get("row")), str(c.get("col"))): c for c in cells if isinstance(c, dict)}
    for i, row in enumerate(rows):
        y = top + 34 + i * row_h
        parts.append(_text(MARGIN + 10, y + 27, _clip(row.get("label"), 16), 11, INK, 600))
        for j, col in enumerate(cols):
            x = MARGIN + label_w + j * col_w
            cell = lookup.get((str(row.get("id") or row.get("label")), str(col.get("id") or col.get("label")))) or {}
            parts.append(_rect(x, y, col_w, row_h, PAPER, "#e2e8f0", 0))
            text = _clip(cell.get("text") or "", max(6, int(col_w / 11)))
            parts.append(_text(x + 10, y + 20, text, 9.5, "#172033"))
            if cell.get("status"):
                parts.append(_text(x + 10, y + 35, _clip(cell["status"], 12), 8.5, ACCENT_2))
    return _doc("".join(parts), height, meta,
                legend=[("满足", ACCENT_2, ""), ("待核对", MUTED, "5,4")])


# ── 鱼骨图 ───────────────────────────────────────────────────────────────
def render_fishbone(spec: dict, meta: dict) -> str:
    effect = _clip(spec.get("effect") or "问题", 20)
    categories = [c for c in (spec.get("categories") or []) if isinstance(c, dict)][:6]
    top = HEADER_H + 30
    height = 560
    center_y = top + 210
    parts = []
    parts.append(_line(MARGIN + 40, center_y, W - MARGIN - 230, center_y, INK, 2))
    parts.append(_rect(W - MARGIN - 240, center_y - 26, 240, 52, "#eff6ff", ACCENT, 6))
    parts.append(_text(W - MARGIN - 120, center_y + 4, _clip(effect, 14), 11, ACCENT, 700, "middle"))
    for i, cat in enumerate(categories):
        above = i % 2 == 0
        x = MARGIN + 150 + (i // 2) * 250
        tip_y = center_y - 90 if above else center_y + 90
        base_y = center_y - 150 if above else center_y + 150
        parts.append(_line(x, center_y, x - 50, tip_y, LINE, 1.5))
        parts.append(_text(x - 60, tip_y + (18 if above else -8), _clip(cat.get("name"), 10), 10, INK, 600, "end"))
        causes = [str(c) for c in (cat.get("causes") or [])][:4]
        for j, cause in enumerate(causes):
            parts.append(_text(x - 16, tip_y + (above and (j + 1) * 16 or -(j + 1) * 16), _clip(cause, 14), 8.5, MUTED))
    return _doc("".join(parts), height, meta)


# ── 金字塔/目标分层 ──────────────────────────────────────────────────────
def render_pyramid(spec: dict, meta: dict) -> str:
    levels = [l for l in (spec.get("levels") or []) if isinstance(l, dict) and l.get("label")][:6]
    if not levels:
        levels = [{"label": "目标", "items": ["目标一"]}, {"label": "能力", "items": ["能力一"]}]
    top = HEADER_H + 34
    row_h = 62
    height = int(top + len(levels) * row_h + 70 + FOOTER_H)
    parts = []
    for i, level in enumerate(levels):
        y = top + i * row_h
        width = (W - MARGIN * 2) * (0.44 + 0.56 * (i / max(1, len(levels) - 1)))
        x = (W - width) / 2
        parts.append(f'<polygon points="{x:.1f},{y:.1f} {x + width:.1f},{y:.1f} {x + width - 24:.1f},{y + 50:.1f} {x + 24:.1f},{y + 50:.1f}" fill="{BAND if i else "#eff6ff"}" stroke="{LINE}" stroke-width="1"/>')
        parts.append(_text(x + 24, y + 18, f"L{i + 1}", 8.5, SOFT))
        parts.append(_text(W / 2, y + 22, _clip(level.get("label"), 18), 12, ACCENT if i == 0 else INK, 600, "middle"))
        items = [str(v) for v in (level.get("items") or [])][:4]
        if items:
            parts.append(_text(W / 2, y + 40, " · ".join(_clip(v, 12) for v in items), 9, MUTED, anchor="middle"))
    return _doc("".join(parts), height, meta)


# ── 雷达 ─────────────────────────────────────────────────────────────────
def render_radar(spec: dict, meta: dict) -> str:
    metrics = [m for m in (spec.get("metrics") or []) if isinstance(m, dict) and m.get("label")][:8]
    if len(metrics) < 3:
        metrics = [{"label": "能力A", "current": 3, "target": 5, "max": 5},
                   {"label": "能力B", "current": 2, "target": 4, "max": 5},
                   {"label": "能力C", "current": 4, "target": 5, "max": 5}]
    height = 560
    cx, cy, radius = W / 2, HEADER_H + 210, 170
    parts = []
    n = len(metrics)
    for ring in (0.25, 0.5, 0.75, 1):
        pts = []
        for i in range(n):
            angle = -math.pi / 2 + 2 * math.pi * i / n
            pts.append(f"{cx + radius * ring * math.cos(angle):.1f},{cy + radius * ring * math.sin(angle):.1f}")
        parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="#e2e8f0" stroke-width="1"/>')
    def value_polygon(key, color, fill, dash=""):
        pts = []
        for i, metric in enumerate(metrics):
            angle = -math.pi / 2 + 2 * math.pi * i / n
            maximum = float(metric.get("max") or 5) or 5
            value = max(0, min(maximum, float(metric.get(key) or 0))) / maximum
            pts.append(f"{cx + radius * value * math.cos(angle):.1f},{cy + radius * value * math.sin(angle):.1f}")
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return f'<polygon points="{" ".join(pts)}" fill="{fill}" stroke="{color}" stroke-width="1.8"{dash_attr}/>'
    parts.append(value_polygon("target", ACCENT, "rgba(29,78,216,0.08)"))
    parts.append(value_polygon("current", MUTED, "rgba(100,116,139,0.08)", "6,4"))
    maximum = max([float(m.get("max") or 5) for m in metrics] or [5]) or 5
    for ratio in (0.25, 0.5, 0.75, 1):
        parts.append(_text(cx + 4, cy - radius * ratio + 3, str(round(maximum * ratio, 1)), 7.5, SOFT))
    for i, metric in enumerate(metrics):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        lx, ly = cx + (radius + 34) * math.cos(angle), cy + (radius + 34) * math.sin(angle)
        parts.append(_text(lx, ly, _clip(metric.get("label"), 10), 10, INK, anchor="middle"))
    return _doc("".join(parts), height, meta,
                legend=[("目标", ACCENT, ""), ("现状", MUTED, "6,4")])


# ── 组织/职责 ─────────────────────────────────────────────────────────────
def render_org(spec: dict, meta: dict) -> str:
    root = spec.get("root") if isinstance(spec.get("root"), dict) else {"label": "项目组织", "children": []}
    children = [c for c in (root.get("children") or []) if isinstance(c, dict)][:8]
    top = HEADER_H + 36
    height = 470
    cx = W / 2
    parts = []
    parts.append(_rect(cx - 110, top, 220, 44, "#eff6ff", ACCENT, 6))
    parts.append(_text(cx, top + 27, _clip(root.get("label"), 16), 13, ACCENT, 700, "middle"))
    child_w = min(180, (W - MARGIN * 2 - 24 * max(1, len(children) - 1)) / max(1, len(children)))
    start_x = (W - (child_w * len(children) + 24 * max(0, len(children) - 1))) / 2
    if children:
        bus_y = top + 76
        first_cx = start_x + child_w / 2
        last_cx = start_x + (len(children) - 1) * (child_w + 24) + child_w / 2
        parts.append(_line(min(cx, first_cx), bus_y, max(cx, last_cx), bus_y, LINE, 1.5))
        parts.append(_line(cx, top + 44, cx, bus_y, LINE, 1.5))
    for i, child in enumerate(children):
        x = start_x + i * (child_w + 24)
        y = top + 110
        parts.append(_line(x + child_w / 2, top + 76, x + child_w / 2, y, LINE, 1.5))
        parts.append(_rect(x, y, child_w, 48, PAPER, "#dbe3ef", 6))
        parts.append(_text(x + child_w / 2, y + 22, _clip(child.get("label"), 12), 10.5, INK, 600, "middle"))
        sub = _clip(child.get("role") or child.get("description") or "", 18)
        if sub:
            parts.append(_text(x + child_w / 2, y + 38, sub, 8.5, MUTED, anchor="middle"))
        grandchildren = [g for g in (child.get("children") or []) if isinstance(g, dict)][:3]
        for j, grand in enumerate(grandchildren):
            gy = y + 78 + j * 42
            parts.append(_line(x + child_w / 2, y + 48, x + child_w / 2, gy, LINE, 1))
            parts.append(_rect(x + 14, gy, child_w - 28, 32, "#f8fafc", "#e2e8f0", 4))
            parts.append(_text(x + child_w / 2, gy + 21, _clip(grand.get("label"), 12), 9.5, INK, anchor="middle"))
    return _doc("".join(parts), height, meta)


RENDERERS = {
    "layered": render_layered,
    "data_flow": render_data_flow,
    "sequence": render_sequence,
    "gantt": render_gantt,
    "matrix": render_matrix,
    "fishbone": render_fishbone,
    "pyramid": render_pyramid,
    "radar": render_radar,
    "org": render_org,
}


def normalize_spec(template: str, raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("图表 spec 必须是对象")
    if template not in RENDERERS:
        raise ValueError("未知生产图模板")
    data = raw.get("spec") if isinstance(raw.get("spec"), dict) else raw
    # 基础保护：限制结构规模，避免无限输入
    if "bands" in data:
        data["bands"] = [b for b in data.get("bands") or [] if isinstance(b, dict)][:7]
    if "stages" in data:
        data["stages"] = [s for s in data.get("stages") or [] if isinstance(s, dict)][:6]
    return data


async def extract_spec(config, template: str, block: dict, brief: dict) -> dict:
    from services.diagram_design import generate_once
    if template not in SCHEMAS:
        raise ValueError("未知生产图模板")
    text = str(brief.get("text") or "")[:8000] or str(block.get("content") or "")[:8000]
    system = (
        "你是投标技术方案图表编辑。请从正文中抽取事实，输出 JSON，不要解释、不要 Markdown。"
        f"template={template}（{TEMPLATES[template]}）。JSON schema：{SCHEMAS[template]}。"
        "只使用正文中的事实；不编造数字/产品/承诺；信息不足时减少节点，不要硬凑。"
        "标签要短：节点/任务/单元格不超过 18 个汉字。"
    )
    user = f"章节：{brief.get('section') or block.get('title') or ''}\n正文：\n{text}\n只输出 JSON。"
    raw = await generate_once(config, system, user, project_id=int(block["project_id"]),
                              block_id=int(block["id"]), section=str(brief.get("section") or ""),
                              model=str(config.model))
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw).strip(), flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("图表 JSON 无法解析") from exc
    if not isinstance(data, dict):
        raise ValueError("图表 JSON 不是对象")
    return data


async def generate_asset(block: dict, brief: dict) -> dict:
    from services.diagram_design import resolve_config
    from services.diagram_render import render_png
    from services.diagram_svg import export_svg

    template = str(brief.get("template") or "layered")
    async with get_db() as db:
        config, info = await resolve_config(db, block)
    spec = brief.get("spec")
    if isinstance(spec, dict) and spec:
        spec = normalize_spec(template, spec)
    else:
        spec = await extract_spec(config, template, block, brief)
    html_text = RENDERERS[template](spec, {
        "title": brief.get("title") or spec.get("title") or TEMPLATES[template],
        "claim": brief.get("claim") or spec.get("claim") or "",
        "score_refs": brief.get("score_refs") or [],
        "section": brief.get("section") or "",
        "evidence": brief.get("source_refs") or brief.get("evidence") or [],
    })
    asset_id = diagram_assets.new_asset_id()
    html_file = diagram_assets.html_path(int(block["project_id"]), asset_id)
    png_file = diagram_assets.png_path(int(block["project_id"]), asset_id)
    html_file.parent.mkdir(parents=True, exist_ok=True)
    html_file.write_text(html_text, encoding="utf-8")
    width, height, geometry = await render_png(html_file, png_file, scale=2)
    svg = ""
    try:
        svg = await export_svg(html_file, diagram_assets.svg_path(int(block["project_id"]), asset_id))
    except Exception:
        pass
    checks = {"pipeline": "production", "template": template, "view": template,
              "geometry": geometry, "score_visible": True, "claim_visible": bool(brief.get("claim")),
              "elements_missing": [], "model_source": info}
    await diagram_assets.save_asset(
        int(block["project_id"]), int(block["id"]), section=str(brief.get("section") or ""),
        title=str(brief.get("title") or TEMPLATES[template]), skill_type=template,
        size_preset="doc-wide", html=html_text, png=png_file.read_bytes(), svg=svg,
        brief={**brief, "spec": spec}, checks=checks, model=str(config.model),
        width=width, height=height, asset_id=asset_id, prompt_hash="",
    )
    return {"asset_id": asset_id, "title": brief.get("title") or TEMPLATES[template],
            "checks": checks, "model": str(config.model)}
