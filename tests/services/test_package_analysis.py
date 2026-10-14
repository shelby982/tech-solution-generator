"""标包识别：**只抽取原文事实**，不衍生。

用户口径：以招标文件的事实为准，模型不能做额外的衍生或提炼；
一个文件可能属于多个标包（没有严格对应关系）。
"""
from services.package_analysis import (
    analyze_packages, cn_to_int, extract_package_mentions,
)


def test_cn_to_int_basic():
    assert cn_to_int("1") == "1"
    assert cn_to_int("二") == "2"
    assert cn_to_int("两") == "2"
    assert cn_to_int("十") == "10"
    assert cn_to_int("十二") == "12"
    assert cn_to_int("二十") == "20"
    assert cn_to_int("三十五") == "35"
    # 不认识的写法不猜，原样返回
    assert cn_to_int("甲") == "甲"
    assert cn_to_int("") == ""


def test_extract_mentions_records_quote_and_kind():
    text = "本项目分为标的1标包2、标的1标包3；详见第二章。"
    got = extract_package_mentions(text)
    pairs = {(m["target"], m["package_no"], m["kind"]) for m in got}
    assert ("1", "2", "pair") in pairs
    assert ("1", "3", "pair") in pairs
    # 配对句里的"标的1/标包2"是同一条事实的残缺视角，不再各出一条（否则界面像有 3 个标包）
    assert len(got) == 2, got
    assert all("标的" in m["quote"] for m in got if m["kind"] == "pair")


def test_extract_mentions_keeps_standalone_outside_pair():
    """配对跨度**之外**的单独表述是独立事实，照旧保留（不推断它们属于某个标包）。"""
    got = extract_package_mentions("标的1标包2。另：本项目共两个标段，标段一与标段二。")
    kinds = {(m["target"], m["package_no"], m["kind"]) for m in got}
    assert ("1", "2", "pair") in kinds
    # 中文数字会被规范化成阿拉伯数字
    assert ("", "1", "section") in kinds
    assert ("", "2", "section") in kinds


def test_extract_mentions_handles_cn_numbers_and_ordinals():
    got = extract_package_mentions("本次招标设第3标包，另见标段二。")
    kinds = {m["kind"] for m in got}
    assert "ordinal_package" in kinds
    assert "section" in kinds
    assert any(m["package_no"] == "3" for m in got)
    assert any(m["package_no"] == "2" for m in got)


def test_extract_mentions_empty_when_no_fact():
    """找不到就是找不到 —— 不猜测、不补全。"""
    assert extract_package_mentions("本文件为通用条款，与江苏有关。") == []
    assert extract_package_mentions("") == []


def test_analyze_packages_groups_and_keeps_evidence():
    chunks = [
        {"material_id": "m1", "chunk_index": 0, "page": 3,
         "content": "本项目分标的1标包2、标的1标包3两个标包，分别报价。"},
        {"material_id": "m2", "chunk_index": 1, "page": 9,
         "content": "标的1标包2的技术要求见第五章。"},
    ]
    materials = [{"id": "m1", "filename": "招标文件.pdf"}, {"id": "m2", "filename": "技术规范书.docx"}]
    got = analyze_packages(chunks, materials)
    by_key = {(p["target"], p["package_no"]): p for p in got}
    assert set(by_key) == {("1", "2"), ("1", "3")}
    p2 = by_key[("1", "2")]
    # 两个文件都提到了它 → 来源文件是两条
    assert p2["source_material_ids"] == ["m1", "m2"]
    assert p2["mentions"] >= 2
    # 证据里有文件名与页码，能回到原文核对
    assert any(e["filename"] == "招标文件.pdf" and e["page"] == 3 for e in p2["evidence"])
    # 名字**不编造**（留给用户确认）
    assert p2["package_name"] == ""


def test_analyze_packages_confidence_is_evidence_based():
    one = analyze_packages([{"material_id": "m1", "content": "见标包2。"}], [])
    assert one[0]["confidence"] == "low"
    many = analyze_packages([
        {"material_id": "m1", "content": "标的1标包2。"},
        {"material_id": "m1", "content": "标的1标包2的报价。"},
    ], [])
    # 成对表述（标的X标包Y）出现 ≥2 次 → 高置信
    assert [p for p in many if p["target"] == "1" and p["package_no"] == "2"][0]["confidence"] == "high"


def test_analyze_packages_returns_empty_without_facts():
    assert analyze_packages([{"material_id": "m1", "content": "通用条款。"}], []) == []


# ── 落库与执行（真库：SQLite in-memory）──────────────────────────────────────
async def _make_db_with_content(tmp_path):
    import aiosqlite
    from db import init_db

    conn = await aiosqlite.connect(str(tmp_path / "pa.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    await conn.execute(
        """INSERT INTO materials (id, project_id, filename, role, parse_status)
           VALUES (10, 1, '招标文件.pdf', 'main_rfp', 'done'),
                  (11, 1, '技术规范书.docx', 'spec', 'done')""")
    await conn.executemany(
        "INSERT INTO material_chunks (material_id, chunk_index, page, content) VALUES (?,?,?,?)",
        [(10, 0, 3, "本项目分标的1标包2、标的1标包3，分别报价。"),
         (11, 0, 9, "标的1标包2的技术要求见第五章。")])
    await conn.commit()
    return conn


async def test_run_analysis_persists_packages_and_many_to_many_links(tmp_path):
    """分析结果落库；**同一文件可关联多个标包**（用户口径）。"""
    from services.package_analysis import (
        load_analysis, load_material_packages, run_analysis,
    )

    conn = await _make_db_with_content(tmp_path)
    try:
        pkgs = await run_analysis(conn, 1)
        assert {(p["target"], p["package_no"]) for p in pkgs} == {("1", "2"), ("1", "3")}

        loaded = await load_analysis(conn, 1)
        assert len(loaded) == 2
        # 证据（出处）落库后仍可读回
        p2 = [p for p in loaded if p["package_no"] == "2"][0]
        assert p2["evidence"] and p2["evidence"][0]["filename"] == "招标文件.pdf"

        links = await load_material_packages(conn, 1)
        # 招标文件（10）同时关联标包2与标包3 —— 多对多成立
        got10 = {(x["target"], x["package_no"]) for x in links["10"]}
        assert got10 == {("1", "2"), ("1", "3")}
    finally:
        await conn.close()


async def test_run_analysis_empty_when_no_chunks(tmp_path):
    """没有正文 → 空清单（不猜）。"""
    import aiosqlite
    from db import init_db
    from services.package_analysis import run_analysis

    conn = await aiosqlite.connect(str(tmp_path / "empty.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    await conn.execute("INSERT INTO projects (id, name) VALUES (1, 'P')")
    await conn.commit()
    try:
        assert await run_analysis(conn, 1) == []
    finally:
        await conn.close()
