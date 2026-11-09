"""生产级部署拓扑图：LLM 只抽取结构化事实，确定性渲染 SVG/PNG。

与概念图（diagram_design）分开：
- 输入是结构化 spec：zones / nodes / edges；
- 布局和样式由本模块计算，不由模型即兴画；
- 所以箭头端点、层带、图例、图号稳定可校验。
"""
from __future__ import annotations

import html
import json
import re

from db import get_db
from services import diagram_assets

W, H = 1280, 760
MARGIN = 28
HEADER_H = 88
CONTENT_TOP = 132
CONTENT_BOTTOM = H - 84
NODE_H = 52
NODE_GAP = 13
NODE_KINDS = {"gateway", "server", "database", "cloud", "user", "storage", "security", "network", "service"}
ZONE_KINDS = {"internet", "dmz", "app", "data", "management", "backup", "cloud", "internal"}
EDGE_KINDS = {"call", "support", "read", "data"}
EDGE_LABELS = {"call": "调用", "support": "支撑", "read": "只读", "data": "数据流"}


class DeploymentSpecError(ValueError):
    pass


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "-", str(value or "")).strip("-").lower()
    return text[:40] or fallback


def normalize_spec(raw: dict) -> dict:
    """规整 LLM/用户给的 spec，补 id、校验引用；不合格抛错。"""
    data = raw.get("spec") if isinstance(raw, dict) and isinstance(raw.get("spec"), dict) else raw
    if not isinstance(data, dict):
        raise DeploymentSpecError("部署拓扑 spec 不是对象")
    zones = []
    for index, item in enumerate(data.get("zones") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()[:30]
        if not label:
            continue
        zones.append({
            "id": _slug(item.get("id") or label, f"zone{index + 1}"),
            "label": label,
            "kind": str(item.get("kind") or "internal") if str(item.get("kind") or "internal") in ZONE_KINDS else "internal",
            "description": str(item.get("description") or "")[:80],
        })
    if len(zones) < 2:
        raise DeploymentSpecError("部署拓扑至少需要 2 个区域（如 DMZ、应用区、数据区）")
    zone_ids = {z["id"] for z in zones}
    zone_by_label = {z["label"]: z["id"] for z in zones}
    nodes = []
    for index, item in enumerate(data.get("nodes") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()[:40]
        if not label:
            continue
        zone = str(item.get("zone") or item.get("zone_id") or "").strip()
        zone = zone if zone in zone_ids else zone_by_label.get(zone, zones[0]["id"])
        kind = str(item.get("kind") or "server").strip().lower()
        if kind not in NODE_KINDS:
            kind = "server"
        nodes.append({
            "id": _slug(item.get("id") or label, f"node{index + 1}"),
            "label": label[:18],
            "kind": kind,
            "zone": zone,
            "tech": str(item.get("tech") or item.get("sublabel") or "")[:22],
            "evidence": str(item.get("evidence") or "")[:40],
        })
    if len(nodes) < 3:
        raise DeploymentSpecError("部署拓扑至少需要 3 个节点（如 LB、应用、数据库）")
    node_ids = {n["id"] for n in nodes}
    node_by_label = {n["label"]: n["id"] for n in nodes}
    edges = []
    for index, item in enumerate(data.get("edges") or []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("from") or item.get("source") or "").strip()
        target = str(item.get("to") or item.get("target") or "").strip()
        source = source if source in node_ids else node_by_label.get(source, "")
        target = target if target in node_ids else node_by_label.get(target, "")
        if not source or not target or source == target:
            continue
        kind = str(item.get("kind") or "call").strip().lower()
        if kind not in EDGE_KINDS:
            kind = "call"
        edges.append({
            "id": _slug(item.get("id") or f"e{index + 1}", f"e{index + 1}"),
            "from": source, "to": target, "kind": kind,
            "label": str(item.get("label") or EDGE_LABELS[kind])[:18],
            "port": str(item.get("port") or "")[:20],
            "protocol": str(item.get("protocol") or "")[:20],
        })
    if len(edges) < 2:
        raise DeploymentSpecError("部署拓扑至少需要 2 条节点间连接")
    # 去重同源同目标，保留第一条
    seen = set()
    unique_edges = []
    for edge in edges:
        key = (edge["from"], edge["to"])
        if key in seen:
            continue
        seen.add(key)
        unique_edges.append(edge)
    if len(unique_edges) < 2:
        raise DeploymentSpecError("部署拓扑至少需要 2 条不同节点间的连接")
    if len(zones) > 5:
        zones = zones[:5]
        allowed = {z["id"] for z in zones}
        for node in nodes:
            if node["zone"] not in allowed:
                node["zone"] = zones[0]["id"]
    return {
        "view": "deployment",
        "title": str(data.get("title") or "")[:120],
        "claim": str(data.get("claim") or "")[:300],
        "zones": zones,
        "nodes": nodes,
        "edges": unique_edges,
    }


def _zone_color(kind: str) -> str:
    return {
        "internet": "#f8fafc", "dmz": "#eff6ff", "app": "#f8fafc",
        "data": "#f1f5f9", "management": "#f8fafc", "backup": "#f8fafc",
    }.get(kind, "#f8fafc")


def _icon(kind: str, cx: float, cy: float) -> str:
    common = 'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"'
    if kind == "database" or kind == "storage":
        return (f'<g transform="translate({cx - 8},{cy - 8})" color="#1d4ed8"><ellipse cx="8" cy="3" rx="6" ry="2" {common}/>'
                f'<path d="M2 3v9c0 1.1 2.7 2 6 2s6-.9 6-2V3" {common}/>'
                f'<path d="M2 8c0 1.1 2.7 2 6 2s6-.9 6-2" {common}/></g>')
    if kind == "cloud":
        return (f'<g transform="translate({cx - 9},{cy - 8})" color="#1d4ed8"><path d="M5 14h8a3 3 0 0 0 .4-6A5 5 0 0 0 4 9a3 3 0 0 0 1 5z" {common}/>'
                f'<path d="M7 13h4" {common}/></g>')
    if kind == "user":
        return (f'<g transform="translate({cx - 8},{cy - 9})" color="#1d4ed8"><circle cx="8" cy="5" r="3" {common}/>'
                f'<path d="M2 16c0-3 2.7-5 6-5s6 2 6 5" {common}/></g>')
    if kind == "security":
        return (f'<g transform="translate({cx - 8},{cy - 9})" color="#1d4ed8"><path d="M8 2l6 2v5c0 4-3 6-6 7-3-1-6-3-6-7V4z" {common}/>'
                f'<path d="M5.5 9l1.7 1.7L11 7" {common}/></g>')
    if kind == "gateway" or kind == "network":
        return (f'<g transform="translate({cx - 8},{cy - 8})" color="#1d4ed8"><rect x="1" y="3" width="14" height="10" rx="2" {common}/>'
                f'<path d="M4 7h8M8 5v4" {common}/></g>')
    return (f'<g transform="translate({cx - 8},{cy - 8})" color="#1d4ed8"><rect x="2" y="2" width="12" height="12" rx="2" {common}/>'
            f'<path d="M5 6h6M5 9h6M5 12h3" {common}/></g>')


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def _label_lines(value: str, width: int = 9) -> list[str]:
    text = str(value or "").strip()
    if len(text) <= width:
        return [text]
    return [text[:width], text[width:width * 2]]


def render_html(spec: dict, *, title: str = "", score_refs: list[str] | None = None,
                section: str = "", evidence: list[str] | None = None) -> str:
    spec = normalize_spec(spec)
    zones = spec["zones"][:5]
    nodes = spec["nodes"][:20]
    node_by_id = {n["id"]: n for n in nodes}
    zone_index = {z["id"]: i for i, z in enumerate(zones)}
    max_nodes_per_zone = max([sum(1 for n in nodes if n["zone"] == z["id"]) for z in zones] or [1])
    zone_h = max(220, max_nodes_per_zone * (NODE_H + NODE_GAP) + 72)
    canvas_h = CONTENT_TOP + zone_h + 84
    content_bottom = CONTENT_TOP + zone_h
    gap = 16
    zone_w = (W - MARGIN * 2 - gap * (len(zones) - 1)) / len(zones)
    zone_x = {z["id"]: MARGIN + i * (zone_w + gap) for i, z in enumerate(zones)}
    # 每个 zone 的节点纵向等距
    node_pos = {}
    for zone in zones:
        items = [n for n in nodes if n["zone"] == zone["id"]]
        count = len(items)
        usable = zone_h - 58
        step = min(74, max(NODE_H + NODE_GAP, usable / max(1, count)))
        start = CONTENT_TOP + 58
        for i, node in enumerate(items):
            node_pos[node["id"]] = {
                "x": zone_x[zone["id"]] + 14,
                "y": start + i * step,
                "w": zone_w - 28,
                "h": NODE_H,
            }

    def edge_path(edge: dict) -> tuple[str, float, float, str]:
        src, dst = node_by_id[edge["from"]], node_by_id[edge["to"]]
        a, b = node_pos[src["id"]], node_pos[dst["id"]]
        acx, acy = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
        bcx, bcy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        if zone_index[src["zone"]] == zone_index[dst["zone"]]:
            if bcy > acy:
                sx, sy = acx, a["y"] + a["h"]
                tx, ty = bcx, b["y"]
            else:
                sx, sy = acx, a["y"]
                tx, ty = bcx, b["y"] + b["h"]
            mid = (sy + ty) / 2
            return f"M {sx:.1f} {sy:.1f} V {mid:.1f} H {tx:.1f} V {ty:.1f}", (sx + tx) / 2, mid, "v"
        if bcx > acx:
            sx, sy = a["x"] + a["w"], acy
            tx, ty = b["x"], bcy
            mid = (sx + tx) / 2
            return f"M {sx:.1f} {sy:.1f} H {mid:.1f} V {ty:.1f} H {tx:.1f}", mid, (sy + ty) / 2, "h"
        sx, sy = a["x"], acy
        tx, ty = b["x"] + b["w"], bcy
        mid = (sx + tx) / 2
        return f"M {sx:.1f} {sy:.1f} H {mid:.1f} V {ty:.1f} H {tx:.1f}", mid, (sy + ty) / 2, "h"

    markers = """<defs>
<marker id="arrow-call" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="#1d4ed8"/></marker>
<marker id="arrow-support" markerWidth="9" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 1 L8 4 L0 7" fill="none" stroke="#64748b" stroke-width="1.4"/></marker>
<marker id="arrow-read" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto"><circle cx="3.5" cy="3.5" r="2.2" fill="none" stroke="#64748b" stroke-width="1.2"/></marker>
<marker id="arrow-data" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="#0f766e"/></marker>
</defs>"""
    edge_svg = []
    for edge in spec["edges"]:
        path, lx, ly, _orient = edge_path(edge)
        kind = edge["kind"]
        stroke = {"call": "#1d4ed8", "support": "#64748b", "read": "#64748b", "data": "#0f766e"}[kind]
        dash = {"call": "", "support": ' stroke-dasharray="5,4"', "read": ' stroke-dasharray="2,4"', "data": ""}[kind]
        label = _clip(edge["label"], 14)
        if edge["port"]:
            label += f" · {edge['port']}"
        safe_label = html.escape(label)
        box_w = max(40, min(150, len(label) * 11 + 14))
        edge_svg.append(f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="1.6"{dash} marker-end="url(#arrow-{kind})"/>')
        edge_svg.append(f'<rect x="{lx - box_w / 2:.1f}" y="{ly - 10:.1f}" width="{box_w}" height="18" rx="3" fill="#ffffff" opacity="0.95"/>')
        edge_svg.append(f'<text x="{lx:.1f}" y="{ly + 3:.1f}" text-anchor="middle" font-size="9" fill="#475569">{safe_label}</text>')

    node_svg = []
    for node in nodes:
        pos = node_pos[node["id"]]
        x, y, w, h = pos["x"], pos["y"], pos["w"], pos["h"]
        node_svg.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>')
        node_svg.append(_icon(node["kind"], x + 20, y + h / 2))
        label_lines = _label_lines(_clip(node["label"], 18), max(7, int((w - 44) / 13)))
        for line_index, label_line in enumerate(label_lines[:2]):
            node_svg.append(f'<text x="{x + 38:.1f}" y="{y + 19 + line_index * 14:.1f}" font-size="13" font-weight="600" fill="#0f172a">{html.escape(label_line)}</text>')
        if node["tech"]:
            tech_y = y + (38 if len(label_lines) == 1 else 46)
            node_svg.append(f'<text x="{x + 38:.1f}" y="{tech_y:.1f}" font-size="9.5" fill="#64748b">{html.escape(_clip(node["tech"], max(8, int((w - 42) / 10))))}</text>')

    zone_svg = []
    for zone in zones:
        x = zone_x[zone["id"]]
        zone_svg.append(f'<rect x="{x:.1f}" y="{CONTENT_TOP}" width="{zone_w:.1f}" height="{zone_h}" rx="8" fill="{_zone_color(zone["kind"])}" stroke="#e2e8f0" stroke-width="1"/>')
        zone_svg.append(f'<rect x="{x + 12:.1f}" y="{CONTENT_TOP + 10}" width="{min(zone_w - 24, 110):.1f}" height="20" rx="3" fill="#ffffff" stroke="#e2e8f0"/>')
        zone_svg.append(f'<text x="{x + 20:.1f}" y="{CONTENT_TOP + 24}" font-size="11" font-weight="600" fill="#334155">{html.escape(zone["label"])}</text>')
        if zone["description"]:
            zone_svg.append(f'<text x="{x + 20:.1f}" y="{CONTENT_TOP + 44}" font-size="9" fill="#64748b">{html.escape(_clip(zone["description"], max(6, int((zone_w - 34) / 9))))}</text>')

    score_line = "、".join((score_refs or [])[:2])
    header = [
        f'<text x="{MARGIN}" y="38" font-size="25" font-weight="700" fill="#0f172a">{html.escape(title or spec["title"] or "部署拓扑图")}</text>',
    ]
    if score_line:
        header.append(f'<text x="{MARGIN}" y="58" font-size="11" fill="#1d4ed8">对应评分点：{html.escape(score_line)}</text>')
    if spec["claim"]:
        header.append(f'<text x="{MARGIN}" y="77" font-size="11.5" fill="#334155">结论：{html.escape(_clip(spec["claim"], 58))}</text>')
    if section:
        header.append(f'<text x="{W - MARGIN}" y="58" text-anchor="end" font-size="9" fill="#94a3b8">对应章节：{html.escape(_clip(section, 24))}</text>')

    legend_items = [("call", "调用"), ("support", "支撑"), ("read", "只读"), ("data", "数据流")]
    legend_x = MARGIN
    legend = [f'<text x="{legend_x}" y="{canvas_h - 50}" font-size="9" fill="#64748b">图例</text>']
    legend_x += 34
    for kind, label in legend_items:
        color = {"call": "#1d4ed8", "support": "#64748b", "read": "#64748b", "data": "#0f766e"}[kind]
        dash = {"call": "", "support": ' stroke-dasharray="5,4"', "read": ' stroke-dasharray="2,4"', "data": ""}[kind]
        legend.append(f'<line x1="{legend_x}" y1="{canvas_h - 53}" x2="{legend_x + 22}" y2="{canvas_h - 53}" stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#arrow-{kind})"/>')
        legend.append(f'<text x="{legend_x + 28}" y="{canvas_h - 50}" font-size="9" fill="#475569">{label}</text>')
        legend_x += 78
    footer = ""
    if evidence:
        footer = f'<text x="{MARGIN}" y="{canvas_h - 24}" font-size="8.5" fill="#94a3b8">依据：{html.escape(_clip("；".join(evidence[:3]), 80))}</text>'

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {canvas_h}" width="{W}" height="{canvas_h}" role="img" aria-labelledby="deploy-title deploy-desc">
<title id="deploy-title">{html.escape(title or spec["title"] or "部署拓扑图")}</title>
<desc id="deploy-desc">生产级部署拓扑：区域、节点、连接关系与证据。</desc>
<rect width="{W}" height="{canvas_h}" fill="#ffffff"/>
{markers}
{''.join(zone_svg)}
{''.join(edge_svg)}
{''.join(node_svg)}
{''.join(header)}
{''.join(legend)}
{footer}
</svg>'''
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#fff}text{font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}</style></head><body>' + svg + '</body></html>'


async def extract_spec(config, block: dict, brief: dict) -> dict:
    from services.diagram_design import generate_once
    text = str(brief.get("text") or "")[:7000]
    if not text:
        text = str(block.get("content") or "")[:7000]
    system = (
        "你是投标技术方案的部署架构师。请从给定正文中抽取**部署拓扑事实**，输出 JSON，不要解释、不要 Markdown。"
        'JSON 格式：{"zones":[{"id":"dmz","label":"DMZ","kind":"dmz","description":"..."}],'
        '"nodes":[{"id":"lb","label":"负载均衡","zone":"dmz","kind":"gateway","tech":"F5","evidence":"依据"}],'
        '"edges":[{"from":"lb","to":"app1","kind":"call","label":"HTTPS 443","port":"443"}]}。'
        'zone kind 只能是 internet/dmz/app/data/management/backup/internal；node kind 只能是 gateway/server/database/cloud/user/storage/security/network/service；'
        'edge kind 只能是 call/support/read/data。只使用正文/素材出现的事实，不编造产品、版本、数字；'
        '没有依据的节点/连线宁可省略。至少 2 个区域、3 个节点、2 条连线。'
    )
    user = "章节：" + str(brief.get("section") or block.get("title") or "") + "\n正文：\n" + text
    raw = await generate_once(config, system, user, project_id=int(block["project_id"]),
                              block_id=int(block["id"]), section=str(brief.get("section") or ""),
                              model=str(config.model))
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw).strip(), flags=re.I)
    try:
        return normalize_spec(json.loads(cleaned))
    except json.JSONDecodeError as exc:
        raise DeploymentSpecError(f"部署拓扑 JSON 无法解析：{exc}") from exc


async def generate_asset(block: dict, brief: dict) -> dict:
    from services.diagram_design import resolve_config
    from services.diagram_render import render_png
    from services.diagram_svg import export_svg

    async with get_db() as db:
        config, info = await resolve_config(db, block)
    spec = brief.get("spec")
    if isinstance(spec, dict) and spec.get("zones"):
        spec = normalize_spec(spec)
    else:
        spec = await extract_spec(config, block, brief)
    title = str(brief.get("title") or spec.get("title") or "部署拓扑图")
    if brief.get("claim"):
        spec["claim"] = str(brief["claim"])[:300]
    if title:
        spec["title"] = title
    html_text = render_html(spec, title=title, score_refs=brief.get("score_refs") or [],
                            section=str(brief.get("section") or ""),
                            evidence=brief.get("source_refs") or brief.get("evidence") or [])
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
    checks = {
        "pipeline": "deployment",
        "view": "deployment",
        "zones": len(spec["zones"]), "nodes": len(spec["nodes"]), "edges": len(spec["edges"]),
        "score_visible": True, "claim_visible": bool(spec.get("claim")),
        "elements_missing": [], "geometry": geometry, "model_source": info,
        "connector_count": len(spec["edges"]),
        "connector_styles": sorted({e["kind"] for e in spec["edges"]}),
    }
    await diagram_assets.save_asset(
        int(block["project_id"]), int(block["id"]), section=str(brief.get("section") or ""),
        title=title, skill_type="deployment", size_preset="doc-wide",
        html=html_text, png=png_file.read_bytes(), svg=svg,
        brief={**brief, "spec": spec}, checks=checks, model=str(config.model),
        width=width, height=height, asset_id=asset_id,
        prompt_hash="",
    )
    return {"asset_id": asset_id, "title": title, "checks": checks, "model": str(config.model)}
