"""章节写作上下文：把标包规范、计划要求、本节正文、素材组装给模型。

写作层原来的输入只有 section.points / basis.ref / 整章前 2000 字；
本模块补上：
- block.spec_requirement_ids → spec_requirements 条款全文；
- writing_plans 本章 requirements；
- 当前节正文（按 ## 标题切）；
- 本节按需素材检索（material_chunks）。
"""
from __future__ import annotations

import json
import re

from db import get_db

SECTION_CONTEXT_VERSION = 2
_SPEC_MAX = 4
_REQ_MAX = 8
_MATERIAL_MAX = 4


def section_requirement_ids(section: dict) -> set[str]:
    """大纲节可显式声明 requirement_ids / evidence_need_ids。"""
    ids = []
    for key in ("requirement_ids", "evidence_need_ids"):
        value = (section or {}).get(key)
        if isinstance(value, list):
            ids.extend(str(x) for x in value if str(x).strip())
        elif isinstance(value, str):
            ids.extend(str(x) for x in value.split(",") if str(x).strip())
    return set(ids)


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        data = json.loads(str(value or "[]"))
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def section_body(content: str, title: str) -> str:
    """取当前节正文；找不到标题时返回空串，由调用方兜底。"""
    from services.section_outline import section_ranges
    text = str(content or "")
    for name, start, end in section_ranges(text):
        if name.strip() == str(title or "").strip():
            return text[start:end].strip()
    # 标题不完全一致时做包含匹配；仍找不到才空
    for name, start, end in section_ranges(text):
        if title and (str(title) in name or name in str(title)):
            return text[start:end].strip()
    return ""


async def _load_spec_clauses(db, block: dict, thread_id: str) -> list[dict]:
    ids = [str(x) for x in _json_list(block.get("spec_requirement_ids")) if str(x).strip()]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = await (await db.execute(
        f"SELECT requirement_id, topic, title, text, mandatory_level, verification_method "
        f"FROM spec_requirements WHERE project_id=? AND requirement_id IN ({marks}) "
        f"AND (run_thread_id=? OR COALESCE(run_thread_id,'')='') ORDER BY order_idx",
        (int(block.get("project_id") or 0), *ids, str(thread_id or "")),
    )).fetchall()
    if not rows:
        rows = await (await db.execute(
            f"SELECT requirement_id, topic, title, text, mandatory_level, verification_method "
            f"FROM spec_requirements WHERE project_id=? AND requirement_id IN ({marks}) ORDER BY order_idx",
            (int(block.get("project_id") or 0), *ids),
        )).fetchall()
    out = []
    for row in rows[:_SPEC_MAX]:
        text = str(row["text"] or "").strip()
        if text:
            out.append({"requirement_id": row["requirement_id"], "topic": row["topic"] or "",
                        "title": row["title"] or "", "text": text[:900],
                        "mandatory_level": row["mandatory_level"] or "",
                        "verification_method": row["verification_method"] or ""})
    return out


async def _load_plan_requirements(db, block: dict, thread_id: str) -> list[dict]:
    from services.writing_plans import plan_of_block
    found = await plan_of_block(db, int(block.get("project_id") or 0),
                                str(thread_id or ""), str(block.get("block_id") or ""))
    if not found:
        return []
    _, plan = found
    chapter = next((item for item in plan.get("chapters") or []
                    if str(item.get("block_id")) == str(block.get("block_id"))), None)
    if not chapter:
        return []
    wanted = section_requirement_ids(block.get("_section") or {})
    out = []
    for item in (chapter.get("requirements") or [])[:_REQ_MAX]:
        if wanted:
            rid = str((item or {}).get("spec_requirement_id") or (item or {}).get("id") or "")
            if rid not in wanted:
                continue
        text = str((item or {}).get("text") or "").strip()
        if text:
            out.append({"id": item.get("id") or "", "text": text,
                        "type": item.get("type") or "text",
                        "source": item.get("source") or ""})
    return out


async def _load_package(db, thread_id: str) -> dict:
    row = await (await db.execute(
        "SELECT wr.package_no, wr.package_id, p.display_name, p.package_code "
        "FROM workflow_runs wr LEFT JOIN packages p ON p.id=wr.package_id WHERE wr.thread_id=?",
        (str(thread_id or ""),),
    )).fetchone()
    if not row:
        return {}
    return {"package_no": row["package_no"] or "", "package_id": row["package_id"],
            "name": row["display_name"] or "", "package_code": row["package_code"] or ""}


async def _load_manual_materials(db, block: dict, section: str) -> str:
    from services.material_context import chapter_material_context, render_for_prompt
    context = await chapter_material_context(db, int(block.get("id") or 0),
                                             str(block.get("block_id") or ""), section)
    return render_for_prompt(context)


async def _search_materials(db, block: dict, section: dict, spec: list[dict], reqs: list[dict]) -> list[dict]:
    from services.material_rerank import search_materials
    return await search_materials(db, int(block.get("project_id") or 0), section, spec, reqs, limit=_MATERIAL_MAX)


def _select_spec_for_section(spec: list[dict], section: dict) -> list[dict]:
    """章节规范条款较多时，优先取与本节标题/要点/依据相关的条款。"""
    if len(spec) <= 2:
        return spec
    from services.material_search import extract_terms
    section_text = " ".join([
        str(section.get("title") or ""),
        " ".join(str(x) for x in (section.get("points") or [])),
        " ".join(str((b or {}).get("ref") or "") for b in (section.get("basis") or [])),
    ])
    section_terms = set(extract_terms(section_text, limit=24))
    if not section_terms:
        return spec[:3]
    scored = []
    for clause in spec:
        blob = str(clause.get("title") or "") + " " + str(clause.get("text") or "")
        clause_terms = set(extract_terms(blob, limit=16))
        hits = len(section_terms & clause_terms)
        scored.append((hits, clause))
    scored.sort(key=lambda item: (-item[0], spec.index(item[1])))
    picked = [clause for hits, clause in scored if hits > 0][:2]
    return picked or spec[:3]


async def build_section_context(block: dict, section: dict, thread_id: str = "") -> dict:
    async with get_db() as db:
        spec = await _load_spec_clauses(db, block, thread_id)
        wanted = section_requirement_ids(section)
        if wanted:
            spec = [item for item in spec if str(item.get("requirement_id")) in wanted]
        spec = _select_spec_for_section(spec, section)
        reqs = await _load_plan_requirements(db, dict(block, _section=section), thread_id)
        package = await _load_package(db, thread_id or str(block.get("run_thread_id") or ""))
        manual = await _load_manual_materials(db, block, str(section.get("title") or ""))
        materials = await _search_materials(db, block, section, spec, reqs)
        facts_row = await (await db.execute('SELECT facts FROM project_writing_facts WHERE project_id=?', (block['project_id'],))).fetchone()
        import json
        facts = json.loads(facts_row['facts']) if facts_row else {}
    return {"project_facts": facts, "package": package, "spec_clauses": spec, "chapter_requirements": reqs,
            "section_text": section_body(block.get("content") or "", str(section.get("title") or "")),
            "manual_materials": manual, "materials": materials}


def render_context(context: dict) -> str:
    """把上下文渲染成模型可读文本；空项不占位。"""
    parts: list[str] = []
    package = context.get("package") or {}
    if package:
        label = package.get("name") or package.get("package_code") or package.get("package_no") or ""
        if label:
            parts.append("【当前标包】" + str(label)[:80])
    if context.get('project_facts'):
        parts.append('【用户确认的项目统一资料】\n' + '\n'.join(f'- {key}：{value}' for key, value in context['project_facts'].items()))
    spec = context.get("spec_clauses") or []
    if spec:
        lines = ["【本章技术规范条款（必须逐条回应）】"]
        for item in spec:
            lines.append(f"- [{item.get('requirement_id')}] {item.get('title') or ''}")
            lines.append(f"  原文：{item.get('text') or ''}")
            if item.get("verification_method"):
                lines.append(f"  验证方式：{item.get('verification_method')}")
        parts.append("\n".join(lines))
    reqs = context.get("chapter_requirements") or []
    if reqs:
        parts.append("【本章编写要求】\n" + "\n".join(
            f"- {item.get('id') or ''} {item.get('text')}" for item in reqs))
    section_text = str(context.get("section_text") or "").strip()
    if section_text:
        parts.append("【当前节已有正文（用于衔接，不要整段照抄）】\n" + section_text[:2200])
    materials = context.get("materials") or []
    manual = str(context.get("manual_materials") or "").strip()
    if materials:
        lines = ["【自动检索参考（仅用于理解背景，不得作为我方事实或承诺的依据）】"]
        for item in materials:
            page = f"第{item.get('page')}页" if item.get("page") else ""
            lines.append(f"- {item.get('filename')} {page}：{item.get('content')}")
        parts.append("\n".join(lines))
    if manual:
        parts.append(manual)
    return "\n\n".join(parts)


async def coverage_for_section(block: dict, thread_id: str, text: str) -> dict:
    async with get_db() as db:
        spec = await _load_spec_clauses(db, block, thread_id)
    return coverage_for_text(text, {"spec_clauses": spec})


def coverage_for_text(text: str, context: dict) -> dict:
    """轻量覆盖检查：规范条款的关键要素是否在正文出现。"""
    from services.material_search import extract_terms
    clauses = context.get("spec_clauses") or []
    missing = []
    missing_detail = []
    covered = 0
    for clause in clauses:
        terms = extract_terms(str(clause.get("title") or ""), str(clause.get("text") or ""), limit=10)
        if not terms:
            continue
        hits = sum(1 for term in terms if term in str(text or ""))
        if hits >= max(2, min(3, len(terms) // 3)):
            covered += 1
        else:
            key = clause.get("requirement_id") or clause.get("title") or ""
            missing.append(key)
            missing_detail.append({"requirement_id": key, "title": str(clause.get("title") or "")[:80]})
    return {"total": len(clauses), "covered": covered, "missing": missing[:8],
            "missing_detail": missing_detail[:8]}
