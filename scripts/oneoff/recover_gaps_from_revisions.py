#!/usr/bin/env python3
"""从"清理前"的 revision 里把【待补充】重新分类并落库成待办。

分类规则（产品口径）：
- **表格待填**（占位在 Markdown 表格行内）→ 留在正文表格里，不进待办
- **依据缺失**（其余占位）→ 落库为「依据核对」待办

一次性使用；重复执行会先清空本项目 GEN_GAP 再重建（幂等）。
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from db import get_db
from services.chapter_cleanup import classify_placeholders, looks_like_basis, record_generation_gaps

async def main():
    async with get_db() as db:
        # 1) 从"清理前"的 revision 重新分类
        cur = await db.execute(
            """SELECT br.block_id, b.block_id AS code, b.title, br.content
               FROM block_revisions br JOIN blocks b ON b.id = br.block_id
               WHERE b.project_id = 29 AND br.summary LIKE '清理待核对占位内容%'"""
        )
        rows = await cur.fetchall()
        plan = {}
        for r in rows:
            cls = classify_placeholders(r['content'] or '')
            basis = list(cls['basis'])
            plan[r['code']] = basis
            print(f"  {r['code']} {r['title'][:14]:14s} 依据缺失 {len(basis)} · 表格待填 {len(cls['table'])}")

        # 2) 删除现有 GEN_GAP，按新分类重建（只留依据缺失）
        await db.execute("DELETE FROM quality_findings WHERE check_code='GEN_GAP' AND project_id=29")
        await db.commit()
        total = 0
        for code, gaps in plan.items():
            if not gaps:
                continue
            total += await record_generation_gaps(db, 29, '24840930-be03-4745-99b3-8626200cf5ec', code, gaps)
        print(f"\n  重建后「依据核对」合计 {total} 条（表格待填不再计入）")

asyncio.run(main())
