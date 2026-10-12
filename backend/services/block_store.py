"""
SQLite CRUD 封装 — 所有业务表的 async 操作。
所有函数接受 aiosqlite.Connection 作为第一参数。
Row 对象通过 dict(row) 转为普通字典后返回。
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


# 材料角色优先级（列表与派生输入共用一个顺序）：
# 主招标/规范书类排最前 —— 它定义了章节骨架，也是 digest 8,000 字预算最该先吃的部分；
# 评分表/评审要素表随后（多用于按评分项拆章）。同档内仍按上传顺序（id）稳定排序。
# 之前统一是 `ORDER BY id`：把一份老的素材文件改成 spec，它会插队到所有规范书之前。
ROLE_PRIORITY_SQL = """
    CASE role
        WHEN 'spec' THEN 0
        WHEN 'main_rfp' THEN 0
        WHEN 'requirement' THEN 0
        WHEN 'scoring' THEN 1
        WHEN 'evaluation' THEN 2
        ELSE 3
    END, id
"""


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
        f"SELECT * FROM materials WHERE project_id = ? ORDER BY {ROLE_PRIORITY_SQL}",
        (project_id,)
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
    run_thread_id: str | None = None,
    *,
    fallback_all: bool = False,
) -> list[dict]:
    """按项目取 blocks，可选按 run 归属过滤。

    - ``run_thread_id=None``：不过滤，等价改造前的行为。
    - ``run_thread_id="<tid>"``：只取该次 run 写的行。

    ``fallback_all=True`` 时，若该 run 在本项目一行都没有（老项目、或 run 还没
    落库），退回返回全部行 —— 否则老项目页面会突然全空。

    **``fallback_all`` 只允许读路径用**：写路径（sync_outline_placeholders、
    extract 的跳过判断）必须严格，退回会拿到别的 run 的行，sync 会把它们误判成
    自己的孤儿行删掉。

    背景：``block_id`` 是位置派生的（parse 给 s1..sN，目录派生给 s1/s1.1），两次
    run 的顶层 id 会撞车、行在同一个 project_id 下长期共存，所以需要这层归属过滤。
    """
    if run_thread_id is None:
        cursor = await db.execute(
            "SELECT * FROM blocks WHERE project_id = ? ORDER BY order_idx", (project_id,)
        )
    else:
        cursor = await db.execute(
            """SELECT * FROM blocks
                WHERE project_id = ?
                  AND (run_thread_id = ?
                       OR (? AND NOT EXISTS (
                             SELECT 1 FROM blocks
                              WHERE project_id = ? AND run_thread_id = ?)))
                ORDER BY order_idx""",
            (project_id, run_thread_id,
             1 if fallback_all else 0, project_id, run_thread_id),
        )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def current_run_thread_id(
    db: aiosqlite.Connection,
    project_id: int,
) -> str | None:
    """本项目最新一次 workflow run 的 thread_id；一次都没跑过则返回 None。"""
    cursor = await db.execute(
        "SELECT thread_id FROM workflow_runs WHERE project_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


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


def _is_same_section(row, title: str, level: int, order_idx: int) -> bool:
    """旧行的坐标是否与新写入的完全一致 —— 一致才认为真的是同一节重跑。

    block_id 是**位置派生**的（parse 给 s1..sN 流水号，派生目录给 s1/s1.1/…），
    换个文件顺序、换一组文件，同一个 id 就指向另一节。坐标三者全同是「同一节」
    唯一可用的判据，读不出来（NULL / 脏值）时按「不是同一节」处理，宁可清也不要串。
    """
    try:
        return (
            row["title"] == title
            and int(row["level"]) == int(level)
            and int(row["order_idx"]) == int(order_idx)
        )
    except (TypeError, ValueError):
        return False


async def upsert_outline_block(
    db: aiosqlite.Connection,
    project_id: int,
    block_id: str,
    *,
    level: int,
    title: str,
    order_idx: int,
    matrix: dict | None = None,
    run_thread_id: str | None = None,
) -> dict:
    """按 (project_id, block_id, run_thread_id) upsert 大纲提炼结果。

    - matrix=None：占位（解析阶段拿到 toc 后即可建空壳，让取消/刷新仍能看到）
    - matrix=dict：把 8 字段写入；status 升级为 'outline_done'

    title / level / order_idx 始终覆盖（解析端是权威源）。

    run_thread_id：该行归属哪一次 workflow run。block_id 是位置派生的，两次 run
    的顶层 id 会撞车 —— 带上归属，新 run 才不会改写上一次 run 的行。
    """
    cursor = await db.execute(
        "SELECT id, status, title, level, order_idx FROM blocks "
        "WHERE project_id = ? AND block_id = ? AND run_thread_id IS ?",
        (project_id, block_id, run_thread_id),
    )
    row = await cursor.fetchone()

    if matrix is None:
        if row:
            if _is_same_section(row, title, level, order_idx):
                await db.execute(
                    """UPDATE blocks
                       SET title = ?, level = ?, order_idx = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (title, level, order_idx, row["id"]),
                )
            else:
                # 同一个 block_id 换了节：旧行的 8 字段属于上一节，不清就会让新章节
                # 顶着旧文档的应标要求显示（实测：目录里的「总则」挂着上一份 PDF
                # 某章的 requirement）。content（正文）不动 —— 沿用
                # sync_outline_placeholders 的约定，宁可留一条脏数据也不删用户内容。
                await db.execute(
                    """UPDATE blocks
                       SET title = ?, level = ?, order_idx = ?,
                           requirement = '', key_points = '', veto_items = '',
                           bonus_items = '', score_items = '', evidence_required = '',
                           constraint_level = 'recommended', indicators = '',
                           error = '', error_kind = '',
                           score = '[]', status = 'empty',
                           updated_at = CURRENT_TIMESTAMP
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
                score, source, order_idx, content, status, run_thread_id)
               VALUES (?, ?, 'content', ?, ?, ?, '', '', '', '', '', '', '',
                       'recommended', '', '', '[]', ?, '', 'empty', ?)""",
            (project_id, block_id, level, title, title, order_idx, run_thread_id),
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
                   error = ?, error_kind = ?,
                   status = 'outline_done',
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (title, level, order_idx,
             requirement, key_points, veto_items,
             bonus_items, score_items, evidence_required,
             constraint_level, indicators,
             str(matrix.get("error") or ""), str(matrix.get("error_kind") or ""),
             row["id"]),
        )
        await db.commit()
        return await get_block(db, row["id"])

    cursor = await db.execute(
        """INSERT INTO blocks
           (project_id, block_id, kind, level, title, domain, parent_title,
            requirement, key_points, veto_items, bonus_items,
            score_items, evidence_required, constraint_level, indicators,
            error, error_kind,
            score, source, order_idx, content, status, run_thread_id)
           VALUES (?, ?, 'content', ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   '', '[]', ?, '', 'outline_done', ?)""",
        (project_id, block_id, level, title, title,
         requirement, key_points, veto_items, bonus_items,
         score_items, evidence_required, constraint_level, indicators,
         str(matrix.get("error") or ""), str(matrix.get("error_kind") or ""),
         order_idx, run_thread_id),
    )
    await db.commit()
    return await get_block(db, cursor.lastrowid)


async def sync_outline_placeholders(
    db: aiosqlite.Connection,
    project_id: int,
    sections,
    run_thread_id: str | None = None,
) -> dict:
    """把当前 toc 同步进 blocks 表：清理不在 toc 的孤儿行 + 逐节 upsert 占位。

    parse 与 outline_draft 各调一次（目录换版后 block_id 会整体变化）。

    ``run_thread_id`` 限定同步范围：只清理、只 upsert 本次 run 的行。其他 run 的
    行原样留着（前端按当前 run 过滤，历史行只是不显示）。**这里不做 fallback**：
    退回拿到别的 run 的行会把它们当成本次 run 的孤儿行删掉。

    清理保护：**只删 content 为空的行**。目录重排后若该行已有正文，宁可留一条
    脏数据也不删用户内容。

    sections: 可迭代的 Section-like（需有 .id / .level / .title），顺序即 order_idx。

    返回 ``{"removed": int, "created": int, "kept": int}``。``kept`` 是被保护下来
    的孤儿行数（不在 toc 但有正文），仅供观测：这个数长期 > 0 说明目录换版后
    有正文留在了错位的节上。
    """
    sections = list(sections)
    toc_ids = {sec.id for sec in sections}

    existing = await list_blocks(db, project_id, run_thread_id)
    stale_ids = [
        b.get("id") for b in existing
        if b.get("block_id") not in toc_ids and not (b.get("content") or "").strip()
    ]
    for stale_pk in stale_ids:
        await db.execute("DELETE FROM blocks WHERE id = ?", (stale_pk,))
    if stale_ids:
        await db.commit()

    kept = [
        b for b in existing
        if b.get("block_id") not in toc_ids and (b.get("content") or "").strip()
    ]
    if kept:
        logger.info(
            "sync_outline_placeholders：项目 %s 有 %d 个不在目录内的章节保留了正文（block_id=%s），未删除",
            project_id,
            len(kept),
            ", ".join(str(b.get("block_id")) for b in kept[:10]),
        )

    for idx, sec in enumerate(sections):
        await upsert_outline_block(
            db,
            project_id=project_id,
            block_id=sec.id,
            level=int(sec.level or 1),
            title=sec.title or sec.id,
            order_idx=idx,
            matrix=None,
            run_thread_id=run_thread_id,
        )

    return {"removed": len(stale_ids), "created": len(sections), "kept": len(kept)}


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


async def create_chunk(db, material_id: int, chunk_index: int, content: str,
                       page: int = 0) -> dict:
    cursor = await db.execute(
        "INSERT INTO material_chunks (material_id, chunk_index, content, page) VALUES (?, ?, ?, ?)",
        (material_id, chunk_index, content, int(page or 0)),
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


async def list_chunks_with_filename(
    db,
    project_id: int,
    *,
    roles: tuple[str, ...] | None = None,
    exclude_roles: tuple[str, ...] | None = None,
) -> list[dict]:
    """取项目的素材切片，附带 filename 与**素材角色**。

    ``material_role`` 是 m.role 的别名，调用方据此分池（见方案 §4.D.1）：
    - **我方素材池**：``roles=("source",)``（用户在「素材」窗口上传的内容）
    - **招标文件池**：``roles=("spec","main_rfp","requirement","scoring","evaluation")``

    历史实现没有任何 role 过滤，导致匹配池里 57% 是招标文件自己的文字
    （实测全库要求类 83 段 / 素材类 63 段），既白烧 token 又会把招标原文当我方素材。
    不传参数时保持旧行为（全部角色），避免影响其它调用点。
    """
    if roles and exclude_roles:
        raise ValueError("roles 与 exclude_roles 不能同时给")
    where = ["m.project_id = ?"]
    params: list = [project_id]
    if roles:
        where.append(f"m.role IN ({','.join('?' * len(roles))})")
        params.extend(roles)
    if exclude_roles:
        where.append(f"m.role NOT IN ({','.join('?' * len(exclude_roles))})")
        params.extend(exclude_roles)
    cursor = await db.execute(
        f"""SELECT mc.*, m.filename, m.role AS material_role
            FROM material_chunks mc
            JOIN materials m ON mc.material_id = m.id
            WHERE {' AND '.join(where)}
            ORDER BY mc.material_id, mc.chunk_index""",
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_chunk_with_filename(db, chunk_id: int) -> dict | None:
    """按 id 取单个素材切片（附 filename / material_role）。人工挂接要用它建契约。"""
    cursor = await db.execute(
        """SELECT mc.*, m.filename, m.role AS material_role
             FROM material_chunks mc
             JOIN materials m ON mc.material_id = m.id
            WHERE mc.id = ?""",
        (chunk_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_block_source(db, block_id: int, rows: list[dict]) -> dict | None:
    """整版写 blocks.source（匹配阶段写依据的那一列）。

    与 ``update_block_matrix_fields`` 同一约定：拿到的是**完整** rows，不是增量 ——
    人工挂接/解绑都是"改这一章的依据列表"，语义上就是整版替换，避免读改写竞态。
    """
    import json as _json

    async with db.execute(
        "UPDATE blocks SET source = ? WHERE id = ?",
        (_json.dumps(rows or [], ensure_ascii=False), block_id),
    ) as cur:
        if cur.rowcount == 0:
            return None
    await db.commit()
    return await get_block(db, block_id)


# 素材池角色常量：与 infra.retrieval.assign.TENDER_ROLES 对应（避免调用方各写一份）
TENDER_MATERIAL_ROLES = ("spec", "main_rfp", "requirement", "scoring", "evaluation")
OURS_MATERIAL_ROLES = ("source",)


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


# 允许被局部更新的提炼字段（列名白名单，拼 SQL 只能来自这里）
_MATRIX_UPDATE_COLUMNS = (
    "key_points", "veto_items", "bonus_items", "score_items",
    "evidence_required", "indicators", "constraint_level",
)


async def update_block_matrix_fields(db, block_id_int: int, fields: dict) -> dict | None:
    """按 key **局部**更新提炼字段（工作台参考卡片的单字段编辑）。

    与 ``update_outline_fields`` 的区别：那个是"确认内容大纲"的整行写入（缺的字段会被
    写成空串），适合一次提交整份矩阵；这里只写调用方给的列，改一个字段不会清空别的。
    未知列名直接忽略。
    """
    cols = [c for c in _MATRIX_UPDATE_COLUMNS if c in fields]
    if not cols:
        return await get_block(db, block_id_int)
    assignments = ", ".join(f"{c} = ?" for c in cols)
    await db.execute(
        f"UPDATE blocks SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        [*[fields[c] for c in cols], block_id_int],
    )
    await db.commit()
    return await get_block(db, block_id_int)


async def update_outline_fields(
    db,
    project_id: int,
    block_id: str,
    matrix: dict,
    run_thread_id: str | None = None,
) -> int:
    """只更新提炼出来的 8 字段，不动 title / level / order_idx / content。

    用于「确认内容大纲及要求」：用户在提炼结果上改完，把编辑写回。
    ``constraint_level`` 用 COALESCE 兜底 —— 前端表单不提交它，缺省时保持原值
    而不是被覆写成默认值。

    返回受影响行数（0 表示该 run 下没有这个 block_id）。
    """
    cursor = await db.execute(
        """UPDATE blocks
              SET requirement = ?, key_points = ?, veto_items = ?, bonus_items = ?,
                  score_items = ?, evidence_required = ?, indicators = ?,
                  constraint_level = COALESCE(?, constraint_level),
                  updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ? AND block_id = ? AND run_thread_id IS ?""",
        (
            matrix.get("requirement") or "",
            matrix.get("key_points") or "",
            matrix.get("veto_items") or "",
            matrix.get("bonus_items") or "",
            matrix.get("score_items") or "",
            matrix.get("evidence_required") or "",
            matrix.get("indicators") or "",
            matrix.get("constraint_level"),
            project_id,
            block_id,
            run_thread_id,
        ),
    )
    await db.commit()
    return cursor.rowcount


async def apply_outline_structure(
    db: aiosqlite.Connection,
    project_id: int,
    nodes: list[dict],
    run_thread_id: str | None = None,
    purge: bool = False,
) -> dict:
    """按用户编辑后的目录结构重写 blocks：改标题/层级/顺序、建新占位、删空行。

    ``nodes`` 是**有序**列表 ``[{block_id, level, title}]``（层级由顺序推导，见
    ``domain/spec/structure.py``），顺序即 ``order_idx``。

    与 :func:`sync_outline_placeholders` 的关键区别：**不经过
    ``upsert_outline_block`` 的「同 id 换节」重置逻辑**。用户把某一节改标题或调层级
    时，同一个 ``block_id`` 仍然是同一节 —— 已提炼的 8 个字段必须保留（那是用户刚
    提炼出来、正要审的内容）。所以已有行只 UPDATE title/level/order_idx，绝不碰
    8 字段、status 与正文。

    删除保护沿用既有约定：**只删 content 为空的行**；已有正文的宁可留一条脏数据。
    但"保留"的行仍在库里、也仍会被导出（``routes/export.py`` 取全量 blocks）——
    所以用户主动删除时给出选择：``purge=True`` 表示**连正文一起删**（用户在确认框里
    选"移除并删除正文"）。默认 False，保持"不删用户内容"的既有语义。

    返回 ``{"created", "updated", "removed", "kept"}``。
    """
    nodes = list(nodes)
    ids = {n["block_id"] for n in nodes}

    existing = await list_blocks(db, project_id, run_thread_id)
    existing_ids = {b.get("block_id") for b in existing}

    stale = [
        b for b in existing
        if b.get("block_id") not in ids
        and (purge or not (b.get("content") or "").strip())
    ]
    for row in stale:
        await db.execute("DELETE FROM blocks WHERE id = ?", (row.get("id"),))

    kept = [] if purge else [
        b for b in existing
        if b.get("block_id") not in ids and (b.get("content") or "").strip()
    ]
    if kept:
        logger.info(
            "apply_outline_structure：项目 %s 有 %d 个被移出目录的章节保留了正文，未删除（block_id=%s）",
            project_id, len(kept),
            ", ".join(str(b.get("block_id")) for b in kept[:10]),
        )

    created = updated = 0
    for idx, node in enumerate(nodes):
        block_id = node["block_id"]
        level = int(node.get("level") or 1)
        title = node["title"]
        if block_id in existing_ids:
            await db.execute(
                """UPDATE blocks
                      SET title = ?, level = ?, order_idx = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = ? AND block_id = ? AND run_thread_id IS ?""",
                (title, level, idx, project_id, block_id, run_thread_id),
            )
            updated += 1
        else:
            await upsert_outline_block(
                db,
                project_id=project_id,
                block_id=block_id,
                level=level,
                title=title,
                order_idx=idx,
                matrix=None,
                run_thread_id=run_thread_id,
            )
            created += 1

    await db.commit()
    return {"created": created, "updated": updated,
            "removed": len(stale), "kept": len(kept)}
