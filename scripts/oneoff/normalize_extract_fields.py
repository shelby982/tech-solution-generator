"""一次性脚本：把已提炼的「评分口径」从 requirement 归位到 score_items。

为什么需要：2026-09-21 才修正提炼字段口径（见 agents/prompts.py 的字段口径注释）——
在那之前，模型会把整段**评分细则**（优良 7-10 分 / 中等 6 分 / 未作响应不得分…）抄进
requirement。这些条目不可能有素材依据，于是匹配页满屏"无依据"（用户实测反馈）。
改 prompt 只影响**新提炼**的章节，历史数据不会自己变好。

这个脚本用**确定性规则**（domain/spec/field_normalize.py，与界面同一套判定）归位：
- 只动 requirement → score_items 一个方向，不丢信息（同一行只是换字段）；
- 带义务动词的句子（我方要做的事）不搬；
- 如果 requirement 整段都是评分口径则不动（不能把主字段清空）。

两处都要写（读者不同）：
- ``blocks`` 表：工作台 8 字段展示读它；
- checkpoint 里的 ``spec.outline_matrix``：**撰写阶段读的是它**（zhuge_liang 从 state 取矩阵）。

用法：
    cd backend
    python scripts/oneoff/normalize_extract_fields.py --project 18            # 预演（不改库）
    python scripts/oneoff/normalize_extract_fields.py --project 18 --apply    # 真正写回
    python scripts/oneoff/normalize_extract_fields.py --project 18 --thread <tid> --apply   # 指定 run
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from db import get_db                                    # noqa: E402
from domain.spec.field_normalize import normalize_row_fields   # noqa: E402
from services.block_store import (  # noqa: E402
    list_blocks, update_block_matrix_fields, update_block_requirement,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def _resolve_thread(db, project_id: int, thread_id: str | None) -> str | None:
    if thread_id:
        return thread_id
    from services.block_store import current_run_thread_id
    return await current_run_thread_id(db, project_id)


async def _normalize_blocks(db, project_id: int, thread_id: str | None, apply: bool) -> dict:
    # 只处理**这一次运行**的行：默认取最新 run。以前传 None 会把该项目所有历史 run 的
    # 行一起改（实测 128 行 vs 本次 run 的 33 行）—— 历史 run 不是用户在看的目录。
    blocks = await list_blocks(db, project_id, thread_id, fallback_all=True)
    stats = {"chapters": len(blocks), "touched": 0, "moved": 0,
             "dropped_duplicate": 0, "pruned_indicators": 0, "samples": []}
    for b in blocks:
        keep_req = b.get("requirement") or ""
        keep_score = b.get("score_items") or ""
        probe = {"requirement": keep_req, "score_items": keep_score,
                 "indicators": b.get("indicators") or ""}
        res = normalize_row_fields(probe)
        if not (res["moved"] or res["dropped"] or res["pruned_indicators"]):
            continue
        stats["touched"] += 1
        stats["moved"] += len(res["moved"])
        stats["dropped_duplicate"] += len(res["dropped"])
        stats["pruned_indicators"] += len(res["pruned_indicators"])
        if len(stats["samples"]) < 5:
            stats["samples"].append({
                "block_id": b.get("block_id"),
                "title": (b.get("title") or "")[:24],
                "moved": [m[:60] for m in res["moved"][:3]],
                "dropped": [m[:60] for m in res["dropped"][:3]],
                "pruned_indicators": [m[:60] for m in res["pruned_indicators"][:3]],
                "kept_lines": len((probe["requirement"] or "").splitlines()),
            })
        if apply:
            # ⚠️ requirement 不在 update_block_matrix_fields 的列白名单里
            # （那里是"提炼 8 字段除 requirement 外"），漏掉它会导致：
            # 统计里报了 dropped 36 条、库里却一行没删（实测踩到）。
            await update_block_requirement(db, int(b["id"]), probe["requirement"])
            await update_block_matrix_fields(db, int(b["id"]), {
                "score_items": probe["score_items"],
                "indicators": probe["indicators"],
            })
    return stats


async def _normalize_checkpoint(project_id: int, thread_id: str | None, apply: bool) -> dict:
    """把 checkpoint 里的 spec.outline_matrix 也归位（撰写阶段读它）。"""
    from orchestrator import checkpointer as ck
    from orchestrator.checkpointer import checkpointer_from_path
    from orchestrator.graph import GraphDeps, build_graph

    async with get_db() as db:
        tid = await _resolve_thread(db, project_id, thread_id)
    if not tid:
        return {"skipped": "没有运行记录"}

    stats = {"thread_id": tid, "touched": 0, "moved": 0,
             "dropped_duplicate": 0, "pruned_indicators": 0}
    async with checkpointer_from_path(ck.DB_PATH) as saver:
        graph = build_graph(GraphDeps(None, None, None, None, None, None), checkpointer=saver)
        cfg = {"configurable": {"thread_id": tid}}
        snap = await graph.aget_state(cfg)
        state = snap.values or {}
        matrix = dict((state.get("spec") or {}).get("outline_matrix") or {})
        if not matrix:
            return {**stats, "skipped": "checkpoint 里没有 outline_matrix"}
        changed = {}
        for bid, row in matrix.items():
            data = row.to_dict() if hasattr(row, "to_dict") else dict(row)
            res = normalize_row_fields(data)
            if not (res["moved"] or res["dropped"] or res["pruned_indicators"]):
                continue
            stats["touched"] += 1
            stats["moved"] += len(res["moved"])
            stats["dropped_duplicate"] += len(res["dropped"])
            stats["pruned_indicators"] += len(res["pruned_indicators"])
            changed[bid] = data
        if apply and changed:
            await graph.aupdate_state(cfg, {"spec": {"outline_matrix": changed}})
    return stats


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--thread", default=None, help="默认取该项目最新一次运行")
    ap.add_argument("--apply", action="store_true", help="不加就是预演（只打印，不写库）")
    args = ap.parse_args()

    async with get_db() as db:
        tid = await _resolve_thread(db, args.project, args.thread)
        blocks = await _normalize_blocks(db, args.project, tid, args.apply)
    ckpt = await _normalize_checkpoint(args.project, args.thread, args.apply)

    print(json.dumps({"blocks": blocks, "checkpoint": ckpt, "apply": args.apply},
                     ensure_ascii=False, indent=2))
    if not args.apply:
        print("\n（预演模式：加 --apply 才会写库）")


if __name__ == "__main__":
    asyncio.run(main())
