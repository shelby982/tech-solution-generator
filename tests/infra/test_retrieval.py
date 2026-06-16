"""infra/retrieval 测试：BM25 关键词检索 + LLM 重排。"""
import json
import pytest

from infra.retrieval import keyword_search, llm_rerank, Match


# ─────────────────────────────────────────────
# 关键词检索
# ─────────────────────────────────────────────

def test_keyword_search_returns_top_k():
    """top_k 命中相关 chunk，按 BM25 得分降序。"""
    chunks = [
        {"id": 1, "content": "微服务架构基于容器化部署，采用 Kubernetes 编排"},
        {"id": 2, "content": "传统单体架构部署简单，但扩展性较差"},
        {"id": 3, "content": "数据库索引优化，查询性能提升"},
        {"id": 4, "content": "微服务架构需要服务发现与配置中心"},
        {"id": 5, "content": "前端框架对比"},
    ]
    results = keyword_search(chunks, query="微服务 架构 容器", top_k=2)
    assert len(results) == 2
    # 命中含"微服务架构"的两条（id=1 和 id=4）
    assert {r["id"] for r in results} <= {1, 4}


def test_keyword_search_empty_chunks():
    """空 chunks 返回空列表，不抛错。"""
    assert keyword_search([], query="任意查询") == []


def test_keyword_search_empty_query_falls_back_to_first_k():
    """无效 query（无可分词词项）退化为前 K 条，不抛错。"""
    chunks = [{"id": i, "content": "x"} for i in range(3)]
    results = keyword_search(chunks, query="   ", top_k=2)
    assert len(results) == 2


# ─────────────────────────────────────────────
# LLM 重排（mock）
# ─────────────────────────────────────────────

def _fake_config():
    from infra.llm import LLMConfig
    return LLMConfig(
        provider="openai",
        api_key="sk-fake",
        base_url="https://example.invalid/v1",
        model="gpt-test",
    )


@pytest.mark.asyncio
async def test_llm_rerank_returns_top_n_sorted_by_score(monkeypatch):
    """LLM 返回的 matches 按 score 降序排列，截到 top_n。"""
    fake_response = json.dumps({
        "matches": [
            {"chunk_id": "a", "score": 7.0, "reason": "中等", "hit_points": ["要点1"]},
            {"chunk_id": "b", "score": 9.5, "reason": "高度相关", "hit_points": ["要点2"]},
            {"chunk_id": "c", "score": 3.0, "reason": "弱相关", "hit_points": []},
            {"chunk_id": "d", "score": 8.0, "reason": "较强", "hit_points": ["要点3"]},
        ]
    }, ensure_ascii=False)

    async def _fake_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        return fake_response

    monkeypatch.setattr("infra.retrieval.rerank.generate_oneshot_openai", _fake_oneshot)

    chunks = [
        {"chunk_id": "a", "content": "x"},
        {"chunk_id": "b", "content": "y"},
        {"chunk_id": "c", "content": "z"},
        {"chunk_id": "d", "content": "w"},
    ]
    matches = await llm_rerank(
        chunks, query="架构 微服务", requirement="微服务化",
        top_n=2, configs=[_fake_config()], rr_start_index=0,
    )
    assert len(matches) == 2
    assert matches[0].chunk_id == "b"
    assert matches[0].score == 9.5
    assert matches[1].chunk_id == "d"
    assert isinstance(matches[0], Match)


@pytest.mark.asyncio
async def test_llm_rerank_empty_chunks_returns_empty():
    """chunks 为空时返回空列表，不调用 LLM。"""
    matches = await llm_rerank(
        [], query="x", requirement="y",
        top_n=5, configs=[_fake_config()], rr_start_index=0,
    )
    assert matches == []


@pytest.mark.asyncio
async def test_llm_rerank_total_failure_returns_empty(monkeypatch):
    """所有 config 全部失败时返回空列表（不抛错）。"""
    async def _boom(config, system_prompt, user_prompt, max_tokens=1500):
        raise RuntimeError("simulated LLM failure")

    monkeypatch.setattr("infra.retrieval.rerank.generate_oneshot_openai", _boom)

    chunks = [{"chunk_id": "a", "content": "x"}]
    matches = await llm_rerank(
        chunks, query="x", requirement="y",
        top_n=5, configs=[_fake_config()], rr_start_index=0,
    )
    assert matches == []
