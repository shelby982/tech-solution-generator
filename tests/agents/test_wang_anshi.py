"""王安石 agent 测试 — 技术评审。

依赖 infra.llm 的 mock 路由（环境变量 LLM_MODE=mock）；
对 LLM 客户端的 monkeypatch 走 ``agents.wang_anshi.generate_oneshot_openai``。
"""

import json

import pytest

from agents.wang_anshi import AGENT_NAME, WangAnshiAgent
from domain.proposal import BlockOutput
from domain.review import Finding
from domain.spec import OutlineMatrixRow


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
async def test_review_returns_finding_per_block(monkeypatch):
    """每个 block 都有 Finding，包含 score / issues / strengths 字段。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    agent = WangAnshiAgent(configs_provider=_fake_configs)

    blocks = {
        "s1": BlockOutput(block_id="s1", kind="tech", content="技术架构方案..."),
        "s2": BlockOutput(block_id="s2", kind="tech", content="安全设计..."),
    }
    matrix = {
        "s1": OutlineMatrixRow(block_id="s1", title="架构", requirement="高可用"),
        "s2": OutlineMatrixRow(block_id="s2", title="安全", requirement="加密"),
    }

    results = await agent.review(blocks=blocks, outline_matrix=matrix)
    assert set(results.keys()) == {"s1", "s2"}
    for finding in results.values():
        assert isinstance(finding, Finding)
        assert finding.agent == AGENT_NAME == "wang_anshi"
        assert 0 <= finding.score <= 100
        assert isinstance(finding.issues, list)
        assert isinstance(finding.strengths, list)
        assert finding.error == ""


@pytest.mark.asyncio
async def test_review_returns_empty_when_no_configs():
    """无 LLM 配置：每行返回 Finding(error="未配置 LLM")。"""
    agent = WangAnshiAgent(configs_provider=_no_configs)
    blocks = {
        "s1": BlockOutput(block_id="s1", kind="tech", content="X"),
    }
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="X", requirement="Y")}

    results = await agent.review(blocks=blocks, outline_matrix=matrix)
    assert results["s1"].error == "未配置 LLM"
    assert results["s1"].score == 0
    assert results["s1"].agent == "wang_anshi"


# ─────────────────────────────────────────────
# JSON 解析
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_parses_valid_response(monkeypatch):
    """LLM 返回完整 JSON：score/issues/strengths 全部正确解析。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    fake_response = json.dumps({
        "score": 85,
        "issues": [
            {"severity": "high", "point": "缺少容灾设计", "suggestion": "补充异地多活方案"},
            {"severity": "low", "point": "措辞略冗", "suggestion": "精简表达"},
        ],
        "strengths": ["架构清晰", "技术指标响应完整"],
    }, ensure_ascii=False)

    async def fake_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        return fake_response

    monkeypatch.setattr("agents.wang_anshi.generate_oneshot_openai", fake_oneshot)

    agent = WangAnshiAgent(configs_provider=_fake_configs)
    blocks = {"s1": BlockOutput(block_id="s1", kind="tech", content="x")}
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="架构", requirement="r")}

    results = await agent.review(blocks=blocks, outline_matrix=matrix)
    s1 = results["s1"]
    assert s1.score == 85
    assert len(s1.issues) == 2
    assert s1.issues[0].severity == "high"
    assert s1.issues[0].point == "缺少容灾设计"
    assert s1.issues[0].suggestion == "补充异地多活方案"
    assert s1.strengths == ["架构清晰", "技术指标响应完整"]


@pytest.mark.asyncio
async def test_review_clamps_score_to_0_100(monkeypatch):
    """score 超出 [0, 100] 被 clamp。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    async def fake_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        return json.dumps({"score": 150, "issues": [], "strengths": []})

    monkeypatch.setattr("agents.wang_anshi.generate_oneshot_openai", fake_oneshot)

    agent = WangAnshiAgent(configs_provider=_fake_configs)
    blocks = {"s1": BlockOutput(block_id="s1", kind="tech", content="x")}
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="X", requirement="r")}

    results = await agent.review(blocks=blocks, outline_matrix=matrix)
    assert results["s1"].score == 100


@pytest.mark.asyncio
async def test_review_handles_invalid_json(monkeypatch):
    """LLM 返回非 JSON：Finding.error 写入失败，不抛错。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    async def fake_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        return "this is not json"

    monkeypatch.setattr("agents.wang_anshi.generate_oneshot_openai", fake_oneshot)

    agent = WangAnshiAgent(configs_provider=_fake_configs)
    blocks = {"s1": BlockOutput(block_id="s1", kind="tech", content="x")}
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="X", requirement="r")}

    results = await agent.review(blocks=blocks, outline_matrix=matrix)
    assert results["s1"].error
    assert "JSON" in results["s1"].error or "解析" in results["s1"].error


# ─────────────────────────────────────────────
# 失败隔离
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_isolates_block_failure(monkeypatch):
    """单 block 全 API 失败：写入 Finding(error=...)，其它 block 不受影响。"""
    monkeypatch.setenv("LLM_MODE", "mock")

    call_count = {"n": 0}

    async def flaky_oneshot(config, system_prompt, user_prompt, max_tokens=1500):
        call_count["n"] += 1
        if call_count["n"] == 1:  # s1 第一次（也是唯一一次，单个 config）失败
            raise RuntimeError("simulated API failure")
        return json.dumps({"score": 80, "issues": [], "strengths": ["ok"]})

    monkeypatch.setattr("agents.wang_anshi.generate_oneshot_openai", flaky_oneshot)

    agent = WangAnshiAgent(configs_provider=_fake_configs)
    blocks = {
        "s1": BlockOutput(block_id="s1", kind="tech", content="x"),
        "s2": BlockOutput(block_id="s2", kind="tech", content="y"),
    }
    matrix = {
        "s1": OutlineMatrixRow(block_id="s1", title="A", requirement="r"),
        "s2": OutlineMatrixRow(block_id="s2", title="B", requirement="r"),
    }
    results = await agent.review(blocks=blocks, outline_matrix=matrix)
    assert results["s1"].error  # 全失败兜底
    assert results["s1"].score == 0
    assert results["s2"].error == ""
    assert results["s2"].score == 80


# ─────────────────────────────────────────────
# 事件回调
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_emits_per_block_events(monkeypatch):
    """每个 block 触发 review_block_start + review_block_done。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    events = []

    async def emitter(event_type, payload):
        events.append((event_type, payload))

    agent = WangAnshiAgent(configs_provider=_fake_configs)
    blocks = {
        "s1": BlockOutput(block_id="s1", kind="tech", content="x"),
        "s2": BlockOutput(block_id="s2", kind="tech", content="y"),
    }
    matrix = {
        "s1": OutlineMatrixRow(block_id="s1", title="A", requirement="r"),
        "s2": OutlineMatrixRow(block_id="s2", title="B", requirement="r"),
    }
    await agent.review(blocks=blocks, outline_matrix=matrix, emitter=emitter)

    starts = [e for e in events if e[0] == "review_block_start"]
    dones = [e for e in events if e[0] == "review_block_done"]
    assert len(starts) == 2
    assert len(dones) == 2
    assert all(p[1]["agent"] == "wang_anshi" for p in events)


@pytest.mark.asyncio
async def test_review_supports_sync_emitter(monkeypatch):
    """sync 回调也能正常触发。"""
    monkeypatch.setenv("LLM_MODE", "mock")
    captured = []

    def sync_emitter(event_type, payload):
        captured.append(event_type)

    agent = WangAnshiAgent(configs_provider=_fake_configs)
    blocks = {"s1": BlockOutput(block_id="s1", kind="tech", content="x")}
    matrix = {"s1": OutlineMatrixRow(block_id="s1", title="X", requirement="r")}
    await agent.review(blocks=blocks, outline_matrix=matrix, emitter=sync_emitter)
    assert "review_block_start" in captured
    assert "review_block_done" in captured


def test_parse_finding_reads_material_fields():
    """_parse_finding 解析 needs_material / material_query。"""
    agent = WangAnshiAgent(configs_provider=lambda: ([], 0))
    raw = json.dumps({
        "score": 60,
        "issues": [
            {
                "severity": "critical",
                "point": "未提供型式试验数据",
                "suggestion": "补充试验报告",
                "needs_material": True,
                "material_query": "配电柜型式试验报告",
            },
            {
                "severity": "low",
                "point": "表述冗长",
                "suggestion": "精简",
            },
        ],
        "strengths": [],
    })

    finding = agent._parse_finding("s1", raw)

    assert finding.issues[0].needs_material is True
    assert finding.issues[0].material_query == "配电柜型式试验报告"
    # 缺省字段必须退化为"非补料问题"，否则会把重写类问题误转成检索请求
    assert finding.issues[1].needs_material is False
    assert finding.issues[1].material_query == ""
