"""张衡 agent 测试 — parse / extract 两个阶段。

依赖 infra.llm 的 mock 路由（环境变量 LLM_MODE=mock）。
"""

import io

import pytest

from agents.zhang_heng import OutlineDraftError, SpecParseResult, ZhangHengAgent
from domain.spec import OutlineMatrixRow
from domain.spec import Section as DomainSection


# ─────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────

def _fake_configs():
    """provider 返回最小可用配置，触发 mock 路由。"""
    from services.config_store import LLMConfig
    return ([
        LLMConfig(
            provider="openai",
            api_key="sk-fake",
            base_url="https://example.invalid/v1",
            model="gpt-test",
        ),
    ], 0)


def _build_minimal_docx() -> io.BytesIO:
    """构造最小 docx：两个 Heading 1 + 各一段正文，第二章带 ★ 标记。"""
    from docx import Document

    doc = Document()
    doc.add_heading("第一章 项目概述", level=1)
    doc.add_paragraph("这是项目背景介绍段落。")
    doc.add_heading("第二章 ★技术方案要求", level=1)
    doc.add_paragraph("系统须采用微服务架构。")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────
# parse
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_returns_domain_sections(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=_fake_configs)

    result = await agent.parse(
        [(_build_minimal_docx(), ".docx", "test.docx")],
    )

    assert isinstance(result, SpecParseResult)
    assert len(result.toc) == 2
    assert all(isinstance(s, DomainSection) for s in result.toc)
    assert "项目概述" in result.toc[0].title
    # ★ 标记应在 special_marks 中
    assert "★" in result.toc[1].special_marks


@pytest.mark.asyncio
async def test_parse_merges_multiple_requirement_files(monkeypatch):
    """多份要求文件的目录按上传顺序拼接，id 连续重编号（各文档原生 id 都是 s1..sN，会撞车）。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=lambda: ([], 0))

    result = await agent.parse([
        (_build_minimal_docx(), ".docx", "招标文件.docx"),
        (_build_minimal_docx(), ".docx", "评分表.docx"),
    ])

    assert len(result.toc) == 4
    assert [s.id for s in result.toc] == ["s1", "s2", "s3", "s4"]
    # 两份文档的标题拼在一起（fixture 两份同名，所以是两个「第一章 项目概述」）
    assert result.doc_title.count("、") == 1


@pytest.mark.asyncio
async def test_parse_skips_broken_file_but_keeps_the_rest(monkeypatch):
    """单份文件坏掉只跳过它 —— 之前一份坏文件会让整次解析失败，规范书+评分表全白解析。

    坏文件用真实路径构造：`.pptx` 在上传白名单之外（现在会被 400 挡），但历史数据或
    磁盘损坏的 PDF/DOC 仍会走到这里。
    """
    agent = ZhangHengAgent(configs_provider=lambda: ([], 0))

    result = await agent.parse([
        (io.BytesIO(b"not a real pptx"), ".pptx", "坏文件.pptx"),
        (_build_minimal_docx(), ".docx", "招标文件.docx"),
    ])

    assert len(result.toc) == 2, "好文件的章节必须保留"
    assert result.failed_files == ["坏文件.pptx"]
    # 每节都带上来源文件（按区段切段靠它还原文件边界）
    assert {s.source_file for s in result.toc} == {"招标文件.docx"}


@pytest.mark.asyncio
async def test_parse_records_source_file_per_section(monkeypatch):
    """多份文件拼接后，每节要记住自己来自哪份文件（否则段会跨文件吞内容）。"""
    agent = ZhangHengAgent(configs_provider=lambda: ([], 0))

    result = await agent.parse([
        (_build_minimal_docx(), ".docx", "招标文件.docx"),
        (_build_minimal_docx(), ".docx", "评分表.docx"),
    ])

    assert [s.source_file for s in result.toc] == [
        "招标文件.docx", "招标文件.docx", "评分表.docx", "评分表.docx",
    ]


@pytest.mark.asyncio
async def test_parse_raises_when_every_file_fails(monkeypatch):
    """全部文件都解析失败时明确抛错（带文件名），而不是产出一份空目录。"""
    from agents.zhang_heng import RequirementParseError

    agent = ZhangHengAgent(configs_provider=lambda: ([], 0))

    with pytest.raises(RequirementParseError) as ei:
        await agent.parse([(io.BytesIO(b"x"), ".pptx", "坏.pptx")])

    assert "全部解析失败" in str(ei.value)
    assert "坏.pptx" in str(ei.value)


@pytest.mark.asyncio
async def test_parse_without_sources_raises(monkeypatch):
    """没有任何要求文件时明确抛错，而不是静默产出一份空目录。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=_fake_configs)

    with pytest.raises(ValueError):
        await agent.parse([])


# ─────────────────────────────────────────────
# extract
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_fills_eight_fields_for_each_section(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=_fake_configs)
    toc = [
        DomainSection(id="s1", level=1, title="技术架构", raw_content="系统须采用微服务"),
        DomainSection(id="s2", level=1, title="安全要求", raw_content="须支持 TLS 1.3", special_marks=["★"]),
    ]

    matrix = await agent.extract(toc)

    assert set(matrix.keys()) == {"s1", "s2"}
    for row in matrix.values():
        assert isinstance(row, OutlineMatrixRow)
        # mock fixture 8 字段全部非空
        assert row.requirement
        assert row.key_points
        assert row.constraint_level in ("mandatory", "recommended", "optional")
        assert row.error == ""


@pytest.mark.asyncio
async def test_extract_returns_empty_rows_when_no_configs(monkeypatch):
    """无 LLM 配置：每行 error 字段写"未配置 LLM"，其它字段保持默认空。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=lambda: ([], 0))
    toc = [DomainSection(id="s1", level=1, title="X", raw_content="Y")]

    matrix = await agent.extract(toc)

    assert matrix["s1"].error == "未配置 LLM"
    assert matrix["s1"].requirement == ""
    assert matrix["s1"].constraint_level == "recommended"


@pytest.mark.asyncio
async def test_extract_propagates_special_marks_into_dispatch(monkeypatch):
    """带 ★ 的章节：special_marks 被传给 dispatch_outline_json，
    本测试不验证 prompt，仅验证 extract 不丢字段。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=_fake_configs)
    toc = [
        DomainSection(id="s1", level=1, title="否决项 X", raw_content="必须满足", special_marks=["★"]),
    ]

    matrix = await agent.extract(toc)

    assert matrix["s1"].block_id == "s1"
    assert matrix["s1"].title == "否决项 X"


@pytest.mark.asyncio
async def test_extract_handles_dispatch_error_per_section(monkeypatch):
    """模拟 dispatch_outline_json 对某章节回 error 字段，
    extract 应把 error 字段透传到 OutlineMatrixRow.error，不抛。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    async def fake_dispatch(configs, rr_start, sections):
        for s in sections:
            if "失败" in s["title"]:
                yield {
                    "title": s["title"], "error": "全部 API 失败：mock",
                    "requirement": "", "key_points": "", "veto_items": "",
                    "bonus_items": "", "score_items": "", "evidence_required": "",
                    "constraint_level": "recommended", "indicators": "",
                }
            else:
                yield {
                    "title": s["title"], "error": "",
                    "requirement": "ok", "key_points": "ok", "veto_items": "",
                    "bonus_items": "", "score_items": "", "evidence_required": "",
                    "constraint_level": "recommended", "indicators": "",
                }

    # zhang_heng.py 在函数内部 `from infra.llm import dispatch_outline_json`，
    # 因此 patch 源模块 infra.llm 即可。
    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_json", fake_dispatch)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    toc = [
        DomainSection(id="s1", level=1, title="正常章节", raw_content=""),
        DomainSection(id="s2", level=1, title="失败章节", raw_content=""),
    ]

    matrix = await agent.extract(toc)

    assert matrix["s1"].error == ""
    assert matrix["s1"].requirement == "ok"
    assert "失败" in matrix["s2"].error or "mock" in matrix["s2"].error


# ─────────────────────────────────────────────
# draft_outline
# ─────────────────────────────────────────────

def _no_configs():
    return ([], 0)


@pytest.mark.asyncio
async def test_draft_outline_raises_without_llm_configs():
    """无 LLM 配置 → 抛 OutlineDraftError（不再静默沿用规范书原始目录）。"""
    agent = ZhangHengAgent(configs_provider=_no_configs)
    source = [
        DomainSection(id="s1", level=1, title="技术方案", raw_content="正文",
                      special_marks=["★"]),
    ]

    with pytest.raises(OutlineDraftError) as ei:
        await agent.draft_outline(source)

    assert "未配置可用模型" in str(ei.value)


@pytest.mark.asyncio
async def test_draft_outline_raises_on_empty_source_toc():
    agent = ZhangHengAgent(configs_provider=_fake_configs)
    with pytest.raises(OutlineDraftError) as ei:
        await agent.draft_outline([])
    assert "没有解析出任何章节" in str(ei.value)


@pytest.mark.asyncio
async def test_draft_outline_degrades_when_dispatch_raises(monkeypatch):
    async def _boom(configs, rr_start, payload):
        raise RuntimeError("全部 API 失败")

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _boom)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    with pytest.raises(OutlineDraftError) as ei:
        await agent.draft_outline(source)

    assert "模型调用失败" in str(ei.value) and "全部 API 失败" in str(ei.value)


@pytest.mark.asyncio
async def test_draft_outline_degrades_when_nodes_unparsable(monkeypatch):
    async def _garbage(configs, rr_start, payload):
        return {"nodes": ["不是 dict", {"title": "  "}]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _garbage)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    with pytest.raises(OutlineDraftError) as ei:
        await agent.draft_outline(source)

    assert "无法解析" in str(ei.value)


@pytest.mark.asyncio
async def test_draft_outline_raises_when_too_few_top_level(monkeypatch):
    """一级章节 < MIN_TOP_LEVEL_NODES 视为目录不可信 → 抛（不再降级）。"""
    async def _thin(configs, rr_start, payload):
        return {"nodes": [{"level": 1, "title": "唯一一级"}]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _thin)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    with pytest.raises(OutlineDraftError) as ei:
        await agent.draft_outline(source)

    assert "结构不完整" in str(ei.value)


@pytest.mark.asyncio
async def test_draft_outline_locates_by_instruction_before_digest(monkeypatch):
    """提炼要求指向哪一部分，进 prompt 的就是那一部分 —— 不是整份文件。

    实测场景：164 页采购文件解析出 300+ 章，用户要求「按照标包2的技术评分要求
    拆分章节」，但整份文件灌进去时封面/招标公告/合同范本先把 8000 字预算占满。
    """
    captured: dict = {}

    async def _capture(configs, rr_start, payload):
        captured.update(payload)
        return {"nodes": [
            {"level": 1, "title": "项目理解"},
            {"level": 1, "title": "技术响应"},
            {"level": 1, "title": "服务保障"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _capture)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = (
        [DomainSection(id=f"s{i}", level=1, title=f"第{i}页",
                       raw_content="招标人：广东电网有限责任公司")
         for i in range(1, 200)]
        + [DomainSection(id="s200", level=1, title="标包2 技术评分要求",
                         raw_content="评分因素：技术方案完整性、团队经验、实施计划")]
    )

    await agent.draft_outline(source, instruction="按照标包2的技术评分要求拆分章节")

    digest = captured["spec_digest"]
    assert "标包2 技术评分要求" in digest
    assert "评分因素：技术方案完整性" in digest
    # 噪声章节不该跟着进 prompt
    assert "招标人：广东电网有限责任公司" not in digest
    # 这份语料里没有文档锚点，走的是章级定位 → 没定位到「对应部分」，结构不锁死
    assert captured["follow_structure"] is False


def _anchored_source() -> list[DomainSection]:
    """仿真实采购文件：公告/须知在前，标包1/2/3 的评审标准夹在中间，合同范本在后。"""
    return [
        DomainSection(id="s1", level=1, title="招标公告",
                      raw_content="招标人：某某电网有限责任公司\n招标文件（标准文件范本）"),
        DomainSection(id="s2", level=1, title="投标人须知",
                      raw_content="投标人应当具备下列条件…"),
        DomainSection(id="s3", level=1, title="评审标准", raw_content="\n".join([
            "下列评审标准适用的标的/标包：标包1：关键业务场景研究与验证标包",
            "标包1 的商务要求：具备相关资质",
            "下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包",
            "技术评分标准：技术方案、项目管理、实施方案、交付成果、服务团队",
            "下列评审标准适用的标的/标包：标包3：主数据管理研究及全过程技术管控标包",
            "标包3 的商务要求：具备相关业绩",
        ])),
        DomainSection(id="s4", level=1, title="合同范本", raw_content="合同条款范本"),
    ]


@pytest.mark.asyncio
async def test_draft_outline_keeps_only_selected_segment_body_in_digest(monkeypatch):
    """定位到标包2 时：只有标包2 的正文进 prompt，其余段落只留标题。

    实测一份 164 页采购文件里标包2 的评分标准只占 7.3%，章级检索命中不了它
    （那 34 个章节的标题里一个「标包2」都没有），只能按文档锚点整段切。
    """
    captured: dict = {}

    async def _capture(configs, rr_start, payload):
        captured.update(payload)
        return {"nodes": [{"level": 1, "title": t} for t in ("项目理解", "技术响应", "服务保障")]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _capture)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    await agent.draft_outline(
        _anchored_source(),
        instruction="按照主招标文件中的标包2的技术评分要求的点进行大纲拆分章节",
    )

    # 定位到了用户点名的那一部分 → 目录要按这部分自身的结构拆
    assert captured["follow_structure"] is True

    digest = captured["spec_digest"]
    # 选中段的正文进来了
    assert "技术评分标准：技术方案、项目管理" in digest
    # 其它标包只留标题，正文不进 —— 这就是省 token 的地方
    assert "标包1：关键业务场景研究与验证标包" in digest
    assert "标包1 的商务要求：具备相关资质" not in digest
    assert "标包3 的商务要求：具备相关业绩" not in digest
    # 文档开头的公告/须知整段丢掉（它们连标题都不该占预算）
    assert "招标人：某某电网有限责任公司" not in digest


@pytest.mark.asyncio
async def test_draft_outline_grounds_from_unselected_content(monkeypatch):
    """「未选中」只影响 digest 里给模型看什么，不影响 grounding 从哪取料。

    这是整个设计能成立的关键：标包2 之外的材料（比如另一份没写标包号的
    技术招标文件）仍然能被 BM25 挂回派生的章节，extract 的 8 字段提炼不缺输入。
    """
    async def _tree(configs, rr_start, payload):
        return {"nodes": [
            {"level": 1, "title": "标包1 的商务要求"},
            {"level": 1, "title": "技术响应"},
            {"level": 1, "title": "服务保障"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _tree)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    result = await agent.draft_outline(
        _anchored_source(),
        instruction="按照主招标文件中的标包2的技术评分要求的点进行大纲拆分章节",
    )

    by_title = {s.title: s for s in result.sections}
    assert "具备相关资质" in by_title["标包1 的商务要求"].raw_content


@pytest.mark.asyncio
async def test_draft_outline_skips_scope_model_without_scope_token(monkeypatch):
    """要求里没有范围标识（「按评分项逐条拆章」）时不调模型 —— 无从下手。"""
    seen_payload: list = []

    async def _capture_kw(configs, rr_start, payload):
        seen_payload.append(payload)
        return {"files": [], "keywords": ["标包2"]}

    async def _tree(configs, rr_start, payload):
        return {"nodes": [{"level": 1, "title": t} for t in ("A", "B", "C")]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_scope_select_json", _capture_kw)
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _tree)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    await agent.draft_outline(_anchored_source(), instruction="按评分项逐条拆章")

    assert seen_payload == []


@pytest.mark.asyncio
async def test_draft_outline_scope_model_fallback_runs_once(monkeypatch):
    """点了范围却找不到锚点（文档换了个说法）时才兜底一次，且把标题给模型。"""
    seen_payload: list = []

    async def _capture_kw(configs, rr_start, payload):
        seen_payload.append(payload)
        return {"files": [], "keywords": ["标包2"]}

    async def _tree(configs, rr_start, payload):
        return {"nodes": [{"level": 1, "title": t} for t in ("A", "B", "C")]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_scope_select_json", _capture_kw)
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _tree)

    # 文档里只有「第四包」这类别的写法，锚点族认不出
    source = [
        DomainSection(id="s1", level=1, title=f"第{i}页",
                      raw_content="招标人：某某公司") for i in range(20)
    ]
    agent = ZhangHengAgent(configs_provider=_fake_configs)
    await agent.draft_outline(source, instruction="按照标包2的要求拆分章节")

    assert len(seen_payload) == 1
    assert "第0页" in seen_payload[0]["section_titles"]


@pytest.mark.asyncio
async def test_draft_outline_keeps_full_digest_without_instruction(monkeypatch):
    """没写提炼要求时定位退化为全量，行为与改造前一致。"""
    captured: dict = {}

    async def _capture(configs, rr_start, payload):
        captured.update(payload)
        return {"nodes": [
            {"level": 1, "title": "项目理解"},
            {"level": 1, "title": "技术响应"},
            {"level": 1, "title": "服务保障"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _capture)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [
        DomainSection(id="s1", level=1, title="甲章节", raw_content="甲正文"),
        DomainSection(id="s2", level=1, title="乙章节", raw_content="乙正文"),
    ]

    await agent.draft_outline(source, instruction="")

    assert "甲章节" in captured["spec_digest"]
    assert "乙章节" in captured["spec_digest"]
    # 没定位到用户点名的部分 → 结构仍由模型自己组织
    assert captured["follow_structure"] is False


@pytest.mark.asyncio
async def test_draft_outline_builds_hierarchical_ids(monkeypatch):
    async def _tree(configs, rr_start, payload):
        return {"nodes": [
            {"level": 1, "title": "项目理解"},
            {"level": 2, "parent": 0, "title": "需求理解"},
            {"level": 1, "title": "技术响应"},
            {"level": 2, "parent": 2, "title": "架构设计"},
            {"level": 3, "parent": 3, "title": "数据层"},
            {"level": 1, "title": "服务保障"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _tree)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="正文")]
    result = await agent.draft_outline(source)

    assert [(s.id, s.level) for s in result.sections] == [
        ("s1", 1), ("s1.1", 2), ("s2", 1), ("s2.1", 2), ("s2.1.1", 3), ("s3", 1),
    ]


@pytest.mark.asyncio
async def test_draft_outline_grounds_raw_content_and_inherits_marks(monkeypatch):
    """派生章节靠 BM25 挂回规范书原文，raw_content 与 ★/▲ 都从原文章节继承。"""
    async def _tree(configs, rr_start, payload):
        return {"nodes": [
            {"level": 1, "title": "技术方案"},
            {"level": 1, "title": "实施计划"},
            {"level": 1, "title": "售后服务"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _tree)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [
        DomainSection(id="s1", level=1, title="技术方案要求",
                      raw_content="系统须采用微服务架构。", special_marks=["★"]),
        DomainSection(id="s2", level=1, title="实施计划要求",
                      raw_content="工期为 90 日历天。", special_marks=[]),
    ]
    result = await agent.draft_outline(source)

    by_title = {s.title: s for s in result.sections}
    assert "微服务" in by_title["技术方案"].raw_content
    assert by_title["技术方案"].special_marks == ["★"]
    assert "90 日历天" in by_title["实施计划"].raw_content


@pytest.mark.asyncio
async def test_draft_outline_passes_instruction_into_prompt(monkeypatch):
    """用户提炼要求必须真的进 prompt，且带最高优先级标注。"""
    seen = {}

    async def _capture(configs, rr_start, payload):
        seen.update(payload)
        return {"nodes": [
            {"level": 1, "title": "A"}, {"level": 1, "title": "B"},
            {"level": 1, "title": "C"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _capture)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="正文")]
    await agent.draft_outline(source, instruction="按评分项逐条拆章")

    assert seen["instruction"] == "按评分项逐条拆章"
    assert "技术方案" in seen["spec_digest"]
    # 本轮不读素材，扩展点保留但恒为空
    assert seen["material_digest"] == ""
    assert seen["previous_outline"] == ""


@pytest.mark.asyncio
async def test_draft_outline_passes_previous_outline_on_redraft(monkeypatch):
    """整版重出时把上一版目录喂回 prompt，迭代才有连续性。"""
    seen = {}

    async def _capture(configs, rr_start, payload):
        seen.update(payload)
        return {"nodes": [
            {"level": 1, "title": "A"}, {"level": 1, "title": "B"},
            {"level": 1, "title": "C"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _capture)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    previous = [
        DomainSection(id="s1", level=1, title="上一版一级", raw_content=""),
        DomainSection(id="s1.1", level=2, title="上一版二级", raw_content=""),
    ]
    await agent.draft_outline(source, previous_toc=previous)

    assert "上一版一级" in seen["previous_outline"]
    assert "  - 上一版二级" in seen["previous_outline"]


@pytest.mark.asyncio
async def test_draft_outline_reports_warnings_in_error(monkeypatch):
    """部分节点被截断/重挂属于「成功但有警告」，不能算降级。"""
    async def _messy(configs, rr_start, payload):
        return {"nodes": [
            {"level": 1, "title": "A"},
            {"level": 1, "title": "A"},
            {"level": 1, "title": "B"},
            {"level": 1, "title": "C"},
        ]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _messy)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    result = await agent.draft_outline(source)

    assert "重名" in result.error
    assert len(result.sections) == 3


@pytest.mark.asyncio
async def test_draft_outline_reports_when_scope_not_located(monkeypatch):
    """用户点名了范围却定位不到 → 原因必须回到 outline_error（前端闸门会展示）。

    旧行为只有 logger.info：目录实际按全文拆的，用户从界面完全看不出范围没生效。
    """
    async def _outline(configs, rr_start, payload):
        return {"nodes": [
            {"level": 1, "title": "项目理解"},
            {"level": 1, "title": "技术响应"},
            {"level": 1, "title": "服务保障"},
        ]}

    async def _no_keyword(configs, rr_start, payload):
        return {"files": [], "keywords": []}      # 模型兜底也认不出

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _outline)
    monkeypatch.setattr(infra.llm, "dispatch_scope_select_json", _no_keyword)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [
        DomainSection(id="s1", level=1, title="技术规范", raw_content="没有任何锚点的正文"),
    ]

    result = await agent.draft_outline(source, instruction="按照标包2的技术评分要求拆分章节")

    assert "标包2" in result.error
    assert "锚点" in result.error
    assert "重新拆分目录" in result.error          # 给出下一步动作
    assert result.sections, "范围没定位到也要照常派生目录（只是没锁定结构）"


@pytest.mark.asyncio
async def test_draft_outline_reports_truncated_output(monkeypatch):
    """输出被截断（抢救出的部分成功）→ error 里要说清，闸门 1 才能提示重试。"""
    async def _outline(configs, rr_start, payload):
        return {"nodes": [
            {"level": 1, "title": "项目理解"},
            {"level": 1, "title": "技术响应"},
            {"level": 1, "title": "服务保障"},
        ], "_truncated_nodes": 3}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _outline)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术规范", raw_content="正文")]

    result = await agent.draft_outline(source, instruction="按评分项拆章")

    assert "截断" in result.error and "3 个章节" in result.error
    assert "重新拆分目录" in result.error
    assert len(result.sections) == 3        # 部分成功仍然落目录


@pytest.mark.asyncio
async def test_draft_outline_scopes_to_named_file_without_model_call(monkeypatch):
    """要求里**点名文件**（「按照评分表拆分章节」）→ 确定性地用那份文件当材料。

    这种说法里没有标包/标段词族，区段定位与模型兜底的门控都不会触发；而它恰恰是
    最常见的诉求。命中文件名后不该再花一次模型调用。
    """
    captured: dict = {}
    scope_calls = {"n": 0}

    async def _outline(configs, rr_start, payload):
        captured.update(payload)
        return {"nodes": [
            {"level": 1, "title": "评分项一"},
            {"level": 1, "title": "评分项二"},
            {"level": 1, "title": "评分项三"},
        ]}

    async def _scope(configs, rr_start, payload):
        scope_calls["n"] += 1
        return {"files": [], "keywords": []}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _outline)
    monkeypatch.setattr(infra.llm, "dispatch_scope_select_json", _scope)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [
        DomainSection(id="s1", level=1, title="投标人须知",
                      raw_content="与评分无关的正文", source_file="招标文件.pdf"),
        DomainSection(id="s2", level=1, title="技术评分标准",
                      raw_content="评分项：技术方案完整性", source_file="评分表.pdf"),
    ]

    result = await agent.draft_outline(source, instruction="按照评分表拆分章节")

    digest = captured["spec_digest"]
    assert "评分项：技术方案完整性" in digest          # 点名文件的内容进来了
    assert "与评分无关的正文" not in digest            # 其它文件没进来
    assert captured["follow_structure"] is True        # 点名文件 → 结构锁死
    assert scope_calls["n"] == 0, "确定性命中不该再调模型兜底"
    assert "评分表.pdf" in result.error                # 闸门能看到定位依据


@pytest.mark.asyncio
async def test_draft_outline_uses_model_picked_file_as_scope(monkeypatch):
    """要求里是「标包2 的评分表」这种说法时：模型认出是哪份文件 → 用那份文件当材料。

    prompt 一直要求模型同时给 keywords 与 files，旧实现只取 keywords、还恒传空
    file_names，于是「点名文件」这条完全没救。
    """
    captured: dict = {}

    async def _outline(configs, rr_start, payload):
        captured.update(payload)
        return {"nodes": [
            {"level": 1, "title": "评分项一"},
            {"level": 1, "title": "评分项二"},
            {"level": 1, "title": "评分项三"},
        ]}

    async def _scope(configs, rr_start, payload):
        assert payload["file_names"] == ["招标文件.pdf", "评分表.pdf"]
        return {"files": ["评分表.pdf"], "keywords": []}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _outline)
    monkeypatch.setattr(infra.llm, "dispatch_scope_select_json", _scope)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [
        DomainSection(id="s1", level=1, title="投标人须知",
                      raw_content="与评分无关的正文", source_file="招标文件.pdf"),
        DomainSection(id="s2", level=1, title="技术评分标准",
                      raw_content="评分项：技术方案完整性", source_file="评分表.pdf"),
    ]

    await agent.draft_outline(source, instruction="按照标包2的评分表拆分章节")

    digest = captured["spec_digest"]
    assert "评分项：技术方案完整性" in digest
    assert "与评分无关的正文" not in digest
    assert captured["follow_structure"] is True


def test_named_files_in_matches_full_name_or_stem():
    """文件名匹配：整名或去扩展名的主干、忽略空白与大小写；不误伤无关键。"""
    from agents.zhang_heng import named_files_in

    files = ["招标文件.pdf", "评分表.pdf", "评审要素表.docx"]
    assert named_files_in("按照评分表拆分章节", files) == ["评分表.pdf"]
    assert named_files_in("按 评分表 拆", files) == ["评分表.pdf"]
    assert named_files_in("按照招标文件.pdf里的评分表", files) == ["招标文件.pdf", "评分表.pdf"]
    assert named_files_in("按评分项逐条拆章", files) == []
    assert named_files_in("", files) == []
    assert named_files_in("按评分表拆", []) == []


async def test_extract_does_not_fill_missing_sections_when_cancelled():
    """取消时不要给未提炼的章节补空行。

    兜底补出来的行是 status='outline_done' + 8 字段全空（error=「提炼缺失」），前端按
    status 判定就成了"已提炼" —— 取消后页面显示「提炼完成」、字段却是空的，用户没法
    判断到底跑没跑，「继续提炼」也会被这份假完成态误导。
    """
    from agents.zhang_heng import OutlineMatrixRow, ZhangHengAgent
    from domain.spec import Section as DomainSection

    agent = ZhangHengAgent.__new__(ZhangHengAgent)
    section = DomainSection(id="s1", level=1, title="第一章", raw_content="内容",
                            special_marks=[])

    fired: list[str] = []

    class _Cfg:
        provider, model = "deepseek", "deepseek-v4-flash"

    async def _on_section(sec, row):
        fired.append(sec.id)

    # dispatch 一个都不产出（模拟取消后 generator 提前结束）
    import infra.llm

    async def _empty_dispatch(configs, rr, sections, **kw):
        if False:
            yield {}
        return

    orig = infra.llm.dispatch_outline_json
    infra.llm.dispatch_outline_json = _empty_dispatch
    agent._configs_provider = lambda: ([_Cfg()], 0)
    try:
        cancelled = await agent.extract([section], on_section=_on_section,
                                        should_cancel=lambda: True)
        assert cancelled == {}, "取消时不该补出空行"
        assert fired == []

        # 没取消：兜底照旧补一行（防 dispatch bug）
        filled = await agent.extract([section], on_section=_on_section,
                                     should_cancel=lambda: False)
        assert list(filled) == ["s1"]
        assert filled["s1"].error == "提炼缺失"
    finally:
        infra.llm.dispatch_outline_json = orig
