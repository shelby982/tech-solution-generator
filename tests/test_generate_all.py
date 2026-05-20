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

import pytest
from unittest.mock import patch

@pytest.mark.asyncio
async def test_dispatch_block_write_yields_tokens():
    from services.llm import dispatch_block_write
    from services.config_store import LLMConfig

    fake_config = LLMConfig(
        provider="openai", model="gpt-4o-mini",
        api_key="sk-test", base_url="https://api.openai.com/v1",
    )

    async def fake_stream(*args, **kwargs):
        for token in ["技", "术", "方", "案"]:
            yield token

    with patch("services.llm.stream_generate", side_effect=fake_stream):
        tokens = []
        async for t in dispatch_block_write(
            configs=[fake_config], rr_start_index=0,
            title="技术方案", requirement="需满足 ISO 标准",
            chunks=[{"content": "参考案例：某项目采用…"}],
        ):
            tokens.append(t)

    assert tokens == ["技", "术", "方", "案"]

import json as _json
from fastapi.testclient import TestClient
from main import app

def _parse_sse_events(text: str) -> list[dict]:
    events, cur = [], {}
    for line in text.splitlines():
        if line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            cur["data"] = _json.loads(line[5:].strip())
        elif line == "" and cur:
            events.append(cur); cur = {}
    if cur:
        events.append(cur)
    return events

def test_generate_all_no_blocks():
    """项目无 blocks 时返回 generate_done(generated=0)"""
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/projects", json={"name": "gen-test"})
    pid = r.json()["id"]
    resp = client.post(f"/api/projects/{pid}/generate-all")
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    names = [e["event"] for e in events]
    assert "generate_start" in names
    assert "generate_done" in names
    done = next(e for e in events if e["event"] == "generate_done")
    assert done["data"]["generated"] == 0
