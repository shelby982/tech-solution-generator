"""匹配总览：把「章节 × 应答条目 × 依据 × 素材」聚合成一屏可看的统计。

为什么需要：匹配跑完之后，界面上只有一个章节行上的「依据 N」小徽标 —— 用户看不到
「哪些章节缺依据、缺哪几条、哪份素材没被用上」。这个模块只做**投影**（零模型调用、
零写库），把同一份 `blocks.source` 与应答条目聚合成：

- 顶部总览：章节数 / 依据数 / 有依据章节数 / 条目覆盖数
- 每章明细：依据数、条目级 / 章节级各多少、未覆盖的条目 id（撰写阶段做覆盖度自检要用）
- 素材侧：每份素材供了哪些章、几条依据（反向视图）

放在 domain 层而不是路由里：它是纯函数，可单测；路由只负责取数与序列化。
"""

from __future__ import annotations

from domain.material.models import MaterialEvidence
from domain.spec.response_items import expand_response_items


def _evidence_rows(block: dict) -> list[dict]:
    """blocks.source（可能是 JSON 字符串 / 已解析列表 / 空）→ 依据字典列表。"""
    import json

    raw = (block or {}).get("source")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def build_match_overview(blocks: list[dict]) -> dict:
    """``blocks``（含 ``source`` 与 8 字段）→ 匹配总览。顺序沿用传入顺序（= order_idx）。"""
    chapters: list[dict] = []
    materials: dict[int, dict] = {}

    req_total = req_covered = 0
    act_total = act_covered = 0          # 只统计"要回应的事项"（见 response_items.is_informative）
    ev_total = ev_item_level = ev_chapter_level = 0
    with_evidence = 0

    for b in blocks or []:
        bid = str((b or {}).get("block_id") or (b or {}).get("id") or "")
        if not bid:
            continue
        items = expand_response_items(bid, b or {})
        rows = _evidence_rows(b)

        covered_ids: set[str] = set()
        for r in rows:
            for iid in r.get("item_ids") or []:
                covered_ids.add(iid)

        # 条目覆盖只统计**真实存在的条目**（脏 id 不算覆盖，避免"覆盖 6/6"式虚高）
        valid_ids = {it["item_id"] for it in items}
        covered = len(covered_ids & valid_ids)
        req_total += len(items)
        req_covered += covered
        # 「要回应的事项」= 排除评分口径/量化档位（它们不可能有素材依据，混进来就是满屏"无依据"）
        actionable = [it for it in items if not it.get("informative")]
        act_ids = {it["item_id"] for it in actionable}
        act_hit = len(covered_ids & act_ids)
        act_total += len(actionable)
        act_covered += act_hit

        item_level = sum(1 for r in rows if r.get("item_ids"))
        ev_total += len(rows)
        ev_item_level += item_level
        ev_chapter_level += len(rows) - item_level
        if rows:
            with_evidence += 1

        for r in rows:
            mid = r.get("material_id")
            if mid is None:
                continue
            rec = materials.setdefault(int(mid), {
                "material_id": int(mid),
                "filename": r.get("filename") or f"素材 #{mid}",
                "chapters": [], "evidence": 0, "items": 0,
            })
            rec["evidence"] += 1
            rec["items"] += len([i for i in (r.get("item_ids") or []) if i in valid_ids])
            if bid not in [c["block_id"] for c in rec["chapters"]]:
                rec["chapters"].append({"block_id": bid, "title": (b or {}).get("title") or ""})

        chapters.append({
            "block_id": bid,
            "title": (b or {}).get("title") or "",
            "level": int((b or {}).get("level") or 1),
            "evidence": len(rows),
            "item_evidence": item_level,
            "chapter_level_evidence": len(rows) - item_level,
            "items_total": len(items),
            "items_covered": covered,
            "actionable_items_total": len(actionable),
            "actionable_items_covered": act_hit,
            "informative_items_total": len(items) - len(actionable),
            # 未覆盖的条目 id（撰写阶段覆盖度自检的输入；无依据不阻断撰写）。
            # 只列"要回应的事项"——评分口径条目永远没有依据，列出来只是噪声。
            "uncovered_item_ids": [it["item_id"] for it in actionable
                                   if it["item_id"] not in covered_ids],
            "uncovered_texts": [it["text"] for it in actionable
                                if it["item_id"] not in covered_ids],
        })

    total = len(chapters)
    return {
        "chapters": {
            "total": total,
            "with_evidence": with_evidence,
            "without_evidence": total - with_evidence,
        },
        "requirements": {
            "total": req_total,
            "covered": req_covered,
            "uncovered": max(req_total - req_covered, 0),
            # 界面上的"条目覆盖 x/y"用这两个：分母只算要回应的事项，
            # 否则评分档位（优良 7-10 分…）会把分母撑大、覆盖率永远难看
            "actionable_total": act_total,
            "actionable_covered": act_covered,
            "actionable_uncovered": max(act_total - act_covered, 0),
        },
        "evidence": {
            "total": ev_total,
            "item_level": ev_item_level,
            "chapter_level": ev_chapter_level,
        },
        "materials": sorted(materials.values(), key=lambda m: (-m["evidence"], m["material_id"])),
        "chapters_detail": chapters,
    }


def evidence_from_chunk(chunk: dict, *, item_ids: list[str] | None = None,
                        kind: str | None = None, locator: str | None = None) -> MaterialEvidence:
    """素材切片 → 契约（人工挂接用）。

    ``chunk`` 需要 ``id / material_id / content``（来自 list_chunks_with_filename），
    ``filename`` 可选。页码与出处由调用方按需给；这里复用 assign 的 build_locator
    保证人工挂接的出处格式与自动归属一致（同一份数据不该有两种出处写法）。
    """
    from infra.retrieval.assign import build_locator, classify_kind

    content = chunk.get("content") or ""
    return MaterialEvidence(
        chunk_id=int(chunk.get("id") or chunk.get("chunk_id") or 0),
        material_id=int(chunk.get("material_id") or 0),
        content=content,
        filename=chunk.get("filename") or "",
        locator=locator or build_locator(chunk),
        page=int(chunk.get("page") or 0),
        kind=kind or classify_kind(content),
        source_kind="ours",
        by="human",
        confidence=1.0,
        score=0.0,
        item_ids=list(item_ids or []),
        locked=True,      # 人工挂接：重匹配不覆盖（见 assign.assign_evidence 的 only_chunk_ids）
    )
