"""应答要求条目化：把提炼出的 8 字段展开成"该章节必须回应的条目"。

为什么需要：匹配阶段的挂载点是**要求条目**（"并发 ≥2000"这条依据是哪段素材），而不是整章；
撰写阶段也要按条目逐条作答 + 做覆盖度自检（见 docs/superpowers/plans/
2026-09-21-response-coverage-and-matching-plan.md §2、§3）。

这一步**零模型调用**：`requirement` 与 `key_points` 在提炼 prompt 里就要求"分点列出"，
其余字段本身就是短列表，按下述规则拆即可。
"""

from __future__ import annotations

import re

# 字段 → 条目类型
FIELD_TO_TYPE: dict[str, str] = {
    "requirement": "requirement",        # 技术要求
    "key_points": "key_point",           # 响应建议（该写什么）
    "veto_items": "veto",                # 否决项（一票否决）
    "bonus_items": "bonus",              # 加分项
    "score_items": "score",              # 评分项与分值
    "evidence_required": "evidence",     # 证明材料
    "indicators": "indicator",           # 量化指标
}

# 条目 id 的类型前缀（稳定、可读、便于前端分组）
TYPE_PREFIX: dict[str, str] = {
    "requirement": "R",
    "key_point": "K",
    "veto": "V",
    "bonus": "B",
    "score": "S",
    "evidence": "E",
    "indicator": "I",
}

# 行内拆分：分号 / 序号（1. 1、1) （1） ① - ·）
_SPLIT_RE = re.compile(r"\n|(?<=[；;])|(?=(?:^|\s)(?:\d{1,2}[.、)）]|[①-⑳]|[-·•]))")
_LEADING_MARK_RE = re.compile(r"^(?:\d{1,2}[.、)）]|[①-⑳]|[-·•*]|（\d{1,2}）|\(\d{1,2}\))\s*")
_PLACEHOLDER_RE = re.compile(r"^-{2,}$|^—+$|^无$|^None$|^暂无", re.I)


def split_items(text: str) -> list[str]:
    """把一段多行/分号文本拆成条目列表（去序号、去空、去占位符）。"""
    if not text or not str(text).strip():
        return []
    out: list[str] = []
    for raw in _SPLIT_RE.split(str(text)):
        line = (raw or "").strip()
        if not line or _PLACEHOLDER_RE.match(line):
            continue
        line = _LEADING_MARK_RE.sub("", line).strip()
        if len(line) < 2:
            continue
        out.append(line)
    return out


# 「评分口径」特征：档位（优良 7-10 分）、得分/不得分、满分、评分标准…
# 为什么需要它：提炼会把整段**评分细则**塞进 requirement 字段（实测项目 18 的 s1.1：
# requirement = 优良(7-10分)/中等(6分)/一般(1-5分)/未作响应不得分…），于是展开出的
# 十几条全是"评委会怎么打分"，而不是"我方要做什么"。它们不可能有素材依据 ——
# 混进同一张清单就会变成满屏"无依据"，用户完全看不懂（实测反馈）。
# 这类条目在界面上单独归入「评分与量化指标（说明性）」：不参与覆盖度统计、不给「找素材」。
_SCORING_CLAUSE_RE = re.compile(
    # 档位写法：（7-10分）/（6分）/（1-5分）—— 单值与区间都要认，
    # 只认区间会漏掉「中等（6分）」这种单值档位（实测漏了一半）
    r"[（(]\s*\d+(?:\s*[-~—至]\s*\d+)?\s*分\s*[)）]"
    r"|得\s*\d+\s*分|不得分|评分标准|满分\s*\d*\s*分|分值"
)
# 字段级判定：这两个字段本身就是在描述评分与指标
_INFORMATIVE_FIELDS = ("score_items", "indicators")

# 「带动作」特征：句子里有对投标方的义务要求 → 即使提到分值，它也是"要回应的事"，
# 不能当成评分口径。
# 实测必要性（项目 18 的 s1.2）：「投标响应**须**完全满足招标技术要求，这是获得中等（6分）
# 及以上评分的前提。」—— 只按分值特征判定会把它当评分口径挪走，requirement 就丢了一条
# 我方必须做的事（requirement 是撰写阶段的主抓手，丢动作比留下评分口径更糟）。
# 注意 `不得(?!分)`：「不得分」是评分规则，「不得出现」是义务。
# `应` 必须排除**词内**出现的情况：响应/对应/适应/供应/相应/反应/应答 —— 否则
# 「按响应度分档评审」「投标响应度较高」这种纯评分口径会被当成义务句（实测漏搬一半）。
_OBLIGATION_RE = re.compile(
    r"(?<![响对适供相反])应(?!答)"
    r"|须|需|必须|应当|不得(?!分)|提供|提交|编制|承诺|确保|保证"
)


def is_informative(field: str, text: str) -> bool:
    """是不是"说明性条目"（评分口径 / 量化档位），而不是"我方要回应的事项"。"""
    if field in _INFORMATIVE_FIELDS:
        return True
    t = text or ""
    if _OBLIGATION_RE.search(t):
        return False
    return bool(_SCORING_CLAUSE_RE.search(t))


def expand_response_items(block_id: str, row: dict) -> list[dict]:
    """把某章节的 8 字段展开成条目列表（顺序稳定：要求 → 建议 → 否决 → 加分 → 评分 → 证明 → 指标）。

    每条带 ``informative``：True = 评分口径/量化档位（说明性，不参与覆盖度、不给"找素材"）；
    False = 我方要回应的事项（覆盖度统计与素材挂载的对象）。
    """
    items: list[dict] = []
    counters: dict[str, int] = {}
    for field, type_ in FIELD_TO_TYPE.items():
        for text in split_items((row or {}).get(field) or ""):
            counters[type_] = counters.get(type_, 0) + 1
            items.append({
                "item_id": f"{block_id}#{TYPE_PREFIX[type_]}{counters[type_]}",
                "block_id": block_id,
                "type": type_,
                "text": text,
                "source_field": field,
                "informative": is_informative(field, text),
            })
    return items


def expand_all_items(outline_matrix: dict) -> dict[str, list[dict]]:
    """整份矩阵 → ``{block_id: [条目…]}``（保持矩阵里的章节顺序）。"""
    return {
        bid: expand_response_items(bid, row or {})
        for bid, row in (outline_matrix or {}).items()
    }
