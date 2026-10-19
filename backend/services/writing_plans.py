"""技术评分点一级目录的编写计划：先规划 → 用户确认 → 再并发生成（P0）。

**计划单位**：抽象为「技术评分点的一级目录」——即评分根节点（标题「技术评分项应答」，
``response_part == 'scoring'``）下的**直接子目录**。p29 是 7 个（对项目的理解 / 技术方案 /
项目管理 / …），确认 7 次而不是每节 124 次。

**计划覆盖**：该一级目录整棵子树的**叶子章节**，每节给出
- ``tasks``：正文 / 表格 / 图示 三类任务（由章节类型的构成项确定性推导，不烧模型）
- ``gaps``：素材缺口（章节的 ``evidence_required`` 有要求但本章没有已确认素材）

**确认后**：按叶子章节起 job（复用 ``services.chapter_writing`` 的多 Agent 协作），并把
**确认过的计划预置**进 ``job.artifacts.chief`` —— 主编不再重新规划，确认过的就是执行计划。
"""

from __future__ import annotations

import json
import re
import uuid

from agents.chapter_team import Plan
from services.block_store import list_blocks
from services.chapter_studio import LAYOUTS, recommend_layout

SCORING_ROOT_TITLE = "技术评分项应答"


def _ids(value) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    try:
        parsed = json.loads(value or "[]")
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _inside(block_id: str, root_id: str) -> bool:
    return block_id.startswith(root_id + ".")


def _depth(block_id: str) -> int:
    return str(block_id).count(".")


def _direct_children(blocks: list[dict], root: dict) -> list[dict]:
    """一级目录 = 根的直接子节点（按 block_id 的点号深度判定，不依赖 level 数字）。"""
    root_id = str(root["block_id"])
    wanted = _depth(root_id) + 1
    return [b for b in blocks if _depth(str(b["block_id"])) == wanted and _inside(str(b["block_id"]), root_id)]


def _leaves(blocks: list[dict], root: dict) -> list[dict]:
    root_id = str(root["block_id"])
    descendants = [b for b in blocks if _inside(str(b["block_id"]), root_id)]
    leaves = [b for b in descendants
              if not any(_inside(str(other["block_id"]), str(b["block_id"])) for other in descendants)]
    return leaves or ([root] if not descendants else [])


# 项目理解的 8 类表模板（参考件提炼）：按章名落到具体表/图，而不是每章都一样。
UNDERSTANDING_TEMPLATES = {
    "背景": [("text", "政策依据与项目背景结论"), ("table", "政策依据映射表（文件/要求/对应工作）"),
             ("diagram", "趋势或现状对比图")],
    "现状": [("text", "现状判断与差距结论"), ("table", "现状维度表（维度/现状/挑战/影响）")],
    "目标": [("text", "建设目标与承诺边界"), ("table", "目标指标表（指标/当前/目标/说明）")],
    "范围": [("text", "范围与边界说明"), ("table", "纳入/不纳入对照表"), ("diagram", "范围边界图")],
    "内容": [("text", "建设内容与交付物说明"), ("table", "交付物清单表（交付物/能力/验收）"),
             ("diagram", "建设内容分解图")],
    "重难点": [("text", "重难点与风险结论"), ("table", "难点表（难点/复杂度/影响/风险等级）"),
               ("table", "对策表（措施/技术路线/预期成效）")],
    "需求": [("text", "业务需求及特点"), ("table", "需求特点表（维度/指标/目标/验证/保障）"),
             ("diagram", "需求—架构映射图")],
    "痛点": [("text", "痛点与影响分析"), ("table", "痛点表（痛点/表现/影响/紧迫度）")],
    "措施": [("text", "解决措施与落地路径"), ("table", "措施表（方向/方案/预期成效）"),
             ("diagram", "措施落地路径图")],
}


def _template_tasks(leaf: dict) -> list[dict] | None:
    """按章名匹配 8 类模板；没匹配到返回 None（回落到章节类型的通用构成）。"""
    title = str(leaf.get("title") or "")
    for hint, items in UNDERSTANDING_TEMPLATES.items():
        if hint in title:
            return [{"id": f"t{index}", "kind": kind, "title": name,
                     "purpose": "先给结论句，再给分维度事实与论据；引用表/图前写导语、后写落点",
                     "requirement_ids": []}
                    for index, (kind, name) in enumerate(items, 1)]
    return None


REQUIREMENT_PREFIX = {"text": "写明", "table": "用表格呈现", "figure": "配图说明", "material": "引用"}


def _requirements_for(leaf: dict, recipe: dict) -> list[dict]:
    """按配方骨架 + 必表 + 必图 + 素材缺口，起草本章的**逐条编写要求**。

    产品口径：要求由主编写人员输出/确认，系统只做**初稿**（确定性、不调模型、可逐条编辑）。
    """
    title = str(leaf.get("title") or "")
    # 按章名裁剪：只保留与本章相关的骨架项/表/图（否则每个理解类章节都是 13 条通用要求）
    def _relevant(text: str) -> bool:
        """章名与配方项的相关性：用字符重合度判断（"建设范围"↔"范围与内容"、"解决措施"↔"对策表（措施…）"）。"""
        overlap = set(title) & set(str(text))
        return len(overlap) >= 2

    skeleton = [text for text in (recipe.get("skeleton") or []) if _relevant(text)]
    tables = [name for name in (recipe.get("tables") or []) if _relevant(name)]
    figures = [name for name in (recipe.get("figures") or []) if _relevant(name)]
    items: list[dict] = []
    # 优先用 8 类章名模板（本来就是按"背景/范围/内容/重难点/需求/痛点/措施"设计的，最贴合本章）
    templated = _template_tasks(leaf)
    if templated:
        kind_map = {"text": "text", "table": "table", "diagram": "figure"}
        for task in templated:
            items.append({"text": task["title"], "type": kind_map.get(task["kind"], "text")})
    else:
        for text in (skeleton or (recipe.get("skeleton") or [])[:2])[:4]:
            items.append({"text": f"{text}（要给出结论与依据）", "type": "text"})
        for name in (tables or [])[:3]:
            items.append({"text": f"{name}", "type": "table"})
        for name in (figures or [])[:2]:
            items.append({"text": f"{name}", "type": "figure"})
    evidence = str(leaf.get("evidence_required") or "").strip()
    if evidence:
        items.append({"text": evidence, "type": "material"})
    for index, item in enumerate(items, 1):
        item["id"] = f"r{index}"
    return items


def _tasks_for(leaf: dict, layout: str) -> list[dict]:
    """确定性计划：优先按 8 类表模板；否则用章节类型的构成项（名字带"表"出表格、"图"出图示）。"""
    if layout == "understanding":
        templated = _template_tasks(leaf)
        if templated:
            requirement_ids = _ids(leaf.get("spec_requirement_ids"))
            if requirement_ids:
                next(task for task in templated if task["kind"] == "text")["requirement_ids"] = requirement_ids
            return templated
    composition = (LAYOUTS.get(layout) or LAYOUTS["solution"])["blocks"]
    purposes = {
        "text": "先给结论句，再给分维度事实与论据；引用表/图前先写导语，引用后写落点",
        "table": "用维度化列呈现（现状/差距/目标/措施），每格有依据或写【待补充】",
        "diagram": "用结构图呈现层级或流程关系，图后给一句结论",
    }
    tasks = []
    for index, item in enumerate(composition, 1):
        kind = "table" if "表" in item else ("diagram" if "图" in item else "text")
        tasks.append({"id": f"t{index}", "kind": kind, "title": item,
                      "purpose": purposes[kind], "requirement_ids": []})
    if not any(task["kind"] == "text" for task in tasks):
        tasks.insert(0, {"id": "t0", "kind": "text", "title": "正文说明",
                         "purpose": purposes["text"], "requirement_ids": []})
    # 规范要求全部归到第一个正文任务：保证"每条要求都有承接任务"（主编原本也这么做）
    requirement_ids = _ids(leaf.get("spec_requirement_ids"))
    if requirement_ids:
        next(task for task in tasks if task["kind"] == "text")["requirement_ids"] = requirement_ids
    return tasks


async def _material_counts(db, project_id: int) -> dict[int, int]:
    rows = await (await db.execute(
        "SELECT cm.block_id, COUNT(*) AS n FROM chapter_materials cm "
        "JOIN blocks b ON b.id = cm.block_id WHERE b.project_id = ? GROUP BY cm.block_id",
        (project_id,),
    )).fetchall()
    return {(r["block_id"] if hasattr(r, "keys") else r[0]): int(r["n"] if hasattr(r, "keys") else r[1])
            for r in rows}


def _root_of(blocks: list[dict]) -> dict | None:
    """评分根：标题为「技术评分项应答」的节点；找不到时回退到第一个顶层节点。"""
    if not blocks:
        return None
    for block in blocks:
        if str(block.get("title") or "").strip() == SCORING_ROOT_TITLE:
            return block
    top = [b for b in blocks if _depth(str(b["block_id"])) == 0]
    return top[0] if top else blocks[0]


async def plan_units(db, project_id: int, thread_id: str) -> dict:
    """返回本应答的评分根 + 一级目录列表（供编写页在相应节点上挂「编写计划」入口）。"""
    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    root = _root_of(blocks)
    if root is None:
        return {"root": None, "units": []}
    units = _direct_children(blocks, root)
    leaves = _leaves(blocks, root)
    # 每个一级目录的最新计划（status: draft|confirmed）—— 前端用它做"生成闸"
    plans = {}
    for row in await (await db.execute(
        "SELECT root_block_id, id, status FROM writing_plans WHERE project_id=? AND thread_id=? "
        "ORDER BY created_at DESC", (project_id, thread_id))).fetchall():
        plans.setdefault(row["root_block_id"], {"id": row["id"], "status": row["status"]})
    return {
        "root": {"block_id": root["block_id"], "title": root["title"]},
        "units": [{
            "block_id": b["block_id"], "title": b["title"], "level": b.get("level"),
            "leaves": len(_leaves(blocks, b)),
            "plan": plans.get(str(b["block_id"])),
        } for b in units],
        "total_leaves": len(leaves),
    }


async def build_plan(db, project_id: int, thread_id: str, root_block_id: str) -> dict:
    """生成某一级目录的编写计划（确定性、不调模型），落库为 draft 供用户确认。"""
    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    root = next((b for b in blocks if str(b["block_id"]) == str(root_block_id)), None)
    if root is None:
        raise ValueError("找不到该一级目录")
    leaves = _leaves(blocks, root)
    if not leaves:
        raise ValueError("该目录下没有可编写的章节")
    materials = await _material_counts(db, project_id)
    from services.writing_specs import list_specs
    specs = (await list_specs(db)).get("specs") or {}
    # 章节类型优先用**用户在编写页选过的**（chapter_studio.layout），没选过才按标题推荐
    stored = {int(r["block_id"] if hasattr(r, "keys") else r[0]):
              str((r["layout"] if hasattr(r, "keys") else r[1]) or "")
              for r in await (await db.execute(
                  "SELECT block_id, layout FROM chapter_studio WHERE block_id IN "
                  "(SELECT id FROM blocks WHERE project_id=? AND run_thread_id=?)",
                  (project_id, thread_id))).fetchall()}
    # block_id → layout（父章节设置的类型要**下传给子节**：一章的子节同属一类）
    stored_by_key = {b["block_id"]: stored.get(int(b["id"])) for b in blocks}

    title_by_key = {b["block_id"]: str(b.get("title") or "") for b in blocks}
    title_hints = [("项目理解", "understanding"), ("对项目的理解", "understanding"),
                   ("技术方案", "solution"), ("技术实现方案", "solution"),
                   ("实施", "implementation"), ("进度", "implementation"),
                   ("人员", "evidence"), ("业绩", "evidence"), ("团队", "evidence"),
                   ("点对点", "point_to_point")]

    def layout_for(leaf) -> str:
        """章节类型：用户设置 > 祖先章节标题 > 按本章标题推荐（一章的子节同属一类）。"""
        key = str(leaf["block_id"])
        while key:
            if stored_by_key.get(key) in LAYOUTS:
                return stored_by_key[key]
            title = title_by_key.get(key) or ""
            for hint, layout in title_hints:
                if hint in title:
                    return layout
            key = key.rsplit(".", 1)[0] if "." in key else ""
        return recommend_layout(leaf, {})

    chapters, gaps = [], []
    for leaf in leaves:
        layout = layout_for(leaf)
        recipe = (specs.get(layout) or {}).get("structured") or {}
        tasks = _tasks_for(leaf, layout)
        need = str(leaf.get("evidence_required") or "").strip()
        have = materials.get(int(leaf["id"]), 0) if leaf.get("id") else 0
        chapter_gaps = [need] if (need and not have) else []
        if chapter_gaps:
            gaps.append(f"{leaf.get('title')}：{need[:60]}")
        plan = Plan(objective=f"{leaf.get('title')}：按《{LAYOUTS[layout]['label']}》结构编制",
                    tasks=tasks, gaps=chapter_gaps).model_dump()
        chapters.append({
            "id": leaf.get("id"),
            "block_id": leaf["block_id"], "title": leaf.get("title"), "level": leaf.get("level"),
            "layout": layout, "layout_label": LAYOUTS[layout]["label"],
            "pages": int(leaf.get("page_budget") or 0),
            "objective": plan["objective"], "tasks": plan["tasks"], "gaps": chapter_gaps,
            "requirements": _requirements_for(leaf, recipe),
        })
    plan_row = {
        "id": uuid.uuid4().hex,
        "project_id": project_id,
        "thread_id": thread_id,
        "root_block_id": str(root["block_id"]),
        "root_title": root.get("title"),
        "chapters": chapters,
        "gaps": gaps[:30],
    }
    payload = json.dumps(plan_row, ensure_ascii=False)
    # 同一线程、同一一级目录还有未确认的 draft → 原地更新，避免每次点开都堆一份
    existing = await (await db.execute(
        "SELECT id FROM writing_plans WHERE project_id=? AND thread_id=? AND root_block_id=? "
        "AND status='draft' ORDER BY created_at DESC LIMIT 1",
        (project_id, thread_id, str(root["block_id"])),
    )).fetchone()
    if existing:
        plan_row["id"] = existing["id"]
        payload = json.dumps(plan_row, ensure_ascii=False)
        await db.execute("UPDATE writing_plans SET plan=? WHERE id=?", (payload, plan_row["id"]))
    else:
        await db.execute(
            "INSERT INTO writing_plans (id, project_id, thread_id, root_block_id, status, plan) "
            "VALUES (?, ?, ?, ?, 'draft', ?)",
            (plan_row["id"], project_id, thread_id, str(root["block_id"]), payload),
        )
    await db.commit()
    return plan_row


async def update_requirements(db, project_id: int, plan_id: str, block_id: str,
                              requirements: list[dict]) -> dict:
    """编辑某章的编写要求（逐条）。只允许改文本/类型，id 由系统补齐。"""
    loaded = await load_plan(db, project_id, plan_id)
    plan = loaded["plan"]
    chapters = plan.get("chapters") or []
    chapter = next((item for item in chapters if str(item.get("block_id")) == str(block_id)), None)
    if chapter is None:
        raise ValueError("计划里没有这一章")
    cleaned = []
    for index, item in enumerate(requirements or [], 1):
        text = str((item or {}).get("text") or "").strip()
        if not text:
            continue
        cleaned.append({"id": f"r{index}", "text": text[:300],
                        "type": str((item or {}).get("type") or "text")})
    chapter["requirements"] = cleaned
    await db.execute("UPDATE writing_plans SET plan=? WHERE id=?",
                     (json.dumps(plan, ensure_ascii=False), plan_id))
    await db.commit()
    return {"plan_id": plan_id, "block_id": block_id, "requirements": cleaned}


def _content_responds(content: str, requirement: dict) -> bool:
    """要求是否在正文里有落点：表/图按数量判断，文本按要求里的关键词判断。"""
    text = str(content or "")
    kind = requirement.get("type") or "text"
    if kind == "table":
        return text.count("| ---") >= 1
    if kind == "figure":
        return ":::diagram" in text or "![" in text
    keywords = [part for part in re.split(r"[\s、，,；;/（）()【】]+", str(requirement.get("text") or ""))
                if len(part) >= 2][:4]
    return bool(keywords) and any(keyword in text for keyword in keywords)


async def requirement_coverage(db, project_id: int, plan_id: str, thread_id: str) -> dict:
    """要求回应率：计划里每条要求是否已在正文里有落点（进度的"标准维度"）。"""
    from services.block_store import list_blocks

    loaded = await load_plan(db, project_id, plan_id)
    plan = loaded["plan"]
    blocks = {str(b.get("block_id")): dict(b) for b in await list_blocks(db, project_id, thread_id)}
    chapters = []
    for chapter in plan.get("chapters") or []:
        block = blocks.get(str(chapter.get("block_id"))) or {}
        content = str(block.get("content") or "")
        items = []
        for requirement in chapter.get("requirements") or []:
            items.append({**requirement, "responded": _content_responds(content, requirement)})
        chapters.append({"block_id": chapter.get("block_id"), "title": chapter.get("title"),
                         "words": len(content.replace(" ", "")),
                         "total": len(items),
                         "responded": sum(1 for item in items if item["responded"]),
                         "requirements": items})
    total = sum(chapter["total"] for chapter in chapters)
    responded = sum(chapter["responded"] for chapter in chapters)
    return {"plan_id": plan_id, "chapters": chapters,
            "total": total, "responded": responded,
            "percent": round(responded / total * 100) if total else 0}


async def chapter_needs(db, project_id: int, plan_id: str, thread_id: str) -> dict:
    """需求级充足度：每条要求 → 素材需求（已补 N 片段 / 待补）。"""
    loaded = await load_plan(db, project_id, plan_id)
    plan = loaded["plan"]
    bound_rows = await (await db.execute(
        "SELECT block_id, requirement_id, citation_id, segment_index FROM need_snippets WHERE plan_id=?",
        (plan_id,),
    )).fetchall()
    for chapter in plan.get("chapters") or []:
        block_id = str(chapter.get("block_id"))
        needs = []
        for requirement in chapter.get("requirements") or []:
            rid = str(requirement.get("id"))
            # 要求 id 是每章内编号（r1/r2…），必须连 block_id 一起判，否则会串章
            bound = [row for row in bound_rows
                     if str(row["requirement_id"]) == rid and str(row["block_id"]) == block_id]
            needs.append({"requirement_id": rid, "text": requirement.get("text"),
                          "kind": requirement.get("type") or "text",
                          "snippets": len(bound), "status": "已补" if bound else "待补"})
        chapter["needs"] = needs
    pending = sum(1 for chapter in plan.get("chapters") or []
                  for need in chapter.get("needs") or [] if need["status"] == "待补")
    return {"plan_id": plan_id, "chapters": plan.get("chapters") or [], "pending": pending}


async def bind_snippets(db, project_id: int, plan_id: str, block_id: str, requirement_id: str,
                        citation_id: str, indices: list[int]) -> dict:
    """把来源里的片段绑到某条要求上（这条要求就算"有素材了"）。"""
    loaded = await load_plan(db, project_id, plan_id)
    chapter = next((item for item in loaded["plan"].get("chapters") or []
                    if str(item.get("block_id")) == str(block_id)), None)
    if chapter is None:
        raise ValueError("计划里没有这一章")
    if not any(str(item.get("id")) == str(requirement_id) for item in chapter.get("requirements") or []):
        raise ValueError("本章没有这条要求")
    for index in indices:
        await db.execute(
            "INSERT OR IGNORE INTO need_snippets (plan_id, block_id, requirement_id, citation_id, segment_index) "
            "VALUES (?,?,?,?,?)", (plan_id, str(block_id), str(requirement_id), citation_id, int(index)),
        )
    await db.commit()
    return {"plan_id": plan_id, "block_id": block_id, "requirement_id": requirement_id,
            "bound": len(indices)}


async def chapter_plan(db, project_id: int, thread_id: str, block_id: str) -> dict:
    """**本章编写计划**（用户口径：计划是针对每一个章节的，不是按大章节设计）。

    只返回这一章自己该知道的东西：
      ① 要求依据：每条要求 + 依据（评分点/规范条款）+ 是否已回应
      ② 缺失素材：每条要求对应的素材需求（已补片段 / 待补）+ 正文素材缺口
    """
    from services.block_store import list_blocks
    from services.quality_check import basis_coverage

    found = await plan_of_block(db, project_id, thread_id, str(block_id))
    chapter = None
    plan_id = ""
    if found:
        plan_id, plan = found
        chapter = next((item for item in plan.get("chapters") or []
                        if str(item.get("block_id")) == str(block_id)), None)
    blocks = {str(b.get("block_id")): dict(b) for b in await list_blocks(db, project_id, thread_id)}
    block = blocks.get(str(block_id)) or {}
    content = str(block.get("content") or "")
    # 页数：篇幅的唯一 owner 是「应答策划」的分配（blocks.page_budget）。
    # 本章自己有预算用它；否则取最近祖先的预算**按可写单元均分**（子节不单独占页）。
    pages_of_block = int(block.get("page_budget") or 0)
    ancestors = []
    ancestor_id = str(block_id)
    while not pages_of_block and "." in ancestor_id:
        ancestor_id = ancestor_id.rsplit(".", 1)[0]
        budget = int((blocks.get(ancestor_id) or {}).get("page_budget") or 0)
        if budget:
            ancestors = [key for key in blocks
                         if key.startswith(ancestor_id)
                         and not any(other != key and other.startswith(key + ".") for other in blocks)]
            pages_of_block = max(1, round(budget / max(1, len(ancestors))))
            break

    # 依据：本章承接的评分点/规范条款
    try:
        basis = await basis_coverage(db, project_id, thread_id, str(block_id))
    except ValueError:
        basis = {"points": [], "clauses": [], "responded": 0, "total": 0}
    basis_labels = [item["label"] for item in (basis.get("points") or []) + (basis.get("clauses") or [])]

    bound_rows = await (await db.execute(
        "SELECT requirement_id, citation_id, segment_index FROM need_snippets WHERE block_id=?", (str(block_id),),
    )).fetchall()
    # 素材状态按"门槛"判定：不同类型的需求要看是否绑到了**对应类型**的片段
    need_kind = {"text": {"text", "table"}, "table": {"table"}, "figure": {"image"}, "material": {"text", "table", "image"}}
    bound_by_req: dict[str, list[dict]] = {}
    for row in bound_rows:
        citation = await (await db.execute(
            "SELECT title, segments, trust FROM citations WHERE id=?", (row["citation_id"],),
        )).fetchone()
        if not citation:
            continue
        try:
            segments = json.loads(citation["segments"] or "[]")
        except ValueError:
            segments = []
        index = int(row["segment_index"])
        segment = segments[index] if 0 <= index < len(segments) else {}
        bound_by_req.setdefault(str(row["requirement_id"]), []).append({
            "kind": segment.get("kind"), "heading": segment.get("heading"),
            "source": citation["title"], "trust": citation["trust"],
            "excerpt": str(segment.get("content") or "")[:120],
        })

    requirements = []
    for requirement in (chapter or {}).get("requirements") or []:
        rid = str(requirement.get("id"))
        kind = requirement.get("type") or "text"
        wanted = need_kind.get(kind, {"text"})
        matched = [item for item in bound_by_req.get(rid, []) if item.get("kind") in wanted]
        status = "已满足" if matched else ("已补不足" if bound_by_req.get(rid) else "待补")
        requirements.append({
            **requirement,
            "basis": basis_labels,                       # 这条要求服务的依据（评分点/条款）
            "responded": _content_responds(content, requirement),
            "material": status,
            "bound": bound_by_req.get(rid, []),
        })
    # C 怎么写更好：读配方（骨架/必表/必图/失分点），只读展示
    layout = block.get("design_layout") or (chapter or {}).get("layout") or "generic"
    from services.writing_specs import list_specs
    specs = (await list_specs(db)).get("specs") or {}
    recipe = (specs.get(layout) or {}).get("structured") or {}
    guidance = {
        "layout": layout,
        "skeleton": (recipe.get("skeleton") or [])[:6],
        "tables": (recipe.get("tables") or [])[:6],
        "figures": (recipe.get("figures") or [])[:4],
        "pitfalls": (recipe.get("pitfalls") or [])[:4],
        "notes": (recipe.get("notes") or [])[:4],
    }
    # E 图表清单：计划要求几张表/几张图 + 用途与来源
    plan_requirements = (chapter or {}).get("requirements") or []
    table_items = [item for item in plan_requirements if (item.get("type") or "") == "table"]
    figure_items = [item for item in plan_requirements if (item.get("type") or "") == "figure"]
    figures_plan = {
        "tables": [{"name": str(item.get("text") or "")[:40],
                    "purpose": str(item.get("text") or ""),
                    "source": "外部来源片段 / 我方材料"} for item in table_items],
        "figures": [{"name": str(item.get("text") or "")[:40],
                     "purpose": str(item.get("text") or ""),
                     "source": "外部来源图片 / 生成示意图"} for item in figure_items],
    }
    gaps = [item for item in (chapter or {}).get("gaps") or []]
    pending = sum(1 for requirement in requirements if requirement["material"] != "已满足")
    return {
        "plan_id": plan_id, "block_id": str(block_id), "title": block.get("title") or (chapter or {}).get("title"),
        "layout": block.get("design_layout") or (chapter or {}).get("layout"),
        "pages": pages_of_block or (chapter or {}).get("pages") or 0,
        "words": len(content.replace(" ", "")),
        "requirements": requirements,
        "basis": basis,
        "guidance": guidance,
        "figures_plan": figures_plan,
        "gaps": gaps,
        "stats": {"requirements": len(requirements),
                  "responded": sum(1 for requirement in requirements if requirement["responded"]),
                  "material_pending": pending,
                  "material_short": sum(1 for requirement in requirements if requirement["material"] == "已补不足")},
    }


async def plan_of_block(db, project_id: int, thread_id: str, block_id: str) -> tuple[str, dict] | None:
    """找到**包含该章**的计划（计划按一级目录分开，不能简单取"最新一份"）。"""
    rows = await (await db.execute(
        "SELECT id, plan FROM writing_plans WHERE project_id=? AND thread_id=? "
        "ORDER BY created_at DESC, rowid DESC", (project_id, thread_id),
    )).fetchall()
    for row in rows:
        try:
            plan = json.loads(row["plan"] or "{}")
        except ValueError:
            continue
        if any(str(item.get("block_id")) == str(block_id) for item in plan.get("chapters") or []):
            return row["id"], plan
    return None


async def needs_for_prompt(db, project_id: int, thread_id: str, block_id: str) -> list[dict]:
    """生成时注入：本章每条要求 + 绑定到它的片段正文（"按要求给素材"）。"""
    found = await plan_of_block(db, project_id, thread_id, block_id)
    if not found:
        return []
    _, plan = found
    chapter = next((item for item in plan.get("chapters") or []
                    if str(item.get("block_id")) == str(block_id)), None)
    if not chapter:
        return []
    bound = await (await db.execute(
        "SELECT requirement_id, citation_id, segment_index FROM need_snippets WHERE block_id=?", (str(block_id),),
    )).fetchall()
    result = []
    for requirement in chapter.get("requirements") or []:
        rid = str(requirement.get("id"))
        snippets = []
        for row_bound in bound:
            if str(row_bound["requirement_id"]) != rid:
                continue
            citation = await (await db.execute(
                "SELECT segments, title FROM citations WHERE id=?", (row_bound["citation_id"],),
            )).fetchone()
            if not citation:
                continue
            try:
                segments = json.loads(citation["segments"] or "[]")
            except ValueError:
                segments = []
            index = int(row_bound["segment_index"])
            if 0 <= index < len(segments):
                snippets.append({"kind": segments[index].get("kind"),
                                 "heading": segments[index].get("heading"),
                                 "content": str(segments[index].get("content") or "")[:600],
                                 "source": citation["title"]})
        result.append({"requirement": requirement.get("text"), "type": requirement.get("type"),
                       "snippets": snippets})
    return result


async def load_plan(db, project_id: int, plan_id: str) -> dict:
    row = await (await db.execute(
        "SELECT * FROM writing_plans WHERE id = ? AND project_id = ?", (plan_id, project_id),
    )).fetchone()
    if not row:
        raise ValueError("找不到编写计划")
    return {"status": row["status"], "confirmed_at": row["confirmed_at"],
            "plan": json.loads(row["plan"] or "{}")}


async def confirm_plan(db, project_id: int, plan_id: str, leaf_block_ids: list[str] | None = None) -> dict:
    """确认计划并起 job（只确认勾选的叶子；不传则全部）。确认过的计划是**执行计划**。"""
    from services.chapter_writing import create_job, queue_job

    loaded = await load_plan(db, project_id, plan_id)
    if loaded["status"] == "confirmed":
        raise ValueError("该计划已确认，不能重复启动")
    plan_row = loaded["plan"]
    wanted = {str(x) for x in leaf_block_ids or []}
    chapters = [c for c in plan_row.get("chapters") or []
                if not wanted or str(c["block_id"]) in wanted]
    if not chapters:
        raise ValueError("没有选中任何章节")
    plans = {str(c["block_id"]): c for c in chapters}

    from routes.chapter_studio import chapter_context, material_rows, source_for
    from services.chapter_studio import build_writing_prompt
    from services.config_store import config_store

    configs, _ = config_store.get_configs_and_next_index()
    if not configs:
        raise ValueError("请先配置可用的生成模型")
    settings = {}
    created = []
    thread_id = plan_row["thread_id"]
    for block_id, chapter in plans.items():
        block = next((b for b in [dict(x) for x in await list_blocks(db, project_id, thread_id)
                                  if str(x.get("block_id")) == block_id]), None)
        if block is None:
            raise ValueError(f"章节不存在：{block_id}")
        reqs, points = await chapter_context(db, block)
        materials = [m for m in await material_rows(db, block) if m["confirmed"]]
        source = await source_for(block)
        _, prompt = build_writing_prompt(block, reqs, points, materials, chapter["layout"], source)
        package = json.loads(prompt) | {"block_id": block["block_id"], "layout": chapter["layout"]}
        from services.writing_specs import spec_for
        from services.citations import citations_for_prompt, citation_line
        package["writing_spec"] = await spec_for(db, chapter["layout"])
        package["citations"] = [citation_line(c) for c in await citations_for_prompt(db, project_id, chapter["block_id"])]
        package["requirements"] = await needs_for_prompt(db, project_id, thread_id, chapter["block_id"])
        job_id = await create_job(db, block, package, settings, auto_save=True)   # 返回 job id
        # 预置主编计划：run_job 看到 artifacts.chief 就不再调主编重新规划
        spec = package.get("writing_spec") or {}
        artifacts = {"chief": {"plan": {"objective": chapter["objective"],
                                        "tasks": chapter["tasks"], "gaps": chapter["gaps"]},
                               "model": "confirmed-plan"},
                     "spec": {"global": (spec.get("global") or {}).get("version"),
                              "recipe": (spec.get("recipe") or {}).get("version")}}
        await db.execute("UPDATE chapter_writing_jobs SET artifacts=? WHERE id=?",
                         (json.dumps(artifacts, ensure_ascii=False), job_id))
        await db.commit()          # create_job 内部用 BEGIN IMMEDIATE，必须先提交再进下一轮
        created.append(job_id)
    await db.execute("UPDATE writing_plans SET status='confirmed', confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
                     (plan_id,))
    await db.commit()
    for job_id in created:
        queue_job(job_id)
    return {"plan_id": plan_id, "jobs": created, "chapters": len(created)}


__all__ = ["plan_units", "build_plan", "load_plan", "confirm_plan",
           "update_requirements", "requirement_coverage", "chapter_needs", "bind_snippets",
           "needs_for_prompt", "plan_of_block", "chapter_plan"]
