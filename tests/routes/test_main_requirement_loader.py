"""spec_loader（main._load_spec_for_project）— 「应标要求」面板的全量取文件。

提炼不再绑定技术规范书：用户只传主招标文件 / 评分表 / 评审要素时，
这些文件同样要进大纲派生的输入。
"""

import aiosqlite
import pytest
import pytest_asyncio

import db as db_module
import main as main_module
from db import init_db


@pytest_asyncio.fixture
async def seeded(tmp_path, monkeypatch):
    """tmp DB + tmp 上传根目录，返回 (insert_material, data_dir)。"""
    db_path = str(tmp_path / "loader.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    data_dir = tmp_path / "data"
    (data_dir / "uploads" / "1").mkdir(parents=True)
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        await conn.execute("INSERT INTO projects (name) VALUES ('loader 测试')")
        await conn.commit()

    async def insert_material(role: str, filename: str, *, on_disk: bool = True):
        rel = f"uploads/1/{filename}"
        if on_disk:
            (data_dir / rel).write_bytes(b"%PDF-1.4 fake")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "INSERT INTO materials (project_id, filename, file_path, role) "
                "VALUES (1, ?, ?, ?)",
                (filename, rel, role),
            )
            await conn.commit()

    return insert_material


@pytest.mark.asyncio
async def test_loads_every_requirement_role_in_role_priority_order(seeded):
    """四种要求角色都算数，按**角色优先级**返回（主招标/规范书在前）—— 不再只认 role='spec'。

    顺序即拼接顺序、也是 digest 吃预算的顺序：主招标文件定义章节骨架，先吃预算；
    同档内仍按上传顺序（id）稳定。展示顺序与此一致（list_materials 共用同一片段）。
    """
    await seeded("main_rfp", "招标文件.pdf")
    await seeded("scoring", "评分表.pdf")
    await seeded("evaluation", "评审要素.pdf")
    await seeded("spec", "规范书.pdf")

    sources = await main_module._load_spec_for_project(1)

    # 同档内按上传顺序（id）稳定：main_rfp(id1) 与 spec(id4) 同属第一档 → id 小的在前
    assert [name for _, _, name in sources] == [
        "招标文件.pdf", "规范书.pdf", "评分表.pdf", "评审要素.pdf",
    ]
    assert all(suffix == ".pdf" for _, suffix, _ in sources)
    assert sources[0][0].read().startswith(b"%PDF")


@pytest.mark.asyncio
async def test_ignores_source_materials(seeded):
    """「原始素材」是沈括的输入，不进大纲派生。"""
    await seeded("source", "素材.pdf")

    with pytest.raises(FileNotFoundError, match="未上传要求文件"):
        await main_module._load_spec_for_project(1)


@pytest.mark.asyncio
async def test_missing_file_on_disk_is_skipped_not_fatal(seeded):
    """一条陈旧 DB 记录（文件已不在）不该让整条链路挂掉。"""
    await seeded("spec", "丢了.pdf", on_disk=False)
    await seeded("main_rfp", "还在.pdf")

    sources = await main_module._load_spec_for_project(1)

    assert [name for _, _, name in sources] == ["还在.pdf"]
