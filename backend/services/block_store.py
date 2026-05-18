"""
SQLite CRUD 封装 — 所有业务表的 async 操作。
所有函数接受 aiosqlite.Connection 作为第一参数。
Row 对象通过 dict(row) 转为普通字典后返回。
"""

import aiosqlite


# ══════════════════════════════════════════════════
# projects
# ══════════════════════════════════════════════════

async def create_project(
    db: aiosqlite.Connection,
    name: str,
    deadline: str | None = None,
) -> dict:
    cursor = await db.execute(
        "INSERT INTO projects (name, deadline) VALUES (?, ?)",
        (name, deadline),
    )
    await db.commit()
    return await get_project(db, cursor.lastrowid)


async def get_project(
    db: aiosqlite.Connection,
    project_id: int,
) -> dict | None:
    cursor = await db.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_projects(
    db: aiosqlite.Connection,
    status: str | None = None,
) -> list[dict]:
    if status is not None:
        cursor = await db.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_project(
    db: aiosqlite.Connection,
    project_id: int,
    **kwargs,
) -> dict | None:
    allowed = {"name", "status", "deadline", "summary", "base_snapshot_id"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return await get_project(db, project_id)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [project_id]
    await db.execute(
        f"UPDATE projects SET {set_clause} WHERE id = ?", values,
    )
    await db.commit()
    return await get_project(db, project_id)


# ══════════════════════════════════════════════════
# materials
# ══════════════════════════════════════════════════

async def create_material(
    db: aiosqlite.Connection,
    project_id: int,
    filename: str,
    type_: str,
    file_path: str,
    role: str,
) -> dict:
    cursor = await db.execute(
        "INSERT INTO materials (project_id, filename, type, file_path, role) VALUES (?, ?, ?, ?, ?)",
        (project_id, filename, type_, file_path, role),
    )
    await db.commit()
    row_cursor = await db.execute(
        "SELECT * FROM materials WHERE id = ?", (cursor.lastrowid,)
    )
    row = await row_cursor.fetchone()
    if row is None:
        raise RuntimeError(f"材料记录插入失败，lastrowid={cursor.lastrowid}")
    return dict(row)


async def list_materials(
    db: aiosqlite.Connection,
    project_id: int,
) -> list[dict]:
    cursor = await db.execute(
        "SELECT * FROM materials WHERE project_id = ? ORDER BY id", (project_id,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_material_parsed(
    db: aiosqlite.Connection,
    material_id: int,
) -> None:
    await db.execute(
        "UPDATE materials SET parsed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (material_id,),
    )
    await db.commit()


# ══════════════════════════════════════════════════
# blocks
# ══════════════════════════════════════════════════

async def create_block(
    db: aiosqlite.Connection,
    project_id: int,
    block_id: str,
    kind: str,
    level: int,
    title: str,
    domain: str,
    parent_title: str,
    requirement: str,
    score: str,
    source: str,
    order_idx: int,
    content: str = "",
    status: str = "empty",
) -> dict:
    cursor = await db.execute(
        """INSERT INTO blocks
           (project_id, block_id, kind, level, title, domain, parent_title,
            requirement, score, source, order_idx, content, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (project_id, block_id, kind, level, title, domain, parent_title,
         requirement, score, source, order_idx, content, status),
    )
    await db.commit()
    return await get_block(db, cursor.lastrowid)


async def list_blocks(
    db: aiosqlite.Connection,
    project_id: int,
) -> list[dict]:
    cursor = await db.execute(
        "SELECT * FROM blocks WHERE project_id = ? ORDER BY order_idx", (project_id,)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_block(
    db: aiosqlite.Connection,
    block_id_int: int,
) -> dict | None:
    cursor = await db.execute(
        "SELECT * FROM blocks WHERE id = ?", (block_id_int,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_block_content(
    db: aiosqlite.Connection,
    block_id_int: int,
    content: str,
    status: str = "done",
) -> dict | None:
    await db.execute(
        """UPDATE blocks
           SET content = ?, status = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (content, status, block_id_int),
    )
    await db.commit()
    return await get_block(db, block_id_int)


async def update_block_status(
    db: aiosqlite.Connection,
    block_id_int: int,
    status: str,
) -> None:
    await db.execute(
        "UPDATE blocks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, block_id_int),
    )
    await db.commit()


async def delete_blocks_by_project(
    db: aiosqlite.Connection,
    project_id: int,
) -> None:
    await db.execute("DELETE FROM blocks WHERE project_id = ?", (project_id,))
    await db.commit()


# ══════════════════════════════════════════════════
# block_revisions
# ══════════════════════════════════════════════════

async def add_revision(
    db: aiosqlite.Connection,
    block_id_int: int,
    content: str,
    summary: str,
    source: str,
) -> dict:
    await db.execute("BEGIN IMMEDIATE")
    try:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(revision_no), 0) FROM block_revisions WHERE block_id = ?",
            (block_id_int,),
        )
        row = await cursor.fetchone()
        next_no = row[0] + 1
        cursor = await db.execute(
            "INSERT INTO block_revisions (block_id, revision_no, content, summary, source) VALUES (?, ?, ?, ?, ?)",
            (block_id_int, next_no, content, summary, source),
        )
        new_id = cursor.lastrowid
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    row_cursor = await db.execute(
        "SELECT * FROM block_revisions WHERE id = ?", (new_id,)
    )
    result = await row_cursor.fetchone()
    return dict(result)


async def list_revisions(
    db: aiosqlite.Connection,
    block_id_int: int,
) -> list[dict]:
    cursor = await db.execute(
        "SELECT * FROM block_revisions WHERE block_id = ? ORDER BY revision_no",
        (block_id_int,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# ══════════════════════════════════════════════════
# project_snapshots
# ══════════════════════════════════════════════════

async def create_snapshot(
    db: aiosqlite.Connection,
    project_id: int,
    trigger: str,
    snapshot_json: str,
) -> dict:
    cursor = await db.execute(
        "INSERT INTO project_snapshots (project_id, trigger, snapshot) VALUES (?, ?, ?)",
        (project_id, trigger, snapshot_json),
    )
    await db.commit()
    return await get_snapshot(db, cursor.lastrowid)


async def get_snapshot(
    db: aiosqlite.Connection,
    snapshot_id: int,
) -> dict | None:
    cursor = await db.execute(
        "SELECT * FROM project_snapshots WHERE id = ?", (snapshot_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None
