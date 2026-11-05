"""写后结果持久化：刷新后仍可核对 context/cited/coverage。"""
from db import get_db
from services.section_results import cited_sources, list_results, save_result


async def test_cited_sources_and_persist():
    async with get_db() as db:
        block = (await db.execute("INSERT INTO blocks(project_id,block_id,title,content,run_thread_id) "
                                  "VALUES(1,'s9','章','', 'result-thread')")).lastrowid
        await db.commit()
    text = "正文【来源：技术规范 P12】和【来源：案例库 P8】；重复【来源：技术规范 P12】"
    assert cited_sources(text) == ["技术规范 P12", "案例库 P8"]
    await save_result(block, "节一", {"context_evidence": [{"source_title": "A"}],
                                      "cited_evidence": cited_sources(text),
                                      "coverage_after": {"total": 3, "covered": 2}})
    items = await list_results(block)
    assert items["节一"]["coverage_after"]["covered"] == 2
    assert items["节一"]["cited_evidence"][0] == "技术规范 P12"


async def test_result_upsert_latest():
    async with get_db() as db:
        block = (await (db.execute("INSERT INTO blocks(project_id,block_id,title,content,run_thread_id) "
                                   "VALUES(1,'s10','章','', 'result-thread')"))).lastrowid
        await db.commit()
    await save_result(block, "节一", {"coverage_after": {"total": 1, "covered": 0}})
    await save_result(block, "节一", {"coverage_after": {"total": 1, "covered": 1}})
    items = await list_results(block)
    assert items["节一"]["coverage_after"]["covered"] == 1
