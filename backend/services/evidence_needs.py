"""证据需求：把本章要求/技术规范/评审补料，派生成"这一节需要什么证据"。

只做派生，不落新表；事实源仍是 writing_plans / spec_requirements / chapter_reviews。
"""
from __future__ import annotations

import hashlib
import json
import re


def _need_id(kind: str, key: str) -> str:
    return f"{kind}:{hashlib.sha1(str(key).encode('utf-8')).hexdigest()[:12]}"


def _section_ids(section: dict) -> set[str]:
    ids = []
    for key in ("requirement_ids", "evidence_need_ids"):
        value = (section or {}).get(key)
        if isinstance(value, list):
            ids.extend(str(x) for x in value if str(x).strip())
        elif isinstance(value, str):
            ids.extend(str(x) for x in value.split(",") if str(x).strip())
    return set(ids)


def _is_evidence_text(text: str) -> bool:
    """写作指令/评分档位不是证据需求；只保留需要外部事实支撑的要求。"""
    text = str(text or "")
    if re.search(r"优良|中等|一般|分值|得分|评分|响应度", text):
        return False
    if re.search(r"写清楚|写明|说明|阐述|描述", text) and not re.search(
            r"提供|证明|材料|数据|案例|业绩|资质|证书|合同|报告|截图|指标|参数|配置|部署", text):
        return False
    return True


def _query(text: str, section: dict) -> str:
    from services.material_search import extract_terms
    terms = extract_terms(str(section.get("title") or ""), str(text or ""), limit=8)
    return " ".join(terms)


async def _plan_requirements(block: dict, thread_id: str) -> list[dict]:
    from db import get_db
    from services.writing_plans import plan_of_block
    async with get_db() as db:
        found = await plan_of_block(db, int(block.get("project_id") or 0), str(thread_id or ""),
                                    str(block.get("block_id") or ""))
    if not found:
        return []
    chapter = next((item for item in found[1].get("chapters") or []
                    if str(item.get("block_id")) == str(block.get("block_id"))), None)
    return list((chapter or {}).get("requirements") or [])


async def _spec_clauses(block: dict, thread_id: str, section: dict) -> list[dict]:
    from services.section_context import _load_spec_clauses, _select_spec_for_section
    from db import get_db
    async with get_db() as db:
        clauses = await _load_spec_clauses(db, block, thread_id)
    return _select_spec_for_section(clauses, section)


async def _review_needs(block: dict, section_title: str) -> list[dict]:
    try:
        from services.chapter_review import load_review
        review = await load_review(int(block.get("id") or 0))
    except Exception:
        return []
    if not review:
        return []
    out = []
    for issue in review.get("needs_material") or []:
        issue_section = str(issue.get("section") or "").strip()
        if issue_section and issue_section != str(section_title or "").strip():
            continue
        text = str(issue.get("material_query") or issue.get("point") or "").strip()
        if text:
            out.append({"text": text, "reason": str(issue.get("point") or "")[:160],
                        "severity": issue.get("severity") or "medium"})
    return out


async def section_evidence_needs(block: dict, section: dict, thread_id: str = "") -> list[dict]:
    """返回本节证据需求；没有派生需求时返回空列表（不要伪造）。"""
    from db import get_db
    section_title = str(section.get("title") or "")
    requirements = await _plan_requirements(block, thread_id)
    specs = await _spec_clauses(block, thread_id, section)
    wanted = _section_ids(section)
    if wanted:
        requirements = [item for item in requirements
                        if str((item or {}).get("spec_requirement_id") or (item or {}).get("id") or "") in wanted]
        specs = [item for item in specs if str(item.get("requirement_id") or "") in wanted]
    reviews = await _review_needs(block, section_title)

    # 用 requirement id / 文本去重，避免 plan 已带 spec 条款又重复派生
    seen: set[str] = set()
    needs: list[dict] = []

    def add(kind: str, key: str, text: str, source_label: str, *, requirement_id: str = "",
            required: bool = True, reason: str = ""):
        text = str(text or "").strip()
        if not text or not _is_evidence_text(text):
            return
        dedupe = requirement_id or text[:120]
        if dedupe in seen:
            return
        seen.add(dedupe)
        need_id = _need_id(kind, key)
        needs.append({
            "need_id": need_id,
            "section": section_title,
            "kind": kind,
            "text": text,
            "source_label": source_label,
            "requirement_id": requirement_id or need_id,
            "required": bool(required),
            "reason": reason,
            "query": _query(text, section),
            "covered": False,
            "evidence_count": 0,
        })

    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            continue
        text = str(requirement.get("text") or "").strip()
        source = str(requirement.get("source") or "")
        requirement_id = str(requirement.get("spec_requirement_id") or "")
        kind = "spec" if (source == "spec" or requirement_id) else "plan"
        add(kind, f"plan:{section_title}:{index}:{requirement_id or text[:80]}", text,
            "技术规范书" if kind == "spec" else "本章编写要求",
            requirement_id=requirement_id, required=True)

    for clause in specs:
        requirement_id = str(clause.get("requirement_id") or "")
        text = str(clause.get("text") or "").strip()
        add("spec", f"spec:{requirement_id}", text, "技术规范书",
            requirement_id=requirement_id, required=True,
            reason=str(clause.get("title") or ""))

    for index, item in enumerate(reviews):
        add("review", f"review:{section_title}:{index}", item.get("text") or "",
            "评审补料要求", required=False, reason=item.get("reason") or "")

    # 绑定覆盖：requirement_id 精确匹配；老数据没有 requirement_id 时不做假覆盖
    if needs:
        async with get_db() as db:
            cols = {row[1] for row in await (await db.execute(
                "PRAGMA table_info(chapter_material_chunks)")).fetchall()}
            if "requirement_id" in cols:
                rows = await (await db.execute(
                    "SELECT requirement_id, COUNT(*) FROM chapter_material_chunks "
                    "WHERE block_id=? AND section=? GROUP BY requirement_id",
                    (int(block.get("id") or 0), section_title))).fetchall()
                counts = {str(row[0] or ""): int(row[1] or 0) for row in rows}
                for need in needs:
                    key = str(need.get("requirement_id") or "")
                    need["evidence_count"] = counts.get(key, 0) if key else 0
                    need["covered"] = bool(key and counts.get(key, 0) > 0)

    # 忽略/恢复状态：按 block + section + need_id 持久化
    if needs:
        async with get_db() as db:
            rows = await (await db.execute(
                "SELECT need_id, action, reason FROM evidence_need_actions "
                "WHERE block_id=? AND section=?", (int(block.get("id") or 0), section_title))).fetchall()
        actions = {str(row[0]): {"action": str(row[1] or ""), "reason": str(row[2] or "")} for row in rows}
        for need in needs:
            state = actions.get(str(need["need_id"]))
            need["ignored"] = bool(state and state["action"] == "ignore")
            need["ignore_reason"] = state["reason"] if state else ""
            need["status"] = ("ignored" if need["ignored"] else
                              "covered" if need.get("covered") else
                              "candidate" if int(need.get("evidence_count") or 0) > 0 else "open")
    return needs
