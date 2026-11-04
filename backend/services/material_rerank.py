"""素材混合检索/重排：关键词召回 + 字符 n-gram TF-IDF + 规范覆盖 + 多样性。"""
from __future__ import annotations

import math
import re
from collections import Counter

_CACHE: dict[int, tuple] = {}


def _grams(text: str) -> list[str]:
    text = str(text or "").lower()
    grams: list[str] = []
    for word in re.findall(r"[a-z][a-z0-9\-]{2,}", text):
        grams.append("w:" + word)
    for seq in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for size in (2, 3):
            for i in range(0, len(seq) - size + 1):
                grams.append(seq[i:i + size])
    return grams


def _vector(text: str) -> Counter:
    return Counter(_grams(text))


def _norm(vec: Counter) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1.0


def _cosine(a: Counter, b: Counter, b_norm: float | None = None) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    return dot / (_norm(a) * (b_norm or _norm(b)))


async def _index_for(db, project_id: int):
    from services.block_store import list_chunks_with_filename
    chunks = await list_chunks_with_filename(db, project_id)
    signature = (len(chunks), max((int(c.get("id") or 0) for c in chunks), default=0))
    cached = _CACHE.get(project_id)
    if cached and cached[0] == signature:
        return cached[1]
    docs = []
    df: Counter = Counter()
    for chunk in chunks:
        vec = _vector(str(chunk.get("content") or ""))
        if not vec:
            continue
        doc = {"chunk": chunk, "vec": vec, "norm": _norm(vec)}
        docs.append(doc)
        df.update(set(vec))
    total = max(1, len(docs))
    idf = {term: math.log((total + 1) / (count + 1)) + 1.0 for term, count in df.items()}
    for doc in docs:
        doc["weighted"] = Counter({k: v * idf.get(k, 1.0) for k, v in doc["vec"].items()})
        doc["wnorm"] = _norm(doc["weighted"])
    index = {"docs": docs, "idf": idf}
    _CACHE[project_id] = (signature, index)
    return index


def clear_cache(project_id: int | None = None) -> None:
    if project_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(project_id, None)


async def search_materials(db, project_id: int, section: dict, spec: list[dict] | None = None,
                           reqs: list[dict] | None = None, limit: int = 4) -> list[dict]:
    from services.material_search import score_chunk, extract_terms
    spec = spec or []
    reqs = reqs or []
    index = await _index_for(db, project_id)
    if not index["docs"]:
        return []
    query_text = " ".join([
        str(section.get("title") or ""),
        " ".join(str(x) for x in (section.get("points") or [])),
        " ".join(str((b or {}).get("ref") or "") for b in (section.get("basis") or [])),
        " ".join(str(x.get("text") or "") for x in spec),
        " ".join(str(x.get("text") or "") for x in reqs),
    ])
    terms = extract_terms(query_text, limit=24)
    if not terms:
        return []
    qvec_raw = _vector(query_text)
    qvec = Counter({k: v * index["idf"].get(k, 1.0) for k, v in qvec_raw.items()})
    qnorm = _norm(qvec)
    req_terms = set(extract_terms(" ".join(str(x.get("text") or "") for x in spec + reqs), limit=30))
    lexical = []
    for doc in index["docs"]:
        content = str(doc["chunk"].get("content") or "")
        lexical.append(score_chunk(content, terms))
    lex_max = max(lexical or [0.0]) or 1.0
    scored = []
    for doc, lex in zip(index["docs"], lexical):
        content = str(doc["chunk"].get("content") or "")
        vector = _cosine(qvec, doc["weighted"], doc["wnorm"])
        req_hit = sum(1 for term in req_terms if term in content) / max(1, len(req_terms))
        title_hit = 1.0 if str(section.get("title") or "") and str(section.get("title") or "") in content else 0.0
        final = 0.45 * (lex / lex_max) + 0.35 * vector + 0.15 * req_hit + 0.05 * title_hit
        if final <= 0.04:
            continue
        scored.append({
            "score": round(final, 4), "lexical": round(lex / lex_max, 4),
            "vector": round(vector, 4), "requirement_hit": round(req_hit, 4),
            "chunk": doc["chunk"], "weighted": doc["weighted"], "wnorm": doc["wnorm"],
        })
    scored.sort(key=lambda item: -item["score"])
    selected = []
    per_file: dict[int, int] = {}
    while scored and len(selected) < max(1, limit):
        best = None
        best_score = -1.0
        for item in scored:
            mid = int(item["chunk"].get("material_id") or 0)
            if per_file.get(mid, 0) >= 2:
                continue
            penalty = 0.0
            if selected:
                penalty = max(_cosine(item["weighted"], other["weighted"], other["wnorm"]) for other in selected)
            score = item["score"] - 0.25 * penalty
            if score > best_score:
                best_score, best = score, item
        if best is None:
            break
        scored.remove(best)
        mid = int(best["chunk"].get("material_id") or 0)
        per_file[mid] = per_file.get(mid, 0) + 1
        best["chunk"] = dict(best["chunk"])
        best["reason"] = {
            "lexical": best["lexical"], "vector": best["vector"],
            "requirement_hit": best["requirement_hit"],
        }
        selected.append(best)
    out = []
    for item in selected:
        chunk = item["chunk"]
        out.append({
            "material_id": int(chunk.get("material_id") or 0),
            "filename": chunk.get("filename") or "",
            "page": chunk.get("page"),
            "content": str(chunk.get("content") or "")[:420],
            "score": item["score"],
            "reason": item["reason"],
        })
    return out
