"""全局一致性检查（终评 pass）。

当前实现是确定性的交叉检查：不调模型，成本为零，专门抓"逐章评审看不出来、
放到整篇才显眼"的问题：
  · 同一问题在多章重复出现（可能是同一根因没统一修）
  · 满足度与分数矛盾（satisfied 但分数低于阈值 / unsatisfied 但高分）
  · 多章 critical 未收口
返回结构直接进 GlobalReport.consistency，前端在全局报告里展示。
"""
from __future__ import annotations

import json
import re

_THRESHOLD = 80


def _loads(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def check_consistency(rows: list[dict], *, threshold: int = _THRESHOLD) -> dict:
    findings: list[dict] = []
    by_issue: dict[str, list[str]] = {}
    critical_chapters: list[str] = []

    for row in rows:
        block = str(row.get("block_code") or row.get("block_id") or "")
        title = str(row.get("title") or block)
        issues = _loads(row.get("issues"))
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            point = str(issue.get("point") or "").strip()
            if point:
                key = re.sub(r"\s+", "", point)[:80]
                by_issue.setdefault(key, []).append(title)
            if str(issue.get("severity") or "").lower() == "critical":
                if title not in critical_chapters:
                    critical_chapters.append(title)

        score = int(row.get("score") or 0)
        satisfied = str(row.get("satisfied") or "").lower()
        if satisfied == "satisfied" and score and score < threshold:
            findings.append({
                "type": "score_satisfied_conflict",
                "chapter": title,
                "detail": f"满足度为 satisfied，但分数 {score} 低于 {threshold}",
            })
        elif satisfied == "unsatisfied" and score >= 90:
            findings.append({
                "type": "score_satisfied_conflict",
                "chapter": title,
                "detail": f"满足度为 unsatisfied，但分数 {score} 偏高",
            })

    for point, chapters in by_issue.items():
        unique = sorted(set(chapters))
        if len(unique) >= 2:
            findings.append({
                "type": "duplicate_issue",
                "chapters": unique[:8],
                "detail": f"同一问题在 {len(unique)} 章重复出现：{point[:80]}",
            })

    if critical_chapters:
        findings.append({
            "type": "critical_open",
            "chapters": critical_chapters[:12],
            "detail": f"{len(critical_chapters)} 章仍有 critical 问题，需在终评前收口",
        })

    return {
        "status": "attention" if findings else "ok",
        "findings": findings[:20],
        "chapters_checked": len(rows),
    }
