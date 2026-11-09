"""提示词「稳定前缀」契约测试（M5 缓存优化）。

背景（2026-09-20 实测）：上下文缓存按**共同前缀**命中，而原实现把稳定长指令放在
user prompt 的**末尾**、开头放变化的标题与正文 —— `build_outline_extract_user` 的
前缀第 10 个字就是章节标题，于是命中率只有 21.5%（extract）/ 29%（正文生成）。

修法是把稳定指令块前置、动态内容后置。这里锁住这个结构：一旦有人把动态字段挪回
前面，缓存收益会静默消失（功能测试全绿、只有账单变贵），所以必须有测试盯着。
"""

import pytest

from agents.prompts import (
    OUTLINE_EXTRACT_INSTRUCTIONS,
    SECTION_OUTLINE_INSTRUCTIONS,
    build_outline_extract_user,
    build_section_outline_user,
)


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


# ─────────────────────────────────────────────
# 稳定块本身不能太短（太短则缓存收益可忽略）
# ─────────────────────────────────────────────

def test_stable_instruction_blocks_are_substantial():
    """稳定块是缓存收益的来源；被改短等于收益消失。"""
    assert len(OUTLINE_EXTRACT_INSTRUCTIONS) > 800
    assert len(SECTION_OUTLINE_INSTRUCTIONS) > 400


# ─────────────────────────────────────────────
# 各 builder：不同章节必须共享同一段前缀
# ─────────────────────────────────────────────

def test_extract_prompt_shares_stable_prefix_across_sections():
    a = build_outline_extract_user(title="第一章 总体要求", content="甲" * 50)
    b = build_outline_extract_user(
        title="第二章 技术架构", content="乙" * 50,
        scoring_context="评分表内容", special_marks="★",
    )

    assert a.startswith(OUTLINE_EXTRACT_INSTRUCTIONS)
    assert b.startswith(OUTLINE_EXTRACT_INSTRUCTIONS)
    # 共同前缀至少覆盖整个稳定块（含块尾的分隔），标题/正文/上下文一律在其后
    assert _common_prefix_len(a, b) >= len(OUTLINE_EXTRACT_INSTRUCTIONS)
    # 动态内容确实还都在
    assert "第一章 总体要求" in a and "第二章 技术架构" in b
    assert "评分表内容" in b and "否决条款" in b


def test_section_outline_prompt_shares_stable_prefix_across_blocks():
    a = build_section_outline_user({"title": "技术方案", "requirement": "X"}, extra_context="素材A")
    b = build_section_outline_user({"title": "实施方案", "requirement": "Y"}, extra_context="素材B")

    assert a.startswith(SECTION_OUTLINE_INSTRUCTIONS)
    assert b.startswith(SECTION_OUTLINE_INSTRUCTIONS)
    assert _common_prefix_len(a, b) >= len(SECTION_OUTLINE_INSTRUCTIONS)
    # 标题不再嵌在首行（嵌了就会让前缀第一个变量处即分叉）
    assert not SECTION_OUTLINE_INSTRUCTIONS.startswith("请为「")
    assert "技术方案" in a and "实施方案" in b


# ─────────────────────────────────────────────
# 正文生成：稳定段（任务陈述 + 6 条核心规则）在所有章节间逐字相同
# ─────────────────────────────────────────────

@pytest.fixture
def capture_gen_prompt(monkeypatch):
    """跑一次 dispatch_block_write，返回实际发出的 user prompt。"""
    captured: list[str] = []

    async def _fake_stream(config, system_prompt, user_prompt, **kw):
        captured.append(user_prompt)
        yield "正文"

    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.setattr("infra.llm.dispatcher.clients.stream_openai", _fake_stream)

    async def _run(title: str, requirement: str):
        from infra.llm import dispatcher

        async for _ in dispatcher.dispatch_block_write(
            configs=[_cfg()], rr_start_index=0, title=title,
            requirement=requirement, chunks=[],
            target_words=1000, max_words=1600,
        ):
            pass
        return captured[-1]

    return _run


def _cfg():
    from services.config_store import LLMConfig

    return LLMConfig(
        provider="openai", api_key="sk-fake",
        base_url="https://example.invalid/v1", model="gpt-test",
    )


@pytest.mark.asyncio
async def test_generation_prompt_stable_head_is_identical_across_blocks(capture_gen_prompt):
    a = await capture_gen_prompt("系统架构设计", "提供系统架构")
    b = await capture_gen_prompt("售后服务方案", "提供售后承诺")

    # 稳定段：任务陈述 + 核心规则（含 6 条）—— 两章逐字相同，实测 569 字
    stable_len = _common_prefix_len(a, b)
    assert stable_len >= 500, f"共同前缀只有 {stable_len} 字，缓存收益太小"
    assert "核心规则" in a[:stable_len]
    # 分叉点必须正好落在动态区起点：【章节】标记之后紧跟各自的标题
    assert a[:stable_len].endswith("【章节】")
    assert a[stable_len:].startswith("系统架构设计")
    assert b[stable_len:].startswith("售后服务方案")


@pytest.mark.asyncio
async def test_generation_prompt_keeps_core_rules_before_dynamic_parts(capture_gen_prompt):
    prompt = await capture_gen_prompt("系统架构设计", "提供系统架构")

    rules_at = prompt.index("核心规则")
    assert prompt.index("【章节】") > rules_at          # 标题在规则之后
    assert prompt.index("正文长度要求") > rules_at       # 长度要求（动态数字）在规则之后
    # 要求/素材块（dispatch_block_write 以「额外优化要求：」拼在末尾）在最后
    assert prompt.index("额外优化要求") > rules_at
