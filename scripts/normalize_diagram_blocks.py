#!/usr/bin/env python3
"""把正文里写法不规范的 `:::diagram` 块规整成标准格式（一次性）。

标准格式：`:::diagram type=flow|layered|timeline 标题` + 结构行 + `:::`
- 旧写法（多行、无 type、节点过长）→ 用 services.diagrams 重新解析后渲染
用法：python scripts/normalize_diagram_blocks.py [--apply] [--project 29]
"""
import argparse, asyncio, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from db import get_db                                    # noqa: E402
from services.chapter_cleanup import strip_pending_sections  # noqa: F401  (保持导入一致性)
from services.diagrams import parse_diagram, render_diagram_markdown  # noqa: E402
from services.block_store import add_revision, update_block_content  # noqa: E402

BLOCK_RE = re.compile(r'(?ms)^:::diagram([^\n]*)\n(.*?)\n:::\s*$')


def normalize_content(content: str) -> tuple[str, int]:
    changed = 0

    def repl(match):
        nonlocal changed
        head = match.group(1).strip()
        body_lines = match.group(2).splitlines()
        structure = parse_diagram(':::diagram ' + head, body_lines)
        nodes = structure.get('nodes') or []
        if len(nodes) < 2:
            return match.group(0)          # 节点不足，留给人工
        for node in nodes:
            node['label'] = str(node.get('label') or '')[:60]
        rendered = render_diagram_markdown(structure)
        if rendered.strip() != match.group(0).strip():
            changed += 1
        return rendered

    return BLOCK_RE.sub(repl, str(content or '')), changed


async def main(apply: bool, project_id: int | None) -> int:
    sql = "SELECT id, block_id, title, content FROM blocks WHERE content LIKE '%:::diagram%'"
    params = ()
    if project_id:
        sql += " AND project_id = ?"
        params = (project_id,)
    async with get_db() as db:
        rows = await (await db.execute(sql, params)).fetchall()
    total = 0
    for row in rows:
        cleaned, count = normalize_content(row['content'])
        if not count:
            continue
        total += count
        print(f"  {row['block_id']} {row['title'][:18]:18s} 规整 {count} 个图块")
        if apply:
            async with get_db() as db:
                await add_revision(db, row['id'], row['content'], summary='规整关系图块（一次性）', source='manual')
                await update_block_content(db, row['id'], cleaned, status='done')
    print(f"\n{'已规整' if apply else '待规整'}：{total} 个图块")
    if not apply and total:
        print('  预演模式；加 --apply 才写库（会先写 revision）')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--project', type=int, default=None)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.apply, args.project)))
