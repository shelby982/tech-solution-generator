"""infra/llm dispatcher + mock 模式测试。"""
import pytest

from infra.llm import (
    LLMConfig,
    dispatch_outline_json,
    generate_letter_content,
    is_letter_section,
)


def _fake_config() -> LLMConfig:
    """构造一份占位配置；mock 模式下不会真的发起网络调用。"""
    return LLMConfig(
        provider="openai",
        api_key="sk-fake",
        base_url="https://example.invalid/v1",
        model="gpt-test",
    )


# ─────────────────────────────────────────────
# letter detection
# ─────────────────────────────────────────────

def test_letter_section_detection():
    assert is_letter_section("投标承诺书") is True
    assert is_letter_section("廉洁承诺函") is True
    assert is_letter_section("授权委托书") is True
    assert is_letter_section("技术方案概述") is False
    assert is_letter_section("") is False


# ─────────────────────────────────────────────
# mock 模式下大纲提炼：8 字段 + title + error
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_outline_json_under_mock_returns_eight_fields(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    cfg = _fake_config()
    sections = [{"title": "技术架构要求", "content": "系统须采用微服务架构…"}]

    results = []
    async for item in dispatch_outline_json([cfg], 0, sections):
        results.append(item)

    assert len(results) == 1
    item = results[0]
    expected_keys = {
        "title",
        "requirement",
        "key_points",
        "veto_items",
        "bonus_items",
        "score_items",
        "evidence_required",
        "constraint_level",
        "indicators",
        "error",
    }
    assert expected_keys.issubset(item.keys())
    assert item["title"] == "技术架构要求"
    assert item["error"] == ""
    assert item["constraint_level"] in {"mandatory", "recommended", "optional"}


# ─────────────────────────────────────────────
# mock 模式下公文生成：必含两个占位符
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_letter_response_contains_required_placeholders(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "mock")
    cfg = _fake_config()
    block = {
        "title": "投标承诺书",
        "requirement": "投标方须承诺产品质量、交付工期、售后服务等。",
    }
    text = await generate_letter_content([cfg], 0, block)
    assert "【招标人/采购人名称】" in text
    assert "【公司全称】" in text


# ─────────────────────────────────────────────
# 协同闭环：dispatch_block_write 注入评审意见
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_block_write_injects_feedback(monkeypatch):
    """feedback_text 非空时注入 extra_prompt，内容出现在实际发出的 prompt 里。"""
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield "ok"

    import infra.llm.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "dispatch_stream_generate", _fake_stream)

    tokens = [
        t async for t in dispatcher.dispatch_block_write(
            configs=[], rr_start_index=0, title="配电系统",
            requirement="提供业绩证明", chunks=[],
            feedback_text="【上轮评审意见】\n1. [CRITICAL] 缺业绩证明",
        )
    ]

    assert tokens == ["ok"]
    assert "上轮评审意见" in captured["extra_prompt"]
    assert "提供业绩证明" in captured["extra_prompt"]


@pytest.mark.asyncio
async def test_dispatch_block_write_without_feedback_unchanged(monkeypatch):
    """不传 feedback_text 时，prompt 内容与改造前一致（仅标签随条目化改名）。"""
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield "ok"

    import infra.llm.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "dispatch_stream_generate", _fake_stream)

    async for _ in dispatcher.dispatch_block_write(
        configs=[], rr_start_index=0, title="配电系统",
        requirement="提供业绩证明", chunks=[],
    ):
        pass

    assert "上轮评审意见" not in captured["extra_prompt"]
    # 逐字节钉死改造前的 prompt —— 仅断言"不含评审意见"是弱断言：
    # 把它改成无条件拼接 f"\n\n{feedback_text}" 也照样通过。
    # （`items` 缺省时走的就是这条纯 requirement 路径）
    assert captured["extra_prompt"] == "【本章应答要求】\n提供业绩证明\n"


# ─────────────────────────────────────────────
# 写作输入补齐 8 字段（方案 §1.1、§4.B）
# ─────────────────────────────────────────────

def _items():
    """真实口径的条目样本：要回应的事项 + 评分口径（informative）。"""
    return [
        {"type": "requirement", "text": "支持国产数据库", "informative": False},
        {"type": "key_point", "text": "给出迁移方案", "informative": False},
        {"type": "veto", "text": "未提供 CMA 检测报告者投标无效", "informative": False},
        {"type": "bonus", "text": "三年免费升级", "informative": False},
        {"type": "score", "text": "技术方案完整性 20 分", "informative": True},
        {"type": "indicator", "text": "响应时间 ≤200ms", "informative": False},
        {"type": "evidence", "text": "提供第三方检测报告", "informative": False},
    ]


def test_build_write_requirement_block_expands_all_eight_fields():
    """8 字段（除 score/indicator 外都是"要回应的事项"）全部进写作输入，且逐条编号。

    改造前只带 requirement 一个字段，评分项/否决项/加分项/指标写手看不到 ——
    这是"评审必扣分 → 回炉"的根因。
    """
    from infra.llm.dispatcher import build_write_requirement_block

    block = build_write_requirement_block("支持国产数据库", _items())

    # 每个字段的原文都必须在，且带类型标号
    for tag, text in [
        ("R1", "支持国产数据库"),
        ("K1", "给出迁移方案"),
        ("V1", "未提供 CMA 检测报告者投标无效"),
        ("B1", "三年免费升级"),
        ("S1", "技术方案完整性 20 分"),
        ("I1", "响应时间 ≤200ms"),
        ("E1", "提供第三方检测报告"),
    ]:
        assert f"{tag} " in block, f"缺少条目标号 {tag}"
        assert text in block, f"缺少条目原文：{text}"

    # 否决项被前置为"必须先逐条写明满足"，评分/量化口径单独成块且**不要求逐条复述**
    assert "否决项必须先逐条写明我方如何满足" in block
    assert block.index("V1 ") > block.index("否决项必须先逐条写明我方如何满足")
    # 评分口径不做成编号题号去"回应评分规则"：实测把它列成"必须回应"会让模型抄打分档位
    assert "不要逐条复述档位或分值" in block
    assert "以下是要回应的事项，逐条作答" in block
    # 评分项不应出现在"要回应的事项"分组里
    assert block.index("S1 ") < block.index("以下是要回应的事项")


def test_build_write_requirement_block_without_items_degrades_to_requirement():
    """无条目（数据缺失/旧调用方）时退回只给 requirement —— 行为与改造前一致。"""
    from infra.llm.dispatcher import build_write_requirement_block

    assert build_write_requirement_block("高可用", None) == "【本章应答要求】\n高可用\n"
    assert build_write_requirement_block("高可用", []) == "【本章应答要求】\n高可用\n"
    # 空 requirement + 空条目 → 空串（不产出空标题）
    assert build_write_requirement_block("", None) == ""


def test_build_write_requirement_block_keeps_requirement_when_not_covered():
    """条目化漏掉 requirement 里的句子时，原文兜底附上（不静默丢要求）。"""
    from infra.llm.dispatcher import build_write_requirement_block

    block = build_write_requirement_block(
        "支持国产数据库\n另外须提供迁移演练报告",
        [{"type": "requirement", "text": "支持国产数据库", "informative": False}],
    )
    assert "要求原文：" in block
    assert "另外须提供迁移演练报告" in block


def test_build_write_requirement_block_truncates_overlong_requirement():
    """超长要求截断到预算内（避免 prompt 超窗），并声明已截断。"""
    from infra.llm.dispatcher import build_write_requirement_block

    items = [
        {"type": "requirement", "text": f"要求{i}：" + "细节" * 200, "informative": False}
        for i in range(20)
    ]
    block = build_write_requirement_block("摘要", items, max_chars=2000)
    assert len(block) <= 2000 + 40
    assert "已截断" in block


@pytest.mark.asyncio
async def test_dispatch_block_write_injects_items_and_omits_reference_when_no_material(capture_stream):
    """有 items 时 8 字段进 user prompt；无素材时**不拼**【参考素材】（§1.3）。"""
    from infra.llm import dispatcher

    async for _ in dispatcher.dispatch_block_write(
        configs=[_fake_config()], rr_start_index=0, title="数据层设计",
        requirement="支持国产数据库", chunks=[], items=_items(),
    ):
        pass

    prompt = capture_stream[0]["user"]
    assert "V1 否决项：未提供 CMA 检测报告者投标无效" in prompt
    assert "S1 评分项：技术方案完整性 20 分" in prompt
    assert "I1 量化指标：响应时间 ≤200ms" in prompt
    # 无素材 → 【参考素材】区块整段不出现（稳定规则里的「（若有）」不算区块），
    # 来源标记规则也不该要求 [M]（否则等于给自造内容盖章成"来自素材"）
    assert "\n【参考素材】" not in prompt
    assert "[M]" not in prompt


@pytest.mark.asyncio
async def test_dispatch_block_write_includes_reference_when_material_present(capture_stream):
    """有素材时仍拼【参考素材】，且来源标记规则仍在（含 [M]）。"""
    from infra.llm import dispatcher

    async for _ in dispatcher.dispatch_block_write(
        configs=[_fake_config()], rr_start_index=0, title="数据层设计",
        requirement="支持国产数据库",
        chunks=[{"content": "达梦 DM8 支持国产化适配"}],
        items=_items(),
    ):
        pass

    prompt = capture_stream[0]["user"]
    assert "【参考素材】" in prompt
    assert "达梦 DM8 支持国产化适配" in prompt
    assert "[M]...[/M]" in prompt


# ─────────────────────────────────────────────
# dispatch_outline_draft_json：目录派生
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_under_mock(monkeypatch):
    """mock 模式下按「提炼应答文件目录」关键词路由到目录 fixture。"""
    from infra.llm import dispatch_outline_draft_json

    monkeypatch.setenv("LLM_MODE", "mock")
    cfg = _fake_config()

    obj = await dispatch_outline_draft_json([cfg], 0, {
        "instruction": "按评分项逐条拆章",
        "doc_summary": "摘要",
        "spec_digest": "【技术方案】\n正文",
    })

    assert isinstance(obj["nodes"], list)
    assert len(obj["nodes"]) > 0
    assert all("title" in n and "level" in n for n in obj["nodes"])
    # fixture 里要有真正的层级，否则前端树渲染在 mock 下测不出东西
    assert max(n["level"] for n in obj["nodes"]) >= 3


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_forwards_follow_structure(monkeypatch):
    """payload 里的 follow_structure 必须走到 user prompt —— 定位命中时目录要复刻结构。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    seen: dict = {}

    async def _capture(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        seen["user"] = user
        return '{"nodes": [{"level": 1, "title": "OK"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _capture)

    await dispatch_outline_draft_json([_fake_config()], 0, {
        "instruction": "按评分要求中的每一点拆章",
        "spec_digest": "【已定位到的材料（请据此编排章节）】\n【标包2】\n技术评分标准",
        "follow_structure": True,
    })
    assert "严格按材料自身的结构拆分章节" in seen["user"]

    await dispatch_outline_draft_json([_fake_config()], 0, {
        "instruction": "按评分项逐条拆章", "spec_digest": "【一】",
    })
    assert "严格按材料自身的结构拆分章节" not in seen["user"]


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_uses_enlarged_budget(monkeypatch):
    """目录派生必须用放大后的预算与超时，并且空响应要抛出来。

    推理模型实测会把 8000 token 全烧在思考上、正文一个字不吐（一次成功的调用
    要 142 秒），沿用默认值就是必然失败。
    """
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients
    import infra.llm.dispatcher as dispatcher

    seen: dict = {}

    async def _capture(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        seen.update(max_tokens=max_tokens, timeout=timeout,
                    raise_on_empty=raise_on_empty, call_site=call_site)
        return '{"nodes": [{"level": 1, "title": "OK"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _capture)

    await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})

    assert dispatcher.OUTLINE_DRAFT_MAX_TOKENS == 32000
    assert dispatcher.OUTLINE_DRAFT_TIMEOUT == 300.0
    assert seen["max_tokens"] == 32000
    assert seen["timeout"] == 300.0
    assert seen["raise_on_empty"] is True
    # 用量记账靠这个标签归因：漏传就全记成空串，「花在哪条路径」就查不出来了
    assert seen["call_site"] == "outline_draft"


@pytest.mark.asyncio
async def test_scope_select_keeps_default_budget(monkeypatch):
    """其余调用点不受影响：仍走 60s 默认超时，空内容仍是回空串而不是抛异常。"""
    from infra.llm import dispatch_scope_select_json
    from infra.llm import clients

    seen: dict = {}

    async def _capture(config, system, user, max_tokens=0, timeout=60.0, raise_on_empty=False, call_site=""):
        seen.update(timeout=timeout, raise_on_empty=raise_on_empty)
        return '{"files": [], "keywords": ["标包2"]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _capture)

    await dispatch_scope_select_json([_fake_config()], 0, {"instruction": "x"})

    assert seen["timeout"] == 60.0
    assert seen["raise_on_empty"] is False


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_raises_without_configs():
    from infra.llm import dispatch_outline_draft_json

    with pytest.raises(ValueError):
        await dispatch_outline_draft_json([], 0, {"spec_digest": "x"})


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_falls_back_to_next_config(monkeypatch):
    """第一个 config 失败要轮询到下一个，而不是直接把异常抛给调用方。"""
    from infra import llm
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    calls = []

    async def _gen(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        calls.append(config.model)
        if config.model == "bad":
            raise RuntimeError("第一个 API 挂了")
        return '{"nodes": [{"level": 1, "title": "OK"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _gen)

    good = _fake_config()
    good.model = "good"
    bad = _fake_config()
    bad.model = "bad"

    obj = await dispatch_outline_draft_json([bad, good], 0, {"spec_digest": "x"})

    assert calls == ["bad", "good"]
    assert obj["nodes"][0]["title"] == "OK"


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_raises_last_error_when_all_fail(monkeypatch):
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _always_fail(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        raise RuntimeError(f"{config.model} 挂了")

    monkeypatch.setattr(clients, "generate_oneshot_openai", _always_fail)

    a = _fake_config()
    a.model = "a"
    b = _fake_config()
    b.model = "b"

    with pytest.raises(RuntimeError, match="b 挂了"):
        await dispatch_outline_draft_json([a, b], 0, {"spec_digest": "x"})


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_rejects_non_nodes_output(monkeypatch):
    """模型输出是合法 JSON 但没有 nodes 数组 → 当失败处理，走降级。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _wrong_shape(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        return '{"chapters": [{"title": "错的字段"}]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _wrong_shape)

    with pytest.raises(ValueError, match="nodes"):
        await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})


# ─────────────────────────────────────────────
# dispatch_scope_select_json：检索范围兜底
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_scope_select_json_under_mock(monkeypatch):
    """mock 按「圈定检索范围」路由。该分支必须排在目录派生之前 ——
    它的 system prompt 同样含「评审」二字，落到「评审」分支会走错 fixture。"""
    from infra.llm import dispatch_scope_select_json

    monkeypatch.setenv("LLM_MODE", "mock")

    obj = await dispatch_scope_select_json([_fake_config()], 0, {
        "instruction": "按标包2 的技术评分要求拆分",
        "file_names": ["主招标文件.pdf"],
        "section_titles": ["第一章 总则"],
    })

    assert isinstance(obj["keywords"], list)
    assert obj["keywords"]
    assert "nodes" not in obj   # 没落到目录派生 fixture


@pytest.mark.asyncio
async def test_dispatch_scope_select_json_raises_without_configs():
    from infra.llm import dispatch_scope_select_json

    with pytest.raises(ValueError):
        await dispatch_scope_select_json([], 0, {"instruction": "x"})


@pytest.mark.asyncio
async def test_dispatch_scope_select_json_falls_back_to_next_config(monkeypatch):
    from infra.llm import dispatch_scope_select_json
    from infra.llm import clients

    calls = []

    async def _gen(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        calls.append(config.model)
        if config.model == "bad":
            raise RuntimeError("第一个 API 挂了")
        return '{"files": [], "keywords": ["标包2"]}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _gen)

    good = _fake_config()
    good.model = "good"
    bad = _fake_config()
    bad.model = "bad"

    obj = await dispatch_scope_select_json([bad, good], 0, {"instruction": "x"})

    assert calls == ["bad", "good"]
    assert obj["keywords"] == ["标包2"]


@pytest.mark.asyncio
async def test_dispatch_scope_select_json_raises_last_error_when_all_fail(monkeypatch):
    from infra.llm import dispatch_scope_select_json
    from infra.llm import clients

    async def _always_fail(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        raise RuntimeError(f"{config.model} 挂了")

    monkeypatch.setattr(clients, "generate_oneshot_openai", _always_fail)

    a = _fake_config()
    a.model = "a"

    with pytest.raises(RuntimeError, match="a 挂了"):
        await dispatch_scope_select_json([a], 0, {"instruction": "x"})


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_salvages_truncated_prefix(monkeypatch):
    """扁平数组格式的核心动机：被 max_tokens 截断后仍能抢救出完整前缀，部分成功。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _truncated(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        # 最后一条写到一半就没了，外层 }]} 也没收尾
        return (
            '{"nodes": [{"level": 1, "title": "完整一"}, '
            '{"level": 1, "title": "完整二"}, {"level": 2, "title": "被截'
        )

    monkeypatch.setattr(clients, "generate_oneshot_openai", _truncated)

    obj = await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})

    assert [n["title"] for n in obj["nodes"]] == ["完整一", "完整二"]


@pytest.mark.asyncio
async def test_dispatch_outline_draft_json_fails_when_nothing_salvageable(monkeypatch):
    """连一个完整节点都没有 → 仍然算失败，由节点层降级。"""
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _barely_started(config, system, user, max_tokens=0, timeout=0, raise_on_empty=False, call_site=""):
        return '{"nodes": [{"level": 1, "tit'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _barely_started)

    with pytest.raises(ValueError):
        await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})


# ─────────────────────────────────────────────
# 正文长度分档（M2）与「无原文」提示词分支（M6）
# ─────────────────────────────────────────────

def test_length_band_selection():
    """分档判据：mandatory 或长要求 → core；中等 → standard；其余 → brief。"""
    from infra.llm import dispatcher

    assert dispatcher.length_band("", "recommended") == (600, 1000)
    assert dispatcher.length_band("简要说明", "recommended") == (600, 1000)
    assert dispatcher.length_band("甲" * 150, "recommended") == (1000, 1600)
    assert dispatcher.length_band("甲" * 400, "recommended") == (1600, 2400)
    assert dispatcher.length_band("短要求", "mandatory") == (1600, 2400)
    assert dispatcher.length_band("", "MANDATORY") == (1600, 2400)  # 大小写不敏感


@pytest.fixture
def capture_stream(monkeypatch):
    """拦下 clients.stream_openai，收集实际发出的 prompt。"""
    captured: list[dict] = []

    async def _fake_stream(config, system_prompt, user_prompt, **kw):
        captured.append({"system": system_prompt, "user": user_prompt})
        yield "正文"

    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.setattr("infra.llm.dispatcher.clients.stream_openai", _fake_stream)
    return captured


@pytest.mark.asyncio
async def test_block_write_without_source_says_requirement_not_expand(capture_stream):
    """M6：自动路径不传规范书原文，就不该要求「按原文逐段扩写」。

    dispatch_block_write 的 original_content 恒为空 —— 旧提示词却写着
    「以下是技术规范书中关于「X」的原文内容结构」+「严格按原文逐段扩写」，
    正文里根本没有原文可对，模型只能自由发挥。
    """
    from infra.llm import dispatcher

    async for _ in dispatcher.dispatch_block_write(
        configs=[_fake_config()], rr_start_index=0, title="系统架构设计",
        requirement="提供系统架构设计", chunks=[], target_words=1600, max_words=2400,
    ):
        pass

    prompt = capture_stream[0]["user"]
    assert "请撰写下面给出的章节的投标应答正文" in prompt
    assert "【章节】系统架构设计" in prompt
    assert "忠于要求" in prompt
    assert "逐段扩写" not in prompt
    assert "以下是技术规范书中" not in prompt


@pytest.mark.asyncio
async def test_block_write_prompt_has_length_band_and_stop_rule(capture_stream):
    """M2：给上限时写入区间与收束要求，且不再出现「不少于」。"""
    from infra.llm import dispatcher

    async for _ in dispatcher.dispatch_block_write(
        configs=[_fake_config()], rr_start_index=0, title="总体方案",
        requirement="提供总体方案", chunks=[], target_words=1000, max_words=1600,
    ):
        pass

    prompt = capture_stream[0]["user"]
    assert "正文长度要求：1000–1600 字" in prompt
    assert "必须收束" in prompt
    assert "不少于" not in prompt


@pytest.mark.asyncio
async def test_stream_generate_with_source_keeps_expand_wording(capture_stream):
    """有原文（工作台手动路径）时保持「逐段扩写」语义与旧措辞。

    未给 max_words → 沿用「不少于 N 字」，不改手动路径行为。
    """
    from infra.llm import dispatcher

    async for _ in dispatcher.dispatch_stream_generate(
        configs=[_fake_config()], rr_start_index=0, section_title="总体要求",
        original_content="原文内容", target_words=600,
    ):
        pass

    prompt = capture_stream[0]["user"]
    assert "请按下面给出的原文，逐段扩写本节的投标应答正文" in prompt
    assert "以下是技术规范书中关于「总体要求」的原文内容结构" in prompt
    assert "忠于原意" in prompt
    assert "扩写后内容字数不少于 600 字" in prompt


@pytest.mark.asyncio
async def test_outline_draft_salvage_marks_truncation(monkeypatch):
    """抢救出来的目录必须带「被截断」标记，否则用户看到的是「派生完成」。

    部分成功不标 = 用户只会发现目录里少了几节，不知道是模型输出被 max_tokens 截断。
    """
    from infra.llm import dispatch_outline_draft_json
    from infra.llm import clients

    async def _truncated(config, system, user, **kw):
        # 尾部未闭合 → salvage 抢救出前缀
        return '{"nodes": [{"level": 1, "title": "A"}, {"level": 1, "title": "B"}, {"level": 1, "tit'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _truncated)

    obj = await dispatch_outline_draft_json([_fake_config()], 0, {"spec_digest": "x"})

    assert [n["title"] for n in obj["nodes"]] == ["A", "B"]
    assert obj["_truncated_nodes"] == 2


@pytest.mark.asyncio
async def test_extract_skips_empty_content_without_calling_model(monkeypatch):
    """没有原文的章节（用户新增 / 纯目录节点）不发模型调用。

    旧行为把空内容块（`---\\n\\n---`）发过去：白花一次调用，还可能让模型凭空编出
    requirement / veto_items —— 那些假要求会流进素材匹配、生成与评审。
    """
    from infra.llm import dispatch_outline_json
    from infra.llm import clients

    called = {"n": 0}

    async def _spy(config, system, user, **kw):
        called["n"] += 1
        return '{"requirement": "编出来的要求"}'

    monkeypatch.setattr(clients, "generate_oneshot_openai", _spy)

    results = []
    async for item in dispatch_outline_json(
        [_fake_config()], 0, [{"title": "用户新增的章节", "content": "   "}],
    ):
        results.append(item)

    assert called["n"] == 0, "空内容不该调用模型"
    assert results[0]["requirement"] == ""
    assert "没有可提炼的原文" in results[0]["error"]
