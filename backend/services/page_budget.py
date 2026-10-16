"""篇幅预算：**总额一次填，系统分配到各章**，人工改过的章节固定不动。

用户要求（2026-10-16）：
1. 「篇幅要跟目录一起看」—— 目录是一个对象，篇幅是它的属性，不是第二张表；
   页数直接落在目录每一行（`blocks.page_budget`）。
2. 「不能让用户一个一个手工去填」—— 填一个总额，按「评分点分值 × 章节类型系数」
   自动分配到各章，合计**精确等于**总额（最大余额法，不差页）。
3. 「篇幅的确认要在目录确认之后」—— 目录没确认就分配，目录一改篇幅全乱；
   因此确认篇幅要求 `response_plan.confirmed` 为真。
4. 手工改过某章 → 该章固定（`page_budget_pinned`），其余章节吸收差额。

存储：
- 总额/来源/是否确认 → `split_answers` key ``page_budget``（JSON）
- 每章页数 → ``blocks.page_budget`` / ``blocks.page_budget_pinned``
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

ANSWER_KEY = "page_budget"

# 章节类型系数：同类章节在「分值」之外再微调。可解释、可改。
# 依据是章节标题关键词（目录节点标题由解析/模板生成，命名相对稳定）。
KIND_FACTOR = (
    ("项目背景", 0.6),
    ("项目目标", 0.7),
    ("建设范围", 0.8),
    ("项目描述", 0.8),
    ("总体要求", 0.7),
    ("技术方案", 1.4),
    ("技术实现", 1.2),
    ("系统架构", 1.3),
    ("架构设计", 1.3),
    ("关键技术", 1.2),
    ("开发要求", 1.2),
    ("功能要求", 1.1),
    ("重点", 1.1),
    ("项目管理", 1.0),
    ("项目进度", 1.0),
    ("人员", 0.9),
    ("团队", 0.9),
    ("业绩", 1.0),
    ("财务", 1.0),
    ("认证", 0.7),
    ("资质", 0.8),
    ("服务承诺", 0.8),
    ("服务质量", 0.8),
    ("售后", 0.8),
    ("点对点", 1.3),
    ("总则", 0.6),
)
DEFAULT_FACTOR = 1.0
# 只有前两层参与分配：更深的叶子节点按"计入上级"处理，避免出现 0.x 页
ALLOCATABLE_MAX_LEVEL = 2


def kind_factor(title: str | None) -> float:
    """按标题关键词取章节类型系数。"""
    text = (title or "").strip()
    for keyword, factor in KIND_FACTOR:
        if keyword in text:
            return factor
    return DEFAULT_FACTOR


def is_allocatable(chapter: dict) -> bool:
    """是否参与篇幅分配（前两层；且必须已确认/有分值线索）。"""
    try:
        level = int(chapter.get("level") or 1)
    except (TypeError, ValueError):
        level = 1
    return level <= ALLOCATABLE_MAX_LEVEL


def _own_score(chapter: dict) -> float:
    """章节自身的分值（score_weight 优先，其次 score）。"""
    raw = chapter.get("score_weight")
    if raw in (None, ""):
        raw = chapter.get("score")
    if isinstance(raw, str):
        # blocks.score 历史上存过文本（如"4 分"），提取数字
        digits = "".join(ch if ch.isdigit() or ch == "." else " " for ch in raw).split()
        raw = digits[0] if digits else None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return score if score > 0 else 0.0


def subtree_scores(chapters: list[dict]) -> dict:
    """每个章节的**子树分值合计**。

    真实目录里分值常挂在叶子（如 s1.1.1 有 4 分），一级/二级自己没有分值；
    只按"自身分值"算权重会退化成均分，因此按子树汇总。
    """
    own = {_key(c): _own_score(c) for c in chapters}
    totals = dict(own)
    for chapter in chapters:
        key = _key(chapter)
        if key is None:
            continue
        prefix = f"{key}."
        for other_key, value in own.items():
            if other_key and str(other_key).startswith(prefix):
                totals[key] = totals.get(key, 0.0) + value
    return totals



def _parent_key(chapters: list[dict], key: str) -> str | None:
    """按 block_id 前缀找父节点（s1.2.1 → s1.2）。"""
    keys = {_key(c) for c in chapters if _key(c)}
    parts = str(key).split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in keys:
            return candidate
    return None


def allocation_targets(chapters: list[dict]) -> list[dict]:
    """分配落点：前两层里**没有可分配子节点**的章节。

    否则父章节与子章节会各自拿到一份额度 → 同一份篇幅被算两遍
    （联调时实测：父章节 57 页，而它的子章节又各自分了页）。
    """
    allocatable = {_key(c) for c in chapters if is_allocatable(c)}
    targets = []
    for chapter in chapters:
        if not is_allocatable(chapter):
            continue
        key = _key(chapter)
        has_allocatable_child = any(
            other != key and str(other).startswith(f"{key}.")
            for other in allocatable
        )
        if not has_allocatable_child:
            targets.append(chapter)
    return targets


def rollup_totals(chapters: list[dict], pages: dict) -> dict:
    """父章节的页数 = 子树合计（只用于展示，不参与分配，避免重复计算）。"""
    out = {}
    for chapter in chapters:
        key = _key(chapter)
        if key is None:
            continue
        own = pages.get(key)
        if own is not None and any(c is chapter for c in []):
            pass
        total = 0.0
        for other_key, value in pages.items():
            if other_key and (other_key == key or str(other_key).startswith(f"{key}.")):
                total += value
        if key in pages:
            out[key] = int(total)
        elif total:
            out[key] = int(total)
    return out


def _weight(chapter: dict, subtree: dict | None = None) -> float:
    """分配权重 = 分值 × 类型系数（分值缺失时退化为类型系数，最低 0.1 防止全 0）。"""
    score = _own_score(chapter)
    if score <= 0 and subtree is not None:
        score = subtree.get(_key(chapter), 0.0)
    if score <= 0:
        score = 1.0
    return max(0.1, score * kind_factor(chapter.get("title")))


def allocate_pages(total: int, chapters: list[dict]) -> dict:
    """把 `total` 页分配到各章，返回 ``{block_id: pages}``。

    - 已固定的章节（``pinned``）保持原值，不参与分配；
    - 其余章节按权重用**最大余额法**分配剩余额度，保证合计精确等于 total；
    - 剩余额度不足时从权重最小的章节逐步回收，不会出现负数。
    """
    total = max(0, int(total or 0))
    allocatable = allocation_targets(chapters)      # 只在最细的可分配层落额度
    pages: dict = {}

    pinned = [c for c in allocatable if c.get("pinned")]
    free = [c for c in allocatable if not c.get("pinned")]

    pinned_sum = 0
    for c in pinned:
        value = max(0, int(c.get("pages") or 0))
        pages[_key(c)] = value
        pinned_sum += value

    rest = max(0, total - pinned_sum)
    if not free:
        return pages

    subtree = subtree_scores(chapters)
    weights = [_weight(c, subtree) for c in free]
    weight_sum = sum(weights) or 1.0
    exact = [rest * w / weight_sum for w in weights]
    floors = [int(x) for x in exact]
    left = rest - sum(floors)

    order = sorted(range(len(free)), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in order:
        if left <= 0:
            break
        floors[i] += 1
        left -= 1
    # 额度为负（固定值已超总额）时从最小的章节回收
    for i in reversed(order):
        if left >= 0:
            break
        if floors[i] > 0:
            floors[i] -= 1
            left += 1

    for chapter, value in zip(free, floors):
        pages[_key(chapter)] = value
    return pages


def rollup_pages(chapters: list[dict], pages: dict) -> dict:
    """父章节的页数 = 子树合计（**仅供展示**，不参与分配，也不参与合计校验）。

    单独一个函数的原因：如果混进 allocate_pages 的返回值，调用方 sum(values())
    会把父子各算一遍（联调实测：120 变成 240）。
    """
    out = dict(pages)
    for chapter in chapters:
        key = _key(chapter)
        if key is None or key in pages:
            continue
        children = [k for k in pages if k and str(k).startswith(f"{key}.")]
        if children:
            out[key] = sum(pages[k] for k in children)
    return out


def _key(chapter: dict):
    return chapter.get("block_id") or chapter.get("id")


def summarize(total: int, chapters: list[dict]) -> dict:
    """合计与校验（页数从章节自身取，调用方负责已写入分配结果）。"""
    total = max(0, int(total or 0))
    targets = {_key(c) for c in allocation_targets(chapters)}
    pages = sum(max(0, int(c.get("pages") or 0)) for c in chapters if _key(c) in targets)
    return {
        "total": total,
        "allocated": pages,
        "chapter_count": len(targets),
        "diff": pages - total,
        "ok": pages == total,
    }


def _load(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _read_answer(db, project_id: int, thread_id: str) -> dict:
    cur = await db.execute(
        "SELECT value FROM split_answers WHERE project_id = ? AND thread_id = ? AND key = ?",
        (project_id, thread_id or "", ANSWER_KEY),
    )
    row = await cur.fetchone()
    if not row:
        return {}
    raw = row["value"] if hasattr(row, "keys") else row[0]
    return _load(raw)


async def _write_answer(db, project_id: int, thread_id: str, payload: dict) -> None:
    await db.execute(
        "INSERT INTO split_answers (project_id, thread_id, key, value, updated_at) "
        "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(project_id, thread_id, key) DO UPDATE SET value = excluded.value, "
        "updated_at = CURRENT_TIMESTAMP",
        (project_id, thread_id or "", ANSWER_KEY, json.dumps(payload, ensure_ascii=False)),
    )


async def list_chapters(db, project_id: int, thread_id: str) -> list[dict]:
    """读目录节点（含篇幅列），按 order_idx 排序。"""
    cur = await db.execute(
        "SELECT id, block_id, title, level, score, score_weight, "
        "       COALESCE(page_budget, 0) AS page_budget, "
        "       COALESCE(page_budget_pinned, 0) AS page_budget_pinned "
        "FROM blocks WHERE project_id = ? AND run_thread_id = ? "
        "ORDER BY COALESCE(order_idx, id)",
        (project_id, thread_id or ""),
    )
    out = []
    for row in await cur.fetchall():
        get = (lambda k: row[k]) if hasattr(row, "keys") else (lambda k, row=row: row[0])
        out.append({
            "block_id": row["block_id"] if hasattr(row, "keys") else row[1],
            "id": row["id"] if hasattr(row, "keys") else row[0],
            "title": row["title"] if hasattr(row, "keys") else row[2],
            "level": row["level"] if hasattr(row, "keys") else row[3],
            "score": row["score"] if hasattr(row, "keys") else row[4],
            "score_weight": row["score_weight"] if hasattr(row, "keys") else row[5],
            "pages": row["page_budget"] if hasattr(row, "keys") else row[6],
            "pinned": bool(row["page_budget_pinned"] if hasattr(row, "keys") else row[7]),
        })
    return out



def _mark_targets(chapters: list[dict]) -> None:
    """标出"分配落点"（前端据此决定哪些行可编辑、哪些只是父级合计）。"""
    targets = {_key(c) for c in allocation_targets(chapters)}
    for chapter in chapters:
        chapter["is_target"] = _key(chapter) in targets


async def get_page_budget(db, project_id: int, thread_id: str) -> dict:
    """读篇幅：总额 + 每章页数 + 合计校验。"""
    answer = await _read_answer(db, project_id, thread_id)
    chapters = await list_chapters(db, project_id, thread_id)
    _mark_targets(chapters)
    total = max(0, int(answer.get("total") or 0))
    payload = {
        "total": total,
        "source": answer.get("source") or "",
        "confirmed": bool(answer.get("confirmed")),
        "chapters": chapters,
    }
    payload.update(summarize(total, chapters))
    return payload


async def save_page_budget(
    db,
    project_id: int,
    thread_id: str,
    *,
    total: int | None = None,
    source: str | None = None,
    allocate: bool = False,
    overrides: list[dict] | None = None,
    confirm: bool = False,
) -> dict:
    """写篇幅：更新总额 → 应用人工调整 → （可选）自动分配 → （可选）确认。

    `overrides` 形如 ``[{"block_id": "3.1", "pages": 6, "pinned": True}]``。
    """
    answer = await _read_answer(db, project_id, thread_id)
    chapters = await list_chapters(db, project_id, thread_id)

    if total is not None:
        answer["total"] = max(0, int(total))
    if source is not None:
        answer["source"] = source

    if overrides:
        by_key = {c["block_id"]: c for c in chapters}
        by_key.update({str(c["id"]): c for c in chapters})
        for item in overrides:
            key = item.get("block_id") or item.get("id")
            chapter = by_key.get(str(key))
            if not chapter:
                continue
            chapter["pages"] = max(0, int(item.get("pages") or 0))
            # 手工改过即固定；显式传 pinned=False 可解除
            chapter["pinned"] = bool(item.get("pinned", True))

    if allocate:
        targets_pages = allocate_pages(int(answer.get("total") or 0), chapters)
        display_pages = rollup_pages(chapters, targets_pages)   # 父章节=子树合计（展示）
        target_keys = {_key(c) for c in allocation_targets(chapters)}
        for chapter in chapters:
            key = _key(chapter)
            if key in display_pages:
                chapter["pages"] = display_pages[key]
            chapter["is_target"] = key in target_keys

    for chapter in chapters:
        await db.execute(
            "UPDATE blocks SET page_budget = ?, page_budget_pinned = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE project_id = ? AND block_id = ? AND run_thread_id = ?",
            (int(chapter.get("pages") or 0), 1 if chapter.get("pinned") else 0,
             project_id, chapter["block_id"], thread_id or ""),
        )

    if confirm:
        answer["confirmed"] = True

    await _write_answer(db, project_id, thread_id, answer)
    await db.commit()

    payload = {
        "total": max(0, int(answer.get("total") or 0)),
        "source": answer.get("source") or "",
        "confirmed": bool(answer.get("confirmed")),
        "chapters": chapters,
    }
    payload.update(summarize(payload["total"], chapters))
    return payload


async def clear_page_budget(db, project_id: int, thread_id: str, total: int | None = None,
                            source: str | None = None) -> dict:
    """清除全部人工调整，按总额重新分配。`total`/`source` 传入时**一并更新**。

    注意：这里必须把 total 与 source 都透传给 save_page_budget —— 只传 total 时，
    调用方传进来的 source 会被静默丢弃（联调时踩到过：来源清不掉）。
    """
    await db.execute(
        "UPDATE blocks SET page_budget = 0, page_budget_pinned = 0, updated_at = CURRENT_TIMESTAMP "
        "WHERE project_id = ? AND run_thread_id = ?",
        (project_id, thread_id or ""),
    )
    await db.commit()
    return await save_page_budget(db, project_id, thread_id, total=total, source=source, allocate=True)
