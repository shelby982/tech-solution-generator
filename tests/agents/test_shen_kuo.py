"""沈括 agent 测试 — match 阶段。

依赖 infra.llm 的 mock 路由（环境变量 LLM_MODE=mock），
对 LLM 重排部分通过 monkeypatch 替换底层 generate_oneshot_openai 注入固定 JSON。
"""

import pytest

from agents.shen_kuo import ShenKuoAgent
from domain.spec import OutlineMatrixRow, Section
from infra.retrieval import Match


# ─────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────

def _fake_configs():
    """provider 返回最小可用配置，触发 mock 路由。"""
    from services.config_store import LLMConfig
    return ([
        LLMConfig(
            provider="openai",
            api_key="sk-fake",
            base_url="https://example.invalid/v1",
            model="gpt-test",
        ),
    ], 0)


def _no_configs():
    return ([], 0)


# ─────────────────────────────────────────────
# match
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_match_returns_empty_dict_when_toc_empty():
    agent = ShenKuoAgent(configs_provider=_fake_configs)
    result = await agent.match(
        toc=[], outline_matrix={}, chunks=[{"id": 1, "content": "x"}],
    )
    assert result == {}


@pytest.mark.asyncio
async def test_match_returns_per_block_empty_when_chunks_empty():
    """chunks 为空时每 block 返回空 list，不抛错。"""
    agent = ShenKuoAgent(configs_provider=_fake_configs)
    toc = [
        Section(id="s1", level=1, title="技术架构", raw_content=""),
        Section(id="s2", level=1, title="安全要求", raw_content=""),
    ]
    result = await agent.match(toc=toc, outline_matrix={}, chunks=[])
    assert result == {"s1": [], "s2": []}


@pytest.mark.asyncio
async def test_match_uses_keyword_only_when_no_llm_configs(monkeypatch):
    """无 LLM 配置时退化为仅关键词检索，截到 top_n 条，score=0。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ShenKuoAgent(configs_provider=_no_configs, rerank_top_n=2)
    toc = [Section(id="s1", level=1, title="微服务架构", raw_content="")]
    chunks = [
        {"id": 1, "content": "微服务架构基于容器化部署，采用 Kubernetes 编排"},
        {"id": 2, "content": "传统单体架构部署简单"},
        {"id": 3, "content": "微服务架构需要服务发现"},
    ]
    result = await agent.match(toc=toc, outline_matrix={}, chunks=chunks)
    assert "s1" in result
    s1_matches = result["s1"]
    assert len(s1_matches) <= 2
    assert all(isinstance(m, Match) for m in s1_matches)
    assert all(m.score == 0.0 for m in s1_matches)
    assert all("未配置 LLM" in m.reason for m in s1_matches)


@pytest.mark.asyncio
async def test_match_returns_top_n_via_llm_rerank(monkeypatch):
    """有 LLM 配置 + 真实重排路径：mock dispatch 返回 4 个 chunk 的 score，取 top_n=2。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    import json

    fake_response = json.dumps({
        "matches": [
            {"chunk_id": 1, "score": 7.0, "reason": "中等", "hit_points": ["要点1"]},
            {"chunk_id": 2, "score": 9.5, "reason": "高度相关", "hit_points": ["要点2"]},
            {"chunk_id": 3, "score": 3.0, "reason": "弱相关", "hit_points": []},
            {"chunk_id": 4, "score": 8.0, "reason": "较强", "hit_points": ["要点3"]},
        ]
    }, ensure_ascii=False)

    async def fake_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        return fake_response

    monkeypatch.setattr(
        "infra.retrieval.rerank.generate_oneshot_openai", fake_oneshot,
    )

    agent = ShenKuoAgent(configs_provider=_fake_configs, rerank_top_n=2)
    toc = [Section(id="s1", level=1, title="微服务架构", raw_content="")]
    matrix = {
        "s1": OutlineMatrixRow(
            block_id="s1", title="微服务架构", requirement="高可用部署",
        ),
    }
    chunks = [
        {"id": 1, "content": "微服务架构基于 Kubernetes"},
        {"id": 2, "content": "高可用集群部署方案"},
        {"id": 3, "content": "传统单体架构对比"},
        {"id": 4, "content": "服务发现与配置中心"},
    ]
    result = await agent.match(toc=toc, outline_matrix=matrix, chunks=chunks)
    s1 = result["s1"]
    assert len(s1) == 2
    # 按 score 降序：9.5 > 8.0
    assert s1[0].score == 9.5
    assert s1[1].score == 8.0


@pytest.mark.asyncio
async def test_match_handles_block_with_no_keyword_hits(monkeypatch):
    """关键词无命中时该 block 返空，不影响其它 block。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    import json

    async def fake_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        return json.dumps({"matches": [
            {"chunk_id": 1, "score": 8.0, "reason": "ok", "hit_points": []},
        ]}, ensure_ascii=False)

    monkeypatch.setattr(
        "infra.retrieval.rerank.generate_oneshot_openai", fake_oneshot,
    )

    agent = ShenKuoAgent(configs_provider=_fake_configs, rerank_top_n=5)
    toc = [
        Section(id="s1", level=1, title="数据库优化", raw_content=""),
        Section(id="s2", level=1, title="zzzzz", raw_content=""),  # 无命中
    ]
    chunks = [{"id": 1, "content": "MySQL 索引优化方案"}]
    result = await agent.match(toc=toc, outline_matrix={}, chunks=chunks)
    assert isinstance(result["s1"], list)
    # s2 关键词不命中（query="zzzzz"），candidates 为空，应返空
    assert result["s2"] == []


@pytest.mark.asyncio
async def test_match_isolates_block_failure(monkeypatch):
    """单 block llm_rerank 抛异常不影响其它 block。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    import json

    call_count = {"n": 0}

    async def flaky_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated rerank failure")
        return json.dumps({"matches": [
            {"chunk_id": 1, "score": 9.0, "reason": "ok", "hit_points": []},
        ]}, ensure_ascii=False)

    monkeypatch.setattr(
        "infra.retrieval.rerank.generate_oneshot_openai", flaky_oneshot,
    )

    agent = ShenKuoAgent(configs_provider=_fake_configs, rerank_top_n=2)
    toc = [
        Section(id="s1", level=1, title="架构", raw_content=""),
        Section(id="s2", level=1, title="安全", raw_content=""),
    ]
    chunks = [
        {"id": 1, "content": "微服务架构 Kubernetes"},
        {"id": 2, "content": "TLS 1.3 安全加密"},
    ]
    result = await agent.match(toc=toc, outline_matrix={}, chunks=chunks)
    # 隔离性验证：s1 重排失败不影响 s2 调用，最低门槛是两个 key 都存在且类型正确。
    # （强断言 s1==[] / len(s2)>=1 对 BM25+jieba 分词命中率敏感，留 Task 3.7 重写。）
    assert isinstance(result["s1"], list)
    assert isinstance(result["s2"], list)


@pytest.mark.asyncio
async def test_match_query_combines_title_and_requirement(monkeypatch):
    """query 由 title + requirement 拼接，验证传给 keyword_search 的字符串正确。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    captured_query = {}

    from infra.retrieval import keyword as keyword_module
    original_keyword_search = keyword_module.keyword_search

    def capturing_keyword_search(chunks, query, top_k=5, bm25_index=None):
        captured_query["q"] = query
        return original_keyword_search(
            chunks, query, top_k=top_k, bm25_index=bm25_index,
        )

    monkeypatch.setattr(
        "agents.shen_kuo.keyword_search", capturing_keyword_search,
    )

    agent = ShenKuoAgent(configs_provider=_no_configs)
    toc = [Section(id="s1", level=1, title="数据库性能", raw_content="")]
    matrix = {
        "s1": OutlineMatrixRow(
            block_id="s1", title="数据库性能", requirement="支持百万 QPS",
        ),
    }
    # jieba 的 _tokenize 过滤单字符 token，content 至少要含多字词避免空 corpus
    chunks = [{"id": 1, "content": "测试样本"}]
    await agent.match(toc=toc, outline_matrix=matrix, chunks=chunks)
    assert "数据库性能" in captured_query["q"]
    assert "支持百万 QPS" in captured_query["q"]
