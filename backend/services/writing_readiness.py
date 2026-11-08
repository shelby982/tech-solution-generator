"""编写就绪度：大纲、证据需求、覆盖、是否允许概括写法。"""
from __future__ import annotations


async def _outline(block: dict, thread_id: str) -> tuple[bool, list[dict]]:
    from db import get_db
    from services.writing_plans import chapter_plan
    async with get_db() as db:
        plan = await chapter_plan(db, int(block.get("project_id") or 0), str(thread_id or ""),
                                  str(block.get("block_id") or ""))
    chapter = {"outline": plan.get("outline") or [], "requirements": plan.get("requirements") or [],
               "tasks": plan.get("tasks") or []}
    if chapter.get("outline"):
        return True, list(chapter["outline"])
    # 只认计划大纲；正文小节不作为大纲来源。
    return False, []


async def section_readiness(block: dict, section: dict, thread_id: str = "") -> dict:
    from services.evidence_needs import section_evidence_needs
    has_outline, _ = await _outline(block, thread_id)
    section_title = str(section.get("title") or "")
    needs = await section_evidence_needs(block, section, thread_id) if has_outline else []
    if not has_outline:
        # 无大纲：章节级需求可展示，但不能进入写作
        chapter_needs = await section_evidence_needs(block, {"title": ""}, thread_id)
        return {
            "section": section_title, "status": "no_outline", "has_outline": False,
            "needs_total": len([n for n in chapter_needs if not n.get("ignored")]),
            "covered": sum(1 for n in chapter_needs if n.get("covered") and not n.get("ignored")),
            "missing": sum(1 for n in chapter_needs if not n.get("covered") and not n.get("ignored")),
            "ignored": sum(1 for n in chapter_needs if n.get("ignored")),
            "next_action": "generate_outline", "generic_allowed": False,
        }
    active = [n for n in needs if not n.get("ignored")]
    covered = sum(1 for n in active if n.get("covered"))
    missing = len(active) - covered
    if not active:
        status = "ready"          # 没有强制证据需求，可直接写
    elif covered <= 0:
        status = "no_evidence"
    elif missing > 0:
        status = "partial"
    else:
        status = "ready"
    return {
        "section": section_title, "status": status, "has_outline": True,
        "needs_total": len(active), "covered": covered, "missing": missing,
        "ignored": sum(1 for n in needs if n.get("ignored")),
        "next_action": ("write" if status == "ready" else
                        "add_evidence" if status == "no_evidence" else "write_or_fill"),
        "generic_allowed": status in ("no_evidence", "partial"),
        "needs": active,
    }


async def chapter_readiness(block: dict, thread_id: str = "") -> dict:
    has_outline, outline = await _outline(block, thread_id)
    if not has_outline:
        item = await section_readiness(block, {"title": ""}, thread_id)
        return {"status": "no_outline", "has_outline": False, "sections": [item],
                "needs_total": item["needs_total"], "covered": item["covered"],
                "missing": item["missing"], "next_action": "generate_outline"}
    sections = [await section_readiness(block, section, thread_id) for section in outline]
    total = sum(item["needs_total"] for item in sections)
    covered = sum(item["covered"] for item in sections)
    missing = sum(item["missing"] for item in sections)
    if any(item["status"] == "no_outline" for item in sections):
        status = "no_outline"
    elif missing and covered:
        status = "partial"
    elif missing:
        status = "no_evidence"
    else:
        status = "ready"
    return {
        "status": status, "has_outline": True, "sections": sections,
        "needs_total": total, "covered": covered, "missing": missing,
        "next_action": "write" if status == "ready" else "prepare",
    }
