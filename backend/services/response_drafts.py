"""按「标包 × 应答类型」解析目录草案 —— 应答策划里类型切换的依据。

为什么需要它（用户 PM 反馈）：
  在应答策划里点「商务」/「技术」，应当**切换并显示该类型的应答目录**；
  但页面一直显示「生成目录草案」，因为前端只读**当前线程**的 response_plan，
  而「(标包, 类型) → 草案线程」这层解析根本不存在：
  草案其实已经在别的线程里，页面拿不到这个事实，只能一直劝用户"生成"。

本模块只做数据解析（不依赖 runner）：
  · 草案 = 该标包下某条 run，其 split_answers.response_plan 记录了应答类型
  · 目录节点数用该 run 自己的 blocks 计数（run 之间的 blocks 相互独立）
  · 是否"已派生（不是原文占位）"由路由用 runner 的 spec.outline_revision 补
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

PLAN_TYPES = ("commerce", "tech")


async def list_response_drafts(db, project_id: int, package_id: int) -> dict:
    """列出该标包下各应答类型的草案（含解析运行线程）。"""
    runs = [dict(r) for r in await (await db.execute(
        "SELECT thread_id, package_no, target, stage, created_at, updated_at "
        "FROM workflow_runs WHERE project_id = ? AND package_id = ? ORDER BY created_at",
        (project_id, package_id))).fetchall()]
    thread_ids = [r["thread_id"] for r in runs]

    plans: dict[str, dict] = {}
    counts: dict[str, int] = {}
    if thread_ids:
        placeholders = ",".join("?" * len(thread_ids))
        rows = await (await db.execute(
            f"SELECT thread_id, value FROM split_answers "
            f"WHERE project_id = ? AND key = 'response_plan' AND thread_id IN ({placeholders})",
            (project_id, *thread_ids))).fetchall()
        for row in rows:
            try:
                plan = json.loads(row["value"] or "{}")
            except Exception:
                continue
            if isinstance(plan, dict):
                plans[row["thread_id"]] = plan
        for row in await (await db.execute(
            f"SELECT run_thread_id, COUNT(*) n FROM blocks "
            f"WHERE project_id = ? AND run_thread_id IN ({placeholders}) GROUP BY run_thread_id",
            (project_id, *thread_ids))).fetchall():
            counts[row["run_thread_id"]] = int(row["n"] or 0)

    drafts: list[dict] = []
    source_thread_id = ""
    for run in runs:
        tid = run["thread_id"]
        plan = plans.get(tid) or {}
        ptype = str(plan.get("type") or "")
        if ptype in PLAN_TYPES:
            drafts.append({
                "type": ptype,
                "thread_id": tid,
                "confirmed": bool(plan.get("confirmed")),
                "source_thread_id": str(plan.get("source_thread_id") or ""),
                "stage": run["stage"],
                "outline_count": counts.get(tid, 0),
                "created_at": run["created_at"],
                "updated_at": run["updated_at"],
            })
        elif not source_thread_id:
            source_thread_id = tid          # 没有草案的那条 = 解析运行

    if not source_thread_id:
        source_thread_id = next((d["source_thread_id"] for d in drafts if d["source_thread_id"]), "")

    order = {t: i for i, t in enumerate(PLAN_TYPES)}
    drafts.sort(key=lambda d: order.get(d["type"], 99))
    return {
        "project_id": project_id,
        "package_id": package_id,
        "package_no": runs[0]["package_no"] if runs else "",
        "target": runs[0]["target"] if runs else "",
        "source_thread_id": source_thread_id,
        "drafts": drafts,
    }
