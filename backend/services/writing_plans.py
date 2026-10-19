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
    return {
        "root": {"block_id": root["block_id"], "title": root["title"]},
        "units": [{
            "block_id": b["block_id"], "title": b["title"], "level": b.get("level"),
            "leaves": len(_leaves(blocks, b)),
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
        tasks = _tasks_for(leaf, layout)
        need = str(leaf.get("evidence_required") or "").strip()
        have = materials.get(int(leaf["id"]), 0) if leaf.get("id") else 0
        chapter_gaps = [need] if (need and not have) else []
        if chapter_gaps:
            gaps.append(f"{leaf.get('title')}：{need[:60]}")
        plan = Plan(objective=f"{leaf.get('title')}：按《{LAYOUTS[layout]['label']}》结构编制",
                    tasks=tasks, gaps=chapter_gaps).model_dump()
        chapters.append({
            "block_id": leaf["block_id"], "title": leaf.get("title"), "level": leaf.get("level"),
            "layout": layout, "layout_label": LAYOUTS[layout]["label"],
            "pages": int(leaf.get("page_budget") or 0),
            "objective": plan["objective"], "tasks": plan["tasks"], "gaps": chapter_gaps,
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
    await db.execute(
        "INSERT INTO writing_plans (id, project_id, thread_id, root_block_id, status, plan) "
        "VALUES (?, ?, ?, ?, 'draft', ?)",
        (plan_row["id"], project_id, thread_id, str(root["block_id"]),
         json.dumps(plan_row, ensure_ascii=False)),
    )
    await db.commit()
    return plan_row


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
        job_id = await create_job(db, block, package, settings, auto_save=True)   # 返回 job id
        # 预置主编计划：run_job 看到 artifacts.chief 就不再调主编重新规划
        artifacts = {"chief": {"plan": {"objective": chapter["objective"],
                                        "tasks": chapter["tasks"], "gaps": chapter["gaps"]},
                               "model": "confirmed-plan"}}
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


__all__ = ["plan_units", "build_plan", "load_plan", "confirm_plan"]
