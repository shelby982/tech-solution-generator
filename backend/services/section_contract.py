"""小节契约（P1）：按"这一节在讲什么"约束产出，并给出可机器判的校验。

来自内容审核的真实教训：
- 行业背景节写成"三法复述" → 跑题
- 现状节全是泛化判断、没有事实与来源 → 无法核验
- 目标节 0 个量化指标 → 不可验收
- 难点节是"能力瓶颈/流程瓶颈"通用话 → 没有措施
"""
from __future__ import annotations

import re

# 节类型判定（按标题关键词）
_KIND_RULES = (
    ("industry", ("行业", "趋势", "发展背景", "市场")),
    ("status", ("现状", "基本情况", "现有")),
    ("gap", ("差距", "问题", "痛点", "瓶颈")),
    ("goal", ("目标", "指标", "成效")),
    ("policy", ("政策", "依据", "标准", "规范")),
    ("scope", ("范围", "边界", "内容")),
    ("necessity", ("必要性", "意义")),
    ("figure", ("图", "表", "对比图")),
)

CONTRACT_PROMPTS = {
    "industry": (
        "本节是**行业背景**：只写行业层面的事实（政策节奏、同业做法、技术演进、市场结构），"
        "**不要复述法律条文要求**（法条在政策依据节讲）；至少给出 2 个行业层面的观察，并说明对本项目的含义。"
    ),
    "status": (
        "本节是**现状描述**：每条判断后面必须跟一个**可核验的事实或来源**（系统名称/版本/规模/数据/文件），"
        "没有事实就不要下判断；确实缺资料的写「【待补事实：xx】」，不要用「规范化程度不足」这类泛化话代替。"
    ),
    "gap": (
        "本节是**差距/难点**：采用四段式——问题（具体现象）→ 影响（对交付的影响）→ 措施（怎么做）→ 验证（怎么算好）；"
        "禁止只写「能力瓶颈/流程瓶颈」这类通用结论。"
    ),
    "goal": (
        "本节是**目标**：每个目标必须给出「指标名 + 目标值/阈值 + 验收口径 + 测量方法」四件套；"
        "没有数值就无法验收，宁可写「待与招标文件核对指标」也不要不写。"
    ),
    "policy": (
        "本节是**政策依据**：引用格式为「《文件名》（文号，施行日期）」，涉及标准的必须给标准号"
        "（如 GB/T 22239-2019）；关键要求要落到条款级（第 X 条 / 技术规范书 X.X）。"
    ),
    "scope": (
        "本节是**范围与边界**：明确「做什么/不做什么/依赖什么」，边界用可判定的表述（在/不在本项目范围内）。"
    ),
    "necessity": ("本节是**必要性**：讲「不做的三类风险（合规/业务/成本）」，不要重复政策条文。"),
    "figure": ("本节是**图表说明**：图/表要有编号与标题，正文给出 3~5 行解读，不要描述「图是怎么画的」。"),
}


def kind_of(title: str) -> str:
    text = str(title or "")
    for kind, keywords in _KIND_RULES:
        if any(word in text for word in keywords):
            return kind
    return ""


def contract_for(title: str) -> str:
    return CONTRACT_PROMPTS.get(kind_of(title), "")


# ── 可机器判的校验 ───────────────────────────────────────────────────────
_UNIT_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|个|项|人|万|亿|台|套|小时|天|分|级|次)")
_SOURCE = re.compile(r"【来源[:：]")
_FACT_HINT = re.compile(r"(系统|平台|版本|用户|并发|数据量|接口|台账|合同|文件|报告|测评|审计|记录)")
_LAW = re.compile(r"《[^》]{2,40}》")


def check_section(title: str, text: str) -> list[dict]:
    """返回该节的契约问题（空=符合）。"""
    kind = kind_of(title)
    body = str(text or "")
    items: list[dict] = []
    if len(re.sub(r"\s+", "", body)) < 20:
        return items

    if kind == "status":
        if len(_SOURCE.findall(body)) == 0 and not _FACT_HINT.search(body):
            items.append({"group": "proof", "severity": "block",
                          "title": f"现状节缺事实与来源：{title[:14]}",
                          "evidence": "整节没有【来源】也没有具体系统/数据等事实",
                          "source": "小节契约", "action": "locate"})
    if kind == "goal":
        if not _UNIT_NUM.search(body):
            items.append({"group": "proof", "severity": "block",
                          "title": f"目标节没有量化指标：{title[:14]}",
                          "evidence": "整节没有任何带单位的数值（%，个，项，小时…）",
                          "source": "小节契约", "action": "locate"})
    if kind == "industry":
        # 行业节里法条占比过高 → 跑题
        laws = len(_LAW.findall(body))
        if laws >= 3:
            items.append({"group": "proof", "severity": "suggest",
                          "title": f"行业节在复述政策条文：{title[:14]}",
                          "evidence": f"本节引用《…》{laws} 处，行业层面观察不足",
                          "source": "小节契约", "action": "locate"})
    if kind == "gap":
        has_measure = any(word in body for word in ("措施", "做法", "应对", "改进"))
        if not has_measure:
            items.append({"group": "proof", "severity": "suggest",
                          "title": f"难点节缺少措施：{title[:14]}",
                          "evidence": "只讲了问题/瓶颈，没有对应措施与验证方式",
                          "source": "小节契约", "action": "locate"})
    return items


def check_chapter(content: str) -> list[dict]:
    """按 `## 小节` 切开，逐节校验契约。"""
    items: list[dict] = []
    text = str(content or "")
    matches = list(re.finditer(r"(?m)^##[ \t]+(.+?)[ \t]*$", text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        items += check_section(match.group(1).strip(), text[start:end])
    return items
