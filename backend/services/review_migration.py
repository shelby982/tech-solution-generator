"""一次性把旧 reviews 明细回填为章级当前态（幂等）。

终评统一模型上线前，工作流评审只写 reviews；上线后 chapter_reviews 是当前态。
这里只为"完全没有章评审"的 (thread, block_code) 补一条 stage='final' 记录，
保留旧分/问题；版本留空 → 终评会视为未验证并重新评审，不会把旧结果当新结论。
"""
from __future__ import annotations

import json


async def backfill_review_tables(conn) -> int:
    try:
        rows = await (await conn.execute(
            """SELECT DISTINCT r.thread_id, r.block_id
               FROM reviews r
               WHERE NOT EXISTS (
                   SELECT 1 FROM chapter_reviews cr
                   JOIN blocks b ON b.id = cr.block_id
                   WHERE b.block_id = r.block_id AND b.run_thread_id = r.thread_id
               )"""
        )).fetchall()
    except Exception:
        return 0

    written = 0
    for thread_id, block_code in rows:
        block = await (await conn.execute(
            "SELECT id, project_id, run_thread_id FROM blocks WHERE block_id=? AND run_thread_id=?",
            (block_code, thread_id),
        )).fetchone()
        if not block:
            continue
        agents = await (await conn.execute(
            "SELECT * FROM reviews WHERE thread_id=? AND block_id=? ORDER BY id",
            (thread_id, block_code),
        )).fetchall()
        if not agents:
            continue
        latest = {}
        for row in agents:
            latest[row["agent"]] = row

        def _load(raw):
            try:
                value = json.loads(raw or "[]")
                return value if isinstance(value, list) else []
            except (ValueError, TypeError):
                return []

        def finding(agent):
            row = latest.get(agent)
            if not row:
                return None
            return {
                "score": int(row["score"] or 0),
                "issues": _load(row["issues"]),
                "satisfied": row["satisfied"] or "",
                "covered_points": _load(row["covered_points"]),
                "missing_points": _load(row["missing_points"]),
                "criterion_text": row["criterion_text"] or "",
            }

        from services.review_store import combine_findings
        combined = combine_findings(finding("wang_anshi"), finding("bao_zheng"))
        await conn.execute(
            """INSERT INTO chapter_reviews
               (block_id, project_id, tech_score, comp_score, score, satisfied, criterion_text,
                covered_points, missing_points, issues, needs_material, tier, stage, thread_id,
                content_version, evidence_version, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(block_id) DO NOTHING""",
            (int(block["id"]), int(block["project_id"] or 0),
             combined["tech_score"], combined["comp_score"], combined["score"],
             combined["satisfied"], combined["criterion_text"],
             json.dumps(combined["covered_points"], ensure_ascii=False),
             json.dumps(combined["missing_points"], ensure_ascii=False),
             json.dumps(combined["issues"], ensure_ascii=False),
             json.dumps(combined["needs_material"], ensure_ascii=False),
             "legacy", "final", str(thread_id or ""), "", ""),
        )
        written += 1
    await conn.commit()
    return written
