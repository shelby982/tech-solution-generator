"""评分点关键词核对：只回答“评分点关键词有没有在本章正文出现”。"""
from __future__ import annotations

import json
import re

# 招投标评分点常见实体词；只用于确定性核对，不引入模型判断
_VOCAB = (
    "项目背景", "背景", "项目目标", "目标", "建设范围", "范围", "需求", "特点",
    "重点", "难点", "解决方案", "措施", "功能实现", "进度", "计划", "保障",
    "团队", "人员", "学历", "证书", "资质", "社保", "业绩", "案例", "合同",
    "验收", "培训", "运维", "服务", "安全", "架构", "接口", "性能", "指标",
)


def _ids(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        data = json.loads(raw or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def _clean(text: str, limit: int = 24) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip("：:；;。.，,、")[:limit]


def _keywords(criterion: str, item: str = "", factor: str = "") -> list[str]:
    text = str(criterion or "")
    out: list[str] = []

    def add(value: str):
        value = _clean(value)
        if len(value) < 2 or value in out:
            return
        if any(ch.isdigit() for ch in value):
            return
        out.append(value)

    add(item)
    add(factor)
    # 1）项目背景；2）项目目标；…… 以及“得4-6分”之类的分档描述
    for match in re.finditer(r"(?:^|[；;：:，,])\s*\d+[）).、]\s*([^；;。\n]{2,48})", text):
        phrase = match.group(1)
        # 取短语里的核心名词；过长时按顿号拆
        parts = re.split(r"[、，,]", phrase)
        for part in parts[:4]:
            add(part[:24])
    for word in _VOCAB:
        if word in text:
            add(word)
    return out[:8]


def _evidence(content: str, keyword: str) -> str:
    for sentence in re.split(r"[。；;\n]+", str(content or "")):
        if keyword and keyword in sentence:
            return sentence.strip()[:120]
    return ""


def review_block_points(block: dict, points: list[dict]) -> dict:
    content = str(block.get("content") or "")
    rows = []
    for point in points:
        if not isinstance(point, dict):
            continue
        criterion = str(point.get("criterion") or point.get("item") or "")
        keywords = _keywords(criterion, str(point.get("item") or ""), str(point.get("factor") or ""))
        matches = []
        for keyword in keywords:
            evidence = _evidence(content, keyword)
            matches.append({"keyword": keyword, "matched": bool(evidence), "evidence": evidence})
        matched = sum(1 for item in matches if item["matched"])
        total = len(matches)
        rows.append({
            "point_id": str(point.get("point_id") or ""),
            "factor": str(point.get("factor") or ""),
            "item": str(point.get("item") or ""),
            "points": point.get("points"),
            "criterion": criterion,
            "keywords": matches,
            "matched": matched,
            "total": total,
            "coverage": round(matched / total, 3) if total else 0.0,
            "status": ("已回应" if total and matched == total else
                       "部分回应" if matched else "未回应"),
        })
    matched_points = sum(1 for row in rows if row["status"] == "已回应")
    return {"points": rows, "total": len(rows), "matched": matched_points,
            "coverage": round(matched_points / len(rows), 3) if rows else 0.0}
