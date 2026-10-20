#!/usr/bin/env python3
"""一次性清理：把正文里的「待核对与补充」占位内容清掉。

产品口径：正文始终是一份完整正常的内容；事实缺口留在待办/质检里。
用法：
  python scripts/cleanup_pending_placeholders.py            # 预演（只统计，不写库）
  python scripts/cleanup_pending_placeholders.py --apply    # 真正执行（写 revision 后更新）
  python scripts/cleanup_pending_placeholders.py --apply --project 29
"""
import argparse, asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from db import get_db                                    # noqa: E402
from services.chapter_cleanup import strip_pending_sections, has_pending   # noqa: E402
from services.block_store import update_block_content, add_revision        # noqa: E402


async def main(apply: bool, project_id: int | None) -> int:
    where = "WHERE (content IS NOT NULL AND content != '')"
    params: tuple = ()
    if project_id:
        where += " AND project_id = ?"
        params = (project_id,)
    async with get_db() as db:
        cursor = await db.execute(f"SELECT id, block_id, title, content FROM blocks {where}", params)
        rows = await cursor.fetchall()
    dirty = [(r['id'], r['block_id'], r['title'], r['content']) for r in rows if has_pending(r['content'])]
    total = 0
    for block_id, code, title, content in dirty:
        cleaned, removed = strip_pending_sections(content)
        total += len(removed)
        print(f"  {code} {title[:22]:22s} 清理 {len(removed):3d} 处  {len(content)} → {len(cleaned)} 字")
        if apply:
            async with get_db() as db:
                await add_revision(db, block_id, content, summary='清理待核对占位内容（一次性）', source='manual')
                await update_block_content(db, block_id, cleaned, status='done')
    print(f"\n{'已清理' if apply else '待清理'}：{len(dirty)} 章 · {total} 处占位")
    if not apply and dirty:
        print("  这是预演；加 --apply 才会写库（会先写 revision，可回滚）")
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--project', type=int, default=None)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.apply, args.project)))
