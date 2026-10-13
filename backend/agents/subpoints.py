"""分项要素 → 应答要点：**模型只做抽取，且结果按内容缓存**。

## 为什么这样设计（用户要求"稳定输出，不要一会儿变这样一会儿变那样"）
目录的**结构**（层级、顺序、章节数、规范书怎么结合）已经由
``infra.parser.outline_builder`` 用规则定死；模型不参与结构决策。

模型只被允许做一件事：把一段异质的分项要素原文**抽成若干应答要点**。
规则拆分在长文/混排文本上会抽出 `%RLRH1`、`δ偏为评标价格` 这类碎片，
这一步模型明显更强。但"用模型"会带来随机性，所以加三道约束：

1. **接地校验**：每个要点必须能在给定原文里找到依据（字符覆盖率 ≥ 阈值），
   否则丢弃 —— 模型不许引入原文没有的内容；
2. **按内容哈希缓存**：同一段原文只抽一次，之后**逐字复用**。
   同一份材料重跑目录，结果完全相同（这才是"不要一会儿这样一会儿那样"）；
3. **失败即回落**规则拆分，不会开天窗。

缓存键含 ``PROMPT_VERSION``：改了提示词即自动失效，不会用到旧口径的结果。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 改了抽取口径就 +1：缓存键含它，旧结果自动失效。
# 为什么必须带版本：否则调好提示词后，缓存里的旧结果会被继续复用，
# 表现为"改了没效果"（本轮就踩过一次类似的坑）。
PROMPT_VERSION = 1

# 接地校验：要点里的字符有多少比例能在原文里找到。
# 0.6 是权衡：太低会放进"看起来像但原文没有"的内容，太高会误杀正常改写
# （原文写"项目背景"，要点写"项目背景理解"，覆盖率约 0.67）。
_GROUND_MIN_RATIO = 0.6

# 明显不是要点的噪音：分值/档位/编号/单字
_NOISE_RE = re.compile(r"(\d+\s*[-–~]?\s*\d*\s*分|得\s*\d|优良|中等|一般|满分|不得分)")
_BAND_ONLY = {"优良", "中等", "一般", "良好", "较差"}

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "cache" / "subpoints.json"


# ── 缓存 ──────────────────────────────────────────────────────

# 送进模型的原文上限。超长分项要素（实测「服务团队」那类混着价格公式的段落）
# 会让模型长篇复述、把 JSON 挤掉（日志里就是 "未找到 JSON 起始 {"）。
# 注意：**缓存键仍用完整原文**，只是送给模型的部分截断。
_MODEL_INPUT_LIMIT = 1500


def criterion_key(criterion: str) -> str:
    """缓存键：分项要素正文 + 抽取口径版本。"""
    norm = re.sub(r"\s+", "", criterion or "")
    return hashlib.sha1(f"v{PROMPT_VERSION}|{norm}".encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    """原子写：先写临时文件再 rename，避免并发/中断留下半个 JSON。"""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(_CACHE_PATH.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0)
        os.replace(tmp, _CACHE_PATH)
    except OSError as e:      # 缓存写失败不影响主流程
        logger.warning(f"细项缓存写入失败：{e}")


def cache_lookup(criterion: str) -> Optional[list[str]]:
    entry = _load_cache().get(criterion_key(criterion))
    if isinstance(entry, dict) and isinstance(entry.get("points"), list):
        return [str(p) for p in entry["points"] if str(p).strip()]
    return None


def cache_store(criterion: str, points: list[str], *, source: str) -> None:
    cache = _load_cache()
    cache[criterion_key(criterion)] = {"points": list(points), "source": source,
                                       "version": PROMPT_VERSION}
    _save_cache(cache)


# ── 接地校验 ──────────────────────────────────────────────────

def is_grounded(point: str, criterion: str) -> bool:
    """要点是否能在原文里找到依据（字符覆盖率）。"""
    p = re.sub(r"\s+", "", point or "")
    c = re.sub(r"\s+", "", criterion or "")
    if len(p) < 2 or not c:
        return False
    hit = sum(1 for ch in p if ch in c)
    return hit / len(p) >= _GROUND_MIN_RATIO


# 垃圾标题：价格公式碎片、编码残留、符号乱码。实测规则拆分在混排长文上会产出
# `%RLRH1`、`δ偏为评标价格`、`基准平均价偏差百分数的绝对` 这类东西 —— 它们不该进目录。
_GARBAGE_RE = re.compile(
    r"([%δΔ√±≥≤]"
    r"|[A-Za-z]{2,}\s*\d"          # %RLRH1 这类编码
    r"|^\W*$"                       # 全是符号
    r"|偏差百分数|评标价格|基准平均价|得分保留|保留两位)"
)


def is_garbage_title(title: str) -> bool:
    """是不是明显不该进目录的标题（公式碎片/编码/乱码）。"""
    t = (title or "").strip()
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    if cjk < 3:
        return True
    return bool(_GARBAGE_RE.search(t))


def validate_points(points, criterion: str, *, limit: int = 10) -> list[str]:
    """过滤模型输出：去噪音、去不接地、去过长、去重，保持原顺序。"""
    out: list[str] = []
    for raw in points or []:
        p = re.sub(r"\s+", " ", str(raw or "")).strip(" 　-—·。，,、：:；;")
        if len(p) < 2 or len(p) > 20:
            continue
        if p in _BAND_ONLY or _NOISE_RE.search(p):
            continue
        if is_garbage_title(p):
            logger.info(f"细项是公式/编码碎片，丢弃：{p!r}")
            continue
        if not is_grounded(p, criterion):
            logger.info(f"细项被接地校验丢弃：{p!r}")
            continue
        if p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out


# ── 抽取 ──────────────────────────────────────────────────────

async def extract_points(
    criterion: str,
    *,
    llm_extract: Optional[Callable[[str], "list[str] | object"]] = None,
    rule_fallback: Optional[Callable[[str], list[str]]] = None,
    use_cache: bool = True,
) -> tuple[list[str], str]:
    """抽取应答要点，返回 ``(要点, 来源)``。来源取值：cache / llm / rules。

    顺序：缓存 → 模型（过接地校验）→ 规则。任何一步失败都往下走，
    保证**一定有结果**（目录里这一层不能是空的）。
    """
    text = (criterion or "").strip()
    if not text:
        return [], "empty"

    if use_cache:
        cached = cache_lookup(text)
        if cached:
            return cached, "cache"

    if llm_extract is not None:
        try:
            raw = await llm_extract(text[:_MODEL_INPUT_LIMIT])
            if isinstance(raw, dict):
                raw = raw.get("points")
            pts = validate_points(raw, text)
            if pts:
                if use_cache:
                    cache_store(text, pts, source="llm")
                return pts, "llm"
            logger.info("模型细项全部未通过接地校验，回落规则拆分")
        except Exception as e:
            logger.warning(f"细项模型抽取失败，回落规则拆分：{e}")

    if rule_fallback is not None:
        raw_rules = [p for p, _ in rule_fallback(text)]
        pts = validate_points(raw_rules, text, limit=12)
        if not pts:
            # 校验全刷掉时，退一步只用垃圾过滤 —— 但**绝不**原样放回
            # （旧写法 `or raw` 会把 %RLRH1 这类碎片写进目录，实测漏出去了）
            pts = [p for p in raw_rules if not is_garbage_title(p)][:4]
        # **不缓存规则结果**：规则拆分是确定且便宜的（同输入必同输出，稳定性已经由
        # 规则本身保证）。一旦把回落结果写进缓存，下次就直接复用、模型再也没有机会 ——
        # 实测踩到：上一轮因缺导出回落成 rules，缓存后这一轮 12 个评分点全是 cache，
        # 模型一次都没跑。
        return pts, "rules"

    return [], "none"
