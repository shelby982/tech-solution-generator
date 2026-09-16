"""张衡 agent 测试 — parse / extract 两个阶段。

依赖 infra.llm 的 mock 路由（环境变量 LLM_MODE=mock）。
"""

import io

import pytest

from agents.zhang_heng import SpecParseResult, ZhangHengAgent
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
        _build_minimal_docx(), suffix=".docx", filename="test.docx",
    )

    assert isinstance(result, SpecParseResult)
    assert len(result.toc) == 2
    assert all(isinstance(s, DomainSection) for s in result.toc)
    assert "项目概述" in result.toc[0].title
    # ★ 标记应在 special_marks 中
    assert "★" in result.toc[1].special_marks
    # mock 模式下 doc_summary 由 mock 兜底返回非空字符串
    assert isinstance(result.doc_summary, str)
    assert result.doc_summary  # 非空


@pytest.mark.asyncio
async def test_parse_when_no_configs_leaves_doc_summary_empty(monkeypatch):
    """无 LLM 配置时 doc_summary 留空，不抛错。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhangHengAgent(configs_provider=lambda: ([], 0))

    result = await agent.parse(
        _build_minimal_docx(), suffix=".docx", filename="test.docx",
    )

    assert result.doc_summary == ""
    assert len(result.toc) == 2


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
async def test_draft_outline_degrades_without_llm_configs():
    """无 LLM 配置时沿用规范书目录，不抛错，error 写明原因。"""
    agent = ZhangHengAgent(configs_provider=_no_configs)
    source = [
        DomainSection(id="s1", level=1, title="技术方案", raw_content="正文",
                      special_marks=["★"]),
    ]

    result = await agent.draft_outline(source)

    assert result.degraded is True
    assert "未配置模型" in result.error
    assert [(s.id, s.level, s.title) for s in result.sections] == [("s1", 1, "技术方案")]
    # 降级路径要原样带上 raw_content / special_marks，extract 仍能正常跑
    assert result.sections[0].raw_content == "正文"
    assert result.sections[0].special_marks == ["★"]


@pytest.mark.asyncio
async def test_draft_outline_degrades_on_empty_source_toc():
    agent = ZhangHengAgent(configs_provider=_fake_configs)
    result = await agent.draft_outline([])
    assert result.degraded is True
    assert result.sections == []


@pytest.mark.asyncio
async def test_draft_outline_degrades_when_dispatch_raises(monkeypatch):
    async def _boom(configs, rr_start, payload):
        raise RuntimeError("全部 API 失败")

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _boom)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    result = await agent.draft_outline(source)

    assert result.degraded is True
    assert "全部 API 失败" in result.error
    assert [s.title for s in result.sections] == ["技术方案"]


@pytest.mark.asyncio
async def test_draft_outline_degrades_when_nodes_unparsable(monkeypatch):
    async def _garbage(configs, rr_start, payload):
        return {"nodes": ["不是 dict", {"title": "  "}]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _garbage)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    result = await agent.draft_outline(source)

    assert result.degraded is True
    assert "无法解析" in result.error


@pytest.mark.asyncio
async def test_draft_outline_degrades_when_too_few_top_level(monkeypatch):
    """一级章节 < MIN_TOP_LEVEL_NODES 视为目录不可信，整体降级。"""
    async def _thin(configs, rr_start, payload):
        return {"nodes": [{"level": 1, "title": "唯一一级"}]}

    import infra.llm
    monkeypatch.setattr(infra.llm, "dispatch_outline_draft_json", _thin)

    agent = ZhangHengAgent(configs_provider=_fake_configs)
    source = [DomainSection(id="s1", level=1, title="技术方案", raw_content="")]
    result = await agent.draft_outline(source)

    assert result.degraded is True
    assert "结构不完整" in result.error


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

    assert result.degraded is False
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
    await agent.draft_outline(source, instruction="按评分项逐条拆章", doc_summary="项目摘要")

    assert seen["instruction"] == "按评分项逐条拆章"
    assert seen["doc_summary"] == "项目摘要"
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
async def test_draft_outline_reports_warnings_in_error_but_not_degraded(monkeypatch):
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

    assert result.degraded is False
    assert "重名" in result.error
    assert len(result.sections) == 3
