"""素材文件丢失检测：记录在、文件没了 → 必须显式暴露。"""
from services.material_integrity import check_materials, resolve_path


def test_resolve_path_relative_and_absolute(tmp_path):
    assert resolve_path("uploads/26/a.pdf", tmp_path) == tmp_path / "uploads/26/a.pdf"
    assert resolve_path(str(tmp_path / "x.pdf"), tmp_path) == tmp_path / "x.pdf"


def test_check_reports_missing_empty_and_no_path(tmp_path):
    good = tmp_path / "uploads" / "26" / "ok.pdf"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"hello")
    empty = tmp_path / "uploads" / "26" / "empty.pdf"
    empty.write_bytes(b"")

    mats = [
        {"id": 1, "filename": "ok.pdf", "role": "main_rfp", "file_path": "uploads/26/ok.pdf"},
        {"id": 2, "filename": "gone.pdf", "role": "spec", "file_path": "uploads/26/gone.pdf"},
        {"id": 3, "filename": "empty.pdf", "role": "spec", "file_path": "uploads/26/empty.pdf"},
        {"id": 4, "filename": "old.pdf", "role": "spec", "file_path": ""},
    ]
    r = check_materials(mats, data_root=tmp_path)
    assert r["total"] == 4 and r["ok"] == 1
    by_id = {x["id"]: x["reason"] for x in r["lost"]}
    assert by_id == {2: "missing", 3: "empty", 4: "no_path"}


def test_check_all_ok_when_files_present(tmp_path):
    f = tmp_path / "uploads" / "26" / "a.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)
    r = check_materials([{"id": 1, "filename": "a.pdf", "file_path": "uploads/26/a.pdf"}],
                        data_root=tmp_path)
    assert r["lost"] == [] and r["ok"] == 1


def test_check_flags_done_but_no_chunks(tmp_path):
    """**最隐蔽的一种**：解析标记 done，却一个正文片段都没有。"""
    f = tmp_path / "uploads" / "26" / "a.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)
    r = check_materials(
        [{"id": 1, "filename": "a.pdf", "file_path": "uploads/26/a.pdf", "parse_status": "done"}],
        data_root=tmp_path, chunk_counts={"1": 0})
    assert r["ok"] == 0 and r["lost"][0]["reason"] == "no_chunks"

    # 有片段就不算问题
    r2 = check_materials(
        [{"id": 1, "filename": "a.pdf", "file_path": "uploads/26/a.pdf", "parse_status": "done"}],
        data_root=tmp_path, chunk_counts={"1": 42})
    assert r2["lost"] == []

    # 没给计数就不判（避免误报"还没开始解析"的新文件）
    r3 = check_materials(
        [{"id": 2, "filename": "b.pdf", "file_path": "uploads/26/a.pdf", "parse_status": "pending"}],
        data_root=tmp_path)
    assert r3["lost"] == []
