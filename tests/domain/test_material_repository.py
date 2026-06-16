"""MaterialRepository 集成测试 — 用内存 SQLite 验证素材与切片往返。"""

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from domain.material import Chunk, Material, MaterialRepository


# ─────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    """每个测试一个内存 SQLite 连接 + 一个预建项目。"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cursor = await conn.execute(
        "INSERT INTO projects (name) VALUES (?)", ("测试项目",)
    )
    await conn.commit()
    proj_id = cursor.lastrowid
    yield conn, proj_id
    await conn.close()


@pytest_asyncio.fixture
async def two_projects():
    """两个预建项目，用于跨项目隔离验证。"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    cur1 = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("A",))
    pa = cur1.lastrowid
    cur2 = await conn.execute("INSERT INTO projects (name) VALUES (?)", ("B",))
    pb = cur2.lastrowid
    await conn.commit()
    yield conn, pa, pb
    await conn.close()


# ─────────────────────────────────────────────────────────
# materials
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_materials(db):
    conn, proj_id = db
    repo = MaterialRepository(conn)

    m1 = await repo.create_material(
        project_id=proj_id, filename="a.docx", type_="docx",
        file_path="/tmp/a.docx", role="material", file_size=100,
    )
    m2 = await repo.create_material(
        project_id=proj_id, filename="b.pdf", type_="pdf",
        file_path="/tmp/b.pdf", role="spec", file_size=200,
    )

    assert isinstance(m1, Material) and isinstance(m2, Material)
    assert m1.id < m2.id

    items = await repo.list_materials(proj_id)
    assert len(items) == 2
    assert all(isinstance(x, Material) for x in items)
    assert [x.id for x in items] == sorted(x.id for x in items)
    assert items[0].filename == "a.docx"
    assert items[0].file_size == 100
    assert items[1].role == "spec"
    assert items[0].parse_status == "pending"


@pytest.mark.asyncio
async def test_delete_material(db):
    conn, proj_id = db
    repo = MaterialRepository(conn)
    m = await repo.create_material(
        project_id=proj_id, filename="x.txt", type_="txt",
        file_path="/tmp/x.txt", role="material",
    )
    ok = await repo.delete_material(m.id)
    assert ok is True
    assert await repo.list_materials(proj_id) == []
    # 再删一次 -> False
    assert await repo.delete_material(m.id) is False
    # 不存在的 id
    assert await repo.delete_material(99999) is False


@pytest.mark.asyncio
async def test_parse_status_transitions(db):
    conn, proj_id = db
    repo = MaterialRepository(conn)
    m = await repo.create_material(
        project_id=proj_id, filename="s.docx", type_="docx",
        file_path="/tmp/s.docx", role="material",
    )

    await repo.mark_parsing(m.id)
    items = await repo.list_materials(proj_id)
    assert items[0].parse_status == "parsing"
    assert items[0].parsed_at is None

    await repo.mark_parsed(m.id)
    items = await repo.list_materials(proj_id)
    assert items[0].parse_status == "done"
    assert items[0].parsed_at is not None

    await repo.mark_failed(m.id)
    items = await repo.list_materials(proj_id)
    assert items[0].parse_status == "failed"


# ─────────────────────────────────────────────────────────
# chunks
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_chunks_by_project(two_projects):
    conn, pa, pb = two_projects
    repo = MaterialRepository(conn)

    ma = await repo.create_material(
        project_id=pa, filename="a.docx", type_="docx",
        file_path="/tmp/a.docx", role="material",
    )
    mb = await repo.create_material(
        project_id=pb, filename="b.docx", type_="docx",
        file_path="/tmp/b.docx", role="material",
    )

    for i in range(3):
        c = await repo.create_chunk(ma.id, i, f"A-{i}")
        assert isinstance(c, Chunk)
        assert c.material_id == ma.id and c.chunk_index == i
    for i in range(3):
        await repo.create_chunk(mb.id, i, f"B-{i}")

    chunks_a = await repo.list_chunks_by_project(pa)
    assert len(chunks_a) == 3
    assert all(isinstance(c, Chunk) for c in chunks_a)
    assert [(c.material_id, c.chunk_index) for c in chunks_a] == [
        (ma.id, 0), (ma.id, 1), (ma.id, 2),
    ]
    assert [c.content for c in chunks_a] == ["A-0", "A-1", "A-2"]
    # list_chunks_by_project 不带 filename
    assert all(c.filename == "" for c in chunks_a)


@pytest.mark.asyncio
async def test_list_chunks_with_filename_and_by_materials(db):
    conn, proj_id = db
    repo = MaterialRepository(conn)

    m1 = await repo.create_material(
        project_id=proj_id, filename="one.docx", type_="docx",
        file_path="/tmp/one.docx", role="material",
    )
    m2 = await repo.create_material(
        project_id=proj_id, filename="two.docx", type_="docx",
        file_path="/tmp/two.docx", role="material",
    )
    await repo.create_chunk(m1.id, 0, "alpha")
    await repo.create_chunk(m1.id, 1, "beta")
    await repo.create_chunk(m2.id, 0, "gamma")

    with_fn = await repo.list_chunks_with_filename(proj_id)
    assert len(with_fn) == 3
    assert all(c.filename != "" for c in with_fn)
    fn_map = {(c.material_id, c.chunk_index): c.filename for c in with_fn}
    assert fn_map[(m1.id, 0)] == "one.docx"
    assert fn_map[(m2.id, 0)] == "two.docx"

    only_m1 = await repo.list_chunks_by_materials([m1.id])
    assert len(only_m1) == 2
    assert all(c.material_id == m1.id for c in only_m1)
    assert all(c.filename == "one.docx" for c in only_m1)

    assert await repo.list_chunks_by_materials([]) == []


@pytest.mark.asyncio
async def test_update_chunk_content_round_trip(db):
    conn, proj_id = db
    repo = MaterialRepository(conn)
    m = await repo.create_material(
        project_id=proj_id, filename="u.docx", type_="docx",
        file_path="/tmp/u.docx", role="material",
    )
    await repo.create_chunk(m.id, 0, "old")

    updated = await repo.update_chunk_content(m.id, 0, "new")
    assert isinstance(updated, Chunk)
    assert updated.content == "new"

    got = await repo.get_chunk(m.id, 0)
    assert got is not None
    assert got.content == "new"

    # 不存在的 chunk
    assert await repo.get_chunk(m.id, 99) is None


# ─────────────────────────────────────────────────────────
# dataclass 自检
# ─────────────────────────────────────────────────────────

def test_material_to_dict_from_dict_round_trip():
    m = Material(
        id=7, project_id=3, filename="x.docx", type="docx",
        file_path="/tmp/x.docx", role="material", file_size=42,
        parsed_at="2026-06-16T00:00:00", parse_status="done",
    )
    assert Material.from_dict(m.to_dict()) == m


def test_chunk_to_dict_from_dict_round_trip():
    c = Chunk(id=11, material_id=5, chunk_index=2, content="hello", filename="f.docx")
    assert Chunk.from_dict(c.to_dict()) == c
