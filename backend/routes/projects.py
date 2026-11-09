"""
backend/routes/projects.py — Vault CRUD + lock

端点：
  GET  /api/projects          — 项目列表（可选 ?status= 过滤）
  POST /api/projects          — 新建项目
  GET  /api/projects/{id}     — 项目详情
  PATCH /api/projects/{id}    — 更新项目信息
  POST /api/projects/{id}/lock — 锁定大纲，生成 base_snapshot
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from db import get_db
from domain.review import WorkflowRunRepository
from services.block_store import (
    create_project,
    get_project,
    list_projects,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["projects"])


# ── GET /api/projects ─────────────────────────────────────

@router.post("/projects/{project_id}/reset-analysis")
async def reset_analysis(
    project_id: int,
    package_id: int | None = Query(default=None, alias="packageId"),
):
    """清空指定标包的拆解结果；旧调用不带 packageId 时维持项目级兼容。

    用户要求：再点一次「拆解评分点」必须**从零开始**，不要让旧数据影响新结果 ——
    此前旧的评分点/规范要求/章节/作答（含范围与类别选择、主题归属）会跨运行残留，
    重新上传材料后仍然生效，导致"新的文件 + 旧的结果"混在一起。

    保留：项目、上传的材料文件（那是输入）、项目级投标范围（用户的显式设定）。
    清空：blocks / review_points / spec_requirements / reviews / split_answers。
    """
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

        if package_id is not None:
            from services.package_scope import require_package
            await require_package(db, project_id, package_id)
            threads = [r[0] for r in await (await db.execute(
                "SELECT thread_id FROM workflow_runs WHERE project_id = ? AND package_id = ?",
                (project_id, package_id),
            )).fetchall()]
        else:
            threads = [r[0] for r in await (await db.execute(
                "SELECT thread_id FROM workflow_runs WHERE project_id = ?", (project_id,)
            )).fetchall()]
        placeholders = ",".join("?" for _ in threads)
        deleted = {}
        scoped = package_id is not None
        statements = []
        if threads:
            thread_params = tuple(threads)
            statements.extend([
                ("block_revisions", f"DELETE FROM block_revisions WHERE block_id IN "
                 f"(SELECT id FROM blocks WHERE run_thread_id IN ({placeholders}))", thread_params),
                ("reviews", f"DELETE FROM reviews WHERE thread_id IN ({placeholders})", thread_params),
                ("chapter_reviews", f"DELETE FROM chapter_reviews WHERE block_id IN "
                 f"(SELECT id FROM blocks WHERE run_thread_id IN ({placeholders}))", thread_params),
                ("review_history", f"DELETE FROM review_history WHERE block_id IN "
                 f"(SELECT id FROM blocks WHERE run_thread_id IN ({placeholders}))", thread_params),
                ("review_issue_states", f"DELETE FROM review_issue_states WHERE block_id IN "
                 f"(SELECT id FROM blocks WHERE run_thread_id IN ({placeholders}))", thread_params),
                ("split_answers", f"DELETE FROM split_answers WHERE thread_id IN ({placeholders})", thread_params),
                ("review_points", f"DELETE FROM review_points WHERE run_thread_id IN ({placeholders})", thread_params),
                ("spec_requirements", f"DELETE FROM spec_requirements WHERE run_thread_id IN ({placeholders})", thread_params),
                ("blocks", f"DELETE FROM blocks WHERE run_thread_id IN ({placeholders})", thread_params),
                ("checkpoints", f"DELETE FROM checkpoints WHERE thread_id IN ({placeholders})", thread_params),
                ("writes", f"DELETE FROM writes WHERE thread_id IN ({placeholders})", thread_params),
            ])
        if scoped:
            statements.append((
                "project_snapshots", "DELETE FROM project_snapshots WHERE project_id = ? AND package_id = ?",
                (project_id, package_id),
            ))
        else:
            # 老项目可能没有 workflow_runs/run_thread_id，只在兼容路径清理这些无归属数据。
            statements.extend([
                ("chapter_reviews", "DELETE FROM chapter_reviews WHERE block_id IN "
                 "(SELECT id FROM blocks WHERE project_id = ?)", (project_id,)),
                ("review_history", "DELETE FROM review_history WHERE block_id IN "
                 "(SELECT id FROM blocks WHERE project_id = ?)", (project_id,)),
                ("review_issue_states", "DELETE FROM review_issue_states WHERE block_id IN "
                 "(SELECT id FROM blocks WHERE project_id = ?)", (project_id,)),
                ("blocks", "DELETE FROM blocks WHERE project_id = ?", (project_id,)),
                ("split_answers", "DELETE FROM split_answers WHERE project_id = ?", (project_id,)),
                ("review_points", "DELETE FROM review_points WHERE project_id = ?", (project_id,)),
                ("spec_requirements", "DELETE FROM spec_requirements WHERE project_id = ?", (project_id,)),
            ])
        for table, sql, params in statements:
            try:
                cur = await db.execute(sql, params)
                deleted[table] = cur.rowcount or 0
            except Exception as e:      # 表不存在等：不因清理失败阻断重跑
                logger.warning(f"reset-analysis：清理 {table} 失败（{e}）")
                deleted[table] = -1
        # 运行记录本身保留（历史可追溯），但标成已作废，避免前端把它当"当前运行"
        if threads:
            if scoped:
                await db.execute(
                    """UPDATE workflow_runs SET stage = 'aborted', updated_at = CURRENT_TIMESTAMP
                       WHERE project_id = ? AND package_id = ?""", (project_id, package_id))
            else:
                await db.execute(
                    "UPDATE workflow_runs SET stage = 'aborted', updated_at = CURRENT_TIMESTAMP "
                    "WHERE project_id = ?", (project_id,))
        await db.commit()

    logger.info(f"reset-analysis：项目 {project_id} 已清空拆解结果 {deleted}")
    return JSONResponse({"project_id": project_id, "deleted": deleted})


@router.get("/projects")
async def get_projects(status: str | None = None):
    async with get_db() as db:
        projects = await list_projects(db, status=status)
    return JSONResponse(content=projects)


# ── POST /api/projects ────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    deadline: str | None = None
    target_pages: int | None = Field(default=None, ge=1, le=5000)


@router.post("/projects")
async def create_project_route(body: ProjectCreateRequest):
    async with get_db() as db:
        project = await create_project(
            db, name=body.name, deadline=body.deadline, target_pages=body.target_pages,
        )
    return JSONResponse(content=project)


# ── GET /api/projects/{id} ────────────────────────────────

@router.get("/projects/{project_id}")
async def get_project_route(project_id: int):
    async with get_db() as db:
        project = await get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return JSONResponse(content=project)


# ── PATCH /api/projects/{id} ──────────────────────────────

# ── POST /api/projects/{id}/lock ──────────────────────────

# ── GET /api/projects/{id}/workflow-runs ──────────────────

@router.get("/projects/{project_id}/response-drafts")
async def get_response_drafts(project_id: int,
                              packageId: int | None = Query(default=None, alias="packageId")):
    """按标包列出各应答类型的目录草案。

    应答策划用它决定：点「商务」/「技术」时是**切到已有目录**还是**生成一份**。
    在此之前前端只读"当前线程"的 response_plan，于是同一个标包已经生成过技术目录，
    页面仍显示「生成目录草案」。
    """
    from services.package_scope import require_package
    from services.response_drafts import list_response_drafts

    if packageId is None:
        raise HTTPException(status_code=400, detail="缺少 packageId")
    async with get_db() as db:
        await require_package(db, project_id, packageId, confirmed=False)
        result = await list_response_drafts(db, project_id, packageId)

    # 目录是否"已派生"要看 run 的 spec：blocks 计数会把原文占位也算进去
    try:
        import routes.workflow as _wf
        runner = getattr(_wf, "_runner", None)
        if runner is not None:
            for draft in result.get("drafts") or []:
                spec = ((await runner.state(draft["thread_id"])) or {}).get("spec") or {}
                draft["has_outline"] = bool(spec.get("outline_revision"))
                draft["outline_count"] = len(spec.get("toc") or []) or draft.get("outline_count", 0)
                draft["source_sections"] = len(spec.get("source_toc") or [])
    except Exception as exc:            # spec 读取失败不影响草案清单
        logger.warning(f"response-drafts：读取 run spec 失败（{exc}）")
    return JSONResponse(content=result)


@router.post("/projects/{project_id}/quality/findings/{finding_id}/ignore")
async def ignore_quality_finding(project_id: int, finding_id: str):
    """把一条质检发现标为"已处理"（不再出现在待修清单）。"""
    from services.quality_check import ignore_finding
    async with get_db() as db:
        if not await ignore_finding(db, project_id, finding_id):
            raise HTTPException(status_code=404, detail="质检发现不存在")
        return {"ignored": True}


@router.get("/projects/{project_id}/quality")
async def project_quality_route(
    project_id: int,
    thread_id: str = Query(alias="threadId", min_length=1),
):
    """章节质检清单：结构完整性 / 图表数量 / 占位与空词 / 篇幅偏差（确定性检查，不调模型）。"""
    from services.quality_check import check_thread
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        from services.quality_check import sync_findings
        data = await check_thread(db, project_id, thread_id)
        return JSONResponse(content=await sync_findings(db, project_id, thread_id, data))


@router.get("/projects/{project_id}/progress")
async def project_progress_route(
    project_id: int,
    package_id: int | None = Query(default=None, alias="packageId"),
):
    """编写进度聚合：编写单元 / 篇幅 / 四阶段（状态行「进度中心」用）。

    口径与编写页一致（单元定义、250 字/页、篇幅取 page_budget 的 total/allocated）。
    """
    from services.project_progress import project_progress
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        return JSONResponse(content=await project_progress(db, project_id, package_id))


@router.get("/projects/{project_id}/todos")
async def project_todos_route(
    project_id: int,
    package_id: int | None = Query(default=None, alias="packageId"),
):
    """待办聚合：目录未确认 / 篇幅未确认 / 素材缺口 / 评审意见 / 运行中断。

    只列"需要用户动手"的事；排序按严重度（high → low）。
    """
    from services.project_progress import project_todos
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        return JSONResponse(content=await project_todos(db, project_id, package_id))


@router.get("/projects/{project_id}/workflow-runs")
async def list_project_workflow_runs(
    project_id: int,
    only_active: bool = False,
    package_id: int | None = Query(default=None, alias="packageId"),
):
    """返回该项目的 workflow_runs（按 created_at DESC）。

    供前端项目列表点击时按最新 thread 的 stage 路由到对应页面。
    """
    async with get_db() as db:
        project = await get_project(db, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        repo = WorkflowRunRepository(db)
        if package_id is not None:
            from services.package_scope import require_package
            await require_package(db, project_id, package_id, confirmed=False)
        runs = await repo.list_by_project(
            project_id, only_active=only_active, package_id=package_id,
        )
        # 补上「这条 run 是不是某类型的目录草案」：同一标包会有
        # **1 条解析运行 + 商务/技术各 1 份目录草案**（见 services/response_drafts.py），
        # 前端据此区分（选 run 时优先落在草案上、运行详情里把解析记录折叠）。
        if runs:
            placeholders = ",".join("?" * len(runs))
            plan_rows = await (await db.execute(
                f"SELECT thread_id, value FROM split_answers "
                f"WHERE project_id = ? AND key = 'response_plan' AND thread_id IN ({placeholders})",
                (project_id, *[r["thread_id"] for r in runs]))).fetchall()
            plans: dict[str, dict] = {}
            for row in plan_rows:
                try:
                    plan = json.loads(row["value"] or "{}")
                except Exception:
                    continue
                if isinstance(plan, dict):
                    plans[row["thread_id"]] = plan
            for run in runs:
                plan = plans.get(run["thread_id"]) or {}
                run["plan_type"] = str(plan.get("type") or "")
                run["plan_confirmed"] = bool(plan.get("confirmed"))
                run["plan_source_thread_id"] = str(plan.get("source_thread_id") or "")
                run["is_parse_record"] = not run["plan_type"]
    return JSONResponse(content=runs)
