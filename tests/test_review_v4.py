"""评审 v4：达标线（rubric）/ 证据链 / 写法回流 的验收测试。"""
from __future__ import annotations

import asyncio


# ────────────────────────── A：达标线 ──────────────────────────

def test_detect_chapter_type():
    from services.chapter_rubric import detect_type

    assert detect_type("1.2 项目背景与现状") == "status"
    assert detect_type("3.1 总体技术方案设计") == "plan"
    assert detect_type("5.2 实施进度计划") == "impl"
    assert detect_type("6.1 项目团队与职责") == "team"
    assert detect_type("2.3 政策依据") == "policy"
    assert detect_type("7.4 验收与质量保障") == "accept"


def test_rubric_status_requires_numbers_and_pain():
    from services.chapter_rubric import check_rubric, rubric_for

    rubric = rubric_for("1.2 项目背景与现状")
    thin = check_rubric("## 一 现状\n目前系统运行正常，用户满意，没有问题。", rubric)
    keys = {item["key"]: item["met"] for item in thin["items"]}
    assert keys["numbers"] is False            # 无数字 → 量化事实不达标
    assert thin["met_count"] < thin["total"]

    rich = check_rubric(
        "## 一 现状\n现有服务器 12 台，用户 3000 人，数据量 2TB，日均请求 12 万次，并发 500。"
        "主要问题：两套账号体系未打通，运维手册滞后 6 个月，因此本项目将统一认证。"
        "【来源：招标文件（第 12 页）】", rubric)
    keys2 = {item["key"]: item["met"] for item in rich["items"]}
    assert keys2["numbers"] is True and keys2["source"] is True
    assert rich["ratio"] >= 80


def test_rubric_plan_requires_means_and_indicator():
    from services.chapter_rubric import check_rubric, rubric_for

    rubric = rubric_for("3.1 总体技术方案")
    result = check_rubric("## 一 总体方案\n采用先进技术，建设一流平台。", rubric)
    keys = {item["key"]: item["met"] for item in result["items"]}
    assert keys["means"] is False or keys["indicator"] is False


# ────────────────────────── B：证据链 ──────────────────────────

def test_attach_evidence_locatable_and_missing():
    from services.review_evidence import attach_evidence

    content = ("## 一 现状\n本项目政策依据以《网络安全法》为主线。"
               "缺失的招标文件条款原文与发文信息按处理，待取得后据实补全。")
    issues = [{"severity": "critical", "point": "正文保留未完成占位（如“缺失的招标文件条款原文与发文信息按处理，待取得后据实补全”）"}]
    out = attach_evidence(content, issues, "政策依据须标注来源与文号")
    evidence = out[0]["evidence"]
    assert evidence["locatable"] is True
    assert "缺失的招标文件" in evidence["quote"]

    miss = attach_evidence(content, [{"severity": "high", "point": "完全不存在的一句话，正文里没有这内容"}])
    assert miss[0]["evidence"]["locatable"] is False
    assert miss[0]["evidence"]["quote"] == ""


def test_attach_evidence_criterion_match():
    from services.review_evidence import attach_evidence

    content = "## 一\n现状描述缺少量化数据。"
    issues = [{"severity": "high", "point": "缺少量化数据"}]
    out = attach_evidence(content, issues, "本章应含量化数据；政策依据须标注来源")
    assert "量化数据" in out[0]["evidence"]["criterion"]


# ────────────────────────── C：写法回流 ──────────────────────────

def test_practice_candidates_from_review():
    from services.practice_recycle import candidates_from_review

    review = {"score": 82, "tier": "full",
              "strengths": ["用表格逐条对应招标条款，覆盖清晰", "指标口径写到小数点位数"]}
    items = candidates_from_review(review)
    assert len(items) == 2
    assert items[0]["source"] == "review_strength"
    assert candidates_from_review(None) == []


def test_practice_apply_writes_section():
    from services.practice_recycle import apply_candidate
    from services.section_practice import load_practices

    async def run():
        import aiosqlite

        db = await aiosqlite.connect(":memory:")
        await db.execute(
            "CREATE TABLE section_practices (project_id INTEGER, block_id TEXT, section TEXT, "
            "practice TEXT, updated_at DATETIME, PRIMARY KEY(project_id, block_id, section))")
        await db.commit()
        saved = await apply_candidate(db, project_id=1, block_id="s1.1", section="现状", text="先给数字，再给结论")
        assert saved["section"] == "现状"
        practices = await load_practices(1, "s1.1")
        assert practices.get("现状") == "先给数字，再给结论"
        await db.close()

    asyncio.run(run())


def test_should_skip_llm_for_empty_chapter():
    """空章/极短章不调 LLM（实测：0 字章被评 2 分是噪音）。"""
    from services.review_precheck import precheck, should_skip_llm

    empty = precheck("")
    assert should_skip_llm(empty) is True
    assert should_skip_llm(precheck("## 一\n太短。")) is True
    long_text = precheck("## 一\n" + "现有服务器 12 台，用户 3000 人，数据量 2TB，2026 年完成改造。" * 12)
    assert should_skip_llm(long_text) is False


def test_chapter_requirement_field_roundtrip_shape():
    """章级统一要求：字段名/长度裁剪口径（避免写出超长提示词）。"""
    payload = {"chapter_requirement": "全章口径统一；每节先结论后依据；禁止待补充"}
    assert payload["chapter_requirement"].count("；") == 2
    clipped = payload["chapter_requirement"][:2000]
    assert len(clipped) <= 2000


def test_preserve_outlines_index_shape():
    """重建计划时按 block_id 回收已有大纲（否则用户"生成一次后又没了"）。"""
    from services.writing_plans import _outline_index

    chapters = [
        {"block_id": "s1.2.1.1", "title": "项目背景", "outline": [{"title": "政策"}, {"title": "目标"}]},
        {"block_id": "s1.2.1.2", "title": "建设范围", "outline": []},
        {"block_id": "s1.2.1.3", "title": "建设内容"},
    ]
    index = _outline_index(chapters)
    assert list(index) == ["s1.2.1.1"]
    assert len(index["s1.2.1.1"]) == 2
