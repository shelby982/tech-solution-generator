"""全量评审跑批（不依赖 HTTP server）：逐章 review_block，进度写日志，结果落库。"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_db
from services.chapter_review import review_block

THREAD = "24840930-be03-4745-99b3-8626200cf5ec"
PROJECT = 29
LOG = "/tmp/full_review.log"

def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

async def targets():
    async with get_db() as db:
        reviewed = {int(r[0]) for r in await (await db.execute("SELECT block_id FROM chapter_reviews")).fetchall()}
        rows = await (await db.execute(
            "SELECT id, block_id, title FROM blocks WHERE project_id=? AND run_thread_id=? "
            "AND COALESCE(content,'')<>'' ORDER BY id", (PROJECT, THREAD))).fetchall()
    return [dict(id=r[0], block_id=r[1], title=r[2]) for r in rows if int(r[0]) not in reviewed]

async def main():
    pending = await targets()
    log(f"待评 {len(pending)} 章：{', '.join(p['block_id'] for p in pending)}")
    sem = asyncio.Semaphore(2)
    done = failed = 0
    async def one(block):
        nonlocal done, failed
        async with sem:
            started = time.time()
            try:
                result = await review_block(int(block["id"]))
                stats = (result.get("tier"), result.get("score"), len(result.get("issues") or []))
                log(f"✓ {block['block_id']} {block['title'][:14]} · tier={stats[0]} · {stats[1]} 分 · {stats[2]} 条问题 · {time.time()-started:.0f}s")
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"✗ {block['block_id']} 失败：{exc}")
    await asyncio.gather(*(one(b) for b in pending))
    log(f"完成：成功 {done} · 失败 {failed}")

asyncio.run(main())
