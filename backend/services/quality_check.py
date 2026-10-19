"""章节质检（P3）：结构完整性 / 图表数量 / 占位与空词 / 篇幅偏差。

确定性检查（不调模型），对照两份东西：
- **L1 模块配方**：骨架项是否在正文里有着落、必图是否出现（必表按 L0"每节≥1 张"）
- **篇幅分配**：已写页数 vs 分配页数（偏差超过 ±30%/低于 40% 提醒）

输出是**清单**，不阻断保存（用户口径：只出清单，不卡手工修正）。
"""

from __future__ import annotations

import re

WPP = 250                      # 1 页 ≈ 250 字（与编写页一致）

# 检查项元数据：分组（规范/计划/依据/素材）+ 一句话 + 可执行动作
CHECK_META = {
    "skeleton": {"group": "规范", "label": "骨架落点", "actions": ["regenerate", "open_spec"]},
    "table": {"group": "规范", "label": "必表", "actions": ["regenerate"]},
    "figure": {"group": "规范", "label": "必图", "actions": ["regenerate"]},
    "vague": {"group": "规范", "label": "空词", "actions": ["regenerate"]},
    "source_mark": {"group": "规范", "label": "来源标注", "actions": ["find_material"]},
    "budget": {"group": "规范", "label": "篇幅", "actions": ["open_budget"]},
    "requirement": {"group": "计划", "label": "要求回应", "actions": ["find_material", "regenerate"]},
    "score_point": {"group": "依据", "label": "评分点覆盖", "actions": ["regenerate"]},
    "spec_clause": {"group": "依据", "label": "规范条款承接", "actions": ["regenerate"]},
    "material": {"group": "素材", "label": "素材不足", "actions": ["find_material"]},
    "citation_unused": {"group": "素材", "label": "来源未引用", "actions": ["regenerate", "find_material"]},
    "pending": {"group": "素材", "label": "待补充", "actions": ["find_material"]},
}


def _issue(code: str, level: str, message: str, **extra) -> dict:
    meta = CHECK_META.get(code, {"group": "规范", "label": code, "actions": []})
    return {"code": code, "level": level, "message": message,
            "group": meta["group"], "check": meta["label"],
            "actions": list(meta["actions"]), **extra}
TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.M)
VAGUE_WORDS = ("非常", "极大", "极大提升", "显著提升", "大幅提升")


def _skeleton_keywords(item: str) -> list[str]:
    """骨架项 → 关键词（按分隔符切开，取前 2~4 字做存在性判断）。"""
    parts = [part for part in re.split(r"[\s/、，,和与及→\-]+", str(item or "").strip()) if len(part) >= 2]
    return [part[:4] for part in parts[:3]]


def _content_responds_requirement(content, requirement) -> bool:
    """与 services/writing_plans._content_responds 同口径（要求是否在正文有落点）。"""
    import re as _re
    text = str(content or "")
    kind = (requirement or {}).get("type") or "text"
    if kind == "table":
        return text.count("| ---") >= 1
    if kind == "figure":
        return ":::diagram" in text or "![" in text
    keywords = [part for part in _re.split(r"[\s、，,；;/（）()【】]+", str((requirement or {}).get("text") or ""))
                if len(part) >= 2][:4]
    return bool(keywords) and any(keyword in text for keyword in keywords)


def check_chapter(content: str, recipe: dict | None = None, budget_pages: int = 0) -> dict:
    text = str(content or "")
    recipe = recipe or {}
    stats = {
        "chars": len(text.replace(" ", "").replace("\n", "")),
        "tables": len(TABLE_SEP.findall(text)),
        "diagrams": text.count(":::diagram"),
        "images": len(re.findall(r"!\[[^\]]*\]\(", text)),
        "pending": len(re.findall(r"【待补充", text)),
    }
    stats["pages"] = round(stats["chars"] / WPP, 1)
    stats["figures"] = stats["diagrams"] + stats["images"]
    issues = []
    for item in recipe.get("skeleton") or []:
        keys = _skeleton_keywords(item)
        if keys and not any(key in text for key in keys):
            issues.append(_issue("skeleton", "medium", f"骨架项「{item}」在正文里没有落点"))
    if not stats["tables"]:
        issues.append(_issue("table", "high", "本章没有表格（规范要求每节至少 1 张）"))
    if (recipe.get("figures") or []) and stats["figures"] == 0:
        issues.append(_issue("figure", "high", "本章没有图示（配方要求至少 1 张）"))
    if stats["pending"]:
        issues.append(_issue("pending", "info", f"还有 {stats['pending']} 处【待补充】"))
    for word in VAGUE_WORDS:
        if word in text:
            issues.append(_issue("vague", "low", f"出现空词「{word}」，建议改成量化表述"))
            break
    if budget_pages and stats["pages"]:
        ratio = stats["pages"] / budget_pages
        if ratio > 1.3:
            issues.append(_issue("budget", "medium", f"已写 {stats['pages']} 页，超出分配 {budget_pages} 页的 30%"))
        elif ratio < 0.4:
            issues.append(_issue("budget", "medium", f"已写 {stats['pages']} 页，不足分配 {budget_pages} 页的 40%"))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    issues.sort(key=lambda issue: order.get(issue["level"], 9))
    return {"stats": stats, "issues": issues,
            "ok": not any(issue["level"] in ("high", "medium") for issue in issues)}


TITLE_HINTS = [("项目理解", "understanding"), ("对项目的理解", "understanding"),
               ("技术方案", "solution"), ("技术实现方案", "solution"),
               ("实施", "implementation"), ("进度", "implementation"),
               ("人员", "evidence"), ("业绩", "evidence"), ("团队", "evidence"),
               ("点对点", "point_to_point")]


def infer_layout(block: dict, blocks: list[dict], stored: dict) -> str:
    """章节类型：计划/设置 > 祖先标题 > 本章推荐（与编写计划同一口径）。"""
    from services.chapter_studio import recommend_layout
    key = str(block["block_id"])
    while key:
        if stored.get(key):
            return stored[key]
        title = next((str(b.get("title") or "") for b in blocks if str(b["block_id"]) == key), "")
        for hint, layout in TITLE_HINTS:
            if hint in title:
                return layout
        key = key.rsplit(".", 1)[0] if "." in key else ""
    return recommend_layout(block, {})


async def _layouts(db, project_id: int, thread_id: str) -> dict:
    """block_id → 章节类型：优先用本项目计划里认定的类型，其次用 chapter_studio 设置。"""
    from services.writing_plans import plan_units  # noqa: F401  (保持依赖清晰)
    layouts = {}
    row = await (await db.execute(
        "SELECT plan FROM writing_plans WHERE project_id=? AND thread_id=? ORDER BY created_at DESC LIMIT 1",
        (project_id, thread_id),
    )).fetchone()
    if row:
        import json
        try:
            plan = json.loads(row["plan"] or "{}")
        except ValueError:
            plan = {}
        for chapter in plan.get("chapters") or []:
            if chapter.get("block_id"):
                layouts[chapter["block_id"]] = chapter.get("layout") or ""
    for stored in await (await db.execute(
        "SELECT b.block_id, cs.layout FROM chapter_studio cs JOIN blocks b ON b.id=cs.block_id "
        "WHERE b.project_id=? AND b.run_thread_id=?", (project_id, thread_id),
    )).fetchall():
        layouts[stored["block_id"]] = stored["layout"] or ""
    return layouts


async def check_thread(db, project_id: int, thread_id: str) -> dict:
    """整章的质检清单：每个可编写章节一份结果 + 汇总。"""
    from services.block_store import list_blocks
    from services.chapter_studio import recommend_layout, writing_units
    from services.writing_specs import list_specs

    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    units = writing_units(blocks)
    specs = (await list_specs(db)).get("specs") or {}
    layouts = await _layouts(db, project_id, thread_id)
    children = {str(b["block_id"]) for b in blocks}
    # 计划里的逐条要求（一次读出，循环里对齐各章）
    plan_requirements: dict[str, list] = {}
    # 取该线程所有计划（新→旧）：同一章若被多个计划覆盖，后写的（requirements 非空）优先
    plan_rows = await (await db.execute(
        "SELECT plan, created_at, rowid FROM writing_plans WHERE project_id=? AND thread_id=? "
        "ORDER BY created_at DESC, rowid DESC", (project_id, thread_id),
    )).fetchall()
    import json as _json
    for plan_row in plan_rows:
        try:
            plan_data = _json.loads(plan_row["plan"] or "{}")
        except ValueError:
            continue
        for chapter in plan_data.get("chapters") or []:
            key = str(chapter.get("block_id"))
            if plan_requirements.get(key):
                continue
            if chapter.get("requirements"):
                plan_requirements[key] = chapter["requirements"]
    results = []
    for unit in units:
        key = str(unit["block_id"])
        has_children = any(other.startswith(key + ".") for other in children if other != key)
        if has_children and not str(unit.get("content") or "").strip():
            continue                       # 纯目录节点不检查
        layout = infer_layout(unit, blocks, layouts)
        recipe = (specs.get(layout) or {}).get("structured") or {}
        written = bool(str(unit.get("content") or "").strip())
        if not written:
            # 还没写正文的章节不算"问题"（否则每个未开始的章节都会报缺表缺图）
            results.append({"block_id": key, "title": unit.get("title"), "layout": layout,
                            "level": unit.get("level"), "started": False,
                            "stats": {"chars": 0, "tables": 0, "figures": 0, "pages": 0.0, "pending": 0},
                            "issues": [], "ok": True})
            continue
        # 结构（骨架）是**整章**的要求：叶子节只查图表/占位/篇幅，骨架在章级聚合里查
        leaf_recipe = {"skeleton": [], "figures": recipe.get("figures") or []}
        row = check_chapter(unit.get("content"), leaf_recipe, int(unit.get("page_budget") or 0))
        content_text = str(unit.get("content") or "")
        # A5 来源标注：正文提到政策/标准/数据，但没有任何【来源】标注
        if written and re.search(r"(政策|标准|规范|指标|数据|报告)", content_text) and "【来源" not in content_text:
            row["issues"].append(_issue("source_mark", "medium",
                "正文引用了政策/标准/数据类信息，但没有【来源】标注"))
            row["ok"] = False
        # C1 评分点覆盖：本章承接的评分点要素在正文里是否有着落
        point_ids = [str(x) for x in (unit.get("review_point_ids") or []) if str(x)]
        if written and point_ids:
            placeholders = ",".join("?" * len(point_ids))
            for point in await (await db.execute(
                f"SELECT point_id, factor, item, criterion FROM review_points "
                f"WHERE run_thread_id=? AND point_id IN ({placeholders})", (thread_id, *point_ids),
            )).fetchall():
                label = str(point["item"] or point["factor"] or "")
                keys = [part for part in re.split(r"[\s、，,；;/（）()【】]+", label) if len(part) >= 2][:3]
                if keys and not any(key in content_text for key in keys):
                    row["issues"].append(_issue("score_point", "high",
                        f"评分点「{label}」在正文里没有落点（该点得分为 0 的风险）"))
                    row["ok"] = False
        # C2 规范条款承接：本章承接的规范条款是否各有落点（最多核 5 条）
        spec_ids = [str(x) for x in (unit.get("spec_requirement_ids") or []) if str(x)][:5]
        if written and spec_ids:
            placeholders = ",".join("?" * len(spec_ids))
            missing = 0
            for requirement in await (await db.execute(
                f"SELECT title, text FROM spec_requirements WHERE run_thread_id=? AND requirement_id IN ({placeholders})",
                (thread_id, *spec_ids),
            )).fetchall():
                label = str(requirement["title"] or requirement["text"] or "")
                keys = [part for part in re.split(r"[\s、，,；;/（）()【】]+", label) if len(part) >= 3][:3]
                if keys and not any(key in content_text for key in keys):
                    missing += 1
            if missing:
                row["issues"].append(_issue("spec_clause", "medium",
                    f"承接的规范条款里有 {missing} 条在正文没有落点"))
        # B1 要求回应：按计划里本章的要求逐条核（要求是主线的准绳）
        for requirement in plan_requirements.get(key, []):
            if not _content_responds_requirement(unit.get("content"), requirement):
                row["issues"].append(_issue("requirement", "high", f"计划要求未回应：{str(requirement.get('text'))[:40]}"))
                row["ok"] = False
        # 素材充足度：有正文但没有任何关联材料/引用来源 → 写作必然大量【待补充】
        if written:
            mats = await (await db.execute(
                "SELECT COUNT(*) AS n FROM chapter_materials cm JOIN blocks b ON b.id=cm.block_id "
                "WHERE b.project_id=? AND b.run_thread_id=? AND b.block_id=?", (project_id, thread_id, key),
            )).fetchone()
            cites = await (await db.execute(
                "SELECT COUNT(*) AS n FROM citations c WHERE c.project_id=? AND c.status='accepted' AND ("
                "  c.block_id=? OR EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = c.id AND l.block_id = ?))",
                (project_id, key, key),
            )).fetchone()
            material_count = int(mats["n"]) if mats else 0
            citation_count = int(cites["n"]) if cites else 0
            row["stats"]["materials"] = material_count
            row["stats"]["citations"] = citation_count
            if citation_count:
                # 有来源但正文没提：多半是"来源接受晚于正文"，需要重新生成才会引用
                cited = await (await db.execute(
                    "SELECT title, publisher, url FROM citations c WHERE c.project_id=? AND c.status='accepted' AND ("
                    "  c.block_id=? OR EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = c.id AND l.block_id = ?))",
                    (project_id, key, key),
                )).fetchall()
                body = str(unit.get("content") or "")
                from services.citations import normalize_title

                def _used(citation) -> bool:
                    key = normalize_title(citation["title"] or "")
                    # 标题要够长才算"指名道姓地引用"（前 10 字会和别的文件撞车，实测误判过）
                    if len(key) >= 12 and key in body.replace(" ", ""):
                        return True
                    return bool(citation["url"] and citation["url"] in body)

                used = any(_used(c) for c in cited)
                if not used:
                    row["issues"].append(_issue("citation_unused", "medium", f"本章已接受 {citation_count} 条来源，但正文里没有引用它们（来源晚于正文时需要重新生成本章）"))
                    row["ok"] = False
            if material_count + citation_count == 0 and row["stats"]["chars"] > 200:
                row["issues"].append(_issue("material", "medium", "素材不足：本章没有关联材料或官方来源，正文容易大量【待补充】"))
                row["ok"] = False
        # 父节点的骨架/图表由子节承担，不做硬性拦截，只做提示
        if has_children:
            row["issues"] = [i for i in row["issues"] if i["code"] == "pending"]
            row["ok"] = True
        results.append({"block_id": key, "title": unit.get("title"), "layout": layout,
                        "level": unit.get("level"), "started": True, **row})
    # 章级：按一级目录（技术评分点）聚合一整棵子树的正文，核骨架与图表
    units = []
    try:
        from services.writing_plans import plan_units as _plan_units
        unit_info = await _plan_units(db, project_id, thread_id)
        subtree = {}
        for block in blocks:
            key = str(block["block_id"])
            for unit in unit_info.get("units") or []:
                uid = str(unit["block_id"])
                if key == uid or key.startswith(uid + "."):
                    subtree.setdefault(uid, []).append(block)
        for unit in unit_info.get("units") or []:
            uid = str(unit["block_id"])
            rows = subtree.get(uid) or []
            content = "\n".join(str(b.get("content") or "") for b in rows if str(b.get("content") or "").strip())
            if not content.strip():
                continue
            base = next((b for b in blocks if str(b["block_id"]) == uid), {})
            layout = infer_layout(base, blocks, layouts)
            recipe = (specs.get(layout) or {}).get("structured") or {}
            budget = sum(int(b.get("page_budget") or 0) for b in rows)
            row = check_chapter(content, recipe, budget)
            units.append({"block_id": uid, "title": unit.get("title"), "layout": layout,
                          "chapters": len(rows), "stats": row["stats"], "issues": row["issues"],
                          "ok": row["ok"]})
    except Exception:
        pass
    started = [r for r in results if r.get("started")]
    summary = {
        "chapters": len(results),
        "started": len(started),
        "clean": sum(1 for r in started if r["ok"]),
        "high": sum(1 for r in started for i in r["issues"] if i["level"] == "high"),
        "medium": sum(1 for r in started for i in r["issues"] if i["level"] == "medium"),
        "groups": {group: sum(1 for r in started for i in r["issues"] if i.get("group") == group)
                   for group in ("规范", "计划", "依据", "素材")},
    }
    summary["units"] = len(units)
    summary["unit_issues"] = sum(1 for u in units for i in u["issues"] if i["level"] in ("high", "medium"))
    return {"thread_id": thread_id, "summary": summary, "chapters": results, "units": units}


async def sync_findings(db, project_id: int, thread_id: str, data: dict) -> dict:
    """把本次结论落库（同一问题只存一条），并用库里的状态过滤/标注。"""
    import uuid as _uuid

    status_map = {}
    for chapter in data.get("chapters") or []:
        for issue in chapter.get("issues") or []:
            message = str(issue.get("message") or "")[:400]
            finding_id = _uuid.uuid4().hex
            await db.execute(
                "INSERT OR IGNORE INTO quality_findings (id, project_id, thread_id, block_id, check_code, "
                "'group', level, message) VALUES (?,?,?,?,?,?,?,?)",
                (finding_id, project_id, thread_id, str(chapter.get("block_id")),
                 str(issue.get("code")), str(issue.get("group")), str(issue.get("level")), message),
            )
            row = await (await db.execute(
                "SELECT id, status FROM quality_findings WHERE thread_id=? AND block_id=? AND check_code=? AND message=?",
                (thread_id, str(chapter.get("block_id")), str(issue.get("code")), message),
            )).fetchone()
            if row:
                issue["finding_id"] = row["id"]
                issue["status"] = row["status"]
                status_map[row["id"]] = row["status"]
    await db.commit()
    # 已标记"已处理"的不再出现在待修清单里
    for chapter in data.get("chapters") or []:
        chapter["issues"] = [issue for issue in chapter.get("issues") or []
                             if issue.get("status") != "ignored"]
        chapter["ok"] = not any(issue["level"] in ("high", "medium") for issue in chapter["issues"])
    data["summary"]["ignored"] = sum(1 for status in status_map.values() if status == "ignored")
    return data


async def ignore_finding(db, project_id: int, finding_id: str) -> bool:
    cursor = await db.execute(
        "UPDATE quality_findings SET status='ignored', updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND project_id=?", (finding_id, project_id),
    )
    await db.commit()
    return cursor.rowcount > 0


__all__ = ["check_chapter", "check_thread", "sync_findings", "ignore_finding", "CHECK_META"]
