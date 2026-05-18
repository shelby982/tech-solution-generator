"""
tests/test_export.py — export 路由测试（TDD）
运行：pytest tests/test_export.py -v
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


def _make_project(base_snapshot_id=1):
    return {"id": 1, "name": "测试项目", "status": "authoring", "base_snapshot_id": base_snapshot_id}


def _make_block(block_id, content, domain="ch1", status="done"):
    return {
        "id": 1, "project_id": 1, "block_id": block_id,
        "kind": "content", "level": 1, "title": block_id,
        "content": content, "domain": domain, "parent_title": "",
        "requirement": "", "score": "", "source": "",
        "order_idx": 0, "status": status,
    }


BASE_BLOCKS_JSON = json.dumps([
    {"block_id": "H-001", "title": "第一章", "content": "旧内容A", "domain": "ch1"},
    {"block_id": "H-OLD", "title": "旧章节", "content": "将删除", "domain": "ch1"},
])

CURRENT_BLOCKS = [
    _make_block("H-001", "新内容A", domain="ch1"),      # changed
    _make_block("H-NEW", "新增内容", domain="ch2"),      # added
]


# ── GET /api/projects/{id}/diff ───────────────────────────

class TestDiff:
    def test_diff_project_not_found(self, client):
        with patch("routes.export.get_project", new=AsyncMock(return_value=None)):
            r = client.get("/api/projects/99/diff")
        assert r.status_code == 404

    def test_diff_no_base_snapshot(self, client):
        """无 base_snapshot_id → 所有 blocks 视为 added"""
        proj = _make_project(base_snapshot_id=None)
        current = [_make_block("H-001", "内容")]
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=current)),
        ):
            r = client.get("/api/projects/1/diff")
        assert r.status_code == 200
        data = r.json()
        assert len(data["added"]) == 1
        assert data["removed"] == []
        assert data["changed"] == []

    def test_diff_added_and_removed(self, client):
        proj = _make_project()
        snap = {"id": 1, "project_id": 1, "trigger": "lock", "snapshot": BASE_BLOCKS_JSON}
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.get_snapshot", new=AsyncMock(return_value=snap)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=CURRENT_BLOCKS)),
        ):
            r = client.get("/api/projects/1/diff")
        data = r.json()
        added_ids = [b["block_id"] for b in data["added"]]
        removed_ids = [b["block_id"] for b in data["removed"]]
        assert "H-NEW" in added_ids
        assert "H-OLD" in removed_ids

    def test_diff_changed(self, client):
        proj = _make_project()
        snap = {"id": 1, "project_id": 1, "trigger": "lock", "snapshot": BASE_BLOCKS_JSON}
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.get_snapshot", new=AsyncMock(return_value=snap)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=CURRENT_BLOCKS)),
        ):
            r = client.get("/api/projects/1/diff")
        changed_ids = [c["block_id"] for c in r.json()["changed"]]
        assert "H-001" in changed_ids
        h001 = next(c for c in r.json()["changed"] if c["block_id"] == "H-001")
        assert h001["old_content"] == "旧内容A"
        assert h001["new_content"] == "新内容A"


# ── POST /api/projects/{id}/diff/apply ───────────────────

class TestDiffApply:
    def test_apply_project_not_found(self, client):
        with patch("routes.export.get_project", new=AsyncMock(return_value=None)):
            r = client.post("/api/projects/99/diff/apply", json={"accept": [], "reject": []})
        assert r.status_code == 404

    def test_apply_reject_calls_add_revision(self, client):
        """reject 的 block → add_revision(source=restore) 被调用"""
        proj = _make_project()
        snap = {"id": 1, "project_id": 1, "trigger": "lock", "snapshot": BASE_BLOCKS_JSON}
        current = [_make_block("H-001", "新内容A")]
        mock_add = AsyncMock()
        mock_snap = AsyncMock(return_value={"id": 2, "project_id": 1, "trigger": "apply_diff", "snapshot": "[]"})
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.get_snapshot", new=AsyncMock(return_value=snap)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=current)),
            patch("routes.export.add_revision", mock_add),
            patch("routes.export.update_block_status", new=AsyncMock()),
            patch("routes.export.create_snapshot", mock_snap),
        ):
            r = client.post("/api/projects/1/diff/apply", json={"accept": [], "reject": ["H-001"]})
        assert r.status_code == 200
        mock_add.assert_called_once()
        assert "restore" in str(mock_add.call_args)

    def test_apply_creates_snapshot(self, client):
        proj = _make_project()
        snap = {"id": 1, "project_id": 1, "trigger": "lock", "snapshot": BASE_BLOCKS_JSON}
        current = [_make_block("H-001", "新内容A")]
        mock_snap = AsyncMock(return_value={"id": 99, "project_id": 1, "trigger": "apply_diff", "snapshot": "[]"})
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.get_snapshot", new=AsyncMock(return_value=snap)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=current)),
            patch("routes.export.add_revision", new=AsyncMock()),
            patch("routes.export.update_block_status", new=AsyncMock()),
            patch("routes.export.create_snapshot", mock_snap),
        ):
            r = client.post("/api/projects/1/diff/apply", json={"accept": ["H-001"], "reject": []})
        assert r.status_code == 200
        assert r.json()["snapshot_id"] == 99
        mock_snap.assert_called_once()
        assert "apply_diff" in str(mock_snap.call_args)


# ── GET /api/projects/{id}/export ────────────────────────

class TestExport:
    def test_export_project_not_found(self, client):
        with patch("routes.export.get_project", new=AsyncMock(return_value=None)):
            r = client.get("/api/projects/99/export")
        assert r.status_code == 404

    def test_export_returns_docx_bytes(self, client):
        proj = _make_project()
        blocks = [_make_block("H-001", "内容A"), _make_block("H-002", "内容B")]
        fake_bytes = b"PK\x03\x04fake-docx"
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=blocks)),
            patch("routes.export.sections_to_docx", return_value=fake_bytes),
        ):
            r = client.get("/api/projects/1/export")
        assert r.status_code == 200
        assert r.content == fake_bytes
        assert "wordprocessingml" in r.headers["content-type"]

    def test_export_filters_empty_blocks(self, client):
        """status=empty 的 block 不传给 sections_to_docx"""
        proj = _make_project()
        blocks = [
            _make_block("H-001", "内容A", status="done"),
            _make_block("H-002", "", status="empty"),
        ]
        captured = {}
        def fake_docx(sections, doc_title=""):
            captured["sections"] = sections
            return b"fake"
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=blocks)),
            patch("routes.export.sections_to_docx", side_effect=fake_docx),
        ):
            r = client.get("/api/projects/1/export")
        assert r.status_code == 200
        assert all(s["id"] != "H-002" for s in captured["sections"])

    def test_export_no_done_blocks_returns_404(self, client):
        proj = _make_project()
        blocks = [_make_block("H-001", "", status="empty")]
        with (
            patch("routes.export.get_project", new=AsyncMock(return_value=proj)),
            patch("routes.export.list_blocks", new=AsyncMock(return_value=blocks)),
        ):
            r = client.get("/api/projects/1/export")
        assert r.status_code == 404
