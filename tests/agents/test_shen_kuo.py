"""沈括 agent 测试 — 按需素材检索（retrieve_for，BM25 关键词）。

旧的「逐章 LLM 重排匹配」路径已删除（2026-11-09），这里只覆盖检索。
"""

import pytest

from agents.shen_kuo import ShenKuoAgent
from domain.material.models import Match


async def test_retrieve_for_empty_inputs_returns_empty():
    """空请求 / 空语料都返回 {}，不抛错。"""
    agent = ShenKuoAgent()

    assert await agent.retrieve_for({}, [{"id": 1, "content": "x"}]) == {}
    assert await agent.retrieve_for({"s1": [{"query": "q"}]}, []) == {}


async def test_retrieve_for_returns_keyword_top_n():
    """按 BM25 关键词返回 top_n，分数为 0、reason 标明仅关键词。"""
    agent = ShenKuoAgent(top_n=2)
    chunks = [
        {"id": 1, "content": "配电柜温升试验报告 型式试验"},
        {"id": 2, "content": "无关内容 园林绿化"},
        {"id": 3, "content": "配电柜温升 试验数据"},
    ]

    result = await agent.retrieve_for({"s1": [{"query": "配电柜温升试验"}]}, chunks)

    assert "s1" in result
    assert 0 < len(result["s1"]) <= 2
    assert all(isinstance(m, Match) for m in result["s1"])
    assert all(m.score == 0.0 for m in result["s1"])
    assert all("仅关键词" in m.reason for m in result["s1"])


async def test_retrieve_for_batches_all_requests():
    """多个 block 的请求在一次调用里处理，每块各得结果。"""
    agent = ShenKuoAgent(top_n=2)
    chunks = [
        {"id": 1, "content": "配电柜温升试验报告"},
        {"id": 2, "content": "安全生产许可证 复印件"},
        # BM25Okapi 在 2 篇语料下所有 idf 恰为 0（rank_bm25 只对负 idf 做
        # epsilon 兜底），keyword_search 的 `scores[i] > 0` 过滤会全部滤掉。
        # 补一条无关键干扰项使语料 ≥3 篇，让本用例真正测到批处理行为。
        {"id": 3, "content": "无关内容 园林绿化"},
    ]

    result = await agent.retrieve_for(
        {
            "s1": [{"query": "配电柜温升试验"}],
            "s2": [{"query": "安全生产许可证"}],
        },
        chunks,
    )

    assert set(result.keys()) == {"s1", "s2"}


async def test_retrieve_for_skips_blank_queries():
    """空 query 的请求被跳过，不产生检索。"""
    agent = ShenKuoAgent()
    chunks = [{"id": 1, "content": "配电柜温升试验报告"}]

    result = await agent.retrieve_for({"s1": [{"query": "   "}, {}]}, chunks)

    assert result == {}
