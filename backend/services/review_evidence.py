"""评审证据链：给每条 issue 补"依据哪段原文 / 命中哪条评分口径"，让评审可审计。

投标场景要能回答"你凭什么说这章不行"。v1 不做 LLM 语义匹配：
  · 原文片段：复用 review_rewrite.locate（引用串 → 长中文段 → 关键词共现，三级定位）
  · 评分口径：criterion_text 里与 issue 文本字面重合最多的那一句
定位不到就明确 locatable=False，不假装有依据。
"""
from __future__ import annotations

import re


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。；\n]", str(text or "")) if len(s.strip()) >= 8]


def _best_criterion(issue_text: str, criterion_text: str) -> str:
    """从评分口径里挑与 issue 最相关的一句（按 2-gram 重合）。"""
    sentences = _split_sentences(criterion_text)
    if not sentences:
        return ""
    body = re.sub(r"\s+", "", str(issue_text or ""))

    def grams(text: str) -> set[str]:
        compact = re.sub(r"\s+", "", text)
        return {compact[i:i + 2] for i in range(len(compact) - 1)}

    issue_grams = grams(body)
    best, best_score = "", 0
    for sentence in sentences:
        score = len(issue_grams & grams(sentence))
        if score > best_score:
            best, best_score = sentence, score
    return best[:120] if best_score >= 2 else ""


def attach_evidence(content: str, issues: list[dict], criterion_text: str = "") -> list[dict]:
    """为 issues 补 evidence 字段（不改原对象，返回新列表）。"""
    from services.review_rewrite import locate

    out: list[dict] = []
    for issue in issues or []:
        item = dict(issue)
        point = str(item.get("point") or "")
        hit = locate(content, point) if content else None
        section = ""
        if hit:
            head = str(content).rfind("\n## ", 0, hit.get("start") or 0)
            if head >= 0:
                line_end = str(content).find("\n", head + 1)
                section = str(content)[head + 3:line_end].strip()[:40]
        item["evidence"] = {
            "quote": (hit or {}).get("before", "")[:160],
            "section": section,
            "criterion": _best_criterion(point, criterion_text),
            "locatable": bool(hit),
        }
        out.append(item)
    return out
