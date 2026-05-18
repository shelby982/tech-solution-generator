"""
tests/test_projects.py — projects 路由测试（TDD）
运行：pytest tests/test_projects.py -v
"""
import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


MOCK_PROJECT = {
    "id": 1,
    "name": "测试项目",
    "status": "init",
    "deadline": "2026-12-31",
    "summary": None,
    "base_snapshot_id": None,
    "created_at": "2026-05-18T10:00:00",
}


# ── GET /api/projects ─────────────────────────────────────

def test_list_projects_empty(client):
    with patch("routes.projects.list_projects", new=AsyncMock(return_value=[])):
        r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == []


def test_list_projects_returns_list(client):
    with patch("routes.projects.list_projects", new=AsyncMock(return_value=[MOCK_PROJECT])):
        r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "测试项目"


def test_list_projects_with_status_filter(client):
    with patch("routes.projects.list_projects", new=AsyncMock(return_value=[])):
        r = client.get("/api/projects?status=done")
    assert r.status_code == 200


# ── POST /api/projects ────────────────────────────────────

def test_create_project_success(client):
    with patch("routes.projects.create_project", new=AsyncMock(return_value=MOCK_PROJECT)):
        r = client.post("/api/projects", json={"name": "测试项目"})
    assert r.status_code == 200
    assert r.json()["name"] == "测试项目"
    assert r.json()["status"] == "init"


def test_create_project_with_deadline(client):
    mock = {**MOCK_PROJECT, "deadline": "2026-06-30"}
    with patch("routes.projects.create_project", new=AsyncMock(return_value=mock)):
        r = client.post("/api/projects", json={"name": "有截止日期的项目", "deadline": "2026-06-30"})
    assert r.status_code == 200
    assert r.json()["deadline"] == "2026-06-30"


def test_create_project_empty_name(client):
    r = client.post("/api/projects", json={"name": ""})
    assert r.status_code == 422


# ── GET /api/projects/{id} ────────────────────────────────

def test_get_project_success(client):
    with patch("routes.projects.get_project", new=AsyncMock(return_value=MOCK_PROJECT)):
        r = client.get("/api/projects/1")
    assert r.status_code == 200
    assert r.json()["id"] == 1


def test_get_project_not_found(client):
    with patch("routes.projects.get_project", new=AsyncMock(return_value=None)):
        r = client.get("/api/projects/999")
    assert r.status_code == 404


# ── PATCH /api/projects/{id} ──────────────────────────────

def test_patch_project_success(client):
    updated = {**MOCK_PROJECT, "name": "新名称"}
    with (
        patch("routes.projects.get_project", new=AsyncMock(return_value=MOCK_PROJECT)),
        patch("routes.projects.update_project", new=AsyncMock(return_value=updated)),
    ):
        r = client.patch("/api/projects/1", json={"name": "新名称"})
    assert r.status_code == 200
    assert r.json()["name"] == "新名称"


def test_patch_project_not_found(client):
    with patch("routes.projects.get_project", new=AsyncMock(return_value=None)):
        r = client.patch("/api/projects/999", json={"name": "不存在"})
    assert r.status_code == 404


# ── POST /api/projects/{id}/lock ──────────────────────────

def test_lock_project_success(client):
    authoring_project = {**MOCK_PROJECT, "status": "authoring", "base_snapshot_id": 5}
    blocks = [
        {"id": 1, "block_id": "H-001", "title": "章节", "content": "内容", "domain": "架构"}
    ]
    mock_snap = {"id": 5, "project_id": 1, "trigger": "lock", "snapshot": "[]"}
    with (
        patch("routes.projects.get_project", new=AsyncMock(return_value=MOCK_PROJECT)),
        patch("routes.projects.list_blocks", new=AsyncMock(return_value=blocks)),
        patch("routes.projects.create_snapshot", new=AsyncMock(return_value=mock_snap)),
        patch("routes.projects.update_project", new=AsyncMock(return_value=authoring_project)),
    ):
        r = client.post("/api/projects/1/lock")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "authoring"
    assert data["base_snapshot_id"] == 5


def test_lock_project_not_found(client):
    with patch("routes.projects.get_project", new=AsyncMock(return_value=None)):
        r = client.post("/api/projects/999/lock")
    assert r.status_code == 404
