"""提炼结果的字段口径归一化：把"评分口径"从 requirement 挪到 score_items。

为什么要这一步（而不是只改 prompt）：prompt 已经明确要求"评分档位不要写进
requirement"（见 agents/prompts.py 的字段口径注释），但：

- 模型不保证听话（实测项目 18 的 s1.1：requirement 里 12 条有 8 条是评分档位）；
- **已经落库的历史数据**不会因为改 prompt 就变好 —— 用户点开匹配页看到的还是满屏"无依据"。

所以在这里做一次**确定性**的兜底：按行拆分 requirement，把明显是评分口径的行搬到
score_items（去重、保留原顺序），并保证 requirement 不会被搬空。

判定复用 ``domain/spec/response_items.is_informative`` 的评分口径特征（一份规则两处用，
避免"展示层说它是评分口径、归一化层不认"的发散）。
"""

from __future__ import annotations

import re

from domain.spec.response_items import is_informative

# 「【评分对应】方案可行性 评分项 8-12分」这类行：信息与 ``score_items`` 完全重复
_CORRESPOND_RE = re.compile(r"^【(?:评分|评审)对应】\s*")
_DEDUPE_STRIP_RE = re.compile(r"\s+|评分项|评审项|条款")


# 「复述评分档位」特征：行里带这些词，且出现的数字全都能在 score_items 里找到
_BAND_HINT_RE = re.compile(r"档位|总分|满分|得分|评分|分值")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 「一次给分/定档陈述」的最小单位。用于识别**混写行**（实测项目 18 的 s2 / s2.2.8）：
#   `1. 方案可行性：技术方案应遵循数字化转型顶层设计…（对应8-12分档）；基本符合…（对应5-7分档）；…`
# 一行里既有"我方要做什么"（应遵循顶层设计），又有整段多档评分规则。
# 只按 `is_informative` 判定搬不动它 —— 行内有义务动词，被"保护"逻辑留下，
# 于是整段档位规则继续留在 requirement 里，写手就会去复述档位（实测：评分档位复述 11 次）。
_AWARD_SEG_RE = re.compile(
    r"得\s*\d+(?:\s*[-~—至]\s*\d+)?\s*分|不得分|"
    r"可获[^，,；;]{0,6}档|降为[^，,；;]{0,6}档|"
    r"对应\s*\d+(?:\s*[-~—至]\s*\d+)?\s*分档|满分\s*\d+\s*分"
)
# 一段里出现 ≥2 次给分陈述才判定为"评分口径为主"（单次给分常夹在动作句中，
# 例如「…必须整体成文并完整提交，无内容则不得分」—— 那是要求，不是档位规则）
_MIN_AWARD_SEGS = 2
# 头段以条件词开头 = 没有动作要求，整行都是评分规则
_COND_START_RE = re.compile(r"^(?:若|如果|如|倘若|当)")


def _numbers(text: str) -> set[str]:
    """行里的数字集合。**排除 0**：「0分（没有内容不得分）」是档位的下界，
    不是用于区分内容的数值 —— 不排除的话「…得分档位：8-12分、5-7分、1-4分、0分」
    会因为多一个 0 而被判定成"不是复述"，去重就漏了（实测踩到）。"""
    return {n for n in _NUM_RE.findall(text or "") if n not in ("0", "0.0")}


def _dedupe_key(text: str) -> str:
    """同一分值的两种写法归一：``方案可行性 评分项 8-12分`` == ``方案可行性 8-12分``。"""
    return _DEDUPE_STRIP_RE.sub("", str(text or ""))


def split_banded_line(line: str) -> tuple[str, str] | None:
    """把「动作要求 + 多档评分规则」的混写行切成 (要保留的动作句, 搬去 score_items 的档位文本)。

    不是混写行 → 返回 ``None``。返回 ``("", 整行)`` 表示整行都是评分规则（没有动作可留）。

    为什么需要它：``is_informative`` 对含义务动词的行一律判为"要回应的事项"（那是为了让
    「投标响应须完全满足招标技术要求」这类动作句不被搬走，实测必要）。但**动作与档位混写**
    的行因此整段留在 requirement 里，档位规则照样被写手看到并复述。这里按"给分陈述的密度"
    区分：一段里 ≥2 次给分/定档才认定为评分口径为主，然后**只切走档位部分、保留动作句**。
    """
    if len(_AWARD_SEG_RE.findall(line)) < _MIN_AWARD_SEGS:
        return None
    first = _AWARD_SEG_RE.search(line)
    head = line[:first.start()]
    # 回退到最后一个子句边界，避免把动作句切在半句上
    cut = max(head.rfind("；"), head.rfind(";"), head.rfind("，"), head.rfind(","))
    if cut <= 0:
        return ("", line)
    action = head[:cut].strip(" \t，,；;。:：")
    if not action or _COND_START_RE.match(action):
        return ("", line)          # 头段只是条件（"若…"）→ 没有动作，整行搬走
    return (action, line[cut + 1:].strip())


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in str(text or "").split("\n") if ln.strip()]


def split_scoring_clauses(requirement: str) -> tuple[str, list[str]]:
    """``requirement`` → (清理后要保留的行, 判定为评分口径的行)。

    保守规则：
    - 只按行判定（不拆句子，避免把"投标方应避免负偏差，否则评分降为一般档"这类
      带动作的句子整条搬走）；
    - **混写行只切走档位部分**（``split_banded_line``）：动作句必须留在 requirement，
      它是撰写阶段的主抓手；
    - 若**所有**行都是评分口径，则原样保留（宁可不搬，也不能把 requirement 清空 ——
      8 字段里 requirement 是章节的主字段，清空会让匹配与撰写都没有抓手）。
    """
    lines = _lines(requirement)
    if not lines:
        return str(requirement or ""), []
    keep: list[str] = []
    scoring: list[str] = []
    for ln in lines:
        if is_informative("requirement", ln):
            scoring.append(ln)
            continue
        mixed = split_banded_line(ln)
        if mixed is None:
            keep.append(ln)
            continue
        action, banded = mixed
        if banded:
            scoring.append(banded)
        if action:
            keep.append(action)
    if not keep:
        return str(requirement or ""), []
    return "\n".join(keep), scoring


def normalize_row_fields(row) -> dict:
    """就地规整一行的 8 字段，返回改动摘要。

    - ``moved``：从 requirement **搬进** score_items 的评分口径行；
    - ``dropped``：requirement 里与 score_items **逐条重复**而被删掉的行。

    为什么要 dropped 这一步（实测项目 18 的 s2.1 方案可行性）：
    ``requirement`` 12 行里有 7 行是「【评分对应】方案可行性 评分项 8-12分」，
    而这 7 条在 ``score_items`` 里**已经有一条一模一样的**（``方案可行性 8-12分``）。
    它们来自提炼 prompt 里那句"请在末尾用【评分对应】追加对应条款" ——
    于是同一个分值在界面上出现两遍（用户截图反馈"上下存在重复"），
    撰写阶段也会被重复喂一遍。
    """
    req = getattr(row, "requirement", None)
    if req is None and isinstance(row, dict):
        req = row.get("requirement")
    existing = _lines(getattr(row, "score_items", "") or
                      (row.get("score_items") if isinstance(row, dict) else "") or "")
    existing_keys = {_dedupe_key(x) for x in existing}

    kept: list[str] = []
    dropped: list[str] = []
    correspond_to_move: list[str] = []
    for line in _lines(req or ""):
        if _CORRESPOND_RE.match(line):
            # 已经是 score_items 里的那一条 → 直接删（不再重复展示/重复喂模型）
            if _dedupe_key(_CORRESPOND_RE.sub("", line)) in existing_keys:
                dropped.append(line)
                continue
            correspond_to_move.append(line)     # score_items 里没有 → 补进去
        else:
            kept.append(line)

    # 剩下的行里再挑"纯评分口径"（档位/得分规则）搬进 score_items
    keep_text, scoring = split_scoring_clauses("\n".join(kept))
    moved = scoring + correspond_to_move
    merged = existing + [s for s in moved if s not in existing]
    new_score = "\n".join(merged)

    # indicators 里"复述评分档位"的行（"…总分：30分"/"技术评分标准分值：10分"）也与
    # score_items 重复 —— 数字全都能在 score_items 找到才算复述，避免误删真正的量化指标
    # （"不少于35人"这类没有档位/总分/评分字样的不会被碰到）。实测 132 行 indicators 里 46 行是复述。
    ind_raw = getattr(row, "indicators", None)
    if ind_raw is None and isinstance(row, dict):
        ind_raw = row.get("indicators")
    ind_lines = _lines(ind_raw or "")
    if ind_lines and merged:
        score_nums: set[str] = set()
        for x in merged:
            score_nums |= _numbers(x)
        kept_ind = [l for l in ind_lines
                    if not (_BAND_HINT_RE.search(l) and _numbers(l)
                            and _numbers(l) <= score_nums)]
        pruned = [l for l in ind_lines if l not in kept_ind]
        if pruned:
            if isinstance(row, dict):
                row["indicators"] = "\n".join(kept_ind)
            else:
                row.indicators = "\n".join(kept_ind)
        else:
            pruned = []
    else:
        pruned = []

    new_req = keep_text if keep_text.strip() else "\n".join(correspond_to_move or kept)

    if isinstance(row, dict):
        row["requirement"] = new_req
        row["score_items"] = new_score
    else:
        row.requirement = new_req
        row.score_items = new_score
    return {"moved": moved, "dropped": dropped, "pruned_indicators": pruned}
