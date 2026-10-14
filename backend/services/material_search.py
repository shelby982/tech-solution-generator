"""素材**按需检索**：编写某章时，用这一章的举证要求/要点去素材库里找相关片段。

为什么需要它
------------
原来的素材是**全量预匹配**：跑一次匹配把所有章节的 `block.source` 都填满。问题是
- 大量章节根本不需要素材（写"项目理解""技术方案思路"不需要资质扫描件）；
- "需不需要、需要什么"其实在逐节提炼时已经算出来了（`blocks.evidence_required`）；
- 章节没跑到匹配时，`block.source` 是空的，界面只能显示"暂无匹配素材"。

所以补一条**真正的按需通道**：给定章节 → 从它的 `evidence_required` / `key_points` /
标题里取关键词 → 直接在 `material_chunks` 里检索 → 返回带出处（文件 + 页码）的片段。
不写库、不预计算，只在用户点「按需素材」时算一次。

检索是**确定性**的（关键词命中 + 词频 + 长度归一），不调模型 —— 快、可解释、可测试。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 中文按 2 字滑窗 + 英文/数字按词切；停用词表只去掉明显无信息量的
# 虚词：出现在滑窗片段里说明那是"半截词"（如 及偏差说 / 对应的投），不作为关键词
_GLUE = set("的及或与和为在对就该把被由从以")

_STOP = {
    "的", "了", "和", "与", "及", "或", "在", "为", "是", "有", "对", "等", "中", "上",
    "要求", "提供", "相关", "内容", "进行", "项目", "投标", "招标", "文件", "材料",
    "the", "and", "for", "with", "that", "this",
}


def extract_terms(*texts: str, limit: int = 24) -> list[str]:
    """从若干段文本里抽关键词（确定性，不调模型）。

    做法分三层，避免纯滑窗产生 "及偏差说" 这类噪声：
    1. **短语优先**：中文按标点/空白切成连续中文段（≤8 字）本身就是强信号，权重 ×3；
    2. **长短语补召回**：超过 8 字的段再切 2~4 字滑窗（如"投标偏差表（技术部分）及偏差说明…"
       能补出"偏差说明"）；
    3. **去包含**：已被更长关键词覆盖的短词丢掉（"技术方案"命中后不再留"技术"）。
    """
    blob = " ".join(t for t in texts if t) or ""
    counts: dict[str, int] = {}

    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", blob):
        w = w.lower()
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 3

    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", blob):
        if len(seg) <= 8 and seg not in _STOP:
            counts[seg] = counts.get(seg, 0) + 3        # ① 短语本身
        if len(seg) > 8:                                 # ② 长短语才补滑窗
            for size in (4, 3, 2):
                for i in range(0, len(seg) - size + 1):
                    w = seg[i:i + size]
                    if w in _STOP:
                        continue
                    # 滑窗切出来的片段若含虚词（"及偏差说" "对应的投"），基本是半截词，
                    # 做关键词只会添噪声 —— 直接丢。
                    if any(ch in w for ch in _GLUE):
                        continue
                    counts[w] = counts.get(w, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    out: list[str] = []
    for w, _ in ranked:
        if any(w != o and w in o for o in out):          # ③ 去包含
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


def score_chunk(content: str, terms: list[str]) -> float:
    """片段相关度：命中词数（长词权重更高）+ 词频，按长度归一。"""
    if not content or not terms:
        return 0.0
    low = content.lower()
    hit = 0.0
    for t in terms:
        n = low.count(t.lower())
        if n:
            hit += (1.0 + 0.15 * len(t)) * min(n, 3) ** 0.5
    if not hit:
        return 0.0
    # 长片段不该因为字多就赢：除以长度的平方根（经验值，够用且可解释）
    return hit / (len(content) ** 0.5)


async def search_block_materials(db, block: dict, *, limit: int = 8,
                                 min_score: float = 0.05) -> dict:
    """给定章节，在素材库里找相关片段。

    返回 ``{"terms": [...], "query_source": "evidence_required"|"key_points"|"title",
    "results": [{material_id, filename, page, content, score}]}``。
    """
    from services.block_store import list_chunks_with_filename

    evidence = str(block.get("evidence_required") or "").strip()
    key_points = str(block.get("key_points") or "").strip()
    title = str(block.get("title") or "").strip()

    # 优先用举证要求（"要什么材料"最直接），没有才退到要点 / 标题
    if evidence:
        terms = extract_terms(evidence, key_points)
        source = "evidence_required"
    elif key_points:
        terms = extract_terms(key_points, title)
        source = "key_points"
    else:
        terms = extract_terms(title)
        source = "title"

    if not terms:
        return {"terms": [], "query_source": source, "results": []}

    chunks = await list_chunks_with_filename(db, int(block.get("project_id") or 0))
    ranked = []
    for c in chunks:
        s = score_chunk(str(c.get("content") or ""), terms)
        if s >= min_score:
            ranked.append((s, c))
    ranked.sort(key=lambda x: -x[0])

    # 同一份素材最多给 2 段，避免一个文件刷满结果
    per_material: dict[int, int] = {}
    results = []
    for s, c in ranked:
        mid = int(c.get("material_id") or 0)
        if per_material.get(mid, 0) >= 2:
            continue
        per_material[mid] = per_material.get(mid, 0) + 1
        results.append({
            "material_id": mid,
            "filename": c.get("filename") or "",
            "page": c.get("page"),
            "content": str(c.get("content") or "")[:600],
            "score": round(s, 4),
        })
        if len(results) >= limit:
            break

    logger.info("按需素材检索：block=%s 命中 %d 段（关键词 %s）",
                block.get("block_id"), len(results), terms[:6])
    return {"terms": terms, "query_source": source, "results": results}
