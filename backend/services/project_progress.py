"""项目级进度与待办聚合 —— 状态行「进度中心」的数据来源。

为什么单独一个服务：状态行原来有「处理待办 / 版本记录 / 运行详情」三个并列入口，
各自只看一件事；编写进度又散在编写页顶部、右栏、阶段头三处。用户要的是一个
"现在到哪了、还差什么、要我做什么"的答案 → 这里把它们聚合成一个口径。

口径（与编写页保持一致，避免两处各算一套）：
- 编写单元：``chapter_studio.writing_units``（父目录有子节点且没有正文时不是单元）
- 已写：单元正文非空；完成：``chapter_studio.writing_status='complete'``
  （没有 studio 记录的老数据回落到 ``blocks.status='done'``）
- 篇幅：直接复用 ``services.page_budget.get_page_budget`` 的 total / allocated /
  confirmed，和编写页读到的是同一份
- 已写页数：按 250 字/页折算（与编写页 WPP 一致）
"""

from __future__ import annotations

import json

from services.block_store import list_blocks
from services.chapter_studio import writing_units
from services.page_budget import get_page_budget
from services.response_drafts import list_response_drafts

TYPE_LABEL = {"commerce": "商务", "tech": "技术"}
WPP = 250  # 1 页 ≈ 250 字（与编写页一致）


def _words(blocks) -> int:
    total = 0
    for block in blocks:
        total += len(str(block.get("content") or "").replace(" ", "").replace("\n", ""))
    return total


async def _studio_status(db, project_id: int, thread_id: str) -> dict:
    rows = await (await db.execute(
        "SELECT block_id, writing_status FROM chapter_studio WHERE block_id IN "
        "(SELECT id FROM blocks WHERE project_id=? AND run_thread_id=?)",
        (project_id, thread_id),
    )).fetchall()
    return {str(r["block_id"]): (r["writing_status"] or "") for r in rows}


async def _type_progress(db, project_id: int, draft: dict) -> dict:
    thread_id = draft.get("thread_id") or ""
    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    units = writing_units(blocks)
    statuses = await _studio_status(db, project_id, thread_id)
    written = complete = 0
    for unit in units:
        key = str(unit.get("block_id") or "")
        has_text = bool(str(unit.get("content") or "").strip())
        state = statuses.get(key) or ""
        if has_text:
            written += 1
        if has_text and (state == "complete" or (not state and unit.get("status") == "done")):
            complete += 1
    total = len(units)
    words = _words(units)
    budget = await get_page_budget(db, project_id, thread_id) if thread_id else {}
    return {
        "type": draft.get("type"),
        "label": TYPE_LABEL.get(draft.get("type"), draft.get("type") or ""),
        "thread_id": thread_id,
        "directory_confirmed": bool(draft.get("confirmed")),
        # list_response_drafts 不含 has_outline（由路由补），这里用节点数兜底判定
        "has_outline": bool(draft.get("has_outline") or draft.get("outline_count") or total),
        "outline_count": int(draft.get("outline_count") or 0),
        "units": {"total": total, "written": written, "complete": complete,
                  "percent": round(complete / total * 100) if total else 0},
        "words": words,
        "written_pages": round(words / WPP, 1),
        "budget": {
            "total": int(budget.get("total") or 0),
            "allocated": int(budget.get("allocated") or 0),
            "confirmed": bool(budget.get("confirmed")),
        },
    }


def _stages(types: list[dict], runs: list[dict], findings: int) -> list[dict]:
    """四阶段状态：done / active / pending。口径与工作台一致（解析 → 策划 → 编写 → 评审）。"""
    drafts = [t for t in types if t.get("has_outline")]
    dir_done = bool(drafts) and all(t["directory_confirmed"] for t in drafts)
    budget_done = bool(drafts) and all(t["budget"]["confirmed"] for t in drafts)
    units_total = sum(t["units"]["total"] for t in types)
    units_done = sum(t["units"]["complete"] for t in types)
    writing_pct = round(units_done / units_total * 100) if units_total else 0
    ready = dir_done and budget_done          # 目录与篇幅都确认 → 可以开始编写
    writing_done = bool(units_total) and writing_pct >= 100
    # 编写：写过就算进行中（不能因为另一类型还没确认目录就说"未开始"）；
    # 评审与交付：没出评审意见就还没完成，写完才进入可评审。
    return [
        {"id": "1", "label": "解析依据",
         "state": "done" if runs else "pending"},
        {"id": "2", "label": "应答策划",
         "state": "done" if ready else ("active" if runs else "pending")},
        {"id": "3", "label": "应答编写",
         "state": "done" if writing_done else ("active" if (units_done > 0 or ready) else "pending"),
         "percent": writing_pct},
        {"id": "4", "label": "评审与交付",
         "state": "done" if findings else ("active" if writing_done else "pending")},
    ]


async def project_progress(db, project_id: int, package_id: int | None = None) -> dict:
    drafts = await list_response_drafts(db, project_id, package_id) if package_id else {"drafts": [], "source_thread_id": ""}
    types = [await _type_progress(db, project_id, d) for d in (drafts.get("drafts") or [])]
    where, args = "project_id = ?", [project_id]
    if package_id:
        where += " AND package_id = ?"
        args.append(package_id)
    runs = [dict(r) for r in await (await db.execute(
        f"SELECT thread_id, stage, interrupted_at, created_at FROM workflow_runs "
        f"WHERE {where} ORDER BY created_at DESC LIMIT 50", tuple(args),
    )).fetchall()]
    # 解析记录按设计就停在中断态（mark_interrupted_runs），不能算成待办噪音：
    # 只统计"有目录草案的线程"的中断。
    draft_threads = {t["thread_id"] for t in types}
    interrupted = [r for r in runs if r.get("interrupted_at") and r["thread_id"] in draft_threads]
    # 只把"当前版本仍被评审"的章算作已评审；正文/依据改过但未复评的章不能
    # 让阶段 4 显示 done（与风险页/交付页的 freshness 口径一致）。
    chapter_rows = await (await db.execute(
        """SELECT cr.content_version, cr.evidence_version,
                  b.id AS block_pk, b.block_id, b.content, b.project_id, b.run_thread_id,
                  b.requirement, b.review_point_ids, b.spec_requirement_ids, b.evidence_required
           FROM chapter_reviews cr JOIN blocks b ON b.id=cr.block_id
           WHERE b.project_id=? AND b.run_thread_id IN (
               SELECT thread_id FROM workflow_runs WHERE project_id=? AND package_id=?)""",
        (project_id, project_id, package_id or -1),
    )).fetchall()
    if chapter_rows:
        from services.writing_workspace import review_freshness
        findings = 0
        freshness_cache: dict = {}
        for row in chapter_rows:
            block = {
                "id": row["block_pk"], "block_id": row["block_id"],
                "content": row["content"], "project_id": row["project_id"],
                "run_thread_id": row["run_thread_id"],
                "requirement": row["requirement"],
                "review_point_ids": row["review_point_ids"],
                "spec_requirement_ids": row["spec_requirement_ids"],
                "evidence_required": row["evidence_required"],
            }
            if await review_freshness(db, block, dict(row), cache=freshness_cache) == "current":
                findings += 1
    else:
        # 迁移前/老项目回退
        legacy = await (await db.execute(
            "SELECT COUNT(*) AS n FROM reviews WHERE thread_id IN ("
            "SELECT thread_id FROM workflow_runs WHERE project_id=? AND package_id=?)",
            (project_id, package_id or -1),
        )).fetchone()
        findings = int(legacy["n"] if hasattr(legacy, "keys") else legacy[0])
    stages = _stages(types, runs, findings)
    current = next((s for s in stages if s["state"] == "active"), None) or next(
        (s for s in stages if s["state"] != "done"), None) or stages[-1]
    return {
        "project_id": project_id,
        "package_id": package_id,
        "source_thread_id": drafts.get("source_thread_id") or "",
        "types": types,
        "stages": stages,
        "current": {"id": current["id"], "label": current["label"], "state": current["state"]},
        "units": {
            "total": sum(t["units"]["total"] for t in types),
            "written": sum(t["units"]["written"] for t in types),
            "complete": sum(t["units"]["complete"] for t in types),
            "percent": (round(sum(t["units"]["complete"] for t in types)
                              / sum(t["units"]["total"] for t in types) * 100)
                        if types and sum(t["units"]["total"] for t in types) else 0),
        },
        "runs": {"total": len(runs), "interrupted": len(interrupted), "latest": runs[0] if runs else None},
        "findings": findings,
    }


async def _latest_job_gaps(db, project_id: int) -> list[str]:
    """最近一次编制 job 的计划里带的素材缺口（主编自己给的，不是我们猜的）。"""
    row = await (await db.execute(
        "SELECT artifacts FROM chapter_writing_jobs WHERE project_id=? "
        "ORDER BY id DESC LIMIT 1", (project_id,),
    )).fetchone()
    if not row:
        return []
    try:
        artifacts = json.loads((row["artifacts"] if hasattr(row, "keys") else row[0]) or "{}")
    except ValueError:
        return []
    plan = (artifacts.get("chief") or {}).get("plan") or {}
    return [str(g) for g in (plan.get("gaps") or []) if str(g).strip()]


async def project_todos(db, project_id: int, package_id: int | None = None) -> dict:
    """待办：只列"需要用户动手"的事，按严重度排序。"""
    progress = await project_progress(db, project_id, package_id)
    todos: list[dict] = []
    for t in progress["types"]:
        if not t["has_outline"]:
            continue
        if not t["directory_confirmed"]:
            todos.append({
                "kind": "directory", "severity": "high", "stage": "2",
                "title": f"{t['label']}应答目录未确认",
                "detail": f"{t['outline_count']} 个节点待确认",
                "action": {"label": "去确认目录", "thread_id": t["thread_id"]},
            })
        elif not t["budget"]["confirmed"]:
            detail = "总页数 0" if not t["budget"]["total"] else f"已分配 {t['budget']['allocated']} 页，未确认"
            todos.append({
                "kind": "budget", "severity": "high", "stage": "2",
                "title": f"{t['label']}篇幅未确认",
                "detail": detail,
                "action": {"label": "去确认篇幅", "thread_id": t["thread_id"]},
            })
    gaps = await _latest_job_gaps(db, project_id)
    if gaps:
        todos.append({
            "kind": "material", "severity": "medium", "stage": "3",
            "title": f"素材缺口 {len(gaps)} 项",
            "detail": "；".join(gaps)[:240],
            "action": {"label": "去编写页处理"},
        })
    # 质检：只报 high/medium（确定性检查结果，不阻塞）
    try:
        from services.quality_check import check_thread
        for draft in (await list_response_drafts(db, project_id, package_id)).get("drafts") or [] if package_id else []:
            if not draft.get("has_outline"):
                continue
            quality = await check_thread(db, project_id, draft["thread_id"])
            if quality["summary"]["high"] or quality["summary"]["medium"]:
                todos.append({
                    "kind": "quality", "severity": "medium", "stage": "3",
                    "title": f"{TYPE_LABEL.get(draft.get('type'), '')}章节质检 {quality['summary']['high'] + quality['summary']['medium']} 项",
                    "detail": f"高优 {quality['summary']['high']} · 中优 {quality['summary']['medium']}（结构/图表/篇幅）",
                    "action": {"label": "看质检清单", "thread_id": draft["thread_id"]},
                })
    except Exception:
        pass
    if progress["findings"]:
        todos.append({
            "kind": "review", "severity": "low", "stage": "4",
            "title": f"评审意见 {progress['findings']} 条",
            "detail": "技术与合规评审已给出意见，可按需修订",
            "action": {"label": "去评审"},
        })
    if progress["runs"]["interrupted"]:
        todos.append({
            "kind": "run", "severity": "medium", "stage": "3",
            "title": f"{progress['runs']['interrupted']} 次运行中断",
            "detail": "中断的运行没有在跑，可在「记录 · 运行」里查看并在编写页重试",
            "action": {"label": "看运行记录"},
        })
    order = {"high": 0, "medium": 1, "low": 2}
    todos.sort(key=lambda x: order.get(x["severity"], 9))
    return {"project_id": project_id, "package_id": package_id, "todos": todos,
            "counts": {s: sum(1 for t in todos if t["severity"] == s) for s in order}}


__all__ = ["project_progress", "project_todos"]
