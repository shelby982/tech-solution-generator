"""上传接口的校验与落盘（A-pptx / A-role / A-filename 三条审查发现的回归）。

- **A-pptx**：`.ppt/.pptx` 曾在白名单里，但解析器只认 pdf/docx/doc —— 上传返回 200
  且显示 done，到「拆分应答目录」才 ValueError，表现为整次 run failed。
- **A-role**：未知 role 的文件会入库落盘，但 `_load_spec_for_project` 取不到、
  界面也不显示（僵尸记录）。
- **A-filename**：`file.filename` 直接参与路径拼接 → `../../x.pdf` 可写出上传根之外；
  同名重复上传覆盖前一份字节，而库里留两条指向同一路径的记录。
"""

import asyncio

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import db as db_module
from db import init_db, get_db
from routes import materials as materials_routes


@pytest.fixture
def app():
    from fastapi import FastAPI

    application = FastAPI()
    application.include_router(materials_routes.router, prefix="/api")
    return application


@pytest_asyncio.fixture
async def upload_env(tmp_path, monkeypatch, app):
    """隔离 DB + 隔离上传根目录，返回 (client, upload_root, project_id)。"""
    db_path = str(tmp_path / "materials.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(materials_routes, "UPLOAD_ROOT", upload_root)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        cur = await conn.execute("INSERT INTO projects (name) VALUES ('上传测试')")
        await conn.commit()
        project_id = cur.lastrowid

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        yield client, upload_root, project_id

    # 上传会起后台解析任务（materials._parse_and_chunk_in_background）。只等这一批
    # （有界），别去动 asyncio 内部任务 —— 取消它们会把事件循环自己卡死。
    tasks = list(materials_routes._PARSE_TASKS)
    if tasks:
        await asyncio.wait(tasks, timeout=3)


async def _upload(client, project_id, filename, *, role="main_rfp", data=b"%PDF-1.4 x"):
    return await client.post(
        f"/api/projects/{project_id}/materials",
        files={"file": (filename, data, "application/octet-stream")},
        data={"material_role": role},
    )


@pytest.mark.asyncio
async def test_ppt_and_pptx_rejected_with_helpful_message(upload_env):
    client, _, pid = upload_env

    for name in ("方案.pptx", "旧稿.ppt", "方案.PPTX"):
        r = await _upload(client, pid, name)
        assert r.status_code == 400, name
        detail = r.json()["detail"]
        assert "不支持的格式" in detail and "PDF" in detail


@pytest.mark.asyncio
async def test_unknown_role_rejected(upload_env):
    """未知 role 不能再静默入库（否则是取不到也看不见的僵尸记录）。"""
    client, _, pid = upload_env

    r = await _upload(client, pid, "招标文件.pdf", role="Requirements")
    assert r.status_code == 400
    assert "未知的材料角色" in r.json()["detail"]

    # 空值会被 FastAPI 当作未传 → 落到默认 main_rfp（合理，不必报错）
    assert (await _upload(client, pid, "招标文件.pdf", role="")).status_code == 200


@pytest.mark.asyncio
async def test_every_allowed_role_accepted(upload_env):
    client, _, pid = upload_env
    from routes.materials import ALLOWED_ROLES

    for i, role in enumerate(sorted(ALLOWED_ROLES)):
        r = await _upload(client, pid, f"f{i}.pdf", role=role)
        assert r.status_code == 200, (role, r.text)


@pytest.mark.asyncio
async def test_traversal_filename_cannot_escape_upload_root(upload_env):
    """`../../evil.pdf` 只能落在项目上传目录内（basename 净化）。"""
    client, upload_root, pid = upload_env

    r = await _upload(client, pid, "../../evil.pdf")
    assert r.status_code == 200

    async with get_db() as db:
        cur = await db.execute("SELECT filename, file_path FROM materials")
        row = await cur.fetchone()

    assert ".." not in row["file_path"]
    assert row["file_path"].startswith(f"uploads/{pid}/")
    stored = upload_root / str(pid) / row["file_path"].split("/")[-1]
    assert stored.is_file()
    assert not (upload_root.parent / "evil.pdf").exists()


@pytest.mark.asyncio
async def test_same_name_upload_does_not_clobber_previous_bytes(upload_env):
    """同名重传：两次落盘路径必须不同，第一份字节不能被覆盖。"""
    client, upload_root, pid = upload_env

    assert (await _upload(client, pid, "评分表.pdf", data=b"FIRST")).status_code == 200
    assert (await _upload(client, pid, "评分表.pdf", data=b"SECOND")).status_code == 200

    async with get_db() as db:
        cur = await db.execute("SELECT filename, file_path FROM materials ORDER BY id")
        rows = await cur.fetchall()

    assert len(rows) == 2
    assert rows[0]["filename"] == rows[1]["filename"] == "评分表.pdf"
    paths = [r["file_path"] for r in rows]
    assert paths[0] != paths[1], "两次上传落到了同一个路径 → 前一份会被覆盖"
    contents = sorted(
        (upload_root / str(pid) / p.split("/")[-1]).read_bytes() for p in paths
    )
    assert contents == [b"FIRST", b"SECOND"]


@pytest.mark.asyncio
async def test_download_resolves_by_file_path_not_filename(upload_env):
    """落盘名带 uuid 前缀后，下载必须按 file_path 解析（按 filename 拼会 404）。"""
    client, _, pid = upload_env
    assert (await _upload(client, pid, "评分表.pdf", data=b"%PDF-1.4 hello")).status_code == 200

    async with get_db() as db:
        cur = await db.execute("SELECT id, filename, file_path FROM materials")
        row = await cur.fetchone()
    assert row["filename"] == "评分表.pdf"
    assert row["file_path"].split("/")[-1] != "评分表.pdf"     # 落盘名确实不同

    r = await client.get(f"/api/materials/{row['id']}/file")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 hello"


@pytest.mark.asyncio
async def test_delete_material_removes_file_from_disk(upload_env):
    """删材料要连盘上文件一起删（此前只删库，盘上成垃圾）。"""
    client, upload_root, pid = upload_env
    await _upload(client, pid, "评分表.pdf")

    async with get_db() as db:
        cur = await db.execute("SELECT id, file_path FROM materials")
        row = await cur.fetchone()
    stored = upload_root / str(pid) / row["file_path"].split("/")[-1]
    assert stored.is_file()

    r = await client.delete(f"/api/materials/{row['id']}")
    assert r.status_code == 200
    assert not stored.exists()


@pytest.mark.asyncio
async def test_spec_role_is_requirement_class_not_ours(upload_env):
    """`spec`（技术规范书）必须归**要求类**，不能被当成我方素材。

    实测踩到：项目 18 的技术规范书 `4_…技术招标文件.docx` 被传成 `source`，
    于是它只躺在素材池里 —— 既没结构化，也没参与目录规划（`source` 不参与要求提炼）。
    角色决定这份文件进哪条链路，所以这里把口径钉死：
    - `spec` ∈ 要求类（`_load_spec_for_project` 取的就是这几个角色）；
    - `source` ∉ 要求类；
    - `REQUIREMENT_ROLES`（main.py 的取文件口径）与 `TENDER_MATERIAL_ROLES` 一致。
    """
    from main import REQUIREMENT_ROLES
    from services.block_store import TENDER_MATERIAL_ROLES, OURS_MATERIAL_ROLES
    from routes.materials import ALLOWED_ROLES

    assert "spec" in REQUIREMENT_ROLES
    assert "source" not in REQUIREMENT_ROLES
    assert set(REQUIREMENT_ROLES) == set(TENDER_MATERIAL_ROLES)
    assert "spec" in TENDER_MATERIAL_ROLES
    assert OURS_MATERIAL_ROLES == ("source",)
    # 两者互斥且合起来覆盖白名单，不留"两边都不算"的角色
    assert set(TENDER_MATERIAL_ROLES).isdisjoint(OURS_MATERIAL_ROLES)
    assert set(TENDER_MATERIAL_ROLES) | set(OURS_MATERIAL_ROLES) == ALLOWED_ROLES


@pytest.mark.asyncio
async def test_uploaded_spec_book_enters_requirement_loader(tmp_path, monkeypatch):
    """技术规范书以 `spec` 上传后，要求文件加载器必须能取到它。

    这条是"解析阶段引入"的端到端判据：**上传 → 落库 → `_load_spec_for_project` 取到**。

    注：upload_env 夹具的上传根与 `main.DATA_DIR` 不是同一处，所以这里按
    `test_main_requirement_loader.py` 的方式自建 tmp 上传根 —— 否则加载器会因
    "磁盘上没有"而跳过（那本身是它的正确行为）。
    """
    import aiosqlite
    import db as db_module
    import main as main_module
    from db import init_db

    db_path = str(tmp_path / "roles.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    data_dir = tmp_path / "data"
    (data_dir / "uploads" / "1").mkdir(parents=True)
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        await conn.execute("INSERT INTO projects (name) VALUES ('role 测试')")
        # 技术规范书以 spec（要求类）入库；再放一份 source 作对照
        for role, name in (("spec", "4_高可靠技术专题研究与验证-技术招标文件.docx"),
                           ("source", "产品规格书.pdf")):
            rel = f"uploads/1/{name}"
            (data_dir / rel).write_bytes(b"%PDF-1.4 fake")
            await conn.execute(
                "INSERT INTO materials (project_id, filename, file_path, role) VALUES (1, ?, ?, ?)",
                (name, rel, role),
            )
        await conn.commit()

    docs = await main_module._load_spec_for_project(1)
    names = [name for _, _, name in docs]     # (BytesIO, suffix, filename)

    assert any("技术招标文件" in n for n in names), f"要求加载器没取到技术规范书：{names}"
    # 我方素材不得混进要求链路
    assert all("产品规格书" not in n for n in names), f"素材混进了要求链路：{names}"
