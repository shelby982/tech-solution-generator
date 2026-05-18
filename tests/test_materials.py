"""
tests/test_materials.py — materials 路由测试（TDD）
运行：pytest tests/test_materials.py -v
"""
import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from services.parser import ParsedDocument, Section


def _make_parsed_doc(n: int = 3) -> ParsedDocument:
    """生成 n 个章节的 ParsedDocument mock"""
    sections = [
        Section(section_id=f"S-{i:03d}", level=1, title=f"章节 {i}", content_hint="")
        for i in range(1, n + 1)
    ]
    return ParsedDocument(doc_id="doc-1", title="测试文档", sections=sections)


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


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _create_project(client) -> int:
    r = client.post("/api/projects", json={"name": "材料测试项目"})
    return r.json()["id"]


def _upload(client, pid, filename="spec.docx", content=b"PK\x03\x04fake"):
    return client.post(
        f"/api/projects/{pid}/materials",
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


# ── POST /api/projects/{id}/materials ──────────────────────

class TestUploadMaterial:
    def test_upload_returns_record(self, client):
        pid = _create_project(client)
        r = _upload(client, pid)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["project_id"] == pid
        assert d["filename"] == "spec.docx"
        assert d["type"] == "docx"
        assert d["parsed_at"] is None

    def test_upload_saves_file(self, client, tmp_path):
        import routes.materials as m
        orig = m.UPLOAD_ROOT
        m.UPLOAD_ROOT = tmp_path / "uploads"
        pid = _create_project(client)
        _upload(client, pid, content=b"hello-docx")
        saved = m.UPLOAD_ROOT / str(pid) / "spec.docx"
        assert saved.exists()
        m.UPLOAD_ROOT = orig

    def test_upload_project_not_found(self, client):
        r = _upload(client, 99999)
        assert r.status_code == 404

    def test_upload_unsupported_format(self, client):
        pid = _create_project(client)
        r = client.post(
            f"/api/projects/{pid}/materials",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert r.status_code == 400

    def test_upload_empty_file(self, client):
        pid = _create_project(client)
        r = client.post(
            f"/api/projects/{pid}/materials",
            files={"file": ("empty.docx", io.BytesIO(b""), "application/octet-stream")},
        )
        assert r.status_code == 400


# ── GET /api/projects/{id}/materials ───────────────────────

class TestListMaterials:
    def test_list_after_upload(self, client):
        pid = _create_project(client)
        _upload(client, pid)
        r = client.get(f"/api/projects/{pid}/materials")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_list_empty(self, client):
        pid = _create_project(client)
        r = client.get(f"/api/projects/{pid}/materials")
        assert r.json() == []

    def test_list_not_found(self, client):
        r = client.get("/api/projects/99999/materials")
        assert r.status_code == 404


# ── POST /api/projects/{id}/outline（SSE）──────────────────

class TestOutlineSSE:
    def _upload_and_outline(self, client, n_sections=3):
        pid = _create_project(client)
        _upload(client, pid)
        parsed_doc = _make_parsed_doc(n_sections)
        with patch("routes.materials.parse_document", return_value=parsed_doc):
            r = client.post(f"/api/projects/{pid}/outline")
        return r, pid

    def test_outline_event_sequence(self, client):
        r, _ = self._upload_and_outline(client, 3)
        assert r.status_code == 200
        events = _parse_sse(r.text)
        types = [e["event"] for e in events]
        assert "outline_start" in types
        assert "outline_done" in types
        block_events = [e for e in events if e["event"] == "outline_block"]
        assert len(block_events) == 3 * 2  # heading + content per section

    def test_outline_done_block_count(self, client):
        r, _ = self._upload_and_outline(client, 4)
        events = _parse_sse(r.text)
        done = next(e for e in events if e["event"] == "outline_done")
        assert done["data"]["block_count"] == 8  # 4 * 2

    def test_outline_no_material_returns_404(self, client):
        pid = _create_project(client)
        r = client.post(f"/api/projects/{pid}/outline")
        assert r.status_code == 404

    def test_outline_project_not_found(self, client):
        r = client.post("/api/projects/99999/outline")
        assert r.status_code == 404
