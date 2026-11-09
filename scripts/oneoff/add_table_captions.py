#!/usr/bin/env python3
"""给已有正文里的表格补齐表名（`**表：名**`，表格上方一行）。

用法：python scripts/oneoff/add_table_captions.py [--project 29] [--dry]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from db import get_db  # noqa: E402
from services.body_tables import ensure_table_captions  # noqa: E402


async def main(project_id: int | None, dry: bool) -> int:
    total_blocks = 0
    total_added = 0
    async with get_db() as db:
        sql = "SELECT id, project_id, block_id, content FROM blocks WHERE content LIKE '%|%'"
        args: tuple = ()
        if project_id is not None:
            sql += " AND project_id=?"
            args = (project_id,)
        rows = await (await db.execute(sql, args)).fetchall()
        for row in rows:
            block_id, pid, code, content = row[0], row[1], row[2], row[3] or ""
            cleaned, added = ensure_table_captions(content)
            if not added:
                continue
            total_blocks += 1
            total_added += added
            print(f"  p{pid} {code}：补 {added} 个表名")
            if not dry:
                await db.execute(
                    "INSERT INTO block_revisions (block_id, content, source, summary) VALUES (?,?,?,?)",
                    (block_id, content, "script", "补表名前备份"),
                )
                await db.execute("UPDATE blocks SET content=? WHERE id=?", (cleaned, block_id))
        if not dry:
            await db.commit()
    print(f"\n  共 {total_blocks} 个章节、{total_added} 张表补了表名{'（dry-run 未写入）' if dry else ''}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=int, default=None)
    ap.add_argument("--dry", action="store_true")
    ns = ap.parse_args()
    raise SystemExit(asyncio.run(main(ns.project, ns.dry)))
