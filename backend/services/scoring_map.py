"""评分地图：先看清整份招标文件里的评分结构，再定向筛选要用的那一张表。

用户提出的真实困难（2026-10-13）：
> 拆分需要从整个招标文件中找到对应的技术论述和技术评审的打分点。这里会有很多干扰信息，
> 可能会有多个标包、多个标的，也可能会有商务、技术不同的评分点。

做法（不靠关键词撒网，靠结构化索引）：
1. `infra.parser.scope_index` 建「标的/标包」索引并按「适用标包：」把评分表切成独立段；
2. 本模块对**每一段单独**抽评分点 → `(标的, 标包, 类别) → 条数 / 分值合计 / 来源`；
3. 按提炼要求里的范围词（标的1 / 标包2 / 名称）**定向选中**一张表；
4. 其余各表全部列出并说明**为什么没选中** —— 干扰信息不是被丢弃，而是被归类且可核对；
5. **分值合计**是硬校验：选中集合的技术分值对不上招标文件的构成，就说明选错范围或漏行。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CATEGORY_LABEL = {"tech": "技术", "commerce": "商务", "price": "报价", "other": "其他", "": "未分类"}


def _extract_per_table(tables: list[dict]) -> list[dict]:
    """对每张切好的评分表**单独**抽评分点（这是修掉"多标包混成一套"的关键）。"""
    from infra.parser.review_points import extract_review_points

    out = []
    for tb in tables:
        table = extract_review_points([{
            "id": tb.get("section_id") or "s0", "level": 1,
            "title": tb.get("title") or "", "raw_content": tb.get("content") or "",
            "source_file": tb.get("source_file") or "",
        }])
        points = list(table.points)
        if not points:
            continue          # 不是评分表（正文里恰好出现"分值"字样的章节）
        out.append({**tb, "points": points})
    return out


def build_scoring_map(sections, instruction: str = "") -> dict:
    """产出评分地图 + 选中/未选中结论。

    返回::

        {"index": [...标的/标包索引...],
         "entries": [...每张评分表一条，含 scope/categories/条数/分值/是否选中/原因...],
         "selected": {...} | None,
         "selection": {"by": "标的1", "reason": ...},
         "issues": [...需要用户确认的问题...]}
    """
    from infra.parser.review_points import extract_review_points
    from infra.parser.scope_index import (
        normalize_scope_name, parse_package_index, parse_target_tokens,
        resolve_scope, split_scoring_tables,
    )

    index = parse_package_index(sections)
    tables = split_scoring_tables(sections)
    extracted = _extract_per_table(tables)

    # 旧式兜底：没有「适用标包」声明的文件（整篇一张表）→ 用全文抽一次
    if not extracted:
        table = extract_review_points(sections)
        if table.points:
            extracted = [{"scope_label": "", "section_id": "", "title": "",
                          "source_file": "", "content": "", "points": list(table.points)}]

    entries = []
    for i, tb in enumerate(extracted, start=1):
        scope = resolve_scope(tb.get("scope_label") or "", index)
        cats: dict[str, dict] = {}
        for p in tb["points"]:
            cat = p.category or ""
            slot = cats.setdefault(cat, {"count": 0, "score": 0.0})
            slot["count"] += 1
            slot["score"] += float(p.points or 0)
        entries.append({
            "table_no": i,
            "scope_label": tb.get("scope_label") or "",
            "section_id": tb.get("section_id") or "",
            "title": tb.get("title") or "",
            "source_file": tb.get("source_file") or "",
            "resolved": scope["resolved"],
            "target": scope.get("target") or "",
            "target_name": scope.get("target_name") or "",
            "package_no": scope.get("package_no") or "",
            "package_name": scope.get("package_name") or "",
            "scope_reason": scope.get("reason") or "",
            "categories": [
                {"category": c, "label": CATEGORY_LABEL.get(c, c),
                 "count": v["count"], "score": round(v["score"], 2)}
                for c, v in sorted(cats.items(), key=lambda kv: -kv[1]["score"])
            ],
            "total_count": len(tb["points"]),
            "total_score": round(sum(float(p.points or 0) for p in tb["points"]), 2),
            "points": tb["points"],
        })

    # ── 定向筛选 ──
    tokens = parse_target_tokens(instruction)
    selected = None
    selection = {"by": "", "reason": ""}
    for token in tokens:
        kind, num = token[:2], token[2:]
        for e in entries:
            if not e["resolved"]:
                continue
            if kind == "标的" and e["target"] == token:
                selected = e
                selection = {"by": token,
                             "reason": f"提炼要求指定「{token}」，命中{e['target']}的评分表"}
                break
            if kind in ("标包", "标段") and e["package_no"] == num:
                selected = e
                selection = {"by": token,
                             "reason": f"提炼要求指定「{token}」，命中{e['target']}/标包{e['package_no']}"}
                break
        if selected:
            break

    issues: list[dict] = []
    if selected is None and len(entries) == 1:
        selected = entries[0]
        selection = {"by": "唯一评分表", "reason": "全文只有一张评分表"}
    if selected is None:
        unresolved = [e for e in entries if not e["resolved"]]
        issues.append({
            "key": "scope_unresolved",
            "question": "要按哪张评分表拆分？",
            "detail": ("提炼要求里的范围词（" + "、".join(tokens) + "）没有匹配到任何评分表"
                       if tokens else "提炼要求里没写标的/标包") +
                      f"；检测到 {len(entries)} 张评分表，其中 {len(unresolved)} 张无法归属标包",
            "options": [
                {"table_no": e["table_no"], "label": (
                    f"{e['target']}/标包{e['package_no']}（{e['package_name'][:20]}）"
                    if e["resolved"] else f"表{e['table_no']}：{e['scope_label'][:30] or '无范围声明'}"),
                 "tech_count": next((c["count"] for c in e["categories"] if c["category"] == "tech"), 0),
                 "tech_score": next((c["score"] for c in e["categories"] if c["category"] == "tech"), 0.0)}
                for e in entries
            ],
        })
    elif len(entries) > 1:
        others = [e for e in entries if e is not selected]
        issues.append({
            "key": "scope_excluded",
            "question": "是否只按选中的这张评分表拆分？",
            "detail": (f"选中 {selection['by']}（{selection['reason']}）；"
                       f"另有 {len(others)} 张评分表未选中：" +
                       "；".join(f"{e['target']}/标包{e['package_no']} "
                                f"{CATEGORY_LABEL.get('tech')}{next((c['count'] for c in e['categories'] if c['category'] == 'tech'), 0)} 条"
                                for e in others[:3])),
        })

    # 选中表的技术类分值合计 —— 与招标文件的构成对不上就是硬伤
    tech_score = 0.0
    if selected is not None:
        tech_score = next((c["score"] for c in selected["categories"] if c["category"] == "tech"), 0.0)
        if tech_score and abs(tech_score - 100.0) > 0.01:
            issues.append({
                "key": "tech_score_sum",
                "question": f"选中表的技术类分值合计 {tech_score:g} 分，是否需要核对？",
                "detail": "技术评分构成通常合计 100 分；不等于 100 可能是漏解析了行，或该表的"
                          "技术分值另有构成（如含主观分+客观分分列）",
            })

    return {
        "index": index,
        "entries": [{k: v for k, v in e.items() if k != "points"} for e in entries],
        "selected_table_no": (selected or {}).get("table_no"),
        "selection": selection,
        "tokens": tokens,
        "issues": issues,
        "stats": {
            "tables": len(entries),
            "resolved_tables": sum(1 for e in entries if e["resolved"]),
            "selected_points": (selected or {}).get("total_count", 0),
            "selected_tech_score": tech_score,
        },
        "_selected_points": (selected or {}).get("points") or [],
    }
