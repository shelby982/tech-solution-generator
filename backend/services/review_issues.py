"""评审 issue 状态机：让"修好了"可验证，而不是只看分数涨没涨。

问题：应用补丁/重写后只能看分数变化。分数 58→61 但同一条问题仍在 = 假修复；
反之问题修好但总分被别的章拉低 = 被误判。所以必须按 **issue 级**追踪。

状态流转：open → fixed（已修改待复核）→ verified（明确复核通过）。
评审遗漏的问题继续保留。正文或采用依据变化后，旧的通过结论退回待复核。

指纹（fingerprint）用于跨轮识别"同一条问题"：
    sha1(severity + "|" + normalize(point))
    normalize：去空白标点、数字统一为 #、取前 40 字
—— 这样"缺 3 个量化指标"与"缺 5 个量化指标"、"缺量化指标。" 视为同一条。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

FINGERPRINT_LEN = 40

_STRIP = re.compile(r"[\s，。；：、！？,.;:!?\"'“”‘’（）()\[\]【】<>《》\-—_/\\|]+")


def normalize_point(point: str) -> str:
    """问题文本归一化：去空白与标点、数字→#、截断。"""
    text = _STRIP.sub("", str(point or "").lower())
    text = re.sub(r"\d+", "#", text)
    return text[:FINGERPRINT_LEN]


def fingerprint(issue: dict) -> str:
    """issue → 稳定指纹（severity + 归一化问题文本）。"""
    severity = str((issue or {}).get("severity") or "medium").lower()
    body = normalize_point((issue or {}).get("point") or "")
    raw = f"{severity}|{body}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def sync_issues(db, block_pk: int, issues: list[dict]) -> dict:
    """用本轮评审结果更新状态机，返回 {open, fixed, verified} 计数。

    规则（spec C3）：
      · 本轮出现的 issue → open（若此前 verified 也回 open，视为复发）
      · 本轮未出现的问题保留原状态，等待单项明确复核
      · 上一轮 fixed 且本轮仍出现 → 回到 open（假修复）
    """
    now = _now()
    current = {}
    for issue in issues or []:
        print_ = fingerprint(issue)
        current.setdefault(print_, {
            "fingerprint": print_,
            "severity": str(issue.get("severity") or "medium").lower(),
            "point": str(issue.get("point") or "")[:200],
        })

    rows = await (await db.execute(
        "SELECT fingerprint, status, fixed_at FROM review_issue_states WHERE block_id=?", (block_pk,))).fetchall()
    previous = {r[0]: {"status": r[1], "fixed_at": r[2]} for r in rows}

    for print_, item in current.items():
        prev = previous.get(print_)
        if prev is None:
            await db.execute(
                "INSERT INTO review_issue_states (block_id, fingerprint, status, severity, point, "
                "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
                (block_pk, print_, "open", item["severity"], item["point"], now, now))
        elif prev["status"] == "verified":
            # 复发：verified → open
            await db.execute(
                "UPDATE review_issue_states SET status='open', last_seen_at=?, point=?, severity=? "
                "WHERE block_id=? AND fingerprint=?",
                (now, item["point"], item["severity"], block_pk, print_))
        else:
            # open / fixed（假修复）都回到 open
            await db.execute(
                "UPDATE review_issue_states SET status='open', last_seen_at=?, point=?, severity=? "
                "WHERE block_id=? AND fingerprint=?",
                (now, item["point"], item["severity"], block_pk, print_))

    # Missing findings remain pending until an explicit, targeted verification.
    await db.commit()
    return await counts(db, block_pk)


async def mark_fixed(db, block_pk: int, issues: list[dict]) -> int:
    """应用定点补丁后：把仍为 open 的这些问题置 fixed（等复评验证）。"""
    now = _now()
    marked = 0
    for issue in issues or []:
        print_ = fingerprint(issue)
        rows = await (await db.execute(
            "SELECT status FROM review_issue_states WHERE block_id=? AND fingerprint=?",
            (block_pk, print_))).fetchall()
        if not rows:
            await db.execute(
                "INSERT INTO review_issue_states (block_id, fingerprint, status, severity, point, "
                "first_seen_at, last_seen_at, fixed_at) VALUES (?,?,?,?,?,?,?,?)",
                (block_pk, print_, "fixed", str(issue.get("severity") or "medium").lower(),
                 str(issue.get("point") or "")[:200], now, now, now))
            marked += 1
            continue
        if rows[0][0] == "open":
            await db.execute(
                "UPDATE review_issue_states SET status='fixed', fixed_at=? WHERE block_id=? AND fingerprint=?",
                (now, block_pk, print_))
            marked += 1
    await db.commit()
    return marked


async def counts(db, block_pk: int) -> dict:
    """本章 issue 状态计数。"""
    rows = await (await db.execute(
        "SELECT status, COUNT(*) FROM review_issue_states WHERE block_id=? GROUP BY status",
        (block_pk,))).fetchall()
    result = {"open": 0, "fixed": 0, "verified": 0}
    for status, count in rows:
        if status in result:
            result[status] = int(count)
    result["total"] = sum(result.values())
    return result


async def list_issues(db, block_pk: int) -> dict:
    """本章 issue 列表（含状态），按 open > fixed > verified、severity 排序。"""
    rows = await (await db.execute(
        "SELECT fingerprint, status, severity, point, first_seen_at, last_seen_at, fixed_at, verified_at, verified_content_version, verified_evidence_version "
        "FROM review_issue_states WHERE block_id=?", (block_pk,))).fetchall()
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    order = {"open": 0, "fixed": 1, "verified": 2}
    items = [{
        "fingerprint": r[0], "status": r[1], "severity": r[2], "point": r[3],
        "first_seen_at": r[4] or "", "last_seen_at": r[5] or "",
        "fixed_at": r[6] or "", "verified_at": r[7] or "",
    } for r in rows]
    from services.writing_workspace import versions
    block = await (await db.execute('SELECT * FROM blocks WHERE id=?', (block_pk,))).fetchone()
    current = await versions(db, dict(block)) if block and any(item["status"] == "verified" for item in items) else {}
    for item, row in zip(items, rows):
        if item['status'] == 'verified' and (row[8] != current.get('content_version') or row[9] != current.get('evidence_version')):
            item['status'] = 'fixed'
    items.sort(key=lambda item: (order.get(item["status"], 9), -rank.get(item["severity"], 0)))
    effective_counts = {status: sum(item['status'] == status for item in items) for status in ('open', 'fixed', 'verified')}
    effective_counts['total'] = len(items)
    return {"items": items, "counts": effective_counts}


def issues_from_review(review: dict) -> list[dict]:
    """从 chapter_review 的合并结果里取 issues（兼容 dict/list）。"""
    if not review:
        return []
    issues = review.get("issues")
    if isinstance(issues, str):
        try:
            issues = json.loads(issues)
        except Exception:
            issues = []
    return [item for item in (issues or []) if isinstance(item, dict)]
