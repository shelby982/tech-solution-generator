"""
SQLite CRUD 封装 — 所有业务表的 async 操作。
所有函数接受 aiosqlite.Connection 作为第一参数。
Row 对象通过 dict(row) 转为普通字典后返回。
"""

import logging
import re

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


async def list_materials_by_ids(
    db: aiosqlite.Connection,
    material_ids: list[int],
) -> list[dict]:
    """按 id 取材料。

    给"按 run 绑定的文件集"用：``spec.material_ids`` 记着那次运行用了哪几份文件，
    需要时能精确定位到它们，而不是"项目当前的全部材料"。
    """
    ids = [int(i) for i in (material_ids or [])]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    cursor = await db.execute(
        f"SELECT * FROM materials WHERE id IN ({placeholders}) ORDER BY id",
        tuple(ids),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def replace_materials_by_role(
    db: aiosqlite.Connection, project_id: int, role: str,
) -> list[str]:
    """删除该项目下该角色的全部材料，返回被删文件的路径列表（供路由清磁盘）。

    用于"重新上传同类型文件"：用户重新选了招标文件 / 技术规范书就该以新文件为准，
    否则同角色的旧文件会被解析一起读进去（旧 + 新同时生效，再拆解就是旧数据污染新结果）。
    """
    cursor = await db.execute(
        "SELECT id, file_path FROM materials WHERE project_id = ? AND role = ?",
        (project_id, role),
    )
    rows = await cursor.fetchall()
    paths: list[str] = []
    for row in rows:
        await db.execute("DELETE FROM materials WHERE id = ?", (row["id"],))
        paths.append(row["file_path"] or "")
    await db.commit()
    return paths


async def delete_material(db: aiosqlite.Connection, material_id: int) -> bool:
    """真删材料记录（磁盘文件由路由一并清）。

    与上一版的取舍：曾经改成"软删保留历史"，但用户口径明确 ——
    **旧版本文件产生的数据是无效数据**，后续不会再被用到，留着没有意义。
    现在配合 ``purge_blocks_except_run``：换文件时新 run 会清掉旧 run 的 blocks，
    所以旧材料也没有"被旧 run 按 id 取回"的需要了，真删即可。

    ``soft_delete_material`` 仍保留（软删能力本身有价值：需要保留历史时可用）。
    """
    cursor = await db.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
    if await cursor.fetchone() is None:
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


async def purge_scope_blocks(
    db: aiosqlite.Connection,
    project_id: int,
    *,
    target: str = "",
    package_no: str = "",
    keep_thread_id: str | None = None,
) -> int:
    """清掉**同一标包**下、不属于 keep 的旧 blocks，返回删除条数。

    为什么不能再用 `purge_blocks_except_run`（清全项目）：用户口径是
    **每个标包是单独的内容** —— 跑标包 1 不能把标包 2 已经拆好的目录清掉。
    所以清理范围必须收敛到"同一个 (target, package_no)"。
    """
    cursor = await db.execute(
        """DELETE FROM blocks
            WHERE project_id = ?
              AND (run_thread_id IS NULL OR run_thread_id != COALESCE(?, ''))
              AND COALESCE(run_thread_id, '') IN (
                  SELECT COALESCE(thread_id, '') FROM workflow_runs
                   WHERE project_id = ?
                     AND COALESCE(target, '') = ?
                     AND COALESCE(package_no, '') = ?
              )""",
        (project_id, keep_thread_id, project_id, target or "", package_no or ""),
    )
    await db.commit()
    return cursor.rowcount or 0


async def purge_blocks_except_run(
    db: aiosqlite.Connection,
    project_id: int,
    keep_thread_id: str | None,
) -> int:
    """清掉项目里**不属于当前 run** 的 blocks，返回删除条数。

    为什么需要：用户换掉要求文件之后，旧目录的数据就是**无效数据**（不会再用到），
    留着只会有两个坏处 —— ① 页面上两套目录混着显示，用户不知道信哪个；
    ② 每次读路径都要靠 run_thread_id 过滤去躲它。新 run 开始时直接清掉最省事。
    """
    if keep_thread_id:
        cursor = await db.execute(
            "DELETE FROM blocks WHERE project_id = ? "
            "AND (run_thread_id IS NULL OR run_thread_id != ?)",
            (project_id, keep_thread_id),
        )
    else:
        cursor = await db.execute(
            "DELETE FROM blocks WHERE project_id = ?", (project_id,)
        )
    await db.commit()
    return cursor.rowcount or 0


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


async def _write_block_chain(db: aiosqlite.Connection, block_pk: int, chain: dict) -> None:
    """把来源链写到 blocks 行上（评分点 / 规范要求 / 分值 / 评分口径 / 规范专题）。

    这是用户确定的核心数据关系在存储层的落点：
       招标评分点 ↔ 技术规范要求 ↔ **应标章节** ↔ 企业素材 ↔ 生成内容 ↔ 审查结果
    在此之前章节只有 8 段散文，"这章为哪个评分点得分、响应了哪条规范要求"无法反查。
    """
    import json as _json

    def _ids(v):
        if isinstance(v, (list, tuple)):
            return _json.dumps([str(x) for x in v], ensure_ascii=False)
        return _json.dumps([str(v)], ensure_ascii=False) if v else "[]"

    await db.execute(
        """UPDATE blocks SET review_point_ids = ?, spec_requirement_ids = ?,
                             score_weight = ?, criterion_text = ?, spec_topic = ?,
                             stable_key = ?
           WHERE id = ?""",
        (_ids(chain.get("review_point_id")),
         _ids(chain.get("spec_requirement_ids")),
         chain.get("score_weight"),
         str(chain.get("criterion_text") or ""),
         str(chain.get("spec_topic") or ""),
         str(chain.get("stable_key") or ""),
         block_pk),
    )


async def carry_over_by_stable_key(
    db: aiosqlite.Connection,
    project_id: int,
    run_thread_id: str | None,
    sections,
    stale_rows: list[dict],
) -> dict:
    """重出目录后，把旧章节的**正文 / 8 字段 / 素材挂接**迁移到新章节。

    为什么必须做：``block_id`` 是位置派生的（s1 / s1.1），重拆换位置就换 id；
    而应答条目 id 里嵌着 block_id（``{block_id}#R1``），所以素材挂接也会失效。
    旧实现只"保护有正文的行不删"—— 结果是正文留在错位的节上、素材指向不存在的
    条目（脏数据），用户看到的是"我写的内容不见了 / 依据对不上了"。

    现在按 ``stable_key``（内容派生：评分点 id + 标题路径）认亲：
    匹配上 → 把内容与挂接**搬到新行**，删掉旧行；匹配不上 → 保持原样（仍不删用户内容）。

    返回 ``{"migrated": int, "orphaned": int, "repointed_items": int}``。
    """
    if not stale_rows:
        return {"migrated": 0, "orphaned": 0, "repointed_items": 0}

    new_by_key = {
        str((getattr(sec, "chain", None) or {}).get("stable_key") or ""): sec.id
        for sec in sections
    }
    new_by_key.pop("", None)

    # 兜底索引：**标题在新目录里唯一**的章节，用归一化标题认亲。
    # 为什么需要：稳定键含父级路径，章节一"搬家"（比如用户要求把规范书专题
    # 从技术方案挪到项目管理）路径就变了、精确键必然失配，正文就搬不过去。
    # 只收唯一标题，避免「工作项1：梳理分析现状」这类同名节点被张冠李戴。
    title_counts: dict[str, int] = {}
    for sec in sections:
        t = re.sub(r"\s+", "", str(getattr(sec, "title", "") or ""))
        if t:
            title_counts[t] = title_counts.get(t, 0) + 1
    new_by_unique_title = {
        re.sub(r"\s+", "", str(getattr(sec, "title", "") or "")): sec.id
        for sec in sections
        if title_counts.get(re.sub(r"\s+", "", str(getattr(sec, "title", "") or "")), 0) == 1
    }

    migrated = 0
    orphaned = 0
    repointed = 0

    for row in stale_rows:
        key = str(row.get("stable_key") or "")
        new_id = new_by_key.get(key) if key else None
        if not new_id:
            # 精确键失配（章节搬了家）→ 用唯一标题兜底
            norm_title = re.sub(r"\s+", "", str(row.get("title") or ""))
            new_id = new_by_unique_title.get(norm_title)
        if not new_id:
            orphaned += 1
            continue

        # 素材挂接里的 item_id 前缀是旧 block_id，搬到新 id 时要一起改
        source, n = _remap_item_ids(row.get("source"), str(row.get("block_id") or ""), new_id)
        repointed += n

        await db.execute(
            """UPDATE blocks SET
                   content = ?, requirement = ?, key_points = ?, veto_items = ?,
                   bonus_items = ?, score_items = ?, evidence_required = ?,
                   constraint_level = ?, indicators = ?, status = ?,
                   error = ?, error_kind = ?, source = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE project_id = ? AND block_id = ? AND run_thread_id IS ?""",
            (row.get("content") or "", row.get("requirement") or "", row.get("key_points") or "",
             row.get("veto_items") or "", row.get("bonus_items") or "", row.get("score_items") or "",
             row.get("evidence_required") or "", row.get("constraint_level") or "",
             row.get("indicators") or "", row.get("status") or "", row.get("error") or "",
             row.get("error_kind") or "", source, project_id, new_id, run_thread_id),
        )
        await db.execute("DELETE FROM blocks WHERE id = ?", (row.get("id"),))
        migrated += 1

    if migrated or orphaned:
        await db.commit()
        logger.info(
            "carry_over_by_stable_key：项目 %s 迁移 %d 个章节的正文/挂接，%d 个无法认亲（保留）",
            project_id, migrated, orphaned,
        )
    return {"migrated": migrated, "orphaned": orphaned, "repointed_items": repointed}


def _remap_item_ids(source: str | None, old_id: str, new_id: str) -> tuple[str, int]:
    """把 ``blocks.source`` 里 item_ids 的旧 block_id 前缀换成新的。

    item_id 形如 ``s3.3#R1``（见 domain/spec/response_items.py）——前缀就是 block_id。
    """
    import json as _json

    raw = (source or "").strip()
    if not raw or raw == "[]" or not old_id or old_id == new_id:
        return source or "[]", 0
    try:
        items = _json.loads(raw)
    except (ValueError, TypeError):
        return source or "[]", 0
    if not isinstance(items, list):
        return source or "[]", 0

    changed = 0
    prefix = f"{old_id}#"
    for it in items:
        if not isinstance(it, dict):
            continue
        ids = it.get("item_ids")
        if not isinstance(ids, list):
            continue
        new_ids = []
        for iid in ids:
            s_iid = str(iid)
            if s_iid.startswith(prefix):
                new_ids.append(f"{new_id}#{s_iid[len(prefix):]}")
                changed += 1
            else:
                new_ids.append(iid)
        it["item_ids"] = new_ids
    return _json.dumps(items, ensure_ascii=False), changed


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
    chain: dict | None = None,
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
            # 已存在的行同样要刷新来源链 —— 项目里绝大多数行都是已存在的（重出目录时
            # block_id 常与上一版相同），只写 INSERT 分支会让链条**永远落不了库**
            # （实测：102 章、0 行带 review_point_ids）。
            if chain:
                await _write_block_chain(db, row["id"], chain)
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
        if chain:
            await _write_block_chain(db, cursor.lastrowid, chain)
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

    # 先把"有正文/有挂接"的旧行收集起来，等新行 upsert 完再按稳定键迁移。
    #
    # ⚠️ 候选**不能**只看"block_id 不在新目录里"：位置 id 会跨版本撞车 ——
    # 旧「方案可行性」在 s1.1，新版本 s1.1 变成了「方案总体架构设计」，
    # 于是旧行不算孤儿、却会被 upsert 的 UPDATE 分支**直接清空正文**（实测）。
    # 判据因此改成：**该 id 在新目录里不存在，或该 id 已被另一个章节占用**（稳定键不同）。
    new_key_by_id = {
        sec.id: str((getattr(sec, "chain", None) or {}).get("stable_key") or "")
        for sec in sections
    }
    carry_candidates = []
    for b in existing:
        has_payload = ((b.get("content") or "").strip()
                       or (b.get("source") or "").strip() not in ("", "[]"))
        if not has_payload:
            continue
        bid = b.get("block_id")
        new_key = new_key_by_id.get(bid)
        old_key = str(b.get("stable_key") or "").strip()
        if new_key is None or new_key != old_key:
            carry_candidates.append(b)

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
            chain=getattr(sec, "chain", None),
        )

    carry = await carry_over_by_stable_key(db, project_id, run_thread_id, sections,
                                           carry_candidates)
    return {"removed": len(stale_ids), "created": len(sections), "kept": len(kept),
            "migrated": carry["migrated"], "orphaned": carry["orphaned"],
            "repointed_items": carry["repointed_items"]}


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


# 材料角色分两类，决定这份文件进哪条链路（与前端 assets/material-roles.mjs 同一口径）：
#   要求类（TENDER_）→ 解析 → 提炼 → 目录 → 评分覆盖
#   素材类（OURS_）  → 只作为"我方技术素材"被检索匹配，**不参与要求提炼**
# `spec` 指**技术规范书 / 技术招标文件**（招标人随招标文件发出的附件，招标文件原文：
# 「二、技术规范书 详见招标文件附件"技术招标文件"」），不是投标人写的文件 ——
# 实测曾被当成 source（我方素材）上传，于是既没结构化、也没参与目录规划。
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
    # 服务层只补**形态闸**（纵深防御），不做完整校验 —— 完整校验含层级归一
    # （clamp_levels），那是调用方的职责，放这里会改变既有语义（实测会误压层级）。
    # 但这道闸必须有：这条路径曾因"纯数字 block_id"毁过一次目录。
    from domain.spec.structure import StructureError
    _numeric = [str(n.get("block_id")) for n in nodes if str(n.get("block_id") or "").isdigit()]
    if _numeric:
        raise StructureError(
            f"节点 id 是纯数字（{_numeric[:3]}）：这看着像数据库行 id，不是目录 id（如 s1.1）。"
            "已拒绝本次提交，目录未做任何改动。"
        )
    nodes = list(nodes)
    ids = {n["block_id"] for n in nodes}

    existing = await list_blocks(db, project_id, run_thread_id)
    existing_ids = {b.get("block_id") for b in existing}

    # 比例闸：正常编辑只会**少量**新增章节（用户点「新增」），绝大多数 id 都该已存在。
    # 若一次提交里大量 id 在库中找不到，几乎一定是 id 体系用错（实测：121/121 未知 →
    # 目录被整体重建）。宁可直接拒，也不让一次误发毁掉一份目录。
    unknown = [n["block_id"] for n in nodes if n["block_id"] not in existing_ids]
    if len(unknown) > max(5, int(0.2 * len(nodes))):
        from domain.spec.structure import StructureError
        raise StructureError(
            f"提交的目录里有 {len(unknown)} 个节点在当前目录中不存在（如 {unknown[:3]}）——"
            "已拒绝本次提交以免误删/重建目录；如确实要新增章节，请逐个添加。"
        )

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

    # 一次保存删掉"异常多"的节点 → 响亮告警。
    # 背景：调试期间出现过两次"库里有、但提交的列表里没有"导致成批删除
    # （8 行 / 22 行），日志里只有一条 200，事后很难定位。
    # 这里不阻塞（用户确实可能批量删），但必须留痕：谁、删了多少、剩多少。
    _kept_n = len([b for b in existing if b.get("block_id") not in ids])
    if _kept_n > max(5, int(0.2 * max(1, len(existing)))):
        logger.warning(
            "apply_outline_structure 异常：一次保存移出了 %d 个节点（原有 %d → 提交 %d）。"
            "若这不是用户主动批量删除，说明提交方（前端）的列表比库里少 —— "
            "block_ids=%s", _kept_n, len(existing), len(nodes),
            ", ".join(str(b.get("block_id")) for b in existing if b.get("block_id") not in ids)[:300],
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
