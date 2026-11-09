"""infra/retrieval 测试：BM25 关键词检索。"""
from infra.retrieval import keyword_search


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
