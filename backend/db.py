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
    target_pages INTEGER,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    filename    TEXT,
    type        TEXT,
    file_path   TEXT,
    role        TEXT,
    file_size   INTEGER,
    parsed_at   DATETIME,
    parse_status TEXT DEFAULT 'pending'
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
    score_items TEXT,
    evidence_required TEXT,
    constraint_level TEXT,
    indicators  TEXT,
    score       TEXT,
    source      TEXT,
    order_idx   INTEGER,
    run_thread_id TEXT,
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

CREATE TABLE IF NOT EXISTS workflow_runs (
    thread_id   TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    stage       TEXT NOT NULL DEFAULT 'idle',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL REFERENCES workflow_runs(thread_id),
    block_id        TEXT NOT NULL,
    agent           TEXT NOT NULL,
    score           INTEGER,
    issues          TEXT,
    strengths       TEXT,
    error           TEXT DEFAULT '',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reviews_thread_block ON reviews(thread_id, block_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_project ON workflow_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_blocks_project ON blocks(project_id);
CREATE INDEX IF NOT EXISTS idx_block_revisions_block ON block_revisions(block_id);

CREATE TABLE IF NOT EXISTS llm_usage (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    call_site              TEXT NOT NULL DEFAULT '',
    provider               TEXT NOT NULL DEFAULT '',
    model                  TEXT NOT NULL DEFAULT '',
    streaming              INTEGER NOT NULL DEFAULT 0,
    input_tokens           INTEGER,
    output_tokens          INTEGER,
    cache_read_tokens      INTEGER,
    cache_creation_tokens  INTEGER,
    total_tokens           INTEGER,
    ok                     INTEGER NOT NULL DEFAULT 1,
    error                  TEXT DEFAULT '',
    latency_ms             INTEGER,
    created_at             DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_site ON llm_usage(call_site, model);
"""


async def init_db(conn: aiosqlite.Connection) -> None:
    """执行建表 DDL（CREATE TABLE IF NOT EXISTS，幂等）。"""
    await conn.executescript(_DDL)
    await conn.commit()
    # 迁移已有数据库：添加新列（如果不存在）
    cursor = await conn.execute("PRAGMA table_info(blocks)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    # run_thread_id：该行属于哪一次 workflow run。老库的行留 NULL（不回填），
    # 读路径在「该 run 一行都没有」时退回显示全部行，见 block_store.list_blocks。
    for col in ("key_points", "veto_items", "bonus_items",
                "score_items", "evidence_required", "constraint_level", "indicators",
                "run_thread_id"):
        if col not in existing_cols:
            await conn.execute(f"ALTER TABLE blocks ADD COLUMN {col} TEXT")
    cursor = await conn.execute("PRAGMA table_info(materials)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if "file_size" not in existing_cols:
        await conn.execute("ALTER TABLE materials ADD COLUMN file_size INTEGER")
    if "parse_status" not in existing_cols:
        await conn.execute("ALTER TABLE materials ADD COLUMN parse_status TEXT DEFAULT 'pending'")
    cursor = await conn.execute("PRAGMA table_info(projects)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if "target_pages" not in existing_cols:
        await conn.execute("ALTER TABLE projects ADD COLUMN target_pages INTEGER")
    # 索引建在加列之后：老库的 blocks 表先执行上面的 ALTER 才有 run_thread_id，
    # 放进 _DDL 会在老库上引用了尚不存在的列而直接报错。
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blocks_project_run ON blocks(project_id, run_thread_id)"
    )
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
