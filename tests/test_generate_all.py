# tests/test_generate_all.py
"""批量编写模块测试 — pytest tests/test_generate_all.py -v"""
from services.retrieval import retrieve_chunks

def _make_chunks(texts: list[str]) -> list[dict]:
    return [
        {"material_id": 1, "chunk_index": i, "content": t}
        for i, t in enumerate(texts)
    ]

def test_retrieve_returns_top_k():
    chunks = _make_chunks(["技术方案 架构设计", "价格报价", "技术方案 实施计划", "售后服务", "技术方案 测试"])
    result = retrieve_chunks(chunks, query="技术方案", top_k=3)
    assert len(result) == 3
    contents = [r["content"] for r in result]
    assert all("技术方案" in c for c in contents)

def test_retrieve_returns_all_when_less_than_k():
    chunks = _make_chunks(["A", "B"])
    result = retrieve_chunks(chunks, query="A", top_k=5)
    assert len(result) == 2

def test_retrieve_empty_chunks():
    result = retrieve_chunks([], query="技术方案", top_k=3)
    assert result == []
