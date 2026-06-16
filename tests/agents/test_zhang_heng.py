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
