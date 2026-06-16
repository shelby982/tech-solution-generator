"""ProposalRepository 集成测试 — 用内存 SQLite 验证 BlockOutput 往返。"""

import json

import aiosqlite
import pytest
import pytest_asyncio

from db import init_db
from domain.proposal import Block, BlockOutput, ProposalRepository, Source
from services.block_store import create_block


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


async def _create_empty_block(
    conn: aiosqlite.Connection,
    project_id: int,
    block_id: str = "s1",
    title: str = "技术方案",
    order_idx: int = 0,
    requirement: str = "",
) -> dict:
    """直接调 services.block_store.create_block 建一个空 block。"""
    return await create_block(
        conn,
        project_id=project_id,
        block_id=block_id,
        kind="tech",
        level=1,
        title=title,
        domain="",
        parent_title="",
        requirement=requirement,
        score="",
        source="",
        order_idx=order_idx,
    )


# ─────────────────────────────────────────────────────────
# save_block_output
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_block_output_writes_content_status_sources(db):
    conn, proj_id = db
    repo = ProposalRepository(conn)
    raw = await _create_empty_block(conn, proj_id)
    db_id = int(raw["id"])

    output = BlockOutput(
        block_id="s1",
        kind="tech",
        needs_diagram=False,
        outline="一、要点 二、方案",
        content="正文 1234",
        sources=[
            Source(material_id=10, chunk_index=2, snippet="片段 A"),
            Source(material_id=11, chunk_index=0, snippet="片段 B"),
        ],
    )
    saved = await repo.save_block_output(db_id, output)
    assert isinstance(saved, Block)
    assert saved.content == "正文 1234"
    assert saved.status == "done"
    assert len(saved.sources) == 2
    assert saved.sources[0].material_id == 10
    assert saved.sources[0].chunk_index == 2
    assert saved.sources[0].snippet == "片段 A"
    assert saved.sources[1].material_id == 11

    got = await repo.get_block(db_id)
    assert got is not None
    assert got.content == "正文 1234"
    assert got.status == "done"
    assert [s.to_dict() for s in got.sources] == [
        s.to_dict() for s in output.sources
    ]


@pytest.mark.asyncio
async def test_save_block_output_with_empty_sources(db):
    conn, proj_id = db
    repo = ProposalRepository(conn)
    raw = await _create_empty_block(conn, proj_id)
    db_id = int(raw["id"])

    output = BlockOutput(
        block_id="s1", kind="tech", content="无引用正文", sources=[]
    )
    saved = await repo.save_block_output(db_id, output)
    assert saved is not None
    assert saved.content == "无引用正文"
    assert saved.status == "done"
    assert saved.sources == []

    got = await repo.get_block(db_id)
    assert got is not None
    assert got.sources == []


@pytest.mark.asyncio
async def test_save_block_output_persists_sources_as_json(db):
    conn, proj_id = db
    repo = ProposalRepository(conn)
    raw = await _create_empty_block(conn, proj_id)
    db_id = int(raw["id"])

    output = BlockOutput(
        block_id="s1",
        content="正文",
        sources=[Source(material_id=5, chunk_index=1, snippet="hi")],
    )
    await repo.save_block_output(db_id, output)

    cursor = await conn.execute("SELECT source FROM blocks WHERE id = ?", (db_id,))
    row = await cursor.fetchone()
    assert row is not None
    raw_json = row[0]
    parsed = json.loads(raw_json)
    assert isinstance(parsed, list)
    assert parsed == [{"material_id": 5, "chunk_index": 1, "snippet": "hi"}]


@pytest.mark.asyncio
async def test_save_block_output_updates_updated_at(db):
    """save_block_output 应触发 updated_at（通过 update_block_content 实现）。"""
    conn, proj_id = db
    repo = ProposalRepository(conn)
    raw = await _create_empty_block(conn, proj_id)
    db_id = int(raw["id"])
    original_updated = raw["updated_at"]

    output = BlockOutput(block_id="s1", content="新正文")
    saved = await repo.save_block_output(db_id, output)
    assert saved is not None
    # updated_at 应非空（CURRENT_TIMESTAMP 已被 update_block_content 触发）
    assert saved.updated_at is not None
    # 最低限度：updated_at 不应早于 original_updated（字符串比较 ISO 时间戳安全）
    assert saved.updated_at >= original_updated


# ─────────────────────────────────────────────────────────
# list_blocks / get_block
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_blocks_returns_ordered_by_order_idx(db):
    conn, proj_id = db
    repo = ProposalRepository(conn)
    await _create_empty_block(conn, proj_id, block_id="b2", title="B", order_idx=2)
    await _create_empty_block(conn, proj_id, block_id="b0", title="A", order_idx=0)
    await _create_empty_block(conn, proj_id, block_id="b1", title="C", order_idx=1)

    blocks = await repo.list_blocks(proj_id)
    assert len(blocks) == 3
    assert all(isinstance(b, Block) for b in blocks)
    assert [b.order_idx for b in blocks] == [0, 1, 2]
    assert [b.block_id for b in blocks] == ["b0", "b1", "b2"]


@pytest.mark.asyncio
async def test_get_block_returns_none_for_missing_id(db):
    conn, _ = db
    repo = ProposalRepository(conn)
    assert await repo.get_block(99999) is None


# ─────────────────────────────────────────────────────────
# mark_block_failed
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_block_failed_sets_status(db):
    conn, proj_id = db
    repo = ProposalRepository(conn)
    raw = await _create_empty_block(conn, proj_id)
    db_id = int(raw["id"])

    await repo.mark_block_failed(db_id)
    got = await repo.get_block(db_id)
    assert got is not None
    assert got.status == "failed"


# ─────────────────────────────────────────────────────────
# Block.from_row 健壮性
# ─────────────────────────────────────────────────────────

def test_block_from_row_handles_missing_source_column():
    base = {
        "id": 1, "project_id": 2, "block_id": "s1",
        "title": "T", "level": 1, "kind": "tech", "order_idx": 0,
    }
    # source = None
    row = {**base, "source": None}
    b = Block.from_row(row)
    assert b.sources == []
    # source = ""
    row = {**base, "source": ""}
    b = Block.from_row(row)
    assert b.sources == []
    # source = 非法 JSON
    row = {**base, "source": "not-json"}
    b = Block.from_row(row)
    assert b.sources == []
    # source = 不是数组的 JSON
    row = {**base, "source": '{"a": 1}'}
    b = Block.from_row(row)
    assert b.sources == []
    # 缺失 source key
    b = Block.from_row(base)
    assert b.sources == []


def test_block_from_row_parses_valid_source_json():
    row = {
        "id": 1, "project_id": 2, "block_id": "s1",
        "title": "T", "level": 1, "kind": "tech", "order_idx": 0,
        "source": json.dumps([
            {"material_id": 7, "chunk_index": 3, "snippet": "x"},
            {"material_id": 8, "chunk_index": 4, "snippet": "y"},
        ]),
    }
    b = Block.from_row(row)
    assert len(b.sources) == 2
    assert b.sources[0].material_id == 7
    assert b.sources[1].snippet == "y"


# ─────────────────────────────────────────────────────────
# dataclass 自检
# ─────────────────────────────────────────────────────────

def test_source_to_dict_from_dict_round_trip():
    s = Source(material_id=3, chunk_index=5, snippet="片段")
    assert Source.from_dict(s.to_dict()) == s


def test_block_output_to_dict_from_dict_round_trip():
    o = BlockOutput(
        block_id="s2",
        kind="letter",
        needs_diagram=True,
        outline="OL",
        content="C",
        sources=[
            Source(material_id=1, chunk_index=0, snippet="a"),
            Source(material_id=2, chunk_index=1, snippet="b"),
        ],
    )
    assert BlockOutput.from_dict(o.to_dict()) == o
