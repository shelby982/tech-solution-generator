"""素材混合检索：关键词 + TF-IDF + 规范覆盖 + 同文件多样性。"""
from db import get_db
from services.material_rerank import clear_cache, search_materials


async def _seed():
    async with get_db() as db:
        pid = (await db.execute("INSERT INTO projects(name) VALUES('素材重排')")).lastrowid
        mid = (await db.execute("INSERT INTO materials(project_id,filename,type,role,parse_status) "
                                "VALUES(?,?,?,?,?)", (pid, "技术规范.docx", "docx", "source", "done"))).lastrowid
        other = (await db.execute("INSERT INTO materials(project_id,filename,type,role,parse_status) "
                                  "VALUES(?,?,?,?,?)", (pid, "业绩证明.docx", "docx", "source", "done"))).lastrowid
        for idx, text in enumerate([
            "云原生平台采用 Kubernetes 服务网关、注册中心和配置中心，并实现熔断限流。",
            "营销活动系统按领域驱动设计划分微服务边界，核心稳定、外围敏态。",
            "近三年完成同类信息化项目业绩证明与合同复印件。",
            "投标人应提供售后服务承诺和人员培训计划。",
        ]):
            await db.execute("INSERT INTO material_chunks(material_id,chunk_index,content,page) VALUES(?,?,?,?)",
                             (mid if idx < 2 else other, idx, text, 10 + idx))
        await db.commit()
    return pid


async def test_relevant_chunk_beats_distractor():
    clear_cache()
    pid = await _seed()
    async with get_db() as db:
        results = await search_materials(db, pid, {
            "title": "Kubernetes 服务治理",
            "points": ["服务网关、注册中心、配置中心", "熔断限流"],
            "basis": [],
        }, [{"text": "基于 Kubernetes 的服务治理要求"}], [], limit=3)
    assert results
    assert results[0]["filename"] == "技术规范.docx"
    assert "Kubernetes" in results[0]["content"]
    assert results[0]["reason"]["vector"] >= 0


async def test_same_file_capped_and_empty_query():
    clear_cache()
    pid = await _seed()
    async with get_db() as db:
        results = await search_materials(db, pid, {
            "title": "云原生", "points": ["Kubernetes"], "basis": [],
        }, [], [], limit=8)
        assert all(results.count(item) <= 1 for item in results)
        per_file = {}
        for item in results:
            per_file[item["filename"]] = per_file.get(item["filename"], 0) + 1
        assert max(per_file.values()) <= 2
        empty = await search_materials(db, pid, {"title": "", "points": [], "basis": []}, [], [], limit=3)
        assert empty == []
