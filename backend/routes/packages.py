"""标包优先流程：项目准备、标包确认与标包工作空间。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services.package_analysis import run_analysis

router = APIRouter(tags=["packages"])


class ConfirmPackageReq(BaseModel):
    display_name: str = Field(default="", max_length=300)
    kind: str = Field(default="package", pattern="^(target|package|section)$")
    spec_material_ids: list[int] | None = None
    no_spec: bool = False


class CreatePackageReq(ConfirmPackageReq):
    target_code: str = Field(default="", max_length=100)
    package_code: str = Field(default="", max_length=100)


class StartPackageRunReq(BaseModel):
    config: dict = Field(default_factory=dict)


class EditPackageReq(CreatePackageReq):
    status: str = Field(default="candidate", pattern="^(candidate|confirmed|archived)$")


class MergePackageReq(BaseModel):
    destination_id: int = Field(gt=0)


@router.post("/projects/{project_id}/packages/{package_id}/merge")
async def merge_package(project_id: int, package_id: int, req: MergePackageReq):
    if package_id == req.destination_id:
        raise HTTPException(status_code=422, detail="请选择其他候选")
    async with get_db() as db:
        source = await _get_package(db, project_id, package_id)
        dest = await _get_package(db, project_id, req.destination_id)
        runs = await (await db.execute(
            "SELECT 1 FROM workflow_runs WHERE package_id=? LIMIT 1", (package_id,),
        )).fetchone()
        if runs:
            raise HTTPException(status_code=409, detail="该标包已有运行成果，请保留独立工作空间；只能合并尚未运行的重复候选")
        evidence = json.loads(dest['evidence'] or '[]') + json.loads(source['evidence'] or '[]')
        ids = sorted(set(map(str, json.loads(dest['source_material_ids'] or '[]') + json.loads(source['source_material_ids'] or '[]'))))
        await db.execute(
            """UPDATE packages SET evidence=?, source_material_ids=?, status='needs_review',
               confidence='manual' WHERE id=?""",
            (json.dumps(evidence, ensure_ascii=False), json.dumps(ids), req.destination_id),
        )
        await db.execute("UPDATE packages SET status='archived', confidence='manual' WHERE id=?", (package_id,))
        for mid in ids:
            await db.execute(
                """INSERT OR IGNORE INTO material_packages
                   (material_id, project_id, package_id, target, package_no)
                   SELECT id, project_id, ?, ?, ? FROM materials WHERE id=? AND project_id=?""",
                (req.destination_id, dest['target_code'], dest['package_code'], mid, project_id),
            )
        await db.commit()
        return _package(await _get_package(db, project_id, req.destination_id))


@router.patch("/projects/{project_id}/packages/{package_id}")
async def edit_package(project_id: int, package_id: int, req: EditPackageReq):
    import sqlite3
    if not req.target_code.strip() and not req.package_code.strip():
        raise HTTPException(status_code=422, detail="请填写标的或标包编号")
    async with get_db() as db:
        old = await _get_package(db, project_id, package_id)
        if (old['target_code'], old['package_code']) != (req.target_code.strip(), req.package_code.strip()):
            run = await (await db.execute('SELECT 1 FROM workflow_runs WHERE package_id=? LIMIT 1', (package_id,))).fetchone()
            if run:
                raise HTTPException(status_code=409, detail="已有运行成果，暂不能修改编号；可修改名称或排除后建立正确标包")
        try:
            await db.execute(
                """UPDATE packages SET target_code=?, package_code=?, display_name=?, kind=?,
                   status=?, confidence='manual', updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (req.target_code.strip(), req.package_code.strip(), req.display_name.strip(),
                 req.kind, req.status, package_id),
            )
            await db.execute(
                'UPDATE material_packages SET target=?, package_no=? WHERE package_id=?',
                (req.target_code.strip(), req.package_code.strip(), package_id),
            )
            await db.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="该编号已存在，请核对重复标包")
        return _package(await _get_package(db, project_id, package_id))


def _package(row) -> dict:
    item = dict(row)
    item["package_id"] = item.pop("id")
    for key in ("source_material_ids", "evidence"):
        try:
            item[key] = json.loads(item.get(key) or "[]")
        except (TypeError, json.JSONDecodeError):
            item[key] = []
    return item


async def _get_package(db, project_id: int, package_id: int):
    row = await (await db.execute(
        "SELECT * FROM packages WHERE id = ? AND project_id = ?",
        (package_id, project_id),
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="标包不存在或不属于该项目")
    return row


@router.get("/projects/{project_id}/intake")
async def project_intake(project_id: int):
    """项目准备页的一次性读模型：材料解析状态、候选标包及是否允许进入工作空间。"""
    async with get_db() as db:
        project = await (await db.execute(
            "SELECT id, name, status FROM projects WHERE id = ?", (project_id,),
        )).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        materials = [dict(row) for row in await (await db.execute(
            """SELECT id, filename, role, parse_status, parsed_at
                 FROM materials WHERE project_id = ? AND deleted_at IS NULL ORDER BY id""",
            (project_id,),
        )).fetchall()]
        packages = [_package(row) for row in await (await db.execute(
            "SELECT * FROM packages WHERE project_id = ? AND status != 'archived' ORDER BY id",
            (project_id,),
        )).fetchall()]
        excluded = [_package(row) for row in await (await db.execute(
            "SELECT * FROM packages WHERE project_id=? AND status='archived' AND confidence='manual' ORDER BY id",
            (project_id,),
        )).fetchall()]
        for p in packages:
            selection = await (await db.execute('SELECT * FROM package_spec_selections WHERE package_id=?', (p['package_id'],))).fetchone()
            p['spec_selection_saved'] = selection is not None
            p['spec_material_ids'] = json.loads(selection['material_ids']) if selection else [
                m['id'] for m in materials if m['role']=='spec' and str(m['id']) in list(map(str,p['source_material_ids']))]
            p['no_spec'] = bool(selection['no_spec']) if selection else False
    tender = [m for m in materials if m.get("role") in {"main_rfp", "requirement", "spec"}]
    parsing = any(m.get("parse_status") in {"pending", "parsing"} for m in tender)
    failed = any(m.get("parse_status") == "failed" for m in tender)
    return {
        "project": dict(project), "materials": materials, "packages": packages,
        "excluded_packages": excluded,
        "readiness": {
            "has_tender_document": bool(tender),
            "parsing": parsing,
            "parse_failed": failed,
            "confirmed_packages": sum(p["status"] == "confirmed" for p in packages),
            "can_enter_workspace": any(p["status"] == "confirmed" for p in packages),
        },
    }


@router.post("/projects/{project_id}/packages/identify")
async def identify_packages(project_id: int):
    """只从已解析的招标正文识别候选标包；解析未完成时强制等待。"""
    async with get_db() as db:
        project = await (await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        tender = await (await db.execute(
            """SELECT parse_status FROM materials WHERE project_id = ? AND deleted_at IS NULL
               AND role IN ('main_rfp','requirement','spec')""", (project_id,),
        )).fetchall()
        if not tender:
            raise HTTPException(status_code=409, detail="请先上传主招标文件或技术规范书")
        if any(row[0] in {"pending", "parsing"} for row in tender):
            raise HTTPException(status_code=409, detail="招标文件仍在解析，请稍后再识别标包")
        if any(row[0] == "failed" for row in tender):
            raise HTTPException(status_code=409, detail="招标文件解析失败，请重新上传后再识别")
        packages, note = await run_analysis(db, project_id)
        stable = [_package(row) for row in await (await db.execute(
            "SELECT * FROM packages WHERE project_id = ? AND status != 'archived' ORDER BY id",
            (project_id,),
        )).fetchall()]
    return {"packages": stable, "detected": len(packages), "note": note}


@router.post("/projects/{project_id}/packages")
async def create_package(project_id: int, req: CreatePackageReq):
    """原文没有规范编号时允许人工补录；记录明确标为人工确认。"""
    if not req.target_code.strip() and not req.package_code.strip():
        raise HTTPException(status_code=422, detail="标的编号和标包/标段编号至少填写一项")
    async with get_db() as db:
        project = await (await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        try:
            cursor = await db.execute(
                """INSERT INTO packages
                   (project_id, kind, target_code, package_code, display_name, status, confidence)
                   VALUES (?,?,?,?,?, 'confirmed', 'manual')""",
                (project_id, req.kind, req.target_code.strip(), req.package_code.strip(),
                 req.display_name.strip()),
            )
            await db.commit()
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="该标包已存在") from exc
            raise
        return _package(await _get_package(db, project_id, cursor.lastrowid))


@router.post("/projects/{project_id}/packages/{package_id}/confirm")
async def confirm_package(project_id: int, package_id: int, req: ConfirmPackageReq):
    async with get_db() as db:
        row = await _get_package(db, project_id, package_id)
        if req.spec_material_ids is not None:
            ids = sorted(set(req.spec_material_ids))
            if req.no_spec and ids:
                raise HTTPException(status_code=422, detail='请选择规范书，或选择无独立规范书，不能同时选择')
            if not ids and not req.no_spec:
                raise HTTPException(status_code=422, detail='请选择技术规范书，或勾选无独立技术规范书')
            for mid in ids:
                material = await (await db.execute("SELECT id FROM materials WHERE id=? AND project_id=? AND role='spec' AND deleted_at IS NULL AND parse_status='done'", (mid,project_id))).fetchone()
                if material is None:
                    raise HTTPException(status_code=422, detail='所选技术规范书不存在或尚未解析完成')
            await db.execute('INSERT INTO package_spec_selections (package_id,material_ids,no_spec) VALUES (?,?,?) ON CONFLICT(package_id) DO UPDATE SET material_ids=excluded.material_ids,no_spec=excluded.no_spec', (package_id,json.dumps(ids),int(req.no_spec)))
        name = req.display_name.strip() or row["display_name"] or (
            f"标包 {row['package_code']}" if row["package_code"] else f"标的 {row['target_code']}"
        )
        await db.execute(
            """UPDATE packages SET status = 'confirmed', kind = ?, display_name = ?,
               updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (req.kind, name, package_id),
        )
        await db.commit()
        return _package(await _get_package(db, project_id, package_id))


@router.get("/projects/{project_id}/packages/{package_id}/workspace")
async def package_workspace(project_id: int, package_id: int):
    async with get_db() as db:
        row = await _get_package(db, project_id, package_id)
        if row["status"] != "confirmed":
            raise HTTPException(status_code=409, detail="请先确认标包，再进入后续五个步骤")
        run = await (await db.execute(
            """SELECT * FROM workflow_runs WHERE project_id = ? AND package_id = ?
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (project_id, package_id),
        )).fetchone()
        counts = {}
        for table in ("review_points", "spec_requirements", "blocks"):
            if run:
                result = await (await db.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = ? AND run_thread_id = ?",
                    (project_id, run["thread_id"]),
                )).fetchone()
                counts[table] = result["n"]
            else:
                counts[table] = 0
        package = _package(row)
        selection = await (await db.execute('SELECT * FROM package_spec_selections WHERE package_id=?', (package_id,))).fetchone()
        if selection:
            ids = json.loads(selection['material_ids'])
            package['spec_material_ids'] = ids
            package['no_spec'] = bool(selection['no_spec'])
            specs = await (await db.execute("SELECT id FROM materials WHERE project_id=? AND role='spec'", (project_id,))).fetchall()
            spec_set = {str(m['id']) for m in specs}
            package['source_material_ids'] = [mid for mid in package['source_material_ids'] if str(mid) not in spec_set] + list(map(str,ids))
    return {"package": package, "latest_run": dict(run) if run else None, "counts": counts}


@router.post("/projects/{project_id}/packages/{package_id}/runs")
async def start_package_run(project_id: int, package_id: int, req: StartPackageRunReq):
    async with get_db() as db:
        row = await _get_package(db, project_id, package_id)
        if row["status"] != "confirmed":
            raise HTTPException(status_code=409, detail="请先确认标包，再启动拆分")
        selection = await (await db.execute('SELECT * FROM package_spec_selections WHERE package_id=?', (package_id,))).fetchone()
        if selection is None:
            raise HTTPException(status_code=409, detail='请先在项目准备页选择对应技术规范书')
        spec_ids = json.loads(selection['material_ids'])
        materials = await (await db.execute("SELECT id,role FROM materials WHERE project_id=? AND deleted_at IS NULL", (project_id,))).fetchall()
        available_specs = {m['id'] for m in materials if m['role']=='spec'}
        if not set(spec_ids).issubset(available_specs):
            raise HTTPException(status_code=409, detail='关联规范书已变更，请返回项目准备重新选择')
        material_ids = [m['id'] for m in materials if m['role'] in ('main_rfp','requirement','scoring','evaluation') or m['id'] in spec_ids]
    from routes.workflow import get_runner
    config = dict(req.config)
    config.update({
        "package_id": package_id,
        "target": row["target_code"],
        "package_no": row["package_code"],
        "material_ids": material_ids,
    })
    thread_id = await get_runner().start(project_id, config)
    return {"thread_id": thread_id, "package_id": package_id}
