"""
LLM mock 实现：按 prompt 关键词返回 fixture，避免测试 / 演示环境真的发起网络调用。
仅供 infra/llm/clients.py 使用，外部不要直接 import。

启用方式：环境变量 `LLM_MODE=mock`
"""

import asyncio
import json
from typing import AsyncIterator


# ─────────────────────────────────────────────
# Fixture
# ─────────────────────────────────────────────

_OUTLINE_FIXTURE: dict = {
    "requirement": "本章节核心技术要求示例（mock）",
    "key_points": "针对每条要求给出响应建议（mock）",
    "veto_items": "硬性约束示例（mock）",
    "bonus_items": "加分项示例（mock）",
    "score_items": "评分项 X 分（mock）",
    "evidence_required": "需提供证明材料（mock）",
    "constraint_level": "recommended",
    "indicators": "量化指标示例（mock）",
}


_LETTER_FIXTURE = (
    "# 投标承诺书\n\n"
    "致：【招标人/采购人名称】\n\n"
    "我方郑重承诺，将严格遵守招标文件各项要求，确保产品质量、交付工期与售后服务到位（mock 内容）。\n\n"
    "一、严格按合同约定按期保质完成各项交付工作。\n"
    "二、提供完善的售后服务与技术支持。\n"
    "三、自觉遵守廉洁与诚信相关规定。\n\n"
    "落款：【公司全称】\n"
    "法定代表人/授权代表（签字）：【法定代表人】\n"
    "（盖章处）\n"
    "日期：【签署日期】\n"
)


_REVIEW_FIXTURE: dict = {
    "score": 85,
    "issues": ["示例问题 1（mock）", "示例问题 2（mock）"],
    "strengths": ["示例亮点 1（mock）", "示例亮点 2（mock）"],
}


# ─────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────

def _select_fixture(system_prompt: str, user_prompt: str) -> str:
    """根据 prompt 关键词选择 fixture。匹配优先级：大纲 > 公文 > 评审 > 默认。"""
    haystack = f"{system_prompt}\n{user_prompt}"

    if "提炼以下八项内容" in haystack:
        return json.dumps(_OUTLINE_FIXTURE, ensure_ascii=False)

    if "承诺书" in haystack:
        return _LETTER_FIXTURE

    if "评审" in haystack:
        return json.dumps(_REVIEW_FIXTURE, ensure_ascii=False)

    return "[mock response] " + (user_prompt or "")[:80]


async def _stream_chunks(text: str, *, n_chunks: int = 5) -> AsyncIterator[str]:
    """把整段文本切成 n_chunks 份，模拟流式输出。"""
    if not text:
        return
    n = max(1, n_chunks)
    size = max(1, len(text) // n)
    pos = 0
    while pos < len(text):
        chunk = text[pos: pos + size]
        pos += size
        # 让出事件循环，模拟真实流式延迟
        await asyncio.sleep(0)
        yield chunk


# ─────────────────────────────────────────────
# 公开入口
# ─────────────────────────────────────────────

async def respond(
    system_prompt: str,
    user_prompt: str,
    *,
    stream: bool,
    max_tokens: int = 1500,
):
    """
    mock 路由：按 prompt 关键词返回 fixture。
    - stream=True  → 返回 AsyncIterator[str]
    - stream=False → 返回 str

    max_tokens 仅作截断保护，与真实模型行为对齐。
    """
    text = _select_fixture(system_prompt, user_prompt)
    if max_tokens and len(text) > max_tokens * 4:
        # 粗略按 4 字符 / token 估算
        text = text[: max_tokens * 4]

    if stream:
        return _stream_chunks(text)
    return text
