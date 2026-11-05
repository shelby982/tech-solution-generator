"""证据卡：文件/网页片段归一为统一 DTO。"""
from db import get_db
from services.material_cards import bound_cards, card, confidence_label, public_dto


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('证据卡')")).lastrowid
        mid = (await db.execute("INSERT INTO materials(project_id,filename,type,role,parse_status) "
                                "VALUES(?,?,?,?,?)", (pid, "技术规范.docx", "docx", "spec", "done"))).lastrowid
        chunk = (await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page,status) "
                                  "VALUES(?,?,?,?,?)", (mid, 0, "Kubernetes 服务网关与注册中心。", 12, "confirmed"))).lastrowid
        cid = "cite-1"
        await db.execute("INSERT INTO citations(id,project_id,title,url,domain,segments) VALUES(?,?,?,?,?,?)",
                         (cid, pid, "发改委通知", "https://www.ndrc.gov.cn/a", "ndrc.gov.cn", "[]"))
        web = (await db.execute("INSERT INTO citation_chunks(citation_id,chunk_index,heading,content,status) "
                                "VALUES(?,?,?,?,?)", (cid, 0, "发改委通知", "政策原文片段。", "confirmed"))).lastrowid
        block = (await db.execute("INSERT INTO blocks(project_id,block_id,title,content,run_thread_id) "
                                  "VALUES(?,?,?,?,?)", (pid, "s1", "技术方案", "## 节一\n\n正文", "card-thread"))).lastrowid
        await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,mode,chunk_kind,requirement_id) "
                         "VALUES(?,?,?,?,?,?)", (block, chunk, "节一", "data", "file", "sr-1"))
        await db.execute("INSERT INTO chapter_material_chunks(block_id,chunk_id,section,mode,chunk_kind,requirement_id) "
                         "VALUES(?,?,?,?,?,?)", (block, -web, "节一", "cite", "cite", "sr-2"))
        await db.commit()
    return pid, block, chunk, web


async def test_file_and_web_card_fields():
    pid, block, chunk, web = await _seed()
    async with get_db() as db:
        file_item = await card(db, chunk, "file", block_id=block, section="节一", requirement_id="sr-1", mode="data")
        web_item = await card(db, -web, "cite", block_id=block, section="节一", requirement_id="sr-2")
    assert file_item["source_type"] == "upload"
    assert file_item["authority"] == "技术规范书" and file_item["page"] == 12
    assert file_item["locator"].endswith("第 12 页")
    assert file_item["confidence"] >= 0.9
    assert web_item["source_type"] == "web"
    assert web_item["signed_id"] == -web
    assert web_item["authority"] in ("政府/官方来源", "标准/主管机构", "公开来源")
    assert public_dto(file_item)["confidence_label"] == "高"


async def test_bound_cards_only_section():
    pid, block, chunk, web = await _seed()
    async with get_db() as db:
        items = await bound_cards(db, block, "节一")
        other = await bound_cards(db, block, "另一节")
    assert len(items) == 2
    assert {item["chunk_kind"] for item in items} == {"file", "cite"}
    assert other == []
    assert confidence_label(0.5) == "待确认"
