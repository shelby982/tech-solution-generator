"""标包优先流程的 API 契约。"""
import aiosqlite
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import db as db_module
from db import get_db, init_db
from routes import packages as package_routes


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(package_routes.router, prefix="/api")
    return application


@pytest_asyncio.fixture
async def package_env(tmp_path, monkeypatch, app):
    db_path = str(tmp_path / "packages.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        cursor = await conn.execute("INSERT INTO projects (name) VALUES ('多标包项目')")
        await conn.commit()
        project_id = cursor.lastrowid
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, project_id


@pytest.mark.asyncio
async def test_identify_requires_completed_tender_parse(package_env):
    client, project_id = package_env
    async with get_db() as db:
        await db.execute(
            "INSERT INTO materials (project_id, filename, role, parse_status) VALUES (?, '招标.pdf', 'main_rfp', 'parsing')",
            (project_id,),
        )
        await db.commit()

    response = await client.post(f"/api/projects/{project_id}/packages/identify")
    assert response.status_code == 409
    assert "仍在解析" in response.json()["detail"]


@pytest.mark.asyncio
async def test_identify_confirm_and_workspace_gate(package_env):
    client, project_id = package_env
    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO materials (project_id, filename, role, parse_status) VALUES (?, '招标.pdf', 'main_rfp', 'done')",
            (project_id,),
        )
        material_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO material_chunks (material_id, chunk_index, content, page) VALUES (?, 0, ?, 3)",
            (material_id, "本项目分为标的1标包2，采购服务器设备。"),
        )
        await db.commit()

    identified = await client.post(f"/api/projects/{project_id}/packages/identify")
    assert identified.status_code == 200
    packages = identified.json()["packages"]
    assert len(packages) == 1
    package_id = packages[0]["package_id"]
    assert packages[0]["status"] == "candidate"
    assert packages[0]["target_code"] == "1"
    assert packages[0]["package_code"] == "2"

    blocked = await client.get(f"/api/projects/{project_id}/packages/{package_id}/workspace")
    assert blocked.status_code == 409

    confirmed = await client.post(
        f"/api/projects/{project_id}/packages/{package_id}/confirm",
        json={"display_name": "服务器采购包", "kind": "package"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["display_name"] == "服务器采购包"

    workspace = await client.get(f"/api/projects/{project_id}/packages/{package_id}/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["package"]["package_id"] == package_id
    assert workspace.json()["latest_run"] is None
    assert workspace.json()["counts"] == {
        "review_points": 0, "spec_requirements": 0, "blocks": 0,
    }

    intake = await client.get(f"/api/projects/{project_id}/intake")
    assert intake.status_code == 200
    assert intake.json()["readiness"]["can_enter_workspace"] is True


@pytest.mark.asyncio
async def test_package_cannot_cross_project_boundary(package_env):
    client, project_id = package_env
    async with get_db() as db:
        other = await db.execute("INSERT INTO projects (name) VALUES ('另一个项目')")
        package = await db.execute(
            "INSERT INTO packages (project_id, target_code, package_code) VALUES (?, '1', '1')",
            (other.lastrowid,),
        )
        await db.commit()

    response = await client.post(
        f"/api/projects/{project_id}/packages/{package.lastrowid}/confirm", json={},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_manual_package_is_confirmed_and_stable(package_env):
    client, project_id = package_env
    created = await client.post(
        f"/api/projects/{project_id}/packages",
        json={"package_code": "A-03", "display_name": "人工确认包", "kind": "section"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "confirmed"
    assert body["confidence"] == "manual"
    assert body["package_code"] == "A-03"

    duplicate = await client.post(
        f"/api/projects/{project_id}/packages", json={"package_code": "A-03"},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_edit_exclude_and_merge_candidates(package_env):
    client, pid = package_env
    first = (await client.post(f'/api/projects/{pid}/packages', json={
        'package_code': '1', 'display_name': '采购包一',
    })).json()
    second = (await client.post(f'/api/projects/{pid}/packages', json={
        'package_code': '2', 'display_name': '重复项',
    })).json()
    edited = await client.patch(f"/api/projects/{pid}/packages/{first['package_id']}", json={
        'target_code': '1', 'package_code': '1', 'display_name': '服务器采购',
        'kind': 'package', 'status': 'confirmed',
    })
    assert edited.status_code == 200
    assert edited.json()['display_name'] == '服务器采购'
    merged = await client.post(f"/api/projects/{pid}/packages/{second['package_id']}/merge", json={
        'destination_id': first['package_id'],
    })
    assert merged.status_code == 200
    assert merged.json()['status'] == 'needs_review'
    intake = (await client.get(f'/api/projects/{pid}/intake')).json()
    assert len(intake['packages']) == 1
    assert intake['readiness']['can_enter_workspace'] is False


@pytest.mark.asyncio
async def test_identify_ignores_enterprise_materials(package_env):
    client, pid = package_env
    async with get_db() as db:
        for role, text in [('main_rfp', '标的1标包2'), ('source', '标的8标包9')]:
            cur = await db.execute(
                "INSERT INTO materials (project_id, filename, role, parse_status) VALUES (?, ?, ?, 'done')",
                (pid, role+'.pdf', role),
            )
            await db.execute('INSERT INTO material_chunks (material_id, chunk_index, content) VALUES (?, 0, ?)', (cur.lastrowid, text))
        await db.commit()
    result = await client.post(f'/api/projects/{pid}/packages/identify')
    assert result.status_code == 200
    assert [(p['target_code'], p['package_code']) for p in result.json()['packages']] == [('1','2')]


@pytest.mark.asyncio
async def test_excluded_candidates_can_be_restored(package_env):
    client, pid = package_env
    p=(await client.post(f'/api/projects/{pid}/packages', json={'package_code':'1'})).json()
    url=f"/api/projects/{pid}/packages/{p['package_id']}"
    await client.patch(url,json={'package_code':'1','status':'archived'})
    intake=(await client.get(f'/api/projects/{pid}/intake')).json()
    assert intake['packages']==[]
    assert intake['excluded_packages'][0]['package_id']==p['package_id']
    restored=await client.patch(url,json={'package_code':'1','status':'candidate'})
    assert restored.status_code==200
    intake=(await client.get(f'/api/projects/{pid}/intake')).json()
    assert len(intake['packages'])==1
    assert intake['excluded_packages']==[]


@pytest.mark.asyncio
async def test_identify_directly_redisplays_previously_excluded_match(package_env):
    from services.package_analysis import save_analysis
    client, pid = package_env
    facts=[{'target':'1','package_no':'2','confidence':'high','evidence':[], 'source_material_ids':[]}]
    async with get_db() as db:
        await save_analysis(db,pid,facts)
        before=await (await db.execute('SELECT id FROM packages WHERE project_id=?',(pid,))).fetchone()
        await db.execute("UPDATE packages SET status='archived',confidence='manual' WHERE id=?",(before['id'],))
        await db.commit()
        await save_analysis(db,pid,facts)
    intake=(await client.get(f'/api/projects/{pid}/intake')).json()
    assert len(intake['packages'])==1
    assert intake['packages'][0]['package_id']==before['id']
    assert intake['packages'][0]['status']=='candidate'


@pytest.mark.asyncio
async def test_confirm_saves_spec_selection_and_rejects_invalid_material(package_env):
    client,pid=package_env
    p=(await client.post(f'/api/projects/{pid}/packages',json={'package_code':'1'})).json()
    async with get_db() as db:
        cur=await db.execute("INSERT INTO materials(project_id,filename,role,parse_status) VALUES (?,'技术规范书.docx','spec','done')",(pid,))
        mid=cur.lastrowid
        await db.commit()
    url=f"/api/projects/{pid}/packages/{p['package_id']}/confirm"
    assert (await client.post(url,json={'spec_material_ids':[],'no_spec':False})).status_code==422
    assert (await client.post(url,json={'spec_material_ids':[mid+100]})).status_code==422
    assert (await client.post(url,json={'spec_material_ids':[mid],'no_spec':True})).status_code==422
    assert (await client.post(url,json={'spec_material_ids':[mid]})).status_code==200
    intake=(await client.get(f'/api/projects/{pid}/intake')).json()
    assert intake['packages'][0]['spec_material_ids']==[mid]
    workspace=(await client.get(f"/api/projects/{pid}/packages/{p['package_id']}/workspace")).json()
    assert workspace['package']['spec_material_ids']==[mid]
    assert str(mid) in workspace['package']['source_material_ids']
