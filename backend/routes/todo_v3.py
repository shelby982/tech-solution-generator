"""待办 v3 接口：章级任务（含服务端状态）+ 决策应用 + 复检 + 项目级根因汇总。"""
from __future__ import annotations


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services.block_store import add_revision, get_block, update_block_content
from services.todo_states import load_states, merge_status, set_state

router = APIRouter(tags=["todo-v3"])


class StateBody(BaseModel):
    block_id: str = ""
    task_id: str = Field(min_length=1)
    action: str = Field(pattern="^(ignore|snooze|resolve|open)$")
    reason: str = ""
    until_days: int = 1


class ApplyBody(BaseModel):
    block_id: str = Field(min_length=1)
    task_id: str = ""
    section: str = ""
    quote: str = ""
    replacement: str = ""


async def _chapter_block(project_id: int, block_code: str) -> dict:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM blocks WHERE project_id=? AND block_id=?", (project_id, block_code))).fetchone()
    if not row:
        raise HTTPException(404, f"章节不存在：{block_code}")
    return dict(row)


async def _tasks(project_id: int, thread_id: str, block_code: str) -> dict:
    from services.writing_plans import chapter_plan

    block = await _chapter_block(project_id, block_code)
    async with get_db() as db:
        plan = await chapter_plan(db, project_id, thread_id, block_code)
    tasks = plan.get("delivery_tasks") or {}
    states = await load_states(project_id, block_code)
    merged = merge_status(tasks, states)
    merged["completion"] = plan.get("completion") or {}
    return merged


@router.post('/projects/{project_id}/todo/state')
async def update_state(project_id: int, body: StateBody):
    try:
        mapped = {"ignore": "ignored", "snooze": "snoozed", "resolve": "resolved",
                  "open": "open"}.get(body.action, body.action)
        result = await set_state(project_id, body.block_id, body.task_id, mapped,
                                 body.reason, body.until_days)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result


@router.post('/projects/{project_id}/todo/apply')
async def apply_decision(project_id: int, body: ApplyBody):
    """按 locator（节 + 原句）由**服务端**替换文本，写 revision，返回提示。"""
    if not body.quote or not body.replacement:
        raise HTTPException(422, "缺少原句或替换文本")
    block = await _chapter_block(project_id, body.block_id)
    content = str(block.get("content") or "")
    if body.quote not in content:
        raise HTTPException(409, "原句已变化，请重新核对待办")
    updated = content.replace(body.quote, body.replacement, 1)
    async with get_db() as db:
        await add_revision(db, int(block["id"]), content, summary="待办决策应用（todo_v3）", source="ai")
        await update_block_content(db, int(block["id"]), updated)
    removed = len(body.quote) - len(body.replacement)
    return {"ok": True, "changed": True, "delta_chars": removed,
            "removed": "已按你的选择替换 1 处" if removed >= 0 else "已按你的选择替换 1 处（内容变长）"}


@router.post('/projects/{project_id}/todo/recheck')
async def recheck(project_id: int, block_id: str, thread_id: str = ''):
    """重新计算该章任务（上传/应用之后调用，实现"做完就算消除"）。"""
    tasks = await _tasks(project_id, thread_id, block_id)
    return {"ok": True, "summary": tasks.get("summary") or {},
            "need_user": tasks.get("need_user") or [], "system": tasks.get("system") or [],
            "suggestion": tasks.get("suggestion") or []}


@router.get('/projects/{project_id}/todo/overview')
async def project_overview(project_id: int, thread_id: str = ''):
    """项目级「交付检查」：按**根因**汇总，不按章列（v3 §4.4）。"""
    from services.writing_plans import chapter_plan

    from services.block_store import list_blocks
    async with get_db() as db:
        rows = await list_blocks(db, project_id, thread_id or None) if thread_id else await list_blocks(db, project_id)
        blocks = [(r.get("id"), r.get("block_id"), r.get("title"), r.get("content"))
                  for r in (rows or [])
                  if not (r.get("is_catalog") or r.get("has_children"))]
        empty: list[str] = []
        unconfirmed: list[str] = []
        material_gap: dict[str, int] = {}
        gap_total = 0
        for row in blocks:
            row_id, block_id, title, content = row[0], row[1], row[2] or "", row[3] or ""
            block_code = str(block_id or "")
            if not block_code:
                continue
            if not content.strip():
                empty.append(block_code)
            try:
                plan = await chapter_plan(db, project_id, thread_id, block_code)
            except Exception:
                continue
            status = ""
            try:
                async with get_db() as db2:
                    row = await (await db2.execute(
                        "SELECT writing_status FROM chapter_studio WHERE block_id=?",
                        (int(row_id),))).fetchone() if row_id else None
                status = (row[0] if row else "") or ""
            except Exception:
                status = ""
            if status != "complete":
                unconfirmed.append(block_code)
            for gap in plan.get("material_gaps") or []:
                if gap.get("availability") == "upload" and int(gap.get("lack") or 0):
                    key = str(gap.get("kind") or "材料")[:20]
                    material_gap[key] = material_gap.get(key, 0) + 1
            gap_total += len(plan.get("generation_gaps") or [])
    return {
        "summary": {
            "empty_chapters": len(empty),
            "unconfirmed_chapters": len(unconfirmed),
            "material_gaps": material_gap,
            "generation_gaps": gap_total,
        },
        "roots": [
            {"key": "empty", "title": f"{len(empty)} 章正文为空", "action": "batch_write",
             "impact": "export_block", "blocks": empty[:200]},
            {"key": "unconfirmed", "title": f"{len(unconfirmed)} 章未确认完成", "action": "confirm_list",
             "impact": "completion_block", "blocks": unconfirmed[:200]},
            *[{"key": f"material:{kind}", "title": f"{count} 章缺「{kind}」（上传一次全部生效）",
               "action": "upload_once", "impact": "completion_block", "kind": kind}
              for kind, count in sorted(material_gap.items(), key=lambda kv: -kv[1])[:5]],
            {"key": "gaps", "title": f"系统待补依据 {gap_total} 条", "action": "system_search",
             "impact": "quality"},
        ],
    }


# ── 交付门（P0）：内部语法 / 内部术语 / 占位符 ────────────────────────────
class GateFixBody(BaseModel):
    apply: bool = True


@router.get('/blocks/{block_id}/delivery-gate')
async def delivery_gate(block_id: int):
    """章级交付门：返回 blocking 清单（带 locator）+ 可自动修数量。"""
    from services.block_store import get_block
    from services.delivery_gate import scan

    async with get_db() as db:
        block = await get_block(db, block_id)
    if not block:
        raise HTTPException(404, "章节不存在")
    result = scan(block.get("content") or "")
    return {"block_id": block_id, "block_code": block.get("block_id"),
            **result}


@router.post('/blocks/{block_id}/studio/citation-enrich')
async def citation_enrich(block_id: int):
    """系统补齐引用：政策文号 + 等级保护标准号（确定性信息，不用模型）。"""
    from services.block_store import add_revision, get_block, update_block_content
    from services.citation_enrich import enrich

    async with get_db() as db:
        block = await get_block(db, block_id)
    if not block:
        raise HTTPException(404, "章节不存在")
    before = block.get("content") or ""
    after, changes = enrich(before)
    if not changes:
        return {"changed": False, "changes": [], "content": before}
    async with get_db() as db:
        await add_revision(db, block_id, before, summary="系统补齐引用：文号/标准号", source="ai")
        await update_block_content(db, block_id, after)
    return {"changed": True, "changes": changes, "content": after}


class PracticeBody(BaseModel):
    section: str = Field(min_length=1)
    practice: str = ""


@router.put('/projects/{project_id}/chapters/{block_code}/plan/practice')
async def save_section_practice(project_id: int, block_code: str, body: PracticeBody):
    """保存某节"写法"（= 该节提示词）；清空则恢复系统默认。"""
    from services.section_practice import save_practice

    await _chapter_block(project_id, block_code)
    return await save_practice(project_id, block_code, body.section, body.practice)
