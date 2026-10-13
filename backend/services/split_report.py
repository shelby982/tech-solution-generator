"""拆分报告：把"拆目录的依据"和"拆完的结果"都摆出来给用户核对。

用户要求（2026-10-13）：这套逻辑作为拆分目录的**固定流程**，并且
- 拆分时**需要确认的内容要与用户沟通**，确保拆分逻辑与最终效果符合需求；
- 拆分完成后列出：完整的**评分点及数量**、**评分单元与子检查项**、
  完整的**技术要求矩阵**、以及**评分与技术要求映射矩阵**。

本模块只读（不改任何业务数据），产出四张表 + 一份待确认清单：

    review_points   评分点清单（含分值/类别/标包/评分口径）+ 数量与分值合计
    scoring_units   评分单元（L1 评分因素 / L2 详细评审项）与其下的子检查项
    tech_matrix     技术要求矩阵（规范书要求，按主题分组）
    mapping         评分 ↔ 技术要求 的双向映射（靠章节链上的 id 关联）

为什么要做：拆分依据（标包识别、类别识别、范围放宽、主题归属）此前只在服务端
日志和一句 note 里，用户看不到、也就无从确认；拆完更没有可核对的清单。
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)


def _load_ids(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _chapter_rows(blocks: list[dict]) -> list[dict]:
    rows = []
    for b in blocks:
        if not b.get("block_id"):
            continue
        rows.append({
            "block_id": str(b["block_id"]),
            "title": b.get("title") or "",
            "level": int(b.get("level") or 1),
            "score_weight": b.get("score_weight"),
            "criterion_text": b.get("criterion_text") or "",
            "review_point_ids": _load_ids(b.get("review_point_ids")),
            "spec_requirement_ids": _load_ids(b.get("spec_requirement_ids")),
            "stable_key": b.get("stable_key") or "",
            "has_content": bool((b.get("content") or "").strip()),
        })
    return rows


def _build_units(chapters: list[dict]) -> list[dict]:
    """评分单元 = L1 评分因素（大评分点）；子检查项 = 它下面的 L2/L3 节点。"""
    units: list[dict] = []
    by_level: dict[int, dict] = {}
    for ch in chapters:
        lv = ch["level"]
        if lv == 1:
            unit = {"title": ch["title"], "block_id": ch["block_id"],
                    "review_point_ids": ch["review_point_ids"],
                    "score_weight": ch["score_weight"],
                    "items": [], "sub_items": []}
            units.append(unit)
            by_level = {1: unit}
            continue
        parent = by_level.get(lv - 1)
        if parent is None:
            # 层级不连续：挂到最近的祖先，避免节点丢失
            parent = next((by_level[k] for k in sorted(by_level, reverse=True)
                           if k < lv and by_level[k]), None)
        if parent is None:
            continue
        entry = {"title": ch["title"], "block_id": ch["block_id"],
                 "score_weight": ch["score_weight"],
                 "spec_requirement_ids": ch["spec_requirement_ids"]}
        if lv == 2:
            parent["items"].append(entry)
            item = entry
            item["sub_items"] = []
            by_level[2] = item
            for k in [k for k in by_level if k > 2]:
                by_level.pop(k, None)
        else:
            host = by_level.get(2) or parent
            host.setdefault("sub_items", []).append(entry)
            by_level[lv] = entry
            for k in [k for k in by_level if k > lv]:
                by_level.pop(k, None)
    return units


async def build_split_report(db, project_id: int, thread_id: str | None,
                             spec: dict | None = None) -> dict:
    from services.block_store import list_blocks
    from services.point_store import list_review_points, list_spec_requirements

    points = [dict(r) for r in await list_review_points(db, run_thread_id=thread_id)]
    reqs = [dict(r) for r in await list_spec_requirements(db, run_thread_id=thread_id)]
    blocks = await list_blocks(db, project_id, thread_id)
    chapters = _chapter_rows(blocks)

    # ── ① 评分点清单 + 数量与分值合计 ──
    rp_rows = []
    for p in points:
        rp_rows.append({
            "point_id": p.get("point_id") or "",
            "factor": p.get("factor") or "",
            "item": p.get("item") or "",
            "points": p.get("points"),
            "category": p.get("category") or "",
            "package_no": p.get("package_no") or "",
            "package_label": p.get("package_label") or "",
            "criterion": (p.get("criterion") or "")[:600],
        })
    rp_total = sum(float(r["points"] or 0) for r in rp_rows)

    # ── ② 评分单元与子检查项 ──
    units = _build_units(chapters)

    # ── ③ 技术要求矩阵（按主题分组）──
    by_topic: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in reqs:
        topic = str(r.get("topic") or "(未归类)")
        by_topic.setdefault(topic, []).append({
            "requirement_id": r.get("requirement_id") or "",
            "title": r.get("title") or "",
            "text": (r.get("text") or "")[:400],
            "mandatory_level": r.get("mandatory_level") or "",
            "source_file": r.get("source_file") or "",
        })
    tech_matrix = [{"topic": t, "count": len(v), "requirements": v}
                   for t, v in by_topic.items()]

    # ── ④ 评分 ↔ 技术要求 映射矩阵（章节链上的 id 就是连接键）──
    req_by_id = {r["requirement_id"]: r for r in reqs}
    point_by_id = {p["point_id"]: p for p in points}

    point_to_reqs: dict[str, set[str]] = {}
    req_to_points: dict[str, set[str]] = {}
    req_landed: set[str] = set()
    for ch in chapters:
        ids = ch["review_point_ids"]
        rids = ch["spec_requirement_ids"]
        for rid in rids:
            if rid in req_by_id:
                req_landed.add(rid)
        for pid in ids:
            point_to_reqs.setdefault(pid, set()).update(rids)
        for rid in rids:
            for pid in ids:
                req_to_points.setdefault(rid, set()).add(pid)

    mapping = []
    for pid, p in point_by_id.items():
        rids = sorted(point_to_reqs.get(pid, set()))
        mapping.append({
            "point_id": pid,
            "factor": p.get("factor") or "",
            "item": p.get("item") or "",
            "points": p.get("points"),
            "requirement_count": len(rids),
            "requirement_topics": sorted({req_by_id[r]["topic"] for r in rids if r in req_by_id}),
            "chapter_count": sum(1 for c in chapters if pid in c["review_point_ids"]),
        })
    # 逐条要求的反向映射（哪个评分点/章节承载了它）
    req_rows = []
    for r in reqs:
        rid = r["requirement_id"]
        pts = sorted(req_to_points.get(rid, set()))
        req_rows.append({
            "requirement_id": rid,
            "topic": r.get("topic") or "",
            "title": r.get("title") or "",
            "point_ids": pts,
            "points_label": "、".join(
                (point_by_id[x].get("item") or point_by_id[x].get("factor") or x)
                for x in pts if x in point_by_id
            ),
            "landed": rid in req_landed,
        })

    scored_ids = {m["point_id"] for m in mapping}

    # 作答 + 项目范围：让确认项显示"已经答过了"，并把范围喂给评分地图
    ctx = {"answers": {}, "project_scope": {}}
    try:
        from services.split_answers import resolve_split_context
        ctx = await resolve_split_context(db, project_id, thread_id)
    except Exception as e:
        logger.warning(f"split_report：读取作答失败（{e}）")
    answers = ctx.get("answers") or {}

    # ── ⑤ 评分地图：全文有几张评分表、选中了哪张、其余为什么没选中 ──
    # 用户提出的困难：多个标的/标包 + 商务/技术混排。地图把"干扰"变成"有归属但未选中"。
    scoring_map = None
    source_toc = (spec or {}).get("source_toc") or []
    if source_toc:
        try:
            from services.scoring_map import build_scoring_map
            instruction = ""
            cfg = (spec or {}).get("config") or {}
            if isinstance(cfg, dict):
                instruction = str(cfg.get("outline_instruction") or "")
            instruction = instruction or str((spec or {}).get("outline_instruction") or "")
            # 与派生时**同一套**范围推断：要求里没写范围词就从材料文件名推
            # （文件名常写着「标的1」）。否则报告会显示"未选中"、而目录其实已按
            # 标的1 选中 —— 两处口径不一致，用户看到的就是自相矛盾的页面。
            from infra.parser.scope_index import parse_target_tokens
            if not parse_target_tokens(instruction):
                inferred: list[str] = []
                for sec in source_toc:
                    fname = (sec.get("source_file") if isinstance(sec, dict)
                             else getattr(sec, "source_file", "")) or ""
                    inferred += parse_target_tokens(str(fname))
                if inferred:
                    instruction = (instruction + " " + " ".join(inferred)).strip()
            full = build_scoring_map(source_toc, instruction,
                                     forced_scope=answers.get("scope") or None)
            scoring_map = {k: v for k, v in full.items() if k != "_selected_points"}
            scoring_map["_issues"] = full.get("issues") or []
        except Exception as e:      # 地图是增强信息，失败不影响其余四张清单
            logger.warning(f"split_report：评分地图构建失败（{e}）")

    return {
        "review_points": {
            "items": rp_rows,
            "count": len(rp_rows),
            "score_total": round(rp_total, 2),
            "categories": sorted({r["category"] for r in rp_rows if r["category"]}),
            "packages": sorted({r["package_no"] for r in rp_rows if r["package_no"]}),
        },
        "scoring_units": {
            "items": units,
            "unit_count": len(units),
            "sub_item_count": sum(len(u.get("items") or []) for u in units),
            "check_item_count": sum(len(i.get("sub_items") or [])
                                    for u in units for i in u.get("items") or []),
        },
        "tech_matrix": {
            "topics": tech_matrix,
            "count": len(reqs),
            "topic_count": len(tech_matrix),
        },
        "mapping": {
            "point_to_requirement": mapping,
            "requirement_to_point": req_rows,
            "mapped_points": len(scored_ids),
            "unmapped_requirements": [r["requirement_id"] for r in req_rows if not r["landed"]],
            "requirements_without_point": [r["requirement_id"] for r in req_rows if not r["point_ids"]],
        },
        "scoring_map": scoring_map,
        "answers": answers,
        "project_scope": ctx.get("project_scope") or {},
        "confirmations": _apply_answers(
            _confirmations(points, rp_rows, rp_total, tech_matrix, mapping,
                           units, {**(spec or {}), "chapters": chapters})
            + list((scoring_map or {}).get("_issues") or []),
            answers,
        ),
    }


ANSWERABLE = {"scope": "scope", "category": "categories", "scope_unresolved": "scope",
              "topic_placement": "topic_placement", "structure": "structure",
              "review_points": "review_points", "scope_excluded": "scope"}


def _apply_answers(items: list[dict], answers: dict) -> list[dict]:
    """用户已作答的确认项 → 标为已识别，并把作答内容回显出来。"""
    out = []
    for it in items:
        key = str(it.get("key") or "")
        ans_key = ANSWERABLE.get(key)
        answered = bool(ans_key and answers.get(ans_key))
        merged = {**it, "answer_key": ans_key, "answered": answered}
        if answered:
            merged["needs_confirm"] = False
            merged["answer"] = answers.get(ans_key)
            merged["detail"] = (str(it.get("detail") or "") + "｜已按你的作答执行").strip("｜")
        out.append(merged)
    return out


def _confirmations(points: list[dict], rp_rows: list[dict], rp_total: float,
                   tech_matrix: list[dict], mapping: list[dict], units: list[dict],
                   spec: dict) -> list[dict]:
    """拆分时**需要用户确认**的事项。

    这些正是此前只写在 note / 服务端日志里、用户看不到的东西：标包怎么认的、
    类别怎么分、范围放宽了什么、哪些规范书主题没挂上、标星项落在哪、分值是否够 100。
    让用户在闸门上看得到、能确认，拆分逻辑才谈得上"符合用户需求"。
    """
    items: list[dict] = []

    # 标包 / 标的识别
    pkg_labels = sorted({(p.get("package_label") or "").strip() for p in points}
                        - {""})
    pkg_nos = sorted({str(p.get("package_no") or "") for p in points} - {""})
    items.append({
        "key": "scope",
        "question": "按哪个标包/标的拆分？",
        "detected": ("编号：" + "、".join(pkg_nos)) if pkg_nos else "未识别到编号标包",
        "detail": "、".join(pkg_labels)[:200] or "（材料里没有标包标识）",
        "needs_confirm": not pkg_nos,
    })

    # 类别识别
    cats: dict[str, int] = {}
    for r in rp_rows:
        cats[r["category"] or "(未分类)"] = cats.get(r["category"] or "(未分类)", 0) + 1
    items.append({
        "key": "category",
        "question": "评分类别识别是否正确？",
        "detected": "、".join(f"{k} {v} 条" for k, v in sorted(cats.items())),
        "detail": "技术类评分点用于派生目录；商务/报价类不进技术应答目录",
        "needs_confirm": not cats.get("tech"),
    })

    # 分值合计
    items.append({
        "key": "score_sum",
        "question": f"评分点分值合计 {rp_total:g} 分是否与招标文件一致？",
        "detected": f"{len(rp_rows)} 个评分点，合计 {rp_total:g} 分",
        "detail": "不足或超出 100 说明有评分点没解析出来（或混入了非本标包的评分点）",
        "needs_confirm": abs(rp_total - 100.0) > 0.01,
    })

    # 评分点确认（两阶段流程的第一步：先确认"要拿哪些分"，再确认"目录怎么拆"）
    items.append({
        "key": "review_points",
        "question": "评分点清单是否正确、是否都已纳入本次拆解？",
        "detected": f"{len(rp_rows)} 个评分点，合计 {rp_total:g} 分",
        "detail": ("确认后进入下一步：目录拆分确认。若有多标包/多标的，"
                   "先在「拆分依据」里选定范围，再确认本项。"),
        "needs_confirm": True,
    })

    # 规范书主题归属
    unmatched = [t for t in tech_matrix if t.get("topic") and not t.get("count") == 0]
    no_point = [m for m in mapping if m["requirement_count"] == 0]
    items.append({
        "key": "topic_placement",
        "question": "规范书要求主题该挂到哪些章节？",
        "detected": (f"{len(tech_matrix)} 个主题、{sum(t['count'] for t in tech_matrix)} 条要求；"
                     f"{len(no_point)} 个评分点名下没有挂到要求"),
        "detail": "匹配不到的会进「技术规范书要求应答」板块。如需指定归属，在下方按「主题=章节标题」填写即可（本项可选，也可跳过）",
        "needs_confirm": bool(no_point),
    })

    # 标星项：直接看目录章节（此前读 spec.toc，而调用方常传空 → 恒为 0 个）
    star = [c for c in spec.get("chapters") or []
            if "★" in str(c.get("title")) or "▲" in str(c.get("title"))]
    items.append({
        "key": "starred",
        "question": "标星项（★/▲）是否都已进入目录？",
        "detected": f"目录中带标星 {len(star)} 个",
        "detail": "、".join(str(c.get("title"))[:30] for c in star[:6]) or "（目录里没有标星章节）",
        "needs_confirm": False,
    })

    # 结构完整性
    no_item = [u for u in units if not u.get("items")]
    items.append({
        "key": "structure",
        "question": "目录结构（大评分点 → 详细评审项 → 子检查项）是否合理？",
        "detected": (f"{len(units)} 个评分单元，"
                     f"{sum(len(u.get('items') or []) for u in units)} 个详细评审项，"
                     f"{sum(len(i.get('sub_items') or []) for u in units for i in u.get('items') or [])} 个子检查项"),
        "detail": (f"{len(no_item)} 个评分单元下没有详细评审项，建议先调整提炼要求再拆"
                   if no_item else "结构完整"),
        "needs_confirm": bool(no_item),
    })

    return items
