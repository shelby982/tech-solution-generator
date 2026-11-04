"""语义覆盖/评标视角核对：规范条款是否在本节正文中有落点。"""
from __future__ import annotations

import hashlib
import json
import re

from db import get_db

_CITE_RE = re.compile(r"【来源[:：]")


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:24]


async def _clauses(block: dict, thread_id: str, section: dict | None = None) -> list[dict]:
    from services.section_context import _load_spec_clauses, _select_spec_for_section
    async with get_db() as db:
        clauses = await _load_spec_clauses(db, block, thread_id)
    if section:
        return _select_spec_for_section(clauses, section)
    return clauses


async def deterministic_coverage(block: dict, section_title: str, text: str, thread_id: str = "",
                                 section: dict | None = None) -> dict:
    from services.material_search import extract_terms
    from services.material_rerank import _cosine, _vector, _norm
    clauses = await _clauses(block, thread_id, section)
    body = str(text or "")
    body_vec = _vector(body)
    body_norm = _norm(body_vec)
    covered, missing, details = 0, [], []
    semantic_covered = 0
    for clause in clauses:
        clause_text = str(clause.get("title") or "") + " " + str(clause.get("text") or "")
        terms = extract_terms(clause_text, limit=10)
        keyword_hits = sum(1 for term in terms if term in body)
        keyword_ok = keyword_hits >= max(2, min(3, len(terms) // 3))
        clause_vec = _vector(clause_text)
        semantic = _cosine(body_vec, clause_vec, _norm(clause_vec)) if clause_vec and body_vec else 0.0
        semantic_ok = semantic >= 0.12
        if keyword_ok or semantic_ok:
            covered += 1
            if semantic_ok:
                semantic_covered += 1
        else:
            key = clause.get("requirement_id") or clause.get("title") or ""
            missing.append(key)
            details.append({"requirement_id": key, "title": str(clause.get("title") or "")[:100],
                            "keyword_hits": keyword_hits, "semantic": round(semantic, 4)})
    total = len(clauses)
    citations = len(_CITE_RE.findall(body))
    return {
        "total": total, "covered": covered, "semantic_covered": semantic_covered,
        "score": round(covered / total, 3) if total else 1.0,
        "missing": missing[:8], "missing_detail": details[:8],
        "citations": citations,
        "reviewer": "deterministic",
    }


def _extract_json(raw: str) -> dict:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


async def llm_review(block: dict, section_title: str, text: str, clauses: list[dict]) -> dict:
    """评标专家语义核对；失败返回空（不阻断生成）。"""
    if not clauses:
        return {}
    from services.config_store import OPENAI_COMPATIBLE_PROVIDERS, config_store
    from infra.llm.clients import generate_oneshot_claude, generate_oneshot_openai
    configs = config_store.get_all() or []
    config = next((c for c in configs if getattr(c, "provider", "") not in {"typesafe"}), None)
    if not config:
        return {}
    schema = [{"requirement_id": c.get("requirement_id"), "title": c.get("title"),
               "text": c.get("text")} for c in clauses]
    system = (
        "你是评标专家。判断本节正文是否逐条回应了技术规范条款。"
        "只输出 JSON：{\"covered\":[\"条款ID\"],\"partial\":[\"条款ID\"],\"missing\":[\"条款ID\"],"
        "\"evidence\":{\"条款ID\":\"正文中对应的短语\"}}。"
        "标准：写清要求→响应→证据才算 covered；只提口号算 partial/missing。不要编造。"
    )
    user = "本节：" + str(section_title) + "\n技术规范条款：\n" + json.dumps(schema, ensure_ascii=False) + \
           "\n\n本节正文：\n" + str(text or "")[:7000]
    generate = generate_oneshot_openai if config.provider in OPENAI_COMPATIBLE_PROVIDERS else generate_oneshot_claude
    try:
        raw = await generate(config, system, user, max_tokens=1200, timeout=120,
                             raise_on_empty=False, call_site="coverage_review")
    except Exception:
        return {}
    data = _extract_json(raw)
    if not data:
        return {}
    covered = [str(x) for x in data.get("covered") or []]
    partial = [str(x) for x in data.get("partial") or []]
    missing = [str(x) for x in data.get("missing") or []]
    if not (covered or partial or missing):
        return {}
    return {"covered": covered, "partial": partial, "missing": missing,
            "evidence": data.get("evidence") or {}, "reviewer": "llm"}


async def _cache(block: dict, section_title: str, text_hash: str, context_version: int,
                 deterministic: dict, llm: dict) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO section_coverage_reviews(block_id,section_title,text_hash,context_version,"
            "deterministic_json,llm_json,updated_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(block_id,section_title,text_hash) DO UPDATE SET context_version=excluded.context_version,"
            "deterministic_json=excluded.deterministic_json,llm_json=excluded.llm_json,"
            "updated_at=CURRENT_TIMESTAMP",
            (int(block.get("id") or 0), str(section_title or ""), text_hash, context_version,
             json.dumps(deterministic, ensure_ascii=False), json.dumps(llm, ensure_ascii=False)),
        )
        await db.commit()


async def review_coverage(block: dict, section_title: str, text: str, *, thread_id: str = "",
                          deep: bool = False, section: dict | None = None) -> dict:
    deterministic = await deterministic_coverage(block, section_title, text, thread_id, section)
    clauses = await _clauses(block, thread_id, section)
    result = dict(deterministic)
    llm = {}
    if deep:
        llm = await llm_review(block, section_title, text, clauses)
        if llm:
            covered = set(llm.get("covered") or [])
            partial = set(llm.get("partial") or [])
            result["llm"] = llm
            result["llm_covered"] = len(covered)
            result["llm_missing"] = [str(c.get("requirement_id")) for c in clauses
                                     if str(c.get("requirement_id")) not in covered | partial]
            result["reviewer"] = "deterministic+llm"
    try:
        version = int(block.get("context_version") or 0)
        await _cache(block, section_title, _hash(text), version, deterministic, llm)
    except Exception:
        pass
    return result
