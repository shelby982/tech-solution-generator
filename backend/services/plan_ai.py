"""计划相关的 AI 能力（答应用户两个决定）：

1. **父章精简为总述**：父章自己写了整章正文，但计划只要求 300–500 字总述
   → 用模型把现有正文压成"总述（全貌 + 3 个结论 + 子节索引）"，旧稿进版本记录。
2. **跨语义条款匹配**：理解类章节与招标技术条款在文字上没有重合，
   关键词匹配为空 → 用模型判断"每条条款应该由哪一章响应"。
"""

from __future__ import annotations

import json
import re

from aiosqlite import Connection

from services.block_store import add_revision, get_block, list_blocks, update_block_content

SUMMARY_MIN, SUMMARY_MAX = 280, 620


def _clean(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^```[a-z]*\n|```$", "", text).strip()
    return text


async def _call_model(system: str, payload: dict, *, call_site: str) -> str:
    from agents.chapter_team import call_role

    raw, _model = await call_role("text", system, payload, {})
    return _clean(raw)


async def condense_to_summary(db: Connection, project_id: int, thread_id: str, block_id: str) -> dict:
    """把父章正文压缩为总述（300–500 字），旧稿写入版本记录。"""
    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    block = next((b for b in blocks if str(b.get("block_id")) == str(block_id)), None)
    if block is None:
        raise ValueError("找不到该章节")
    content = str(block.get("content") or "")
    if not content.strip():
        raise ValueError("本章还没有正文，无需精简")
    children = [str(b.get("title") or "")
                for b in blocks
                if str(b.get("block_id") or "").startswith(str(block_id) + ".")
                and str(b.get("block_id")).count(".") == str(block_id).count(".") + 1]
    summary = ""
    try:
        system = (
            "你是投标文件主编。把这一章的现有正文压缩成一段**总述**，供父章节正文使用。"
            "要求：1) 300–500 字；2) 结构为「本章覆盖什么（全貌）→ 3 个关键结论 → 各子节索引」；"
            "3) 不复述子节细节，不堆数据，不写套话；4) 直接输出总述正文，不要标题、不要解释。"
        )
        payload = {
            "chapter_title": block.get("title"),
            "child_chapters": children,
            "existing_content": content[:8000],
        }
        summary = await _call_model(system, payload, call_site="plan_condense_summary")
    except Exception:
        summary = ""
    if not summary or len(summary) < 80:
        # 兜底：用子节标题拼一段总述（保证不阻断）
        index = "、".join(children[:8]) if children else "（无子节）"
        summary = (
            f"本章围绕「{block.get('title')}」展开，覆盖以下 {len(children)} 个方面：{index}。"
            "总体判断：建设必要性充分、现状存在明确差距、目标与范围清晰、重点难点有对应措施。"
            "详细论述见下列各节。"
        )[:SUMMARY_MAX]
    summary = summary[: max(SUMMARY_MAX, SUMMARY_MIN + 40)]
    # 旧稿进版本记录（可回滚）
    try:
        await add_revision(db, block.get("id"), content, "精简为总述前存档", "condense")
    except Exception:
        pass
    await update_block_content(db, block.get("id"), summary, status=block.get("status") or "done")
    return {"block_id": str(block_id), "summary": summary, "words": len(summary.replace(" ", "")),
            "previous_words": len(content.replace(" ", "")), "children": len(children)}


async def llm_match_clauses(db: Connection, project_id: int, thread_id: str,
                            root_block_id: str) -> dict:
    """用模型把招标技术条款挂到章节（跨语义匹配），失败时退回关键词匹配。"""
    from services.writing_plans import _bind_clauses_to_chapters, _writable_nodes

    blocks = [dict(b) for b in await list_blocks(db, project_id, thread_id)]
    root = next((b for b in blocks if str(b.get("block_id")) == str(root_block_id)), None)
    if root is None:
        raise ValueError("找不到该一级目录")
    chapters = _writable_nodes(blocks, root)
    if not chapters:
        raise ValueError("该目录下没有可写章节")
    rows = await (await db.execute(
        "SELECT requirement_id, topic, title, mandatory_level FROM spec_requirements "
        "WHERE run_thread_id=? ORDER BY order_idx", (thread_id,),
    )).fetchall()
    clauses = [dict(row) for row in rows]
    if not clauses:
        raise ValueError("没有可匹配的招标条款")

    matched: dict[str, list[str]] = {}
    try:
        system = (
            "你是投标响应策划专家。给定若干章节与一批招标技术条款，判断**每条条款应当由哪一章来响应**。"
            "规则：1) **宁缺勿滥**：只有语义明确相关才分配，每章最多 3 条；"
            "2) 同一条款若确实跨章共用，可分配给多章；3) 与所有章节都无关的条款不要分配；"
            "4) 理解类、方案类章节即使字面不重合也要按语义判断；5) 优先分配强制性条款；6) 只输出 JSON："
            '{"<章节 block_id>": ["<requirement_id>", ...], ...}'
        )
        payload = {
            "chapters": [{"block_id": c.get("block_id"), "title": c.get("title")} for c in chapters],
            "clauses": [{"requirement_id": c.get("requirement_id"),
                         "topic": c.get("topic"), "title": c.get("title"),
                         "mandatory": str(c.get("mandatory_level") or "") in ("mandatory", "required")}
                        for c in clauses],
        }
        raw = await _call_model(system, payload, call_site="plan_match_clauses")
        parsed = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        valid_ids = {str(c.get("requirement_id")) for c in clauses}
        valid_blocks = {str(c.get("block_id")) for c in chapters}
        for key, ids in (parsed or {}).items():
            if str(key) not in valid_blocks or not isinstance(ids, list):
                continue
            picked = [str(i) for i in ids if str(i) in valid_ids][:3]
            if picked:
                matched[str(key)] = picked
    except Exception:
        matched = {}

    if matched:
        for block_id, ids in matched.items():
            await db.execute(
                "UPDATE blocks SET spec_requirement_ids=? WHERE project_id=? AND run_thread_id=? AND block_id=?",
                (json.dumps(ids), project_id, thread_id, block_id),
            )
        await db.commit()
        return {"source": "llm", "chapters": len(matched),
                "assignments": {k: len(v) for k, v in matched.items()}}
    # 退回关键词匹配
    bound = await _bind_clauses_to_chapters(db, project_id, thread_id, chapters)
    return {"source": "keyword", "chapters": len(bound), "assignments": bound}
