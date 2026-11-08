"""统一交付判断：把"规则分 / 评审分 / L0 预检 / 否决风险"收成一个数字 + 三条必办。

为什么需要：编写页同时显示"评审 58""就绪度 71""红线风险""待办 4 件"，用户不知道该信哪个。
本模块只做一件事：给一个**唯一的交付判断**，并解释它由哪些输入构成（explain 全暴露）。
"""
from __future__ import annotations

LEVEL_LABEL = {"red": "不可交", "yellow": "接近可交", "green": "可以交"}
LEVEL_ORDER = {"red": 0, "yellow": 1, "green": 2}


def _top_items(tasks: list[dict], limit: int = 3) -> list[dict]:
    """必办清单：优先"会卡交付/废标"的，再按严重度。"""
    rank = {"export_block": 0, "completion_block": 1, "quality": 2, "suggestion": 3}
    ordered = sorted(tasks or [], key=lambda task: rank.get(str(task.get("impact") or ""), 9))
    out = []
    for task in ordered[:limit]:
        out.append({
            "title": str(task.get("title") or "")[:40],
            "why": str(task.get("why") or "")[:80],
            "action": (task.get("action") or {}).get("type") or "",
            "locatable": bool((task.get("target") or {}).get("section") or (task.get("target") or {}).get("quote")),
            "impact": str(task.get("impact") or ""),
        })
    return out


def build_delivery_verdict(*, rule_score: int, review: dict | None, precheck: dict | None,
                           fatal_risks: list[dict] | None, need_user: list[dict] | None) -> dict:
    """统一交付判断。

    level（决定能不能交，以硬条件为准）：
      red    = 有否决风险 / L0 有 critical / 评审 unsatisfied
      yellow = 无 red，但（没评审 / L0 有 high / 评审 partial）
      green  = 以上都不成立，且评审 satisfied
    score（趋势参考）：L0(0/1)×20 + 规则分×40 + 评审分×40；无评审时归一到 100
    """
    review = review or {}
    freshness = review.get('freshness')
    if freshness in ('stale', 'unknown'):
        review = {}
    precheck = precheck or {}
    fatal_risks = fatal_risks or []
    need_user = need_user or []

    pre_counts = (precheck.get("counts") or {})
    has_critical_rule = int(pre_counts.get("critical") or 0) > 0
    has_high_rule = int(pre_counts.get("high") or 0) > 0
    satisfied = str(review.get("satisfied") or "")
    has_review = bool(review.get("score"))

    if fatal_risks or has_critical_rule or satisfied == "unsatisfied":
        level = "red"
    elif not has_review or has_high_rule or satisfied == "partial":
        level = "yellow"
    else:
        level = "green"

    hard_gate = sum(1 for task in need_user
                    if str(task.get("impact") or "") in ("export_block", "completion_block"))
    can_submit = level == "green" and hard_gate == 0

    # 分数：有评审 → 三项加权；无评审 → 规则 + 预检两项归一
    l0_score = 0.0 if has_critical_rule else (0.6 if has_high_rule else 1.0)
    if has_review:
        score = round(l0_score * 20 + int(rule_score) * 0.4 + int(review.get("score") or 0) * 0.4)
    else:
        score = round(l0_score * 40 + int(rule_score) * 0.6)

    top3 = _top_items(need_user, 3)
    if fatal_risks and len(top3) < 3:
        for risk in fatal_risks[:3 - len(top3)]:
            top3.append({"title": f"否决风险：{str(risk.get('point') or '')[:24]}",
                         "why": "可能导致废标", "action": "review_rewrite", "locatable": False,
                         "impact": "export_block"})

    return {
        "score": max(0, min(100, score)),
        "level": level,
        "label": LEVEL_LABEL[level],
        "can_submit": can_submit,
        "top3": top3[:3],
        "explain": {
            "rule": int(rule_score),
            "review": int(review.get("score") or 0) if has_review else None,
            "review_freshness": freshness,
            "review_tier": str(review.get("tier") or ""),
            "precheck": pre_counts,
            "fatal": len(fatal_risks),
            "hard_gate": hard_gate,
        },
    }
