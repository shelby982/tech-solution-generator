"""infra/retrieval/rerank.py — 沈括 agent 用的 LLM 重排。

接 keyword_search 返回的 top-K 候选，让 LLM 给每个 chunk 打 0-10 分 + 命中要点，
取 top_n 输出 Match[]。失败时返回空列表，不抛错（让上游决定降级策略）。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from agents.prompts import RERANK_SYSTEM, build_rerank_user
from infra.llm import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS
from infra.llm import errors as llm_errors
from infra.llm.clients import generate_oneshot_openai, generate_oneshot_claude
from infra.llm.json_utils import extract_json_object as _extract_json_object

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────

@dataclass
class Match:
    """单条素材匹配结果。

    ``content`` 及其后面的字段是"匹配阶段 → 撰写阶段"契约的一部分（见 MaterialEvidence）：
    **撰写阶段必须拿到素材原文**，而不是只有一句 rerank 评语（``reason``）。
    旧数据没有这些字段，所以全部可选、默认空。
    """
    chunk_id: object
    score: float
    reason: str
    hit_points: list[str] = field(default_factory=list)
    content: str = ""            # 素材原文（撰写阶段引用它）
    filename: str = ""           # 出处：文件名
    locator: str = ""            # 出处：第 N 段（P1 升级为页码/锚点）
    kind: str = ""               # fact | solution | background
    source_kind: str = ""        # ours | tender
    by: str = ""                 # rule | semantic | model | human
    confidence: float = 0.0
    locked: bool = False


# ─────────────────────────────────────────────
# 公开入口
# ─────────────────────────────────────────────

async def llm_rerank(
    chunks: list[dict],
    query: str,
    requirement: str,
    *,
    top_n: int = 5,
    configs: list[LLMConfig],
    rr_start_index: int = 0,
) -> list[Match]:
    """
    用 LLM 对候选 chunks 进行重排，按 score 降序取 top_n。

    chunks: keyword_search 返回的 top-K 候选，每条至少有 chunk_id 与 content
    query: 检索拼接（title + requirement）
    requirement: 该 block 的应标要求原文，让 LLM 判断 hit_points
    configs: 可用 LLM 配置列表，按 rr_start_index 起轮询 + fallback
    返回：Match[]，按 score 降序，长度 ≤ top_n。任意原因失败均返回 []。
    """
    if not chunks:
        return []
    n = len(configs)
    if n == 0:
        logger.warning("llm_rerank 未配置任何 LLM，返回空列表")
        return []

    user_prompt = build_rerank_user(chunks, query, requirement)
    last_error: Optional[Exception] = None

    usable = llm_errors.available_configs(configs, rr_start_index)
    for i, config in enumerate(usable):
        try:
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                raw = await generate_oneshot_openai(
                    config, RERANK_SYSTEM, user_prompt, max_tokens=2000,
                    call_site="rerank",
                )
            else:
                raw = await generate_oneshot_claude(
                    config, RERANK_SYSTEM, user_prompt, max_tokens=2000,
                    call_site="rerank",
                )

            obj = _extract_json_object(raw)
            raw_matches = obj.get("matches", []) or []

            matches: list[Match] = []
            for m in raw_matches:
                try:
                    score = float(m.get("score", 0))
                except (TypeError, ValueError):
                    score = 0.0
                hit_points = m.get("hit_points") or []
                if not isinstance(hit_points, list):
                    hit_points = [str(hit_points)]
                matches.append(Match(
                    chunk_id=m.get("chunk_id"),
                    score=score,
                    reason=str(m.get("reason", "")),
                    hit_points=[str(p) for p in hit_points],
                ))

            matches.sort(key=lambda x: x.score, reverse=True)
            return matches[:top_n]

        except Exception as e:
            last_error = e
            kind = llm_errors.note_failure(config, e)
            logger.warning(
                f"llm_rerank API [{config.provider}/{config.model}] 失败"
                f"（{i + 1}/{len(usable)}，{llm_errors.describe_error(e)}）：{e}"
            )
            if llm_errors.blocks_chain(kind):
                break

    logger.error(
        f"llm_rerank 无可用 API：{llm_errors.exhausted_summary(configs, last_error)}"
    )
    return []
