"""节级素材上下文：没有绑定就返回空，不能偷偷用整章素材。"""
from db import get_db
from services.material_context import chapter_material_context, material_prompt_for_block


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('节级素材')")).lastrowid
        mid = (await db.execute("INSERT INTO materials(project_id,filename,type,role,parse_status) "
                                "VALUES(?,?,?,?,?)", (pid, "技术规范.docx", "docx", "spec", "done"))).lastrowid
        chunk = (await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page,status) "
                                  "VALUES(?,?,?,?,?)", (mid, 0, "Kubernetes 服务治理原文。", 8, "selected"))).lastrowid
        block = (await db.execute("INSERT INTO blocks(project_id,block_id,title,content,run_thread_id) "
                                  "VALUES(?,?,?,?,?)", (pid, "s1", "技术方案", "## 节一\n\n正文\n\n## 节二\n\n正文", "ctx-thread"))).lastrowid
        await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,mode,chunk_kind) "
                         "VALUES(?,?,?,?,?)", (block, chunk, "节一", "data", "file"))
        await db.commit()
    return pid, block


async def test_section_without_binding_is_empty():
    pid, block = await _seed()
    async with get_db() as db:
        sec1 = await chapter_material_context(db, block, "s1", "节一")
        sec2 = await chapter_material_context(db, block, "s1", "节二")
        whole = await chapter_material_context(db, block, "s1", "")
    assert sec1["count"] == 1 and sec1["data"]
    assert sec2["count"] == 0 and sec2["data"] == [] and sec2["section_missing"] is True
    assert whole["count"] == 1


async def test_prompt_has_no_fallback():
    pid, block = await _seed()
    prompt = await material_prompt_for_block(block, "s1", "节二")
    assert prompt == ""
