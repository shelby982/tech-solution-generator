"""点对点应答模板：按技术规范书原文顺序生成标准应答表。

不编造“优于/已实测”；默认“完全响应”，响应说明只给结构性承诺，
证据/对应章节从已有映射取；缺失项明确写“本章及附件”。
"""
from __future__ import annotations

import json
import re


def _clean(text: str, limit: int = 900) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value.replace("|", "\\|")


def _ids(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        data = json.loads(raw or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


async def collect_clauses(db, project_id: int, thread_id: str, root_block_id: str) -> dict:
    rows = await (await db.execute(
        "SELECT id, block_id, title, level, order_idx, spec_requirement_ids, content "
        "FROM blocks WHERE project_id=? AND run_thread_id=? ORDER BY order_idx, id",
        (project_id, thread_id))).fetchall()
    all_blocks = [dict(row) for row in rows]
    blocks = [b for b in all_blocks
              if str(b.get("block_id")) == root_block_id or str(b.get("block_id")).startswith(root_block_id + ".")]
    root = next((b for b in blocks if str(b["block_id"]) == root_block_id), None)
    if not root:
        return {"clauses": [], "missing": ["章节不存在"]}
    all_ids = []
    for block in blocks:
        all_ids.extend(_ids(block.get("spec_requirement_ids")))
    all_ids = list(dict.fromkeys(all_ids))
    if not all_ids:
        return {"clauses": [], "missing": ["本章没有绑定技术规范条款"]}
    marks = ",".join("?" for _ in all_ids)
    rows = await (await db.execute(
        f"SELECT requirement_id, title, text, mandatory_level FROM spec_requirements "
        f"WHERE project_id=? AND run_thread_id=? AND requirement_id IN ({marks}) ORDER BY order_idx",
        (project_id, thread_id, *all_ids))).fetchall()
    clauses_by_id = {str(row["requirement_id"]): dict(row) for row in rows}
    # 关联章节：本章之外、绑定了同一 requirement 的前部章节
    related: dict[str, list[str]] = {}
    for block in all_blocks:
        bid = str(block["block_id"])
        if bid == root_block_id or bid.startswith(root_block_id + "."):
            continue
        if not str(block.get("content") or "").strip():
            continue
        for rid in _ids(block.get("spec_requirement_ids")):
            related.setdefault(rid, []).append(str(block.get("title") or block["block_id"]))
    clauses, missing = [], []
    seen = set()
    for block in blocks:
        for rid in _ids(block.get("spec_requirement_ids")):
            if rid in seen:
                continue
            seen.add(rid)
            clause = clauses_by_id.get(rid)
            if not clause or not str(clause.get("text") or "").strip():
                missing.append(rid)
                continue
            clauses.append({
                "requirement_id": rid,
                "block_id": str(block.get("block_id") or ""),
                "block_title": str(block.get("title") or ""),
                "title": str(clause.get("title") or block.get("title") or ""),
                "text": str(clause.get("text") or ""),
                "mandatory": str(clause.get("mandatory_level") or "") in ("mandatory", "required", "强制", "实质性"),
                "related": related.get(rid, [])[:3],
            })
    return {"clauses": clauses, "missing": missing}


def _clause_label(clause: dict) -> str:
    base = str(clause.get("block_title") or "").strip()
    title = str(clause.get("title") or "").strip()
    tail = re.split(r"\s*>\s*", title)[-1] if title else ""
    if base and tail.startswith(base):
        tail = tail[len(base):].strip(" ·-—")
    if tail and tail not in ("", "第1条"):
        return f"{base} · {tail}"
    return base or str(clause.get("requirement_id") or "")


def render_table(clauses: list[dict]) -> dict:
    rows = []
    for index, clause in enumerate(clauses, 1):
        proof = "、".join(clause.get("related") or []) or "本章及附件"
        response = f"满足。我方按招标文件要求组织实施，具体措施见「{proof}」。"
        rows.append({
            "index": index,
            "clause_no": _clause_label(clause),
            "requirement_id": clause.get("requirement_id") or "",
            "requirement": _clean(clause.get("text") or "", 900),
            "response": "完全响应",
            "response_note": _clean(response, 300),
            "proof": proof,
            "deviation": "无偏离",
            "mandatory": bool(clause.get("mandatory")),
        })
    header = ("| 序号 | 招标条款号/章节 | 招标技术要求（原文） | 响应情况 | 响应说明 | 证明材料/对应章节 | 偏离说明 |\n"
              "|---|---|---|---|---|---|---|")
    body = []
    for row in rows:
        no = row["clause_no"] + (" ★" if row["mandatory"] else "")
        body.append(f"| {row['index']} | {_clean(no, 120)} | {row['requirement']} | {row['response']} | "
                    f"{row['response_note']} | {_clean(row['proof'], 160)} | {row['deviation']} |")
    title = "**表：技术规范书点对点应答表**\n\n"
    note = ("\n\n> 说明：本表按技术规范书原文顺序逐条应答；响应说明仅作结构性承诺，"
            "涉及人员、业绩、证书、实测指标等事实以对应证明材料为准。若存在偏离，须在投标文件中专项说明。")
    return {"markdown": title + header + "\n" + "\n".join(body) + note,
            "rows": rows, "count": len(rows)}
