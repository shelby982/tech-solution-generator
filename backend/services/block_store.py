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
    target_pages: int | None = None,
) -> dict:
    cursor = await db.execute(
        "INSERT INTO projects (name, deadline, target_pages) VALUES (?, ?, ?)",
        (name, deadline, target_pages),
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
    allowed = {"name", "status", "deadline", "summary", "base_snapshot_id", "target_pages"}
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
    file_size: int = 0,
) -> dict:
    cursor = await db.execute(
        "INSERT INTO materials (project_id, filename, type, file_path, role, file_size) VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, filename, type_, file_path, role, file_size),
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


async def delete_material(db: aiosqlite.Connection, material_id: int) -> bool:
    cursor = await db.execute("SELECT file_path FROM materials WHERE id = ?", (material_id,))
    row = await cursor.fetchone()
    if row is None:
        return False
    await db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
    await db.commit()
    return True


async def update_material_parsed(
    db: aiosqlite.Connection,
    material_id: int,
) -> None:
    await db.execute(
        "UPDATE materials SET parsed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (material_id,),
    )
    await db.commit()


async def update_material_parse_status(
    db: aiosqlite.Connection,
    material_id: int,
    status: str,
) -> None:
    """status: pending | parsing | done | failed"""
    if status == "done":
        await db.execute(
            "UPDATE materials SET parse_status = ?, parsed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, material_id),
        )
    else:
        await db.execute(
            "UPDATE materials SET parse_status = ? WHERE id = ?",
            (status, material_id),
        )
    await db.commit()


async def update_material_role(
    db: aiosqlite.Connection,
    material_id: int,
    role: str,
) -> None:
    """允许用户修改已上传文件的类型 role：spec / main_rfp / scoring / evaluation / source / requirement。"""
    await db.execute(
        "UPDATE materials SET role = ? WHERE id = ?",
        (role, material_id),
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
    key_points: str = "",
    veto_items: str = "",
    bonus_items: str = "",
    score_items: str = "",
    evidence_required: str = "",
    constraint_level: str = "",
    indicators: str = "",
) -> dict:
    cursor = await db.execute(
        """INSERT INTO blocks
           (project_id, block_id, kind, level, title, domain, parent_title,
            requirement, key_points, veto_items, bonus_items,
            score_items, evidence_required, constraint_level, indicators,
            score, source, order_idx, content, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (project_id, block_id, kind, level, title, domain, parent_title,
         requirement, key_points, veto_items, bonus_items,
         score_items, evidence_required, constraint_level, indicators,
         score, source, order_idx, content, status),
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


async def update_block_source(
    db: aiosqlite.Connection,
    project_id: int,
    block_id: str,
    sources: list,
) -> None:
    """按 (project_id, block_id) 更新 block.source（JSON 字符串）。

    用于沈括匹配阶段把 matches 落库；不存在时静默返回（占位 block 应已被 parse 节点 upsert）。
    """
    import json as _json
    payload = _json.dumps(sources, ensure_ascii=False)
    await db.execute(
        """UPDATE blocks
           SET source = ?, updated_at = CURRENT_TIMESTAMP
           WHERE project_id = ? AND block_id = ?""",
        (payload, project_id, block_id),
    )
    await db.commit()


async def upsert_outline_block(
    db: aiosqlite.Connection,
    project_id: int,
    block_id: str,
    *,
    level: int,
    title: str,
    order_idx: int,
    matrix: dict | None = None,
) -> dict:
    """按 (project_id, block_id) upsert 大纲提炼结果。

    - matrix=None：占位（解析阶段拿到 toc 后即可建空壳，让取消/刷新仍能看到）
    - matrix=dict：把 8 字段写入；status 升级为 'outline_done'

    title / level / order_idx 始终覆盖（解析端是权威源）。
    """
    cursor = await db.execute(
        "SELECT id, status FROM blocks WHERE project_id = ? AND block_id = ?",
        (project_id, block_id),
    )
    row = await cursor.fetchone()

    if matrix is None:
        if row:
            await db.execute(
                """UPDATE blocks
                   SET title = ?, level = ?, order_idx = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (title, level, order_idx, row["id"]),
            )
            await db.commit()
            return await get_block(db, row["id"])
        cursor = await db.execute(
            """INSERT INTO blocks
               (project_id, block_id, kind, level, title, domain, parent_title,
                requirement, key_points, veto_items, bonus_items,
                score_items, evidence_required, constraint_level, indicators,
                score, source, order_idx, content, status)
               VALUES (?, ?, 'content', ?, ?, ?, '', '', '', '', '', '', '',
                       'recommended', '', '', '[]', ?, '', 'empty')""",
            (project_id, block_id, level, title, title, order_idx),
        )
        await db.commit()
        return await get_block(db, cursor.lastrowid)

    # matrix 写入
    requirement = str(matrix.get("requirement") or "")
    key_points = str(matrix.get("key_points") or "")
    veto_items = str(matrix.get("veto_items") or "")
    bonus_items = str(matrix.get("bonus_items") or "")
    score_items = str(matrix.get("score_items") or "")
    evidence_required = str(matrix.get("evidence_required") or "")
    constraint_level = str(matrix.get("constraint_level") or "recommended")
    indicators = str(matrix.get("indicators") or "")

    if row:
        await db.execute(
            """UPDATE blocks
               SET title = ?, level = ?, order_idx = ?,
                   requirement = ?, key_points = ?, veto_items = ?,
                   bonus_items = ?, score_items = ?, evidence_required = ?,
                   constraint_level = ?, indicators = ?,
                   status = 'outline_done',
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (title, level, order_idx,
             requirement, key_points, veto_items,
             bonus_items, score_items, evidence_required,
             constraint_level, indicators,
             row["id"]),
        )
        await db.commit()
        return await get_block(db, row["id"])

    cursor = await db.execute(
        """INSERT INTO blocks
           (project_id, block_id, kind, level, title, domain, parent_title,
            requirement, key_points, veto_items, bonus_items,
            score_items, evidence_required, constraint_level, indicators,
            score, source, order_idx, content, status)
           VALUES (?, ?, 'content', ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?,
                   '', '[]', ?, '', 'outline_done')""",
        (project_id, block_id, level, title, title,
         requirement, key_points, veto_items, bonus_items,
         score_items, evidence_required, constraint_level, indicators,
         order_idx),
    )
    await db.commit()
    return await get_block(db, cursor.lastrowid)


async def sync_outline_placeholders(
    db: aiosqlite.Connection,
    project_id: int,
    sections,
) -> dict:
    """把当前 toc 同步进 blocks 表：清理不在 toc 的孤儿行 + 逐节 upsert 占位。

    parse 与 outline_draft 各调一次（目录换版后 block_id 会整体变化）。

    清理保护：**只删 content 为空的行**。目录重排后若该行已有正文，宁可留一条
    脏数据也不删用户内容。

    sections: 可迭代的 Section-like（需有 .id / .level / .title），顺序即 order_idx。

    返回 ``{"removed": int, "created": int}``。
    """
    sections = list(sections)
    toc_ids = {sec.id for sec in sections}

    existing = await list_blocks(db, project_id)
    stale_ids = [
        b.get("id") for b in existing
        if b.get("block_id") not in toc_ids and not (b.get("content") or "").strip()
    ]
    for stale_pk in stale_ids:
        await db.execute("DELETE FROM blocks WHERE id = ?", (stale_pk,))
    if stale_ids:
        await db.commit()

    for idx, sec in enumerate(sections):
        await upsert_outline_block(
            db,
            project_id=project_id,
            block_id=sec.id,
            level=int(sec.level or 1),
            title=sec.title or sec.id,
            order_idx=idx,
            matrix=None,
        )

    return {"removed": len(stale_ids), "created": len(sections)}


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


async def list_project_revisions(
    db: aiosqlite.Connection,
    project_id: int,
) -> list[dict]:
    sql = """
        SELECT br.id, br.block_id, b.title AS block_title,
               b.block_id AS block_code, b.order_idx,
               br.revision_no, br.source, br.summary, br.created_at,
               br.content AS content,
               LAG(br.content) OVER (
                 PARTITION BY br.block_id ORDER BY br.revision_no
               ) AS prev_content,
               LENGTH(br.content) AS char_count,
               LENGTH(br.content) - COALESCE(
                 LAG(LENGTH(br.content)) OVER (
                   PARTITION BY br.block_id ORDER BY br.revision_no
                 ), 0
               ) AS char_delta
        FROM block_revisions br
        JOIN blocks b ON br.block_id = b.id
        WHERE b.project_id = ?
        ORDER BY br.created_at DESC
    """
    cursor = await db.execute(sql, (project_id,))
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def create_chunk(db, material_id: int, chunk_index: int, content: str) -> dict:
    cursor = await db.execute(
        "INSERT INTO material_chunks (material_id, chunk_index, content) VALUES (?, ?, ?)",
        (material_id, chunk_index, content),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM material_chunks WHERE id = ?", (cursor.lastrowid,))).fetchone()
    return dict(row)

async def list_chunks_by_project(db, project_id: int) -> list[dict]:
    cursor = await db.execute(
        """SELECT mc.* FROM material_chunks mc
           JOIN materials m ON mc.material_id = m.id
           WHERE m.project_id = ? ORDER BY mc.material_id, mc.chunk_index""",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_chunks_with_filename(db, project_id: int) -> list[dict]:
    cursor = await db.execute(
        """SELECT mc.*, m.filename FROM material_chunks mc
           JOIN materials m ON mc.material_id = m.id
           WHERE m.project_id = ? ORDER BY mc.material_id, mc.chunk_index""",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_chunks_by_materials(db, material_ids: list[int]) -> list[dict]:
    """按 material_id 列表过滤切片，附带 filename，返回顺序按 material_id 与 chunk_index 升序。"""
    if not material_ids:
        return []
    placeholders = ",".join("?" * len(material_ids))
    cursor = await db.execute(
        f"""SELECT mc.*, m.filename FROM material_chunks mc
            JOIN materials m ON mc.material_id = m.id
            WHERE mc.material_id IN ({placeholders})
            ORDER BY mc.material_id, mc.chunk_index""",
        tuple(material_ids),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_chunk(db, material_id: int, chunk_index: int) -> dict | None:
    cursor = await db.execute(
        "SELECT * FROM material_chunks WHERE material_id = ? AND chunk_index = ?",
        (material_id, chunk_index),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_chunk_content(db, material_id: int, chunk_index: int, content: str) -> dict | None:
    await db.execute(
        "UPDATE material_chunks SET content = ? WHERE material_id = ? AND chunk_index = ?",
        (content, material_id, chunk_index),
    )
    await db.commit()
    return await get_chunk(db, material_id, chunk_index)

async def update_block_requirement(db, block_id_int: int, requirement: str) -> dict | None:
    await db.execute(
        "UPDATE blocks SET requirement = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (requirement, block_id_int),
    )
    await db.commit()
    return await get_block(db, block_id_int)
