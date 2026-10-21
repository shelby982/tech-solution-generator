"""待办 C/D 组检查：要你拍板的（决策）与要你核对的（校对）。

全部是**确定性规则**（不调模型），只报"能指到原文位置"的问题，避免噪音。
"""
from __future__ import annotations

import re

# ── C 组：要你拍板的 ────────────────────────────────────────────────────────
# 绝对化承诺（标书高风险：承诺了做不到要担责）
_HARD_PROMISE = ("保证", "确保", "100%", "绝不", "零故障", "零中断", "全天候", "7×24", "7*24")
# 只有"承诺主体 + 服务级别词"同时出现才算真承诺（避免"确保条款落实"这类普通表述误报）
_COMMIT_SUBJECT = ("我方", "我们", "我司", "本项目", "公司", "承诺")
_SERVICE_TOKEN = ("小时", "分钟", "响应", "可用性", "故障", "中断", "恢复", "SLA", "服务级别",
                  "全年", "连续", "可用", "达标率")
# 图注/图元描述不算承诺
_META_HINT = ("图上", "坐标", "标签", "图注", "避免", "可读", "节点")
# 无依据的极致表述（容易被评委质疑）
_ABSOLUTE = ("领先", "最优", "最佳", "唯一", "首创", "第一")
# 未定口径（交付前必须定）
_PENDING = ("待定", "暂定", "另行确定", "视情况", "有待明确", "待明确")
# 角色口径（同一项目里不能一会儿"牵头"一会儿"参与"）
_ROLES = ("牵头", "参与", "支撑", "配合")

_CITE_RE = re.compile(r"【来源[:：]")
_SECTION_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$")
_SENTENCE_SPLIT = re.compile(r"[。；\n]")


def _sentences(content: str):
    """逐句产出 (节标题, 句子)。"""
    section = ""
    for line in str(content or "").splitlines():
        head = _SECTION_RE.match(line.strip())
        if head:
            section = head.group(1).strip()
            continue
        for sentence in _SENTENCE_SPLIT.split(line):
            text = sentence.strip()
            if text:
                yield section, text


def decision_items(content: str) -> list[dict]:
    """C 组：需要用户拍板的事项（承诺强度 / 极限表述 / 未定口径 / 角色口径）。"""
    items: list[dict] = []
    seen: set[str] = set()

    role_hits: dict[str, list[str]] = {}
    for section, sentence in _sentences(content):
        cited = bool(_CITE_RE.search(sentence))
        def _is_real_promise(sentence: str) -> bool:
            if any(hint in sentence for hint in _META_HINT):
                return False
            if not any(token in sentence for token in _SERVICE_TOKEN):
                return False
            return any(subject in sentence for subject in _COMMIT_SUBJECT)

        for word in _HARD_PROMISE:
            if word in sentence and not cited and _is_real_promise(sentence) and f"promise:{word}" not in seen:
                seen.add(f"promise:{word}")
                items.append({
                    "group": "decide", "title": f"承诺强度：出现「{word}」",
                    "evidence": f"{section or '正文'}：{sentence[:60]}…（无来源支撑）",
                    "source": "承诺检查", "severity": "block",
                    "action": "decide", "target": f"promise:{word}",
                })
        for word in _ABSOLUTE:
            if word in sentence and not cited and f"abs:{word}" not in seen:
                seen.add(f"abs:{word}")
                items.append({
                    "group": "decide", "title": f"极限表述：出现「{word}」",
                    "evidence": f"{section or '正文'}：{sentence[:60]}…（建议给出依据或降级表述）",
                    "source": "表述检查", "severity": "suggest",
                    "action": "decide", "target": f"abs:{word}",
                })
        for word in _PENDING:
            if word in sentence and f"pending:{word}" not in seen:
                seen.add(f"pending:{word}")
                items.append({
                    "group": "decide", "title": f"口径未定：出现「{word}」",
                    "evidence": f"{section or '正文'}：{sentence[:60]}…（交付前需确定）",
                    "source": "口径检查", "severity": "block",
                    "action": "decide", "target": f"pending:{word}",
                })
        for role in _ROLES:
            if role in sentence:
                role_hits.setdefault(role, [])
                if section and section not in role_hits[role]:
                    role_hits[role].append(section)

    # 同一项目里出现多种角色口径 → 需要统一
    if len(role_hits) >= 2:
        detail = "；".join(f"{role}（{'、'.join(secs[:2])}）" for role, secs in role_hits.items())
        items.append({
            "group": "decide", "title": "项目角色口径不一致（牵头/参与/支撑混用）",
            "evidence": detail[:120],
            "source": "一致性检查", "severity": "block",
            "action": "decide", "target": "role",
        })
    return items


# ── D 组：要你核对的 ────────────────────────────────────────────────────────
# 常见术语变体：同一概念的不同写法（统一即可）
_TERM_VARIANTS = (
    ("营销管理系统", "营销系统"),
    ("数智营销一体化平台", "数智化营销一体化平台"),
    ("人工智能", "AI"),
    ("云原生", "Cloud Native"),
)
_CITE_STYLE = (
    ("【来源：", 0),
    ("[来源:", 0),
    ("（来源：", 0),
    ("(来源:", 0),
)


def proofread_items(content: str) -> list[dict]:
    """D 组：术语一致性 + 引用格式一致性。"""
    text = str(content or "")
    items: list[dict] = []

    for canonical, variant in _TERM_VARIANTS:
        n_canonical = text.count(canonical)
        n_variant = text.count(variant)
        # 注意：variant 是 canonical 的子串时避免误报
        if variant in canonical:
            n_variant -= n_canonical
        if n_canonical and n_variant > 0:
            items.append({
                "group": "proof", "title": f"术语不统一：「{canonical}」/「{variant}」",
                "evidence": f"正文出现 {n_canonical} 次「{canonical}」、{n_variant} 次「{variant}」（建议统一）",
                "source": "术语检查", "severity": "suggest",
                "action": "locate", "target": f"term:{canonical}",
            })

    styles = {label: text.count(label) for label, _ in _CITE_STYLE if text.count(label)}
    if len(styles) >= 2:
        detail = "；".join(f"{k}×{v}" for k, v in styles.items())
        items.append({
            "group": "proof", "title": "引用格式不统一",
            "evidence": detail,
            "source": "格式检查", "severity": "suggest",
            "action": "locate", "target": "cite-style",
        })
    return items
