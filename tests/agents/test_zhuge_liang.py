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


# ─────────────────────────────────────────────
# 并发：>5 block 走 semaphore 队列 + 单 block 失败隔离
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_concurrent_blocks_isolated_on_single_failure(monkeypatch):
    """7 个 tech block 并发跑，s3 的 outline 调用抛异常其他不受影响。

    覆盖：
    - >BLOCK_CONCURRENCY (5) 个 block，验证 semaphore 队列能跑完
    - 单 block 内 LLM 调用抛异常被 _run_one 内 try/except 兜底
    - results 顺序与 target_ids 顺序一致
    - failed block 仍出现在 results 中，content/outline 为空
    """
    monkeypatch.setenv("LLM_MODE", "mock")

    # 用 title 识别 s3 — agent 把 row.title 作为 dispatch_block_write 的 title 传入,
    # 把 block_dict（含 title）作为 generate_section_outline 的第三个 arg。
    async def fake_outline(configs, rr_start, block, extra_context=""):
        if block.get("title") == "FAIL_BLOCK_TITLE":
            raise RuntimeError("simulated outline failure")
        return "outline content for " + str(block.get("title"))

    async def fake_block_write(*, configs, rr_start_index, title, requirement,
                               chunks, target_words):
        # 没在 outline 阶段失败的话，正文阶段也不应该失败
        if title == "FAIL_BLOCK_TITLE":
            raise RuntimeError("should never reach: outline already failed")
        for token in ["正文-", title, "-end"]:
            yield token

    import infra.llm
    monkeypatch.setattr(infra.llm, "generate_section_outline", fake_outline)
    monkeypatch.setattr(infra.llm, "dispatch_block_write", fake_block_write)

    # 7 个 block，s3 故意失败
    target_ids = [f"s{i}" for i in range(1, 8)]
    matrix = {
        bid: OutlineMatrixRow(
            block_id=bid,
            title="FAIL_BLOCK_TITLE" if bid == "s3" else f"技术方案-{bid}",
            requirement="R",
        )
        for bid in target_ids
    }

    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    results, _ = await agent.generate(
        outline_matrix=matrix, materials={}, regenerate_targets=None,
    )

    # 全部 7 个 block 都返回（含失败的 s3），顺序保持
    assert list(results.keys()) == target_ids
    # 失败的 s3：try/except 兜底返回空 BlockOutput
    assert results["s3"].content == ""
    assert results["s3"].outline == ""
    # 其他 block 正常出 content
    for bid in target_ids:
        if bid == "s3":
            continue
        assert results[bid].content, f"{bid} 的 content 不应为空"
        assert results[bid].outline, f"{bid} 的 outline 不应为空"


def test_collect_material_requests_extracts_placeholders():
    """从写作大纲的 【待补充：xx】 占位符抽取补料请求。"""
    from agents.zhuge_liang import _collect_material_requests

    outline = (
        "## 配电系统\n"
        "- 引用【待补充：配电柜型式试验报告】证明绝缘性能\n"
        "- 参见【待补充：近三年同类项目业绩证明合同】\n"
        "- 交付周期【待补充：配电柜型式试验报告】\n"
    )

    reqs = _collect_material_requests(outline)

    assert [r["query"] for r in reqs] == [
        "配电柜型式试验报告",
        "近三年同类项目业绩证明合同",
    ]
    assert all(r["reason"] for r in reqs)


def test_collect_material_requests_handles_empty_and_none():
    """无大纲 / 无占位符都返回空列表，不抛错。"""
    from agents.zhuge_liang import _collect_material_requests

    assert _collect_material_requests("") == []
    assert _collect_material_requests("## 纯文字大纲，没有占位符") == []


def test_block_output_carries_material_requests():
    """BlockOutput 的 material_requests 能完整往返。"""
    from domain.proposal import BlockOutput

    out = BlockOutput(
        block_id="s1",
        kind="tech",
        content="正文",
        material_requests=[{"query": "业绩证明", "reason": "缺证据"}],
    )

    d = out.to_dict()
    assert d["material_requests"] == [{"query": "业绩证明", "reason": "缺证据"}]
    assert BlockOutput.from_dict(d).material_requests == d["material_requests"]


def test_block_output_from_dict_tolerates_legacy_payload():
    """老 checkpoint 里的 BlockOutput 没有 material_requests，反序列化不炸。"""
    from domain.proposal import BlockOutput

    legacy = {"block_id": "s1", "kind": "tech", "content": "正文", "sources": []}
    assert BlockOutput.from_dict(legacy).material_requests == []


@pytest.mark.asyncio
async def test_generate_tech_attaches_material_requests_from_outline(monkeypatch):
    """_generate_tech 产出的 BlockOutput 必须挂上大纲占位符抽取的补料请求。

    上述两个单测分别验证了抽取函数与 BlockOutput 字段，但都不覆盖二者之间的
    挂接 —— 删掉 _generate_tech 里的 material_requests=... 调用，前面全部用例
    仍然全绿，补料功能会静默失效。
    """
    monkeypatch.setenv("LLM_MODE", "mock")

    async def fake_outline(*a, **kw):
        return "## 配电系统\n- 引用【待补充：配电柜型式试验报告】\n"

    async def fake_block_write(**kwargs):
        yield "正文"

    import infra.llm
    monkeypatch.setattr(infra.llm, "generate_section_outline", fake_outline)
    monkeypatch.setattr(infra.llm, "dispatch_block_write", fake_block_write)

    agent = ZhugeLiangAgent(configs_provider=_fake_configs)
    results, _ = await agent.generate(
        outline_matrix={
            "s1": OutlineMatrixRow(
                block_id="s1", title="配电系统", requirement="绝缘性能",
            ),
        },
        materials={"s1": []},
    )

    assert results["s1"].material_requests == [
        {"query": "配电柜型式试验报告", "reason": "写作大纲中的待补充占位符"},
    ]
