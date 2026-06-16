"""一次性脚本：把所有切片数 ≤1 的 scoring / evaluation material 用新逻辑重新切。
旧 material（在新切片代码上线前上传的）会保持单 chunk，导致 BM25 分配失败。
此脚本扫描这些 material，删除现有 chunks，重新跑 parse_document + 细粒度切片。

用法：
    cd backend
    python -m scripts.recut_old_materials

或在项目根目录：
    python backend/scripts/recut_old_materials.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 让脚本可以独立运行，无论从哪个目录调用
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from db import get_db
from infra.parser import parse_document
from services.block_store import update_material_parse_status, create_chunk

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def _split_for_scoring_evaluation(parsed) -> list[str]:
    """与 routes/materials.py 中同名函数保持一致：4 行/片，步长 3 滑动窗口。"""
    all_lines: list[str] = []
    for sec in parsed.sections:
        full = f"{sec.title}\n{sec.raw_content or ''}".strip()
        for ln in full.split("\n"):
            ln = ln.strip()
            if ln:
                all_lines.append(ln)
    if not all_lines:
        return []
    window, stride = 4, 3
    chunks: list[str] = []
    i = 0
    while i < len(all_lines):
        piece = "\n".join(all_lines[i:i + window]).strip()
        if piece:
            chunks.append(piece)
        i += stride
    return chunks


UPLOAD_ROOT = BACKEND_DIR / "data" / "uploads"


async def find_targets() -> list[dict]:
    """找出所有 scoring / evaluation 角色、且 chunks 数 ≤ 1 的 material。"""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT m.id, m.project_id, m.filename, m.role,
                   (SELECT COUNT(*) FROM material_chunks WHERE material_id=m.id) AS chunks
            FROM materials m
            WHERE m.role IN ('scoring', 'evaluation')
        """)
        rows = await cursor.fetchall()
    return [dict(r) for r in rows if r["chunks"] <= 1]


async def recut_one(mat: dict) -> tuple[bool, str]:
    mat_id = mat["id"]
    project_id = mat["project_id"]
    filename = mat["filename"]
    abs_path = UPLOAD_ROOT / str(project_id) / filename

    if not abs_path.is_file():
        return False, f"文件不存在: {abs_path}"

    suffix = abs_path.suffix.lower()
    try:
        async with get_db() as db:
            await update_material_parse_status(db, mat_id, "parsing")

        parsed = await asyncio.to_thread(
            parse_document, str(abs_path), suffix=suffix, filename=filename
        )

        chunks = _split_for_scoring_evaluation(parsed)
        if not chunks:
            async with get_db() as db:
                await update_material_parse_status(db, mat_id, "failed")
            return False, "解析后无可用文本"

        async with get_db() as db:
            await db.execute("DELETE FROM material_chunks WHERE material_id = ?", (mat_id,))
            for idx, content in enumerate(chunks):
                await create_chunk(db, material_id=mat_id, chunk_index=idx, content=content)
            await update_material_parse_status(db, mat_id, "done")

        return True, f"切片 {len(chunks)} 条"
    except Exception as e:
        logger.exception(f"重切失败 material_id={mat_id}: {e}")
        try:
            async with get_db() as db:
                await update_material_parse_status(db, mat_id, "failed")
        except Exception:
            pass
        return False, str(e)


async def main():
    targets = await find_targets()
    if not targets:
        logger.info("没有需要重切的 material（所有 scoring/evaluation 切片数都 > 1）")
        return

    logger.info(f"发现 {len(targets)} 个需要重切的 material:")
    for m in targets:
        logger.info(f"  id={m['id']} project={m['project_id']} role={m['role']} "
                    f"file={m['filename']} 当前chunks={m['chunks']}")

    print("\n按回车开始重切，Ctrl+C 取消...")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        return

    success = 0
    fail = 0
    for m in targets:
        logger.info(f"[{m['id']}] {m['filename']} 开始重切...")
        ok, msg = await recut_one(m)
        if ok:
            success += 1
            logger.info(f"[{m['id']}] ✓ {msg}")
        else:
            fail += 1
            logger.warning(f"[{m['id']}] ✗ {msg}")

    logger.info(f"\n完成：成功 {success} 个，失败 {fail} 个")


if __name__ == "__main__":
    asyncio.run(main())
