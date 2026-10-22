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


def _writable_nodes(blocks: list[dict], root: dict) -> list[dict]:
    """可写节点 = 子节点里"有正文的" 或 "没有子节点的"。

    背景：p29 的「对项目的理解」父章自己有 7000 多字，下面还有 7 个子节。
    计划必须给**每个可写节点**建条目 —— 否则用户打开父章会看到"要求 0 条"（实测 bug）。
    """
    root_id = str(root["block_id"])
    descendants = [b for b in blocks if _inside(str(b["block_id"]), root_id)]
    writable = []
    for block in descendants:
        has_children = any(_inside(str(other["block_id"]), str(block["block_id"])) for other in descendants)
        if has_children and not str(block.get("content") or "").strip():
            continue
        writable.append(block)
    return writable or ([root] if not descendants else [])


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


async def _bind_clauses_to_chapters(db, project_id: int, thread_id: str,
                                    chapters: list[dict]) -> dict[str, int]:
    """D1：把招标技术条款按关键词挂到可写章节（写回 blocks.spec_requirement_ids）。

    背景：153 条技术规范条款原本一条都没绑到章节，"对内要求是什么"无从体现。
    规则：章名与条款主题/标题的字符重合 ≥2 → 命中；强制性条款优先；每章最多 5 条；
          已有人工绑定的章节不覆盖。
    """
    rows = await (await db.execute(
        "SELECT requirement_id, topic, title, text, mandatory_level, order_idx "
        "FROM spec_requirements WHERE run_thread_id=? ORDER BY order_idx", (thread_id,),
    )).fetchall()
    clauses = [dict(row) for row in rows]
    if not clauses:
        return {}
    bound: dict[str, int] = {}
    for chapter in chapters:
        block_id = str(chapter.get("block_id"))
        try:
            existing = json.loads(chapter.get("spec_requirement_ids") or "[]")
        except ValueError:
            existing = []
        if existing:
            bound[block_id] = len(existing)
            continue
        title_chars = set(str(chapter.get("title") or ""))
        scored = []
        for clause in clauses:
            hay = set(str(clause.get("topic") or "") + str(clause.get("title") or ""))
            score = len(title_chars & hay)
            if score >= 2:
                mandatory = str(clause.get("mandatory_level") or "") in ("mandatory", "required")
                scored.append((-score, 0 if mandatory else 1, str(clause.get("requirement_id"))))
        picked = [item[2] for item in sorted(scored)[:5]]
        if not picked:
            continue
        await db.execute("UPDATE blocks SET spec_requirement_ids=? WHERE project_id=? AND run_thread_id=? AND block_id=?",
                         (json.dumps(picked), project_id, thread_id, block_id))
        chapter["spec_requirement_ids"] = json.dumps(picked)
        bound[block_id] = len(picked)
    await db.commit()
    return bound


def _criterion_aspects(criterion: str) -> list[str]:
    """从评分点的"满分档"里取出**要求要素**（不取评分档位本身）。

    评分点 criterion 通常是「（1）满分档 … 得 8-10分。（2）基本档 … 得 4-7分…」，
    我们要的是**第一档里要求写清楚的东西**，例如「项目背景、现状、目标、建设范围、
    建设内容、项目重难点」「业务需求及特点」「难点和痛点」「解决措施」。
    """
    text = str(criterion or "").strip()
    if not text:
        return []
    match = re.search(r"[（(]\d[）)](.*?)(?=[（(]\d[）)]|$)", text, re.S)
    band = (match.group(1) if match else text).strip()
    band = re.sub(r"[，。；]?得\s*[\d.\-]+\s*分.*$", "", band).strip()   # 去掉"得 8-10 分…"
    band = re.sub(r"^[，。；、\s]+", "", band)
    tokens, seen = [], set()
    for token in re.split(r"[、，,；;。]", band):
        token = token.strip()
        # 去掉"深刻理解/能较好的分析/本项目建设的…"这类引导词，留下要素名
        for _ in range(6):
            token = re.sub(r"^(深刻|基本|不完全|较好|的|能|并|以及|且|和|分析|理解|提出|本项目|本项目建设的|相关|对|建设|项目)", "", token).strip()
        token = re.sub(r"^[的并和与及]+", "", token)
        token = re.sub(r"等$", "", token).strip(" 。，、；")
        if 2 <= len(token) <= 20 and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens[:12]


def _requirements_for(leaf: dict, recipe: dict, points: list[dict] | None = None) -> list[dict]:
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
    # ① 评分点要素优先，且**按章名挑出本章该写的那几项**
    #    （同一评分点覆盖 7 章：背景/现状/目标/范围/内容/重难点/需求/痛点/措施）
    for point in points or []:
        label = str(point.get("item") or point.get("factor") or "").strip()
        for element in _criterion_aspects(str(point.get("criterion") or "")):
            # 严格匹配：要素名必须是章名的子串（或反之），避免"项目背景现状目标"混进"重难点"
            hits = element in title or (len(title) >= 3 and title in element)
            if not hits:
                continue
            items.append({"text": f"写清楚「{element}」", "type": "text", "source": "point",
                          "source_label": f"评分点·{label[:10]}"})
    if not items:
        # 章名匹配不上时，退到"满分档的整体要求"一句
        for point in points or []:
            label = str(point.get("item") or point.get("factor") or "").strip()
            aspects = _criterion_aspects(str(point.get("criterion") or ""))
            if aspects:
                items.append({"text": f"写清楚：{'、'.join(aspects[:4])}", "type": "text",
                              "source": "point", "source_label": f"评分点·{label[:10]}"})
                break
    # ② 产出要求（表/图）用 8 类章名模板兜底，保证"该出的表图"不缺
    templated = _template_tasks(leaf)
    kind_map = {"text": "text", "table": "table", "diagram": "figure"}
    for task in (templated or []):
        if task.get("kind") == "text":
            continue
        items.append({"text": task["title"], "type": kind_map.get(task["kind"], "table"),
                      "source": "template", "source_label": "模板兜底"})
    # ③ 模板也没有 -> 用配方骨架/必表/必图
    if not items:
        for text in (skeleton or (recipe.get("skeleton") or [])[:2])[:4]:
            items.append({"text": f"{text}（要给出结论与依据）", "type": "text",
                          "source": "template", "source_label": "模板兜底"})
        for name in (tables or [])[:3]:
            items.append({"text": f"{name}", "type": "table", "source": "template", "source_label": "模板兜底"})
        for name in (figures or [])[:2]:
            items.append({"text": f"{name}", "type": "figure", "source": "template", "source_label": "模板兜底"})
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
    leaves = _writable_nodes(blocks, root)
    if not leaves:
        raise ValueError("该目录下没有可编写的章节")
    materials = await _material_counts(db, project_id)
    from services.writing_specs import list_specs
    specs = (await list_specs(db)).get("specs") or {}
    # 总篇幅（「应答策划」确认过的总页数）——系统据此统筹分配
    total_pages = 0
    budget_row = await (await db.execute(
        "SELECT value FROM split_answers WHERE thread_id=? AND key='page_budget'", (thread_id,),
    )).fetchone()
    if budget_row:
        try:
            total_pages = int((json.loads(budget_row["value"]) or {}).get("total") or 0)
        except ValueError:
            total_pages = 0
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
    # 本章承接的评分点（要求来源的第一优先级）
    points_by_block: dict[str, list[dict]] = {}
    for leaf in leaves:
        ids = []
        try:
            ids = [str(x) for x in json.loads(leaf.get("review_point_ids") or "[]")]
        except ValueError:
            ids = []
        if ids:
            placeholders = ",".join("?" * len(ids))
            points_by_block[str(leaf.get("block_id"))] = [dict(row) for row in await (await db.execute(
                f"SELECT point_id, factor, item, criterion, points FROM review_points "
                f"WHERE run_thread_id=? AND point_id IN ({placeholders})", (thread_id, *ids),
            )).fetchall()]
    # 系统统筹分配篇幅：评分点分值 × 关键项 × 已有正文 加权，一次分到各章
    weights = []
    for leaf in leaves:
        weight = 1.0
        ids = []
        try:
            ids = [str(x) for x in json.loads(leaf.get("review_point_ids") or "[]")]
        except ValueError:
            ids = []
        if ids:
            placeholders = ",".join("?" * len(ids))
            row = await (await db.execute(
                f"SELECT COALESCE(SUM(points),0) AS p FROM review_points "
                f"WHERE run_thread_id=? AND point_id IN ({placeholders})", (thread_id, *ids),
            )).fetchone()
            if row and float(row["p"] or 0) > 0:
                weight = float(row["p"])
        if str(leaf.get("mandatory") or "") in ("1", "true", "True"):
            weight *= 1.5
        if str(leaf.get("content") or "").strip():
            weight *= 1.2
        weights.append(weight)
    auto_pages: dict[str, int] = {}
    if total_pages > 0 and weights:
        scale = total_pages / sum(weights)
        assigned = [(str(leaf.get("block_id")), max(1, round(weight * scale))) for leaf, weight in zip(leaves, weights)]
        diff = total_pages - sum(pages for _, pages in assigned)
        if assigned and diff:
            biggest = max(assigned, key=lambda item: item[1])
            assigned = [(block_id, pages + (diff if block_id == biggest[0] else 0)) for block_id, pages in assigned]
        auto_pages = dict(assigned)

    # D1：招标条款 → 章节匹配（一次写入，人工改过的不会被覆盖）
    try:
        await _bind_clauses_to_chapters(db, project_id, thread_id, leaves)
    except Exception:
        pass

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
            "pages": int(leaf.get("page_budget") or 0) or auto_pages.get(str(leaf.get("block_id")), 0),
            "objective": plan["objective"], "tasks": plan["tasks"], "gaps": chapter_gaps,
            "requirements": _requirements_for(leaf, recipe, points_by_block.get(str(leaf.get("block_id")))),
            "has_children": any(str(x.get("block_id") or "").startswith(str(leaf.get("block_id")) + ".")
                                for x in leaves),
            "confirmed": True,   # 系统统筹生成即生效；用户可改，改完仍生效
        })
        # 父章（自己写正文、又有子节）追加"总述"要求 —— 避免与子节内容重复
        if chapters[-1]["has_children"] and str(leaf.get("content") or "").strip():
            children_count = sum(1 for x in leaves
                                 if str(x.get("block_id") or "").startswith(str(leaf.get("block_id")) + "."))
            chapters[-1]["requirements"].insert(0, {
                "text": f"本章只写总述，概述下列 {children_count} 节要点，不重复各节细节",
                "type": "text", "source": "system", "source_label": "父章总述",
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
        "ORDER BY created_at DESC LIMIT 1",
        (project_id, thread_id, str(root["block_id"])),
    )).fetchone()
    if existing:
        plan_row["id"] = existing["id"]
        payload = json.dumps(plan_row, ensure_ascii=False)
        await db.execute("UPDATE writing_plans SET plan=?, version=COALESCE(version,1)+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (payload, plan_row["id"]))
    else:
        await db.execute(
            "INSERT INTO writing_plans (id, project_id, thread_id, root_block_id, status, plan) "
            "VALUES (?, ?, ?, ?, 'confirmed', ?)",
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
    await db.execute("UPDATE writing_plans SET plan=?, version=COALESCE(version,1)+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
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


async def confirm_chapter_plan(db, project_id: int, plan_id: str, block_id: str = "") -> dict:
    """确认计划：block_id 为空 = 整份目录计划（批量，其下所有章一起确认）。"""
    loaded = await load_plan(db, project_id, plan_id)
    plan = loaded["plan"]
    chapters = plan.get("chapters") or []
    if not chapters:
        raise ValueError("计划里没有章节")
    if block_id:
        if not any(str(item.get("block_id")) == str(block_id) for item in chapters):
            raise ValueError("计划里没有这一章")
        for item in chapters:
            if str(item.get("block_id")) == str(block_id):
                item["confirmed"] = True
        await db.execute(
            "UPDATE writing_plans SET plan=?, version=COALESCE(version,1)+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan_id))
        await db.commit()
        return {"plan_id": plan_id, "block_id": str(block_id), "confirmed": True,
                "all_confirmed": all(item.get("confirmed") for item in chapters)}
    for item in chapters:
        item["confirmed"] = True
    await db.execute(
        "UPDATE writing_plans SET plan=?, status='confirmed', confirmed_at=CURRENT_TIMESTAMP, "
        "version=COALESCE(version,1)+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(plan, ensure_ascii=False), plan_id))
    await db.commit()
    return {"plan_id": plan_id, "block_id": "", "confirmed": True, "all_confirmed": True}


def chapter_completion(chapter_plan_data: dict, issues: list[dict]) -> dict:
    """完成判定：要求全落点 + 素材全满足 + 图表齐 + 无高/中优问题。可人工覆盖（另行留痕）。"""
    reasons = []
    requirements = chapter_plan_data.get("requirements") or []
    missing_responded = [item["text"] for item in requirements if not item.get("responded")]
    if missing_responded:
        reasons.append(f"{len(missing_responded)} 条要求正文未回应")
    missing_material = [item["text"] for item in requirements if item.get("material") != "已满足"]
    if missing_material:
        reasons.append(f"{len(missing_material)} 条要求素材未满足")
    figures = chapter_plan_data.get("figures_plan") or {}
    is_parent = bool(chapter_plan_data.get("has_children"))
    if not (figures.get("tables") or figures.get("figures")) and not is_parent:
        reasons.append("没有可执行的图表计划")
    high = [issue for issue in issues if issue.get("level") in ("high", "medium")]
    if high:
        reasons.append(f"{len(high)} 条待修问题")
    if not (chapter_plan_data.get("words") or 0):
        reasons.append("还没有正文")
    return {"ok": not reasons, "reasons": reasons}


async def update_figures_plan(db, project_id: int, plan_id: str, block_id: str,
                               tables: list[dict], figures: list[dict]) -> dict:
    """编辑本章的图表计划（每张表/图的用途与来源）。"""
    loaded = await load_plan(db, project_id, plan_id)
    plan = loaded["plan"]
    chapter = next((item for item in plan.get("chapters") or []
                    if str(item.get("block_id")) == str(block_id)), None)
    if chapter is None:
        raise ValueError("计划里没有这一章")

    def _clean(items: list[dict]) -> list[dict]:
        result = []
        for item in items or []:
            name = str((item or {}).get("name") or "").strip()
            if not name:
                continue
            result.append({"name": name[:60],
                           "purpose": str((item or {}).get("purpose") or "").strip()[:120],
                           "source": str((item or {}).get("source") or "").strip()[:120]})
        return result

    chapter["figures_plan"] = {"tables": _clean(tables), "figures": _clean(figures)}
    await db.execute("UPDATE writing_plans SET plan=?, version=COALESCE(version,1)+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (json.dumps(plan, ensure_ascii=False), plan_id))
    await db.commit()
    return {"plan_id": plan_id, "block_id": block_id, "figures_plan": chapter["figures_plan"]}


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


# ── 可执行计划的六个块（D1~D4）─────────────────────────────────────────────
# 应答人员真正要的：这一段写什么、答哪几点、多少字、用什么料、写完怎么验。
# 数据来源：评分点满分档（对内要求）+ 招标条款（本章承接）+ 参考骨架（必答点）
#          + 交付物（段落/表/图）+ 字数分配 + 素材清单 + 自检。

DELIVERABLE_KIND_LABEL = {"text": "文字", "table": "表格", "figure": "图示"}
TABLE_WORDS, FIGURE_WORDS = 300, 200          # 表/图折算字数


def _content_segments(content: str) -> list[tuple[str, int]]:
    """按标题把正文切成段，返回 [(标题, 字数)]，用于"每块交付物已写多少"。"""
    text = str(content or "")
    if not text.strip():
        return []
    lines = text.splitlines()
    segments, current_title, buffer = [], "（开头）", []
    for line in lines:
        match = re.match(r"^\s*(#{2,4}\s*|[一二三四五六七八九十]+[、.]|\(?\d+[）)]\s*)", line)
        if match and len(line.strip()) <= 60:
            if buffer:
                segments.append((current_title, len("".join(buffer).replace(" ", ""))))
            current_title, buffer = line.strip("# 　").strip(), []
        else:
            buffer.append(line)
    if buffer:
        segments.append((current_title, len("".join(buffer).replace(" ", ""))))
    return [(title, words) for title, words in segments if words > 0]


def _deliverables(requirements: list[dict], content: str) -> list[dict]:
    """交付物清单：每条要求 → 一个交付物（文字/表/图），带必答点、目标字数、已写字数。"""
    segments = _content_segments(content)
    written_total = sum(words for _, words in segments)

    def _written(title_hint: str) -> int:
        keys = [part for part in re.split(r"[「」()（）\s]+", title_hint) if len(part) >= 2][:3]
        for segment_title, words in segments:
            if any(key in segment_title for key in keys):
                return words
        return 0

    result = []
    for requirement in requirements:
        kind = requirement.get("type") or "text"
        title = str(requirement.get("text") or "")
        written = _written(title) if kind == "text" else 0
        result.append({
            "kind": kind,
            "kind_label": DELIVERABLE_KIND_LABEL.get(kind, "文字"),
            "title": title,
            "brief": [requirement.get("source_label") or ""],
            "written_words": written,
            "written_detected": written > 0 if kind == "text" else True,
            "source": requirement.get("source"),
        })
    return result, written_total, segments


def _word_plan(pages: int, deliverables: list[dict], written_total: int) -> dict:
    """字数分配：总字数 = 页数 × 250；表格/图示按折算字数扣掉，余下按文字条目均分。"""
    total = int(pages or 0) * 250
    fixed = sum(TABLE_WORDS if item["kind"] == "table" else FIGURE_WORDS if item["kind"] == "figure" else 0
                for item in deliverables)
    text_items = [item for item in deliverables if item["kind"] == "text"]
    remaining = max(0, total - fixed)
    per_text = round(remaining / len(text_items)) if text_items else 0
    for item in deliverables:
        item["target_words"] = (TABLE_WORDS if item["kind"] == "table"
                                else FIGURE_WORDS if item["kind"] == "figure" else per_text)
        target = item["target_words"]
        written = item.get("written_words") or 0
        item["status"] = ("未写" if not written else
                          "不足" if written < target * 0.6 else
                          "超额" if written > target * 1.5 else "达标")
        item["gap_words"] = written - target
    return {"total": total, "written": written_total,
            "percent": round(written_total / total * 100) if total else 0,
            "over": bool(total and written_total > total * 1.3)}


def _split_key_points(key_points: list[str], deliverables: list[dict]) -> dict[int, list[str]]:
    """把"必须答到的点"按文字交付物顺序均分（如背景→项目由来/建设动因）。"""
    mapping: dict[int, list[str]] = {}
    text_indexes = [index for index, item in enumerate(deliverables) if item.get("kind") == "text"]
    if not key_points or not text_indexes:
        return mapping
    per = max(1, -(-len(key_points) // len(text_indexes)))
    for order, index in enumerate(text_indexes):
        chunk = key_points[order * per: (order + 1) * per]
        if chunk:
            mapping[index] = chunk
    return mapping


def _key_points_from_title(title: str) -> list[str]:
    """参考文档 8 类骨架 → 本章"必须回答的几点"（必答点）。"""
    table = {
        "背景": ["项目由来与建设动因", "政策依据（引用文件与要求）", "现状基础与存在问题"],
        "现状": ["现状能力与数据", "主要问题与风险", "与建设目标的差距"],
        "目标": ["总体目标与分项目标", "可量化指标", "承诺边界（不承诺什么）"],
        "范围": ["纳入范围", "不纳入范围与理由", "边界接口说明"],
        "内容": ["建设内容清单", "交付物与验收方式", "与既有系统的关系"],
        "重难点": ["关键难点识别", "影响与风险等级", "应对思路"],
        "需求": ["业务需求及特点", "性能/可靠性需求", "与其他业务的关系"],
        "痛点": ["痛点表现", "影响范围与紧迫度", "已有缓解措施"],
        "措施": ["解决措施与落地路径", "所需资源", "预期成效与验证方式"],
    }
    for keyword, points in table.items():
        if keyword in str(title or ""):
            return points
    return []


_PUBLIC_HINTS = {
    "政策": ["中国政府网（政策文件）", "国家发改委 / 工信部公开文件"],
    "标准": ["国家标准全文公开系统", "行业标准发布平台"],
    "规范": ["国家标准全文公开系统"],
    "数据": ["国家统计局公开数据", "行业主管部门年度报告"],
    "行业": ["行业主管部门年度报告", "权威研究机构公开报告"],
    "现状": ["政府公开统计公报", "行业年度报告"],
}
_UPLOAD_WORDS = ("业绩", "案例", "资质", "证书", "合同", "验收", "客户", "内部", "取证", "截图", "证明")


def _material_gaps(materials: list[dict], requirements: list[dict]) -> list[dict]:
    """素材缺口三分法：已有可用 / 公开资料可补 / 必须你上传。

    - have：本次已齐（不用动作）
    - public：政策 / 标准 / 数据 / 行业现状 → 公开渠道能补，给建议来源（可一键带入检索）
    - upload：业绩 / 资质 / 合同 / 客户 / 内部数据 → 公开渠道没有，必须用户上传
    """
    gaps: list[dict] = []
    for item in materials:
        kind = str(item.get("kind") or "")
        need = int(item.get("need") or 0)
        have = int(item.get("have") or 0)
        lack = max(0, need - have)
        if not lack:
            continue
        if any(word in kind for word in _UPLOAD_WORDS):
            availability, suggestions = "upload", []
            reason = "评分点要求举证，公开渠道没有这类材料"
        elif any(word in kind for word in _PUBLIC_HINTS):
            availability = "public"
            suggestions = next(v for k, v in _PUBLIC_HINTS.items() if k in kind)
            reason = "属于公开信息，可用官方来源检索补充"
        else:
            availability, suggestions = "upload", []
            reason = "无法判断公开可得性，建议上传或补充说明"
        gaps.append({
            "kind": kind,
            "need": need,
            "have": have,
            "lack": lack,
            "purpose": item.get("purpose") or "",
            "availability": availability,
            "suggestions": suggestions,
            "reason": reason,
        })
    gaps.sort(key=lambda g: {"have": 0, "public": 1, "upload": 2}.get(g["availability"], 3))
    return gaps


def _build_todo(deliverables: list[dict], materials: list[dict], word_plan: dict,
                guidance: dict, content: str, is_parent: bool, is_catalog: bool) -> list[dict]:
    """统一待办：编写人员打开弹窗第一眼要看的东西（按"卡生成 > 内容 > 来源 > 字数 > 图表"排序）。"""
    if is_catalog:
        return []
    body = str(content or "")
    todos: list[dict] = []

    if is_parent:
        written = word_plan.get("written") or 0
        target = word_plan.get("total") or 0
        if target and written < target * 0.6:
            todos.append({"priority": 1, "kind": "summary", "module": "content", "target": "总述",
                          "text": f"总述未写完（已写 {written} 字 / 目标 {target} 字）",
                          "action": "regen", "action_label": "去写总述"})
        elif written > (target or 0) * 1.5:
            todos.append({"priority": 4, "kind": "budget", "module": "budget", "target": "总述",
                          "text": f"总述超建议（{written} 字 / 建议 {target} 字）",
                          "action": "condense", "action_label": "去精简"})
        return todos

    # 1) 素材缺口（缺料就写不出）
    for item in materials:
        if item.get("status") == "已齐":
            continue
        lack = max(0, int(item.get("need") or 0) - int(item.get("have") or 0))
        if lack:
            todos.append({"priority": 1, "kind": "material", "module": "material", "target": item.get("kind"),
                          "text": f"补 {lack} 份「{item.get('kind')}」（{item.get('purpose')}）",
                          "action": "find", "action_label": "去补素材"})
    # 2) 内容未达标（某项交付物没写/不足）
    for item in deliverables:
        if str(item.get("title") or "").endswith("总述") or item.get("source") == "system":
            continue
        status = item.get("status")
        if status in ("未写", "不足"):
            detail = (f"已写 {item.get('written_words') or 0} / 目标 {item.get('target_words')} 字"
                      if item.get("kind") == "text" else "尚未产出")
            todos.append({"priority": 2, "kind": "deliverable", "module": "content", "target": item.get("title"),
                          "text": f"{item.get('title')}：{status}（{detail}）",
                          "action": "regen", "action_label": "去补写"})
    # 3) 来源标注
    if re.search(r"(政策|标准|指标|数据)", body) and "【来源" not in body:
        todos.append({"priority": 3, "kind": "source_mark", "module": "source", "target": "来源标注",
                      "text": "政策 / 标准 / 数据处补【来源】标注",
                      "action": "find", "action_label": "去补来源"})
    # 4) 字数
    total, written = word_plan.get("total") or 0, word_plan.get("written") or 0
    if total and written > total * 1.3:
        todos.append({"priority": 4, "kind": "budget", "module": "budget", "target": "字数",
                      "text": f"字数超计划 {round((written / total - 1) * 100)}%（{written} / {total} 字）",
                      "action": "condense", "action_label": "去精简"})
    elif total and written and written < total * 0.4:
        todos.append({"priority": 4, "kind": "budget", "module": "budget", "target": "字数",
                      "text": f"字数不足（{written} / {total} 字）", "action": "regen", "action_label": "去补写"})
    # 5) 图表缺失
    for item in deliverables:
        if item.get("kind") in ("table", "figure") and item.get("status") == "未写":
            todos.append({"priority": 5, "kind": "figure", "module": "figure", "target": item.get("title"),
                          "text": f"缺{item.get('kind_label')}：{item.get('title')}",
                          "action": "regen", "action_label": "去出图"})
    todos.sort(key=lambda item: item["priority"])
    # 每类最多 2 条，保证"素材 / 内容 / 来源 / 字数 / 图表"都能覆盖到，而不是被同类占满
    picked, per_kind = [], {}
    for item in todos:
        kind = item["kind"]
        if per_kind.get(kind, 0) >= 2:
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1
        picked.append(item)
        if len(picked) >= 5:
            break
    return picked


def _self_check(deliverables: list[dict], word_plan: dict, clauses: list[dict],
                content: str, figures_plan: dict) -> list[dict]:
    """写完自检（可直接勾）。"""
    body = str(content or "")
    items = [{"text": f"交付物齐全（{len(deliverables)} 项）",
              "ok": all((item.get("written_words") or 0) > 0 or item["kind"] != "text" for item in deliverables)}]
    items.append({"text": f"字数在计划区间（计划 {word_plan['total']} 字，已写 {word_plan['written']} 字）",
                  "ok": bool(word_plan["total"]) and 0.6 <= (word_plan["written"] / word_plan["total"]) <= 1.5})
    if clauses:
        items.append({"text": f"承接的 {len(clauses)} 条招标条款都有落点",
                      "ok": all(str(clause.get("title") or "")[:6] in body or str(clause.get("topic") or "")[:6] in body
                                for clause in clauses)})
    items.append({"text": "引用政策/标准/数据处带【来源】",
                  "ok": "【来源" in body or not re.search(r"(政策|标准|指标|数据)", body)})
    tables = len((figures_plan or {}).get("tables") or [])
    figures = len((figures_plan or {}).get("figures") or [])
    if tables or figures:
        items.append({"text": f"表 {tables} 张 / 图 {figures} 张已产出",
                      "ok": body.count("| ---") >= tables and (":::diagram" in body or "![" in body or not figures)})
    return items


def _materials_plan(requirements: list[dict], bound_kinds: dict[str, int],
                    clause_count: int) -> list[dict]:
    """素材清单：需要什么料、已有多少、缺多少。"""
    plan = []
    if any(item.get("type") == "text" for item in requirements):
        plan.append({"kind": "政策/制度原文", "purpose": "支撑政策依据类表述", "need": 2,
                     "have": bound_kinds.get("官方来源", 0), "where": "官方来源"})
    if clause_count:
        plan.append({"kind": "招标条款原文", "purpose": "逐条响应对内要求", "need": clause_count,
                     "have": clause_count, "where": "已解析（技术规范书）"})
    plan.append({"kind": "我方材料（数据/案例/业绩）", "purpose": "提供现状数据与佐证", "need": 1,
                 "have": bound_kinds.get("我方材料", 0), "where": "素材池上传/关联"})
    for item in plan:
        item["status"] = "已齐" if item["have"] >= item["need"] else f"缺 {item['need'] - item['have']}"
    return plan


async def chapter_plan(db, project_id: int, thread_id: str, block_id: str) -> dict:
    """**本章编写计划**（用户口径：计划是针对每一个章节的，不是按大章节设计）。

    只返回这一章自己该知道的东西：
      ① 要求依据：每条要求 + 依据（评分点/规范条款）+ 是否已回应
      ② 缺失素材：每条要求对应的素材需求（已补片段 / 待补）+ 正文素材缺口
    """
    from services.block_store import list_blocks
    from services.quality_check import basis_coverage
    from services.chapter_cleanup import load_generation_gaps

    generation_gaps = await load_generation_gaps(db, str(thread_id), str(block_id))
    found = await plan_of_block(db, project_id, thread_id, str(block_id))
    chapter = None
    plan_id = ""
    plan: dict = {}
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
    stored_figures = (chapter or {}).get("figures_plan") or {}
    figures_plan = {
        "tables": stored_figures.get("tables") or [
            {"name": str(item.get("text") or "")[:40], "purpose": str(item.get("text") or ""),
             "source": "外部来源片段 / 我方材料"} for item in table_items],
        "figures": stored_figures.get("figures") or [
            {"name": str(item.get("text") or "")[:40], "purpose": str(item.get("text") or ""),
             "source": "外部来源图片 / 生成示意图"} for item in figure_items],
    }
    gaps = [item for item in (chapter or {}).get("gaps") or []]
    pending = sum(1 for requirement in requirements if requirement["material"] != "已满足")
    plan_row = await (await db.execute(
        "SELECT version, updated_at, status FROM writing_plans WHERE id=?", (plan_id,),
    )).fetchone() if plan_id else None
    plan_version = int(plan_row["version"] or 1) if plan_row else 0
    plan_updated = (plan_row["updated_at"] or "") if plan_row else ""
    block_updated = str(block.get("updated_at") or "")
    # ── 可执行计划（D1~D4）──
    deliverables, written_total, segments = _deliverables(requirements, content)
    is_parent_chapter = any(key.startswith(str(block_id) + ".") for key in blocks)
    # 纯目录节点（有子节、自己没正文）→ 不单独写正文，只做子节汇总
    is_catalog = is_parent_chapter and not str(content or "").strip()
    if is_catalog:
        deliverables = []
        word_plan = {"total": 0, "written": 0, "percent": 0, "over": False,
                     "advice": "本章是目录节点：不单独写正文，正文在下列各节里写"}
    elif is_parent_chapter:
        # 父章只写总述（300–500 字），子节在各自章节里写，不在这里叠加
        own_words = len(str(content or "").replace(" ", ""))
        for item in deliverables:
            if item.get("source") == "system":
                item["written_words"] = own_words
        for item in deliverables:
            # "总述"那条给 400 字，其余（评分点继承来的要素）在子节里写
            item["target_words"] = 400 if item.get("source") == "system" else 0
            if not item["target_words"] and any(other.get("source") == "system" for other in deliverables):
                item["target_words"] = 0
        own_words = len(str(content or "").replace(" ", ""))
        advice = ""
        if own_words > 600:
            advice = f"父章已写 {round(own_words / 250, 1)} 页（{own_words} 字），远超总述建议（300–500 字）：建议精简为总述，或把细节下沉到子节"
        word_plan = {"total": 400, "written": own_words,
                     "percent": round(own_words / 400 * 100) if own_words else 0,
                     "over": own_words > 600, "advice": advice,
                     "note": "父章只写总述 300–500 字；子节在各自章节里写"}
        for item in deliverables:
            if item["kind"] != "text":
                item["target_words"] = 0
            target = item.get("target_words") or 0
            written = item.get("written_words") or 0
            item["status"] = ("未写" if not written else
                              "不足" if target and written < target * 0.6 else
                              "超额" if target and written > target * 1.5 else "达标")
            item["gap_words"] = written - target
    else:
        word_plan = _word_plan(pages_of_block or (chapter or {}).get("pages") or 0, deliverables, written_total)
    key_points = _key_points_from_title(block.get("title") or "")
    bound_kinds = {"官方来源": 0, "我方材料": 0}
    try:
        cite_rows = await (await db.execute(
            "SELECT COUNT(*) AS n FROM citations WHERE project_id=? AND status='accepted' AND ("
            "  block_id=? OR EXISTS (SELECT 1 FROM citation_links l WHERE l.citation_id = citations.id AND l.block_id = ?))",
            (project_id, str(block_id), str(block_id)),
        )).fetchone()
        bound_kinds["官方来源"] = int(cite_rows["n"] if cite_rows else 0)
        mat_rows = await (await db.execute(
            "SELECT COUNT(*) AS n FROM chapter_materials cm JOIN blocks b ON b.id=cm.block_id "
            "WHERE b.project_id=? AND b.run_thread_id=? AND b.block_id=?", (project_id, thread_id, str(block_id)),
        )).fetchone()
        bound_kinds["我方材料"] = int(mat_rows["n"] if mat_rows else 0)
    except Exception:
        pass
    grade_band = ""
    if basis.get("points"):
        stmt = "SELECT criterion FROM review_points WHERE run_thread_id=? AND point_id=?"
        row = await (await db.execute(stmt, (thread_id, basis["points"][0]["point_id"]))).fetchone()
        if row:
            first = re.search(r"[（(]1[）)](.*?)(?=[（(]2[）)]|$)", str(row["criterion"] or ""), re.S)
            grade_band = (first.group(1) if first else str(row["criterion"] or "")).strip()[:600]
    clauses = [{"requirement_id": item["requirement_id"], "title": item["label"],
                "mandatory": item.get("mandatory_level") in ("mandatory", "required")}
               for item in (basis.get("clauses") or [])]
    has_children = any(key.startswith(str(block_id) + ".") for key in blocks)
    children = []
    if has_children:
        for child_key, child in blocks.items():
            if not child_key.startswith(str(block_id) + ".") or child_key.count(".") != str(block_id).count(".") + 1:
                continue
            child_content = str(child.get("content") or "").replace(" ", "")
            plan_pages = {str(item.get("block_id")): item.get("pages")
                          for item in ((plan or {}).get("chapters") or [])}
            children.append({
                "block_id": child_key, "title": child.get("title"),
                "pages": int(plan_pages.get(child_key) or child.get("page_budget") or 0),
                "written_pages": round(len(child_content) / 250, 1),
                "has_content": bool(child_content),
            })
    material_gaps = _material_gaps(_materials_plan(requirements, bound_kinds, len(clauses)), requirements)
    try:
        completion = chapter_completion({"requirements": requirements, "materials": material_gaps,
                                        "figures_plan": figures_plan, "deliverables": deliverables}, [])
    except Exception:
        completion = None
    return {
        "grade_band": grade_band,
        "clauses": clauses,
        "deliverables": deliverables,
        "word_plan": word_plan,
        "key_points": key_points,
        "key_points_by_deliverable": _split_key_points(key_points, deliverables),
        "materials": _materials_plan(requirements, bound_kinds, len(clauses)),
        "self_check": _self_check(deliverables, word_plan, clauses, content, figures_plan),
        "material_gaps": material_gaps,
        "generation_gaps": generation_gaps or [],
        "outline": (chapter or {}).get("outline") or [],
        "practiceOverrides": await _practice_overrides(db, project_id, block_id),
        "todo": _build_todo(deliverables, _materials_plan(requirements, bound_kinds, len(clauses)),
                            word_plan, guidance, content, is_parent_chapter, is_catalog),
        "todo_items": build_todo_items(deliverables, material_gaps or [], generation_gaps or [],
                                       word_plan, content, is_parent_chapter, is_catalog, completion),
        "todo_groups": TODO_GROUPS,
        "delivery_tasks": _delivery_tasks_payload(body=content, block_key=str(block_id),
                                                 deliverables=deliverables, material_gaps=material_gaps,
                                                 generation_gaps=generation_gaps, word_plan=word_plan,
                                                 completion=completion, is_catalog=is_catalog),
        "children": children,
        "catalog": is_catalog,
        "has_children": has_children,
        "plan_id": plan_id, "block_id": str(block_id), "title": block.get("title") or (chapter or {}).get("title"),
        "has_children": has_children,
        "confirmed": bool((chapter or {}).get("confirmed")) or ((plan_row["status"] == "confirmed") if plan_row else False),
        "plan_version": plan_version,
        "plan_changed_after_content": bool(plan_updated and block_updated and plan_updated > block_updated),
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
    # 计划现在由系统统筹生成即生效（不再有"未确认"态），所以这里不再拦"已确认"；
    # 重复启动由 job 的去重逻辑保证（同一章已在进行中会复用原 job）。
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


__all__ = ["plan_units", "build_plan", "load_plan", "confirm_plan", "_writable_nodes", "_bind_clauses_to_chapters",
           "update_requirements", "requirement_coverage", "chapter_needs", "bind_snippets",
           "needs_for_prompt", "plan_of_block", "chapter_plan", "update_figures_plan",
           "confirm_chapter_plan", "chapter_completion"]


# ---------------------------------------------------------------------------
# 统一待办（v2）：按"谁来消除"分组
#   A user   你要给的（上传/填写）
#   B search 系统去挖的（一键检索）
#   C decide 要你拍板的（选项）
#   D proof  要你核对的（定位）
#   E auto   系统自己修的（折叠 + 一键修复）
#   F gate   交付门槛（阻塞导出）
# ---------------------------------------------------------------------------

TODO_GROUPS = {
    "user": "你要给的",
    "search": "系统去挖的",
    "decide": "要你拍板的",
    "proof": "要你核对的",
    "auto": "系统可自动修复",
    "gate": "交付门槛",
}

_GROUP_ORDER = {"gate": 0, "user": 1, "decide": 2, "search": 3, "proof": 4, "auto": 5}


def _todo_item(group: str, title: str, evidence: str = "", source: str = "",
               action: str = "", action_label: str = "", severity: str = "suggest",
               target: str = "") -> dict:
    return {
        "id": f"{group}:{(target or title)[:40]}",
        "group": group,
        "group_label": TODO_GROUPS.get(group, group),
        "title": title,
        "evidence": evidence,
        "source": source,
        "severity": severity,          # block=阻塞交付 / suggest=建议
        "action": action,
        "action_label": action_label or {"upload": "上传", "search": "一键检索",
                                         "decide": "去选择", "locate": "定位",
                                         "autofix": "一键修复", "go": "去处理"}.get(action, "去处理"),
        "status": "open",
    }


def _delivery_tasks_payload(*, body, block_key, deliverables, material_gaps, generation_gaps,
                            word_plan, completion, is_catalog) -> dict:
    """v3 可执行交付清单（三区）。复用已有检查结果，只换表达与责任归属。"""
    if is_catalog:
        return {"need_user": [], "system": [], "suggestion": [],
                "summary": {"need_user": 0, "system": 0, "suggestion": 0, "impact_export_block": 0,
                            "completion_reasons": []}}
    try:
        from services.todo_checks import citation_items, decision_items, number_items, proofread_items
        from services.todo_tasks import build_delivery_tasks
        from services.section_contract import check_chapter as contract_check
        from services.todo_checks import citation_format_items as cite_fmt, weak_phrasing_items as weak_items
        decisions = decision_items(body) + [i for i in weak_items(body) if i.get("group") == "decide"]
        proofs = (proofread_items(body) + number_items(body) + citation_items(body) + contract_check(body)
                  + [i for i in weak_items(body) if i.get("group") != "decide"] + cite_fmt(body))
        return build_delivery_tasks(block_id=block_key, content=body, deliverables=deliverables,
                                    material_gaps=material_gaps, generation_gaps=generation_gaps,
                                    word_plan=word_plan, decisions=decisions, proofs=proofs,
                                    completion=completion)
    except Exception:
        return {"need_user": [], "system": [], "suggestion": [],
                "summary": {"need_user": 0, "system": 0, "suggestion": 0, "impact_export_block": 0,
                            "completion_reasons": []}}


def build_todo_items(deliverables: list[dict], material_gaps: list[dict], generation_gaps: list[dict],
                     word_plan: dict, content: str, is_parent: bool, is_catalog: bool,
                     completion: dict | None = None) -> list[dict]:
    """把已有的检查结果重排成"按责任分组"的统一待办。"""
    if is_catalog:
        return []
    items: list[dict] = []
    body = str(content or "").strip()

    # A 你要给的 / B 系统去挖的 —— 素材缺口三分法直接映射
    for gap in material_gaps or []:
        kind = str(gap.get("kind") or "材料")
        lack = int(gap.get("lack") or 0)
        if not lack:
            continue
        if gap.get("availability") == "upload":
            items.append(_todo_item(
                "user", f"补 {lack} 份「{kind}」", gap.get("purpose") or gap.get("reason") or "",
                source="素材", action="upload", severity="block", target=kind))
        else:
            hint = "、".join(gap.get("suggestions") or []) or "官方来源"
            items.append(_todo_item(
                "search", f"「{kind}」缺 {lack} 份", f"可查：{hint}",
                source="素材", action="search", target=kind))

    # B 系统去挖的 —— 依据核对缺口
    for gap in generation_gaps or []:
        text = str(gap.get("text") or "").strip()
        if not text:
            continue
        items.append(_todo_item("search", text, "生成时发现该依据缺失，公开渠道可能可补",
                                source="依据核对", action="search", target=text[:30]))

    # E 系统自己修的 —— 交付物没写够 / 图表缺
    for item in deliverables or []:
        title = str(item.get("title") or "")
        if not title or item.get("source") == "system":
            continue
        status = item.get("status")
        if status not in ("未写", "不足"):
            continue
        if item.get("kind") == "text":
            items.append(_todo_item("auto", f"「{title}」字数不足",
                                    f"已写 {item.get('written_words') or 0} / 目标 {item.get('target_words')} 字",
                                    source="计划", action="autofix", target=title))
        else:
            label = "表格" if item.get("kind") == "table" else "图示"
            items.append(_todo_item("auto", f"缺{label}「{title}」", "计划要求但正文没有",
                                    source="计划", action="autofix", target=title))

    # E 系统自己修的 —— 篇幅超建议
    written = int(word_plan.get("written") or 0)
    target = int(word_plan.get("total") or 0)
    if target and written > target * 1.5:
        items.append(_todo_item("auto", f"篇幅超建议（{written} / {target} 字）", "可一键精简",
                                source="计划", action="autofix", target="budget"))

    # F 交付门槛
    if not body:
        items.append(_todo_item("gate", "正文为空，不能确认完成", "先编写或生成本章",
                                source="导出检查", action="go", severity="block", target="empty"))
    if completion is not None:
        if not completion.get("ok", True):
            reasons = completion.get("reasons") or []
            if reasons:
                items.append(_todo_item("gate", f"还有 {len(reasons)} 项未满足完成条件",
                                        "；".join(str(r) for r in reasons[:3]),
                                        source="导出检查", action="go", severity="block", target="completion"))

    # C 组（要你拍板）/ D 组（要你核对）—— 确定性规则，只报能指到原文的
    try:
        from services.section_contract import check_chapter as contract_check
        from services.todo_checks import (citation_format_items, citation_items, decision_items,
                                         number_items, proofread_items, weak_phrasing_items)
        for raw in (decision_items(body) + proofread_items(body) + number_items(body)
                    + citation_items(body) + contract_check(body)
                    + weak_phrasing_items(body) + citation_format_items(body)):
            entry = _todo_item(raw["group"], raw["title"], raw.get("evidence", ""),
                               source=raw.get("source", ""), action=raw.get("action", "decide"),
                               severity=raw.get("severity", "suggest"), target=raw.get("target", ""))
            if raw.get("fix"):
                entry["fix"] = raw["fix"]
            if raw.get("suggestions"):
                entry["suggestions"] = raw["suggestions"]
                entry["sentence"] = raw.get("sentence", "")
            items.append(entry)
    except Exception:
        pass

    items.sort(key=lambda x: (_GROUP_ORDER.get(x["group"], 9), 0 if x["severity"] == "block" else 1))
    return items


async def _practice_overrides(db, project_id: int, block_id: str) -> dict[str, str]:
    """每节的"写法"覆盖（= 该节提示词）。"""
    try:
        rows = await (await db.execute(
            "SELECT section, practice FROM section_practices WHERE project_id=? AND block_id=?",
            (project_id, block_id or ""))).fetchall()
        return {row[0]: row[1] for row in rows if row[1]}
    except Exception:
        return {}
