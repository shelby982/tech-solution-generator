"""
tests/test_blocks.py — blocks 路由测试（TDD）
运行：pytest tests/test_blocks.py -v
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


FAKE_HEADING = {
    "id": 1, "project_id": 1, "block_id": "H-001", "kind": "heading",
    "level": 1, "title": "项目理解", "content": "",
    "domain": "技术方案", "parent_title": "", "requirement": "",
    "score": "", "source": "", "order_idx": 0, "status": "empty",
    "created_at": "2026-05-18T10:00:00", "updated_at": "2026-05-18T10:00:00",
}
FAKE_CONTENT = {
    "id": 2, "project_id": 1, "block_id": "C-001", "kind": "content",
    "level": 1, "title": "项目理解正文", "content": "现有内容",
    "domain": "技术方案", "parent_title": "项目理解",
    "requirement": "需覆盖高可用要求", "score": "20分",
    "source": "招标文件第3章", "order_idx": 1, "status": "empty",
    "created_at": "2026-05-18T10:00:00", "updated_at": "2026-05-18T10:00:00",
}


def _parse_sse(text: str) -> list[dict]:
    events, cur = [], {}
    for line in text.splitlines():
        if line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            cur["data"] = json.loads(line[5:].strip())
        elif line == "" and cur:
            events.append(cur); cur = {}
    return events


# ── GET /api/projects/{id}/blocks ────────────────────────

def test_get_blocks_returns_list(client):
    with patch("routes.blocks.list_blocks", new=AsyncMock(return_value=[FAKE_HEADING, FAKE_CONTENT])):
        r = client.get("/api/projects/1/blocks")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    content_block = next(b for b in data if b["kind"] == "content")
    assert content_block["requirement"] == "需覆盖高可用要求"
    assert content_block["score"] == "20分"


def test_get_blocks_empty(client):
    with patch("routes.blocks.list_blocks", new=AsyncMock(return_value=[])):
        r = client.get("/api/projects/99/blocks")
    assert r.status_code == 200
    assert r.json() == []


# ── PUT /api/blocks/{id} ─────────────────────────────────

FAKE_UPDATED = {**FAKE_CONTENT, "content": "更新后内容", "status": "done"}


def test_put_block_updates_content(client):
    with (
        patch("routes.blocks.get_block", new=AsyncMock(return_value=FAKE_CONTENT)),
        patch("routes.blocks.update_block_content", new=AsyncMock(return_value=FAKE_UPDATED)),
        patch("routes.blocks.add_revision", new=AsyncMock(return_value={"id": 1, "revision_no": 1})),
    ):
        r = client.put("/api/blocks/2", json={"content": "更新后内容"})
    assert r.status_code == 200
    assert r.json()["content"] == "更新后内容"


def test_put_block_auto_revision(client):
    mock_add = AsyncMock(return_value={"id": 1, "revision_no": 1})
    with (
        patch("routes.blocks.get_block", new=AsyncMock(return_value=FAKE_CONTENT)),
        patch("routes.blocks.update_block_content", new=AsyncMock(return_value=FAKE_UPDATED)),
        patch("routes.blocks.add_revision", mock_add),
    ):
        client.put("/api/blocks/2", json={"content": "新内容"})
    mock_add.assert_called_once()
    kwargs = mock_add.call_args.kwargs
    assert kwargs.get("source") == "edit"


def test_put_block_404(client):
    with patch("routes.blocks.get_block", new=AsyncMock(return_value=None)):
        r = client.put("/api/blocks/999", json={"content": "内容"})
    assert r.status_code == 404


# ── POST /api/blocks/{id}/generate（SSE）────────────────

async def _fake_stream(*args, **kwargs):
    for t in ["这是", "生成的", "内容"]:
        yield t


def test_generate_sse_sequence(client):
    mock_cfg = MagicMock()
    mock_config = MagicMock()
    mock_config.is_configured.return_value = True
    mock_config.get_configs_and_next_index.return_value = ([mock_cfg], 0)

    with (
        patch("routes.blocks.get_block", new=AsyncMock(return_value=FAKE_CONTENT)),
        patch("routes.blocks.config_store", mock_config),
        patch("routes.blocks.dispatch_stream_generate", new=_fake_stream),
        patch("routes.blocks.update_block_content",
              new=AsyncMock(return_value={**FAKE_CONTENT, "content": "这是生成的内容"})),
        patch("routes.blocks.update_block_status", new=AsyncMock()),
        patch("routes.blocks.add_revision",
              new=AsyncMock(return_value={"id": 1, "revision_no": 1})),
    ):
        with client.stream("POST", "/api/blocks/2/generate") as resp:
            body = resp.read().decode()

    events = _parse_sse(body)
    types = [e["event"] for e in events]
    assert "token" in types
    assert types[-1] == "block_done"
    done = events[-1]["data"]
    assert "content" in done
    assert "revision_no" in done


def test_generate_sse_block_not_found(client):
    with patch("routes.blocks.get_block", new=AsyncMock(return_value=None)):
        r = client.post("/api/blocks/999/generate")
    assert r.status_code == 404


def test_generate_no_api_config(client):
    mock_config = MagicMock()
    mock_config.is_configured.return_value = False
    with (
        patch("routes.blocks.get_block", new=AsyncMock(return_value=FAKE_CONTENT)),
        patch("routes.blocks.config_store", mock_config),
    ):
        r = client.post("/api/blocks/2/generate")
    assert r.status_code == 400


# ── POST /api/blocks/{id}/ai ─────────────────────────────

@pytest.mark.parametrize("action", ["polish", "expand", "check", "search", "style"])
def test_ai_action_returns_suggestion(client, action):
    mock_config = MagicMock()
    mock_config.is_configured.return_value = True
    mock_cfg = MagicMock()
    mock_cfg.provider = "openai"
    mock_config.get_configs_and_next_index.return_value = ([mock_cfg], 0)
    mock_update = AsyncMock()

    with (
        patch("routes.blocks.get_block", new=AsyncMock(return_value=FAKE_CONTENT)),
        patch("routes.blocks.config_store", mock_config),
        patch("routes.blocks.OPENAI_COMPATIBLE_PROVIDERS", ["openai"]),
        patch("routes.blocks._generate_oneshot_openai",
              new=AsyncMock(return_value=f"{action} 后的建议")),
        patch("routes.blocks.update_block_content", mock_update),
    ):
        r = client.post("/api/blocks/2/ai", json={"action": action})

    assert r.status_code == 200
    d = r.json()
    assert d["action"] == action
    assert len(d["suggestion"]) > 0
    assert d["block_id"] == 2
    mock_update.assert_not_called()  # 不写入 DB


def test_ai_invalid_action(client):
    with patch("routes.blocks.get_block", new=AsyncMock(return_value=FAKE_CONTENT)):
        r = client.post("/api/blocks/2/ai", json={"action": "unknown"})
    assert r.status_code == 422


def test_ai_block_404(client):
    with patch("routes.blocks.get_block", new=AsyncMock(return_value=None)):
        r = client.post("/api/blocks/999/ai", json={"action": "polish"})
    assert r.status_code == 404
