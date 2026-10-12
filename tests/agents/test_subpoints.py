"""细项抽取：接地校验 + 内容哈希缓存 + 回落。

用户要求"稳定输出，不要一会儿变这样一会儿变那样"。这一层是稳定性的关键：
模型的输出会被接地校验筛一遍、再按内容哈希缓存，所以**同一份材料的重跑结果逐字相同**。
"""
from __future__ import annotations

import pytest

from agents import subpoints


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """每个用例用独立缓存文件，避免互相污染真实缓存。"""
    monkeypatch.setattr(subpoints, "_CACHE_PATH", tmp_path / "subpoints.json")


# ── 接地校验 ──────────────────────────────────────────────────

def test_grounded_point_kept():
    c = "（1）深刻理解项目背景、现状、目标、建设范围、建设内容、项目重难点等。"
    assert subpoints.is_grounded("项目背景", c)
    assert subpoints.is_grounded("项目重难点", c)
    # 原文"项目背景" → 要点"项目背景理解"（多一个字）也应通过
    assert subpoints.is_grounded("项目背景理解", c)


def test_ungrounded_point_dropped():
    """模型引入原文没有的内容必须被丢掉（否则就不再可复现）。"""
    c = "（1）深刻理解项目背景、现状、目标。"
    assert not subpoints.is_grounded("火星移民计划", c)


def test_validate_drops_scores_bands_and_noise():
    c = "优良（7-10分）：投标响应度较高。中等（6分）：响应度良好。"
    out = subpoints.validate_points(
        ["优良", "中等", "得8分", "响应度说明", "12-18分", "x"], c)
    assert out == ["响应度说明"]


def test_validate_truncates_and_dedupes_preserving_order():
    c = "方案可行性 方案先进性 项目背景 项目现状"
    out = subpoints.validate_points(["方案可行性", "方案可行性", "方案先进性", "项目背景"],
                                    c, limit=2)
    assert out == ["方案可行性", "方案先进性"]


# ── 缓存：稳定的硬保证 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_criterion_is_cached_and_reused():
    """同一段原文第二次直接命中缓存 —— 不再调模型，结果逐字相同。

    这就是"不要一会儿变这样一会儿那样"的机器化保证：模型只跑一次。
    """
    calls = []

    async def _llm(text):
        calls.append(text)
        return {"points": ["方案可行性", "方案先进性"]}

    c = "1、方案可行性：…2、方案先进性：…"
    first, src1 = await subpoints.extract_points(c, llm_extract=_llm)
    second, src2 = await subpoints.extract_points(c, llm_extract=_llm)

    assert first == second == ["方案可行性", "方案先进性"]
    assert src1 == "llm" and src2 == "cache"
    assert len(calls) == 1, "第二次不该再调模型"


@pytest.mark.asyncio
async def test_cache_key_includes_prompt_version(monkeypatch):
    """改了抽取口径（PROMPT_VERSION）后旧缓存必须失效。

    否则调好提示词也会被旧结果挡住，表现为"改了没效果"（本轮踩过同类的坑）。
    """
    async def _llm(text):
        return {"points": ["项目背景"]}

    c = "项目背景、现状、目标"
    await subpoints.extract_points(c, llm_extract=_llm)
    assert subpoints.cache_lookup(c)

    monkeypatch.setattr(subpoints, "PROMPT_VERSION", subpoints.PROMPT_VERSION + 1)
    assert subpoints.cache_lookup(c) is None


# ── 回落 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_falls_back_to_rules_when_llm_fails():
    """模型失败/输出全是废话时，回落规则拆分 —— 目录这一层不能是空的。"""
    def _rules(text):
        return [("方案可行性", ""), ("方案先进性", "")]

    async def _broken(text):
        raise RuntimeError("API 挂了")

    pts, src = await subpoints.extract_points(
        "1、方案可行性：（1）…2、方案先进性：（1）…", llm_extract=_broken, rule_fallback=_rules)
    assert pts == ["方案可行性", "方案先进性"]
    assert src == "rules"


@pytest.mark.asyncio
async def test_falls_back_to_rules_when_llm_output_ungrounded():
    """模型给了原文里没有的内容 → 全部被接地校验拦下 → 回落规则。"""
    def _rules(text):
        return [("项目背景", ""), ("项目现状", "")]

    async def _hallucinating(text):
        return {"points": ["火星移民计划", "星际殖民方案"]}

    pts, src = await subpoints.extract_points(
        "深刻理解项目背景、现状、目标。", llm_extract=_hallucinating, rule_fallback=_rules)
    assert pts == ["项目背景", "项目现状"]
    assert src == "rules"


@pytest.mark.asyncio
async def test_empty_criterion_returns_empty():
    pts, src = await subpoints.extract_points("")
    assert pts == [] and src == "empty"


# ── 垃圾标题过滤 ──────────────────────────────────────────────

def test_garbage_titles_are_rejected():
    """价格公式碎片/编码残留不能进目录。

    实测：规则拆分在混排长文上产出 `%RLRH1`、`δ偏为评标价格`、`基准平均价偏差百分数的绝对`，
    而回落分支的旧写法 `or raw` 会把它们原样放回目录。
    """
    c = "价格分计算方法：偏差百分数 %RLRH1 δ偏为评标价格 基准平均价偏差百分数的绝对"
    out = subpoints.validate_points(["%RLRH1", "δ偏为评标价格", "价格分计算", "偏差计算"], c)
    assert "%RLRH1" not in out
    assert "δ偏为评标价格" not in out
    assert "价格分计算" in out


def test_short_or_symbol_only_titles_rejected():
    assert subpoints.is_garbage_title("的")
    assert subpoints.is_garbage_title("%%%")
    assert not subpoints.is_garbage_title("方案可行性")


@pytest.mark.asyncio
async def test_rule_fallback_never_returns_garbage():
    """回落分支即使全被刷掉，也不能把垃圾放回去。"""
    async def _broken(text):
        raise RuntimeError("x")

    def _rules(text):
        return [("%RLRH1", ""), ("δ偏为评标价格", "")]

    pts, src = await subpoints.extract_points(
        "偏差百分数 %RLRH1 δ偏为评标价格", llm_extract=_broken, rule_fallback=_rules)
    assert pts == []
    assert src == "rules"
