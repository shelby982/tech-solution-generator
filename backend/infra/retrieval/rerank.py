"""infra/retrieval/rerank.py — 沈括 agent 用的 LLM 重排。

接 keyword_search 返回的 top-K 候选，让 LLM 给每个 chunk 打 0-10 分 + 命中要点，
取 top_n 输出 Match[]。失败时返回空列表，不抛错（让上游决定降级策略）。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from infra.llm import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS
from infra.llm.clients import generate_oneshot_openai, generate_oneshot_claude

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────

@dataclass
class Match:
    """单条素材重排后的匹配结果。Phase 2 会移到 domain/material/models.py。"""
    chunk_id: object
    score: float
    reason: str
    hit_points: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────

_RERANK_SYSTEM_PROMPT = (
    "你是技术应标素材匹配专家。给定一条应标要求与若干候选素材片段，"
    "你需要为每个素材打 0-10 分（10=高度相关，0=完全无关），"
    "并给出简短理由与命中的需求要点。\n"
    "只输出合法 JSON 对象，不要任何额外说明或 markdown 围栏。"
)


def _build_user_prompt(chunks: list[dict], query: str, requirement: str) -> str:
    lines = []
    for i, c in enumerate(chunks, start=1):
        cid = c.get("chunk_id", c.get("id", i))
        content = (c.get("content") or "")[:600]
        lines.append(f"[{i}] chunk_id={cid}\n{content}")
    chunks_block = "\n\n".join(lines)

    return (
        f"【应标要求】\n{requirement}\n\n"
        f"【检索查询】\n{query}\n\n"
        f"【候选素材片段】\n{chunks_block}\n\n"
        '请输出形如 {"matches":[{"chunk_id":...,"score":0-10,"reason":"...","hit_points":["..."]}]} 的 JSON。'
        "score 越高越相关，hit_points 列出该素材命中的应标要点；"
        "无关素材也要列出（score 设低分）。"
    )


# ─────────────────────────────────────────────
# JSON 抽取（最小版本，避免依赖 dispatcher 内部 helper）
# ─────────────────────────────────────────────

def _extract_json_object(text: str) -> dict:
    """从模型返回里抽出第一个完整 JSON 对象，剥代码围栏。"""
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()

    start = s.find("{")
    if start == -1:
        raise ValueError(f"未找到 JSON 起始 {{：{text[:120]}")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError(f"JSON 对象未闭合：{text[:120]}")


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

    user_prompt = _build_user_prompt(chunks, query, requirement)
    last_error: Optional[Exception] = None

    for i in range(n):
        config = configs[(rr_start_index + i) % n]
        try:
            if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                raw = await generate_oneshot_openai(
                    config, _RERANK_SYSTEM_PROMPT, user_prompt, max_tokens=2000,
                )
            else:
                raw = await generate_oneshot_claude(
                    config, _RERANK_SYSTEM_PROMPT, user_prompt, max_tokens=2000,
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
            logger.warning(
                f"llm_rerank API [{config.provider}/{config.model}] 失败"
                f"（{i + 1}/{n}）：{e}"
            )

    logger.error(f"llm_rerank 全部 {n} 个 API 均失败：{last_error}")
    return []
