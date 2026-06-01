"""
SQLite 连接管理与 DDL 初始化。

用法（在路由中）：
    async with get_db() as db:
        rows = await db.execute("SELECT * FROM projects")

DB_PATH 默认为 backend/ 同级目录下的 ge_solution.db，
测试可通过修改 db.DB_PATH = ":memory:" 切换为内存库。
"""

import os
from contextlib import asynccontextmanager

import aiosqlite

_this_dir = os.path.dirname(os.path.abspath(__file__))
DB_PATH: str = os.path.join(_this_dir, "data", "ge_solution.db")
DB_PATH = os.path.normpath(DB_PATH)

_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    status      TEXT DEFAULT 'init',
    deadline    TEXT,
    summary     TEXT,
    base_snapshot_id INTEGER,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    filename    TEXT,
    type        TEXT,
    file_path   TEXT,
    role        TEXT,
    parsed_at   DATETIME
);

CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    block_id    TEXT,
    kind        TEXT,
    level       INTEGER,
    title       TEXT,
    content     TEXT,
    domain      TEXT,
    parent_title TEXT,
    requirement TEXT,
    key_points  TEXT,
    veto_items  TEXT,
    bonus_items TEXT,
    score       TEXT,
    source      TEXT,
    order_idx   INTEGER,
    status      TEXT DEFAULT 'empty',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS block_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER REFERENCES blocks(id),
    revision_no INTEGER,
    content     TEXT,
    summary     TEXT,
    source      TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    trigger     TEXT,
    snapshot    TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db(conn: aiosqlite.Connection) -> None:
    """执行建表 DDL（CREATE TABLE IF NOT EXISTS，幂等）。"""
    await conn.executescript(_DDL)
    await conn.commit()
    # 迁移已有数据库：添加新列（如果不存在）
    cursor = await conn.execute("PRAGMA table_info(blocks)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    for col in ("key_points", "veto_items", "bonus_items"):
        if col not in existing_cols:
            await conn.execute(f"ALTER TABLE blocks ADD COLUMN {col} TEXT")
    await conn.commit()


@asynccontextmanager
async def get_db():
    """
    返回配置了 row_factory 的 aiosqlite 连接，用于 async with 上下文。
    row_factory = aiosqlite.Row 使 fetchone/fetchall 返回可按列名索引的 Row 对象。
    """
    if DB_PATH != ":memory:":
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn
