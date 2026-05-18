"""
block_store 及 db 初始化的单元测试。
所有测试使用 tests/conftest.py 提供的 :memory: db fixture。
"""
import json
import pytest

pytestmark = pytest.mark.asyncio

from services.block_store import (
    create_project, get_project, list_projects, update_project,
    create_material, list_materials, update_material_parsed,
    create_block, list_blocks, get_block,
    update_block_content, update_block_status, delete_blocks_by_project,
    add_revision, list_revisions,
    create_snapshot, get_snapshot,
)


async def test_db_fixture_creates_tables(db):
    """db fixture 应已建好所有表。"""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in await cursor.fetchall()}
    expected = {"projects", "materials", "blocks", "block_revisions", "project_snapshots"}
    assert expected.issubset(tables)


async def test_create_and_get_project(db):
    project = await create_project(db, name="测试项目", deadline="2026-12-31")
    assert project["id"] is not None
    assert project["name"] == "测试项目"
    assert project["status"] == "init"
    assert project["deadline"] == "2026-12-31"

    fetched = await get_project(db, project["id"])
    assert fetched is not None
    assert fetched["name"] == "测试项目"


async def test_get_project_not_found(db):
    result = await get_project(db, 99999)
    assert result is None


async def test_list_projects_with_status_filter(db):
    await create_project(db, name="项目A")
    await create_project(db, name="项目B")
    p3 = await create_project(db, name="项目C")
    await update_project(db, p3["id"], status="done")

    init_list = await list_projects(db, status="init")
    assert len(init_list) >= 2
    done_list = await list_projects(db, status="done")
    assert len(done_list) >= 1


async def test_update_project(db):
    project = await create_project(db, name="旧名称")
    updated = await update_project(db, project["id"], name="新名称", status="active")
    assert updated is not None
    assert updated["name"] == "新名称"
    assert updated["status"] == "active"


async def test_update_project_not_found(db):
    result = await update_project(db, 99999, name="不存在")
    assert result is None


async def test_create_and_list_materials(db):
    project = await create_project(db, name="材料项目")
    mat = await create_material(
        db, project_id=project["id"], filename="规范书.docx",
        type_="docx", file_path="uploads/1/规范书.docx", role="main",
    )
    assert mat["id"] is not None
    assert mat["filename"] == "规范书.docx"
    assert mat["parsed_at"] is None

    materials = await list_materials(db, project["id"])
    assert len(materials) == 1
    assert materials[0]["role"] == "main"


async def test_update_material_parsed(db):
    project = await create_project(db, name="解析项目")
    mat = await create_material(
        db, project_id=project["id"], filename="file.pdf",
        type_="pdf", file_path="uploads/1/file.pdf", role="reference",
    )
    await update_material_parsed(db, mat["id"])
    updated = await list_materials(db, project["id"])
    assert updated[0]["parsed_at"] is not None


async def test_create_and_list_blocks(db):
    project = await create_project(db, name="block项目")
    block = await create_block(
        db, project_id=project["id"], block_id="H-001",
        kind="heading", level=1, title="第一章",
        domain="arch", parent_title="", requirement="高可用",
        score="20分", source="spec.docx", order_idx=0,
    )
    assert block["id"] is not None
    assert block["status"] == "empty"
    blocks = await list_blocks(db, project["id"])
    assert len(blocks) == 1


async def test_update_block_content(db):
    project = await create_project(db, name="更新项目")
    block = await create_block(
        db, project_id=project["id"], block_id="C-001",
        kind="content", level=1, title="正文", domain="",
        parent_title="", requirement="", score="", source="", order_idx=0,
    )
    updated = await update_block_content(db, block["id"], "新内容")
    assert updated["content"] == "新内容"
    assert updated["status"] == "done"


async def test_delete_blocks_by_project(db):
    project = await create_project(db, name="删除项目")
    await create_block(
        db, project_id=project["id"], block_id="H-001",
        kind="heading", level=1, title="章节", domain="",
        parent_title="", requirement="", score="", source="", order_idx=0,
    )
    await delete_blocks_by_project(db, project["id"])
    assert await list_blocks(db, project["id"]) == []


async def test_add_and_list_revisions(db):
    project = await create_project(db, name="修订项目")
    block = await create_block(
        db, project_id=project["id"], block_id="C-001", kind="content",
        level=1, title="章节", domain="", parent_title="",
        requirement="", score="", source="", order_idx=0,
    )
    rev1 = await add_revision(db, block_id_int=block["id"], content="第一版", summary="初稿", source="generate")
    assert rev1["revision_no"] == 1
    rev2 = await add_revision(db, block_id_int=block["id"], content="第二版", summary="修改", source="edit")
    assert rev2["revision_no"] == 2
    revisions = await list_revisions(db, block["id"])
    assert len(revisions) == 2


async def test_create_and_get_snapshot(db):
    project = await create_project(db, name="快照项目")
    payload = json.dumps([{"id": 1}])
    snap = await create_snapshot(db, project_id=project["id"], trigger="lock", snapshot_json=payload)
    assert snap["id"] is not None
    fetched = await get_snapshot(db, snap["id"])
    assert fetched["snapshot"] == payload
