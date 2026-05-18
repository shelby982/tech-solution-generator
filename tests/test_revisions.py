"""
tests/test_revisions.py — revisions 路由测试（TDD）
运行：pytest tests/test_revisions.py -v
"""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


MOCK_BLOCK = {"id": 2, "title": "测试块", "content": "第一版内容", "status": "done"}

MOCK_REVISIONS = [
    {
        "id": 1, "block_id": 2, "revision_no": 1,
        "content": "第一版内容", "summary": "AI 初稿",
        "source": "generate", "created_at": "2026-05-18T10:00:00",
    },
    {
        "id": 2, "block_id": 2, "revision_no": 2,
        "content": "第二版内容", "summary": "手动编辑",
        "source": "edit", "created_at": "2026-05-18T11:00:00",
    },
]

NEW_REVISION = {
    "id": 3, "block_id": 2, "revision_no": 3,
    "content": "第一版内容", "summary": "回溯至 r1",
    "source": "restore", "created_at": "2026-05-18T12:00:00",
}


def test_list_revisions_returns_sorted(client):
    """返回按 revision_no 升序的列表，字段齐全"""
    with (
        patch("routes.revisions.get_block", new=AsyncMock(return_value=MOCK_BLOCK)),
        patch("routes.revisions.list_revisions", new=AsyncMock(return_value=MOCK_REVISIONS)),
    ):
        r = client.get("/api/blocks/2/revisions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["revision_no"] == 1
    assert data[1]["revision_no"] == 2
    required = {"id", "block_id", "revision_no", "content", "summary", "source", "created_at"}
    assert required.issubset(data[0].keys())


def test_list_revisions_empty(client):
    """block 存在但无历史 → 返回空列表"""
    with (
        patch("routes.revisions.get_block", new=AsyncMock(return_value=MOCK_BLOCK)),
        patch("routes.revisions.list_revisions", new=AsyncMock(return_value=[])),
    ):
        r = client.get("/api/blocks/2/revisions")
    assert r.status_code == 200
    assert r.json() == []


def test_list_revisions_block_not_found(client):
    """block 不存在 → 404"""
    with patch("routes.revisions.get_block", new=AsyncMock(return_value=None)):
        r = client.get("/api/blocks/999/revisions")
    assert r.status_code == 404
    assert "detail" in r.json()


def test_restore_success(client):
    """正常回溯：返回正确结构"""
    mock_add = AsyncMock(return_value=NEW_REVISION)
    with (
        patch("routes.revisions.get_block", new=AsyncMock(return_value=MOCK_BLOCK)),
        patch("routes.revisions.list_revisions", new=AsyncMock(return_value=MOCK_REVISIONS)),
        patch("routes.revisions.update_block_content", new=AsyncMock(return_value={**MOCK_BLOCK, "content": "第一版内容"})),
        patch("routes.revisions.add_revision", mock_add),
    ):
        r = client.post("/api/blocks/2/revisions/1/restore")
    assert r.status_code == 200
    data = r.json()
    assert data["block_id"] == 2
    assert data["restored_from"] == 1
    assert data["new_revision_no"] == 3
    assert data["content"] == "第一版内容"


def test_restore_calls_add_revision_with_correct_source(client):
    """add_revision 必须以 source=restore 调用"""
    mock_add = AsyncMock(return_value=NEW_REVISION)
    with (
        patch("routes.revisions.get_block", new=AsyncMock(return_value=MOCK_BLOCK)),
        patch("routes.revisions.list_revisions", new=AsyncMock(return_value=MOCK_REVISIONS)),
        patch("routes.revisions.update_block_content", new=AsyncMock(return_value=MOCK_BLOCK)),
        patch("routes.revisions.add_revision", mock_add),
    ):
        client.post("/api/blocks/2/revisions/1/restore")
    mock_add.assert_called_once()
    call_str = str(mock_add.call_args)
    assert "restore" in call_str
    assert "回溯至 r1" in call_str


def test_restore_block_not_found(client):
    """block 不存在 → 404"""
    with patch("routes.revisions.get_block", new=AsyncMock(return_value=None)):
        r = client.post("/api/blocks/999/revisions/1/restore")
    assert r.status_code == 404


def test_restore_revision_not_found(client):
    """revision_no 不存在 → 404"""
    with (
        patch("routes.revisions.get_block", new=AsyncMock(return_value=MOCK_BLOCK)),
        patch("routes.revisions.list_revisions", new=AsyncMock(return_value=MOCK_REVISIONS)),
    ):
        r = client.post("/api/blocks/2/revisions/99/restore")
    assert r.status_code == 404
    assert "detail" in r.json()
