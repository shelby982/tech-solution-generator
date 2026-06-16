"""诸葛亮 agent 测试 — generate 阶段。

依赖 infra.llm 的 mock 路由（环境变量 LLM_MODE=mock）。
覆盖 letter / tech 双分支、重生路径、事件回调（sync+async）、图占位符识别。
"""

import pytest

from agents.zhuge_liang import ZhugeLiangAgent
from domain.proposal import BlockOutput, Source
from domain.spec import OutlineMatrixRow
from infra.retrieval import Match


# ─────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────

def _fake_configs():
    """返回最小可用 LLMConfig，触发 mock 路由。"""
    from services.config_store import LLMConfig
    return ([
        LLMConfig(
            provider="openai",
            api_key="sk-fake",
            base_url="https://example.invalid/v1",
            model="gpt-test",
        ),
    ], 0)


def _no_configs():
    return ([], 0)


# ─────────────────────────────────────────────
# 基础路径
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_letter_block_contains_letter_placeholders(monkeypatch):
    """letter 分支：标题含「承诺书」，输出 content 含致辞抬头与公司全称占位。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {
        "s1": OutlineMatrixRow(block_id="s1", title="投标承诺书", requirement="r"),
    }

    results, consumed = await agent.generate(
        outline_matrix=matrix, materials={}, regenerate_targets=None,
    )

    assert "s1" in results
    assert results["s1"].kind == "letter"
    # mock fixture 公文 markdown 必含两个占位符
    assert "【招标人/采购人名称】" in results["s1"].content
    assert "【公司全称】" in results["s1"].content
    assert results["s1"].sources == []
    assert results["s1"].outline == ""
    assert results["s1"].needs_diagram is False
    assert consumed == []


@pytest.mark.asyncio
async def test_generate_tech_block_has_outline_and_content(monkeypatch):
    """tech 分支：含 outline，content 非空，sources 来自 matches。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {
        "s1": OutlineMatrixRow(
            block_id="s1", title="技术架构方案", requirement="高可用",
        ),
    }
    matches = {"s1": [
        Match(chunk_id=1, score=9.0, reason="微服务架构", hit_points=["高可用"]),
    ]}

    results, _ = await agent.generate(
        outline_matrix=matrix, materials=matches, regenerate_targets=None,
    )

    assert results["s1"].kind == "tech"
    assert results["s1"].outline  # 非空字符串
    assert results["s1"].content  # 非空字符串
    assert len(results["s1"].sources) == 1
    assert results["s1"].sources[0].material_id == 1


@pytest.mark.asyncio
async def test_generate_returns_empty_when_no_configs():
    """无 LLM 配置：返回空 dict + 完整 consumed_targets。"""
    agent = ZhugeLiangAgent(configs_provider=_no_configs)
    matrix = {
        "s1": OutlineMatrixRow(block_id="s1", title="X", requirement="Y"),
    }
    results, consumed = await agent.generate(
        outline_matrix=matrix, materials={}, regenerate_targets=["s1"],
    )
    assert results == {}
    assert consumed == ["s1"]


# ─────────────────────────────────────────────
# 重生路径
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_with_regenerate_targets_only_runs_targets(monkeypatch):
    """regenerate_targets=[s2]：只跑 s2 一个 block，s1 / s3 不在结果里。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {
        "s1": OutlineMatrixRow(block_id="s1", title="架构", requirement="X"),
        "s2": OutlineMatrixRow(block_id="s2", title="安全", requirement="Y"),
        "s3": OutlineMatrixRow(block_id="s3", title="性能", requirement="Z"),
    }
    results, consumed = await agent.generate(
        outline_matrix=matrix, materials={},
        regenerate_targets=["s2"],
    )
    assert set(results.keys()) == {"s2"}
    assert consumed == ["s2"]


@pytest.mark.asyncio
async def test_regenerate_targets_filter_unknown_block_ids(monkeypatch):
    """regenerate_targets 中的未知 block_id 应被忽略，不抛错。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="技术", requirement="R")}
    results, _ = await agent.generate(
        outline_matrix=matrix, materials={},
        regenerate_targets=["s1", "nonexistent"],
    )
    assert set(results.keys()) == {"s1"}


# ─────────────────────────────────────────────
# 事件回调
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_emits_block_start_token_done_events(monkeypatch):
    """事件回调验证：block_start → token+ → block_done。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    events = []

    async def emitter(event_type, payload):
        events.append((event_type, payload))

    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="技术架构", requirement="R")}

    await agent.generate(
        outline_matrix=matrix, materials={},
        regenerate_targets=None, emitter=emitter,
    )

    types = [e[0] for e in events]
    assert types[0] == "block_start"
    assert "token" in types  # mock 流式至少 1 个 token
    assert types[-1] == "block_done"


@pytest.mark.asyncio
async def test_generate_supports_sync_emitter(monkeypatch):
    """事件回调可以是 sync 函数，agent 应正确处理。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    captured = []

    def sync_emitter(event_type, payload):
        captured.append(event_type)
        # 注意：sync emitter 不 await，无返回值

    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="投标承诺书", requirement="X")}
    await agent.generate(
        outline_matrix=matrix, materials={},
        regenerate_targets=None, emitter=sync_emitter,
    )
    assert "block_start" in captured
    assert "block_done" in captured


# ─────────────────────────────────────────────
# 图占位符识别
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_needs_diagram_flag_set_when_outline_has_placeholder(monkeypatch):
    """outline 中含 【架构图：xxx】 等占位符时 needs_diagram=True。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    # mock generate_section_outline 返回含图占位符的内容
    async def fake_outline(configs, rr_start, block, extra_context=""):
        return "## 系统拓扑\n\n【架构图：xx 系统部署架构】\n\n详细说明..."

    # agent 通过 `from infra.llm import generate_section_outline` 函数内 import，
    # 故 patch 源模块的同名导出即可
    import infra.llm
    monkeypatch.setattr(infra.llm, "generate_section_outline", fake_outline)

    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="技术架构", requirement="R")}
    results, _ = await agent.generate(
        outline_matrix=matrix, materials={}, regenerate_targets=None,
    )
    # tech 分支才会调 generate_section_outline，所以这里 kind=tech
    assert results["s1"].needs_diagram is True


@pytest.mark.asyncio
async def test_needs_diagram_false_when_outline_has_no_placeholder(monkeypatch):
    """outline 不含图占位符时 needs_diagram=False。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="技术方案", requirement="R")}
    results, _ = await agent.generate(
        outline_matrix=matrix, materials={}, regenerate_targets=None,
    )
    # mock 默认 outline fixture 不含 【架构图：】
    assert results["s1"].needs_diagram is False
