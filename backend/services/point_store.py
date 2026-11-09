"""链条左半边的一等数据：招标评分点 / 技术规范要求。

## 为什么要有这个模块（2026-10-13 用户确定的核心数据关系）
用户把产品的核心数据关系定为：

    招标评分点 ↔ 技术规范要求 ↔ 应标章节 ↔ 企业素材 ↔ 生成内容 ↔ 审查结果

其中评分点解决「如何得分」，规范要求解决「需要响应什么」。但在这之前：
- 评分点只在**派生目录的过程中**内存存活，用完即丢；
- `infra/parser/tech_requirements.py` 写好了却**零引用**；
- `blocks` 只有 8 段散文，**没有任何字段能反查**"这章为哪个评分点得分、响应了哪条规范要求"。

于是覆盖度算不出来、审查也无法针对评分口径。本模块补上左半边，并把
**内容派生的稳定 id** 作为连接键：

- `point_id`   = ``rp-<内容哈希>``
- `requirement_id` = ``sr-<内容哈希>``

为什么不用顺序编号（R01/R02、1/2/3）做连接键：插入/删除一条会让后面全部移位，
章节上的引用立刻对不上。内容哈希在"同一条要求"上恒定，重出目录、换文档顺序都不漂。
可读编号仍保留（`label`），只作展示。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _digest(*parts: str, prefix: str) -> str:
    raw = "|".join(_norm(p) for p in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"


def stable_review_point_id(point) -> str:
    """评分点的稳定 id（内容派生）。

    只用**区分性字段**：标包 + 类别 + 条款号 + 评分因素 + 详细评审项。
    不含分值/分项要素全文 —— 招标文件修订分值或微调措辞时，同一评分点仍应被认作同一条。
    """
    return _digest(
        str(getattr(point, "package_no", "") or ""),
        str(getattr(point, "category", "") or ""),
        str(getattr(point, "clause", "") or ""),
        str(getattr(point, "factor", "") or ""),
        str(getattr(point, "item", "") or ""),
        prefix="rp",
    )


def stable_requirement_id(req) -> str:
    """规范要求的稳定 id（内容派生）。"""
    text = str(getattr(req, "requirement_text", "") or "")
    return _digest(
        str(getattr(req, "source_file", "") or ""),
        str(getattr(req, "topic", "") or ""),
        text[:120],
        prefix="sr",
    )


def _bands_json(point) -> str:
    bands = getattr(point, "bands", None) or []
    try:
        return json.dumps([
            {"label": getattr(b, "label", ""), "min": getattr(b, "min_score", None),
             "max": getattr(b, "max_score", None), "text": getattr(b, "text", "")}
            for b in bands
        ], ensure_ascii=False)
    except Exception:
        return "[]"


async def replace_review_points(db, project_id: int, run_thread_id: str | None,
                                points: Iterable) -> dict[str, dict]:
    """把本次运行的评分点落库（先删本 run 的旧行），返回 ``{point_id: row}``。

    幂等：同一 run 重出目录不会累积重复行。
    """
    points = list(points or [])
    await db.execute("DELETE FROM review_points WHERE run_thread_id = ?", (run_thread_id,))
    out: dict[str, dict] = {}
    for p in points:
        pid = stable_review_point_id(p)
        setattr(p, "point_id", pid)          # 回写，供目录节点引用
        row = {
            "point_id": pid,
            "package_no": str(getattr(p, "package_no", "") or ""),
            "category": str(getattr(p, "category", "") or ""),
            "clause": str(getattr(p, "clause", "") or ""),
            "seq": str(getattr(p, "seq", "") or ""),
            "factor": str(getattr(p, "factor", "") or ""),
            "item": str(getattr(p, "item", "") or ""),
            "criterion": str(getattr(p, "criterion", "") or ""),
            "points": getattr(p, "points", None),
            "bands": _bands_json(p),
            "objective": 1 if getattr(p, "objective", False) else 0,
            "source_file": str(getattr(p, "source_file", "") or ""),
        }
        await db.execute(
            """INSERT INTO review_points
               (project_id, run_thread_id, point_id, package_no, category, clause, seq,
                factor, item, criterion, points, bands, objective, source_file)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, run_thread_id, row["point_id"], row["package_no"], row["category"],
             row["clause"], row["seq"], row["factor"], row["item"], row["criterion"],
             row["points"], row["bands"], row["objective"], row["source_file"]),
        )
        out[pid] = row
    await db.commit()
    logger.info(f"评分点落库：{len(out)} 条（project={project_id}）")
    return out


async def replace_spec_requirements(db, project_id: int, run_thread_id: str | None,
                                    requirements: Iterable) -> dict[str, dict]:
    """把本次运行的规范要求落库，返回 ``{requirement_id: row}``。"""
    requirements = list(requirements or [])
    await db.execute("DELETE FROM spec_requirements WHERE run_thread_id = ?", (run_thread_id,))
    out: dict[str, dict] = {}
    for i, r in enumerate(requirements):
        rid = stable_requirement_id(r)
        setattr(r, "stable_id", rid)
        row = {
            "requirement_id": rid,
            "label": str(getattr(r, "requirement_id", "") or ""),   # R01…（人类可读）
            "topic": str(getattr(r, "topic", "") or ""),
            # TechRequirement 没有 title 字段：用 source_location（"章节 · 第 N 条"）
            # 或正文首段当可读标题；章节靠它反查"响应了哪条规范要求"。
            "title": str(getattr(r, "title", "") or getattr(r, "source_location", "")
                         or (getattr(r, "requirement_text", "") or "")[:40]),
            "text": str(getattr(r, "requirement_text", "") or ""),
            "level": getattr(r, "level", None),
            "parent_id": str(getattr(r, "parent_id", "") or ""),
            "mandatory_level": str(getattr(r, "mandatory_level", "") or ""),
            "verification_method": str(getattr(r, "verification_method", "") or ""),
            "source_file": str(getattr(r, "source_file", "") or ""),
            # package_no：技术要求按标包/标的归属（用户口径：不存在"全项目共享"）。
            # 抽取时 TechRequirement.package_id 已经有值，此前没落库 → 面板说不清归属。
            "package_no": str(getattr(r, "package_id", "") or ""),
            "order_idx": i,
        }
        await db.execute(
            """INSERT INTO spec_requirements
               (project_id, run_thread_id, requirement_id, topic, title, text, level,
                parent_id, mandatory_level, verification_method, source_file, package_no,
                order_idx)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, run_thread_id, row["requirement_id"], row["topic"], row["title"],
             row["text"], row["level"], row["parent_id"], row["mandatory_level"],
             row["verification_method"], row["source_file"], row["package_no"],
             row["order_idx"]),
        )
        out[rid] = row
    await db.commit()
    logger.info(f"规范要求落库：{len(out)} 条（project={project_id}）")
    return out


async def list_review_points(db, *, run_thread_id: str | None = None,
                             project_id: int | None = None) -> list[dict]:
    if run_thread_id:
        cur = await db.execute(
            "SELECT * FROM review_points WHERE run_thread_id = ? ORDER BY id", (run_thread_id,))
    else:
        cur = await db.execute(
            "SELECT * FROM review_points WHERE project_id = ? ORDER BY id", (project_id,))
    return [dict(r) for r in await cur.fetchall()]


async def list_spec_requirements(db, *, run_thread_id: str | None = None,
                                 project_id: int | None = None) -> list[dict]:
    if run_thread_id:
        cur = await db.execute(
            "SELECT * FROM spec_requirements WHERE run_thread_id = ? ORDER BY order_idx",
            (run_thread_id,))
    else:
        cur = await db.execute(
            "SELECT * FROM spec_requirements WHERE project_id = ? ORDER BY order_idx",
            (project_id,))
    return [dict(r) for r in await cur.fetchall()]


def requirement_ids_by_title(requirements: Iterable) -> dict[str, list[str]]:
    """``{章节标题: [requirement_id, ...]}`` —— 目录把规范章节挂进评分章节时用它建立关联。

    规范要求的 ``title`` 取自规范书小节标题，与目录里挂进来的节点标题一致，
    所以这张表能把"章节 → 它承载了哪几条规范要求"接起来。
    """
    out: dict[str, list[str]] = {}
    for r in requirements or []:
        rid = getattr(r, "stable_id", "") or stable_requirement_id(r)
        for key in {str(getattr(r, "title", "") or "").strip(),
                    str(getattr(r, "topic", "") or "").strip(),
                    str(getattr(r, "source_location", "") or "").strip()}:
            if not key:
                continue
            out.setdefault(key, [])
            if rid not in out[key]:
                out[key].append(rid)
    return out
