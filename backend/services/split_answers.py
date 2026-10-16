"""拆分确认的**作答**：把"看得到但改不了"变成"回答一次、下次拆分照办"。

用户要求（2026-10-13）：拆分时需要确认的内容可以与用户沟通。
上一版只做到"看得到"（待确认清单 + 未选中表 + 缺口），但用户无法直接作答 ——
只能改提炼要求再重拆，而范围/归属这类东西**用自然语言表达很别扭**
（"把 3.2.1 客户档案管理模块挂到功能要求下面"）。

本模块给出确定性的问答协议，回答落库后在**下次拆分与报告**中生效：

    scope            选哪张评分表：{"table_no": 1} 或 {"target": "标的1", "package_no": "1"}
    categories       参与拆分的类别：{"categories": ["tech"]}
    topic_placement  规范书主题的归属：{"3.2.1. 客户档案管理模块": "功能要求", ...}
                     （值是章节标题或 block_id；"__extra__" 表示强制进"要求应答"板块）
    structure        目录结构确认：{"confirmed": true}

项目级「投标范围」（`projects.scope_*`）优先于提炼要求里的范围词，
因为"我们投哪个标包"是投标人的属性，不是每次能用文字说清的东西。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# review_points：**评分点确认**（用户要求的两阶段：先确认评分点，再确认目录拆分）。
# 落库后报告里该项即为"已作答"，前端据此把面板从"确认评分点"推进到"确认目录"。
VALID_KEYS = ("scope", "categories", "topic_placement", "review_points", "structure", "response_plan")


async def get_answers(db, project_id: int, thread_id: str | None = None) -> dict:
    """读作答（``{key: value}``）。thread_id 为空时只读项目级（thread_id=''）的作答。"""
    cur = await db.execute(
        "SELECT key, value FROM split_answers WHERE project_id = ? AND thread_id = ?",
        (project_id, thread_id or ""),
    )
    out: dict = {}
    for row in await cur.fetchall():
        raw = row["value"] if isinstance(row, dict) or hasattr(row, "keys") else row[1]
        key = row["key"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        try:
            out[key] = json.loads(raw or "{}")
        except (ValueError, TypeError):
            out[key] = {}
    return out


async def set_answer(db, project_id: int, thread_id: str | None, key: str, value) -> dict:
    if key not in VALID_KEYS:
        raise ValueError(f"未知的确认项：{key}（可选：{'、'.join(VALID_KEYS)}）")
    payload = json.dumps(value if isinstance(value, (dict, list)) else {"value": value},
                         ensure_ascii=False)
    await db.execute(
        """INSERT INTO split_answers (project_id, thread_id, key, value, updated_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(project_id, thread_id, key)
           DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
        (project_id, thread_id or "", key, payload),
    )
    await db.commit()
    logger.info("split_answer：项目 %s key=%s 已作答", project_id, key)
    return {"key": key, "value": json.loads(payload)}


async def get_project_scope(db, project_id: int) -> dict:
    """项目级投标范围（投标人视角）：一次设定、贯穿拆分与报告。"""
    cur = await db.execute(
        "SELECT scope_target, scope_package_no, scope_package_name FROM projects WHERE id = ?",
        (project_id,),
    )
    row = await cur.fetchone()
    if not row:
        return {"target": "", "package_no": "", "package_name": ""}
    get = (lambda k: row[k]) if hasattr(row, "keys") else (lambda k: row[0])
    return {
        "target": (row["scope_target"] if hasattr(row, "keys") else "") or "",
        "package_no": (row["scope_package_no"] if hasattr(row, "keys") else "") or "",
        "package_name": (row["scope_package_name"] if hasattr(row, "keys") else "") or "",
    }


async def set_project_scope(db, project_id: int, *, target: str = "", package_no: str = "",
                            package_name: str = "") -> dict:
    await db.execute(
        """UPDATE projects SET scope_target = ?, scope_package_no = ?, scope_package_name = ?
           WHERE id = ?""",
        (target or "", package_no or "", package_name or "", project_id),
    )
    await db.commit()
    logger.info("project_scope：项目 %s 设为 %s/标包%s", project_id, target, package_no)
    return {"target": target, "package_no": package_no, "package_name": package_name}


async def resolve_split_context(db, project_id: int, thread_id: str | None) -> dict:
    """拆分前取"作答 + 项目范围"，供派生与报告使用。

    作答按 **run 级 → 项目级** 合并：先看这次 run 的作答，没有再用项目级默认，
    这样"这一次想换个范围试试"不会把项目设置改坏。
    """
    run_answers = await get_answers(db, project_id, thread_id) if thread_id else {}
    project_answers = await get_answers(db, project_id, None)
    answers = {**project_answers, **run_answers}
    scope = await get_project_scope(db, project_id)
    # 项目级范围 → 等价于 scope 作答（作答优先，便于"临时换一次"）
    if scope.get("target") or scope.get("package_no"):
        answers.setdefault("scope", {"target": scope.get("target") or "",
                                     "package_no": scope.get("package_no") or "",
                                     "package_name": scope.get("package_name") or ""})
    return {"answers": answers, "project_scope": scope}
