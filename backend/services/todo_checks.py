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

# 建议式改写候选（确定性模板，不调模型）：给出 2~3 个可直接替换的说法
_REWRITE_CANDIDATES = {
    "承诺强度": [
        ("保证", "按招标文件要求提供相应服务（具体以合同约定为准）"),
        ("确保", "通过如下措施保障"),
        ("100%", "不低于约定水平"),
        ("零故障", "高可用运行"),
        ("7×24", "按合同约定的响应时限"),
        ("7*24", "按合同约定的响应时限"),
        ("绝不", "原则上不"),
        ("全天候", "按约定时段"),
        ("零中断", "低中断运行"),
    ],
    "极限表述": [
        ("领先", "具备较强"),
        ("最优", "较优"),
        ("最佳", "较适合"),
        ("唯一", "少数可选的"),
        ("首创", "较早"),
        ("第一", "较早"),
    ],
}

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


def _rewrite_candidates(kind: str, word: str) -> list[str]:
    """给定被标记的词，产出 2~3 个可直接替换的表述。"""
    pairs = dict(_REWRITE_CANDIDATES.get(kind) or [])
    base = pairs.get(word)
    if not base:
        return []
    return [
        f"把「{word}」改为「{base}」",
        f"把「{word}」改为「{base}」，并补一句依据【来源：…】",
        "删除该表述，改为客观描述能力与做法",
    ]


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
                    "sentence": sentence,
                    "suggestions": _rewrite_candidates("承诺强度", word),
                })
        for word in _ABSOLUTE:
            if word in sentence and not cited and f"abs:{word}" not in seen:
                seen.add(f"abs:{word}")
                items.append({
                    "group": "decide", "title": f"极限表述：出现「{word}」",
                    "evidence": f"{section or '正文'}：{sentence[:60]}…（建议给出依据或降级表述）",
                    "source": "表述检查", "severity": "suggest",
                    "action": "decide", "target": f"abs:{word}",
                    "sentence": sentence,
                    "suggestions": _rewrite_candidates("极限表述", word),
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
            # 以出现次数多的写法为准（全称优先，其次数量）
            prefer_canonical = n_canonical >= n_variant
            keep, change = (canonical, variant) if prefer_canonical else (variant, canonical)
            items.append({
                "group": "proof", "title": f"术语不统一：「{canonical}」/「{variant}」",
                "evidence": f"正文出现 {n_canonical} 次「{canonical}」、{n_variant} 次「{variant}」（建议统一为「{keep}」）",
                "source": "术语检查", "severity": "suggest",
                "action": "locate", "target": f"term:{keep}",
                "fix": {"label": f"统一为「{keep}」", "replacements": [[change, keep]]},
            })

    styles = {label: text.count(label) for label, _ in _CITE_STYLE if text.count(label)}
    if len(styles) >= 2:
        detail = "；".join(f"{k}×{v}" for k, v in styles.items())
        items.append({
            "group": "proof", "title": "引用格式不统一",
            "evidence": detail + "（建议统一为【来源：…】）",
            "source": "格式检查", "severity": "suggest",
            "action": "locate", "target": "cite-style",
            "fix": {"label": "统一为【来源：…】", "replacements": [
                ["[来源:", "【来源："], ["（来源：", "【来源："], ["(来源:", "【来源："]]},
        })
    return items


# ── D 组补充：数字一致性 ────────────────────────────────────────────────────
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万|千|百|个|项|人|台|套|笔|条|次|分钟|小时|%)")
# 主语里出现这些词说明是"动作/连接"，不是指标主体（避免"预计达到"这类误报）
_SUBJECT_STOP = ("达到", "实现", "约", "为", "是", "预计", "超过", "增至", "提升", "缺口",
                 "预计", "目标", "将", "达到", "可", "需", "要", "从", "到")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+?)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# 数字前的"主语"：取紧邻的 2~8 个汉字/字母数字（去掉标点）
_SUBJECT_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,8})$")


def _normalize_number(value: str, unit: str) -> str:
    """统一成"数值+单位"，便于比较（14.5亿 与 145000万 视为不同，仅同单位比较）。"""
    try:
        num = float(value)
    except ValueError:
        return ""
    return f"{num:g}{unit}"


def number_items(content: str) -> list[dict]:
    """D 组：表格数字需在正文可查 + 同一指标不出现两个数值。"""
    text = str(content or "")
    lines = text.splitlines()
    items: list[dict] = []

    # ① 表格单元格数字必须在正文出现（表是正文的摘要，不能凭空多出数字）
    body_without_tables = "\n".join(
        line for line in lines if not _TABLE_ROW_RE.match(line) and not _TABLE_SEP_RE.match(line)
    )
    body_compact = re.sub(r"\s+", "", body_without_tables)
    missing: list[str] = []
    inside = False
    for line in lines:
        if _TABLE_SEP_RE.match(line):
            inside = True
            continue
        if not inside:
            continue
        if not _TABLE_ROW_RE.match(line):
            inside = False
            continue
        for cell in line.strip().strip("|").split("|"):
            for match in _NUM_RE.finditer(cell):
                token = f"{match.group(1)}{match.group(2)}"
                if token not in body_compact:
                    missing.append(token)
    if missing:
        uniq = list(dict.fromkeys(missing))[:6]
        items.append({
            "group": "proof", "title": f"表格里有 {len(uniq)} 个数字正文未提及",
            "evidence": "、".join(uniq) + "（建议在正文点明关键数字并注明来源，评委主要读正文）",
            "source": "数字核对", "severity": "suggest",
            "action": "locate", "target": "table-numbers",
        })

    # ② 同一指标出现两个不同数值
    by_subject: dict[str, set] = {}
    for line in body_without_tables.splitlines():
        for match in _NUM_RE.finditer(line):
            prefix = line[: match.start()].rstrip()
            subject_match = _SUBJECT_RE.search(prefix)
            if not subject_match:
                continue
            subject = subject_match.group(1)[-4:]
            if len(subject) < 3 or any(stop in subject for stop in _SUBJECT_STOP):
                continue
            normalized = _normalize_number(match.group(1), match.group(2))
            if normalized:
                by_subject.setdefault((subject, match.group(2)), set()).add(normalized)
    conflicts = {key: values for key, values in by_subject.items() if len(values) >= 2}
    for (subject, unit), values in list(conflicts.items())[:4]:
        items.append({
            "group": "proof", "title": f"同一指标出现不同数值：「{subject}」",
            "evidence": " / ".join(sorted(values)) + f"（单位：{unit}，请统一口径）",
            "source": "数字核对", "severity": "suggest",
            "action": "locate", "target": f"num:{subject}",
        })
    return items
