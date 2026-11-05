"""写法回流：把评审的 strengths 变成"本节写法候选"，由用户显式采用后才写入 section_practices。

为什么需要：评审每轮都会产出"写得好的地方"，但只展示一次。
回流成写法后，后续章节/后续重写能直接继承这些经验（越用越准）。

安全边界：**不自动改写法**，必须用户点"采用"。
"""
from __future__ import annotations


def candidates_from_review(review: dict | None, *, limit: int = 3) -> list[dict]:
    """从评审结果生成写法候选（来源=strengths）。"""
    if not review:
        return []
    out: list[dict] = []
    for text in (review.get("strengths") or [])[:limit]:
        cleaned = str(text or "").strip()
        if len(cleaned) < 6:
            continue
        out.append({
            "text": cleaned[:200],
            "source": "review_strength",
            "score": int(review.get("score") or 0),
            "tier": str(review.get("tier") or ""),
        })
    return out


async def apply_candidate(db, *, project_id: int, block_id: str, section: str, text: str) -> dict:
    """把候选写进该节写法（用户显式采用）。"""
    from services.section_practice import save_practice

    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("写法内容为空")
    target_section = str(section or "").strip()
    if not target_section:
        # 没有指定小节：写到"_chapter_"（整章写法）
        target_section = "_chapter_"
    await save_practice(project_id, block_id, target_section, cleaned)
    return {"section": target_section, "practice": cleaned}
