"""历史与运行列表必须按稳定 package_id 隔离。"""
import aiosqlite
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import db as db_module
from db import init_db
from routes import projects, revisions


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(projects.router, prefix="/api")
    application.include_router(revisions.router, prefix="/api")
    return application


@pytest_asyncio.fixture
async def scope_env(tmp_path, monkeypatch, app):
    path = str(tmp_path / "scope.db")
    monkeypatch.setattr(db_module, "DB_PATH", path)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await init_db(db)
        project = await db.execute("INSERT INTO projects (name) VALUES ('隔离项目')")
        pid = project.lastrowid
        p1 = await db.execute(
            """INSERT INTO packages
               (project_id, target_code, package_code, display_name, status)
               VALUES (?, '1', '1', '标包一', 'confirmed')""", (pid,),
        )
        p2 = await db.execute(
            """INSERT INTO packages
               (project_id, target_code, package_code, display_name, status)
               VALUES (?, '1', '2', '标包二', 'confirmed')""", (pid,),
        )
        p1_id, p2_id = p1.lastrowid, p2.lastrowid
        await db.execute(
            """INSERT INTO workflow_runs
               (thread_id, project_id, stage, target, package_no, package_id)
               VALUES ('run-p1', ?, 'done', '1', '1', ?)""", (pid, p1_id),
        )
        await db.execute(
            """INSERT INTO workflow_runs
               (thread_id, project_id, stage, target, package_no, package_id)
               VALUES ('run-p2', ?, 'done', '1', '2', ?)""", (pid, p2_id),
        )
        b1 = await db.execute(
            """INSERT INTO blocks
               (project_id, block_id, title, content, order_idx, run_thread_id, status)
               VALUES (?, 'p1-b1', '标包一章节', '正文一', 1, 'run-p1', 'done')""", (pid,),
        )
        b2 = await db.execute(
            """INSERT INTO blocks
               (project_id, block_id, title, content, order_idx, run_thread_id, status)
               VALUES (?, 'p2-b1', '标包二章节', '正文二', 1, 'run-p2', 'done')""", (pid,),
        )
        await db.execute(
            "INSERT INTO block_revisions (block_id, revision_no, content, source) VALUES (?, 1, '正文一', 'edit')",
            (b1.lastrowid,),
        )
        await db.execute(
            "INSERT INTO block_revisions (block_id, revision_no, content, source) VALUES (?, 1, '正文二', 'edit')",
            (b2.lastrowid,),
        )
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, pid, p1_id, p2_id


@pytest.mark.asyncio
async def test_runs_and_revisions_are_package_scoped(scope_env):
    client, pid, p1_id, p2_id = scope_env

    runs = await client.get(f"/api/projects/{pid}/workflow-runs?packageId={p1_id}")
    assert runs.status_code == 200
    assert [row["thread_id"] for row in runs.json()] == ["run-p1"]

    history = await client.get(f"/api/projects/{pid}/revisions?packageId={p1_id}")
    assert history.status_code == 200
    assert [row["block_code"] for row in history.json()["revisions"]] == ["p1-b1"]



@pytest.mark.asyncio
async def test_package_scope_rejects_cross_project_id(scope_env):
    client, pid, _, _ = scope_env
    async with aiosqlite.connect(db_module.DB_PATH) as db:
        project = await db.execute("INSERT INTO projects (name) VALUES ('其他项目')")
        package = await db.execute(
            """INSERT INTO packages (project_id, package_code, status)
               VALUES (?, 'X', 'confirmed')""", (project.lastrowid,),
        )
        await db.commit()
        foreign_package_id = package.lastrowid

    for path in (
        f"/api/projects/{pid}/workflow-runs?packageId={foreign_package_id}",
        f"/api/projects/{pid}/revisions?packageId={foreign_package_id}",
    ):
        response = await client.get(path)
        assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_reset_analysis_only_clears_selected_package(scope_env):
    client, pid, p1_id, p2_id = scope_env

    reset = await client.post(f"/api/projects/{pid}/reset-analysis?packageId={p1_id}")
    assert reset.status_code == 200

    async with aiosqlite.connect(db_module.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        blocks = await (await db.execute(
            "SELECT block_id, run_thread_id FROM blocks ORDER BY block_id"
        )).fetchall()
        runs = await (await db.execute(
            "SELECT package_id, stage FROM workflow_runs ORDER BY package_id"
        )).fetchall()
        revisions = await (await db.execute(
            "SELECT COUNT(*) AS n FROM block_revisions"
        )).fetchone()

    assert [(row["block_id"], row["run_thread_id"]) for row in blocks] == [("p2-b1", "run-p2")]
    assert [(row["package_id"], row["stage"]) for row in runs] == [
        (p1_id, "aborted"), (p2_id, "done"),
    ]
    assert revisions["n"] == 1
