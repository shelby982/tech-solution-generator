"""沈括 — 应标素材匹配专家 agent。

职责（spec §3）：
对 toc 中每个 block，用 (requirement + title) 关键词检索 top-10 候选 chunks，
再用 LLM 重排取 top-5，输出 {block_id: list[Match]}。

策略：
- 全 corpus BM25 索引一次性构建，避免每 block 重建
- 单 block 重排失败由 llm_rerank 内部兜底为 [] — 不影响其它 block
- chunks 为空 / toc 为空 / 无 LLM 配置 → 返回空 dict 或全空 list，不抛错
"""

import logging
from typing import Callable, Optional

from domain.spec import OutlineMatrixRow, Section
from infra.retrieval import Match, keyword_search, llm_rerank
from infra.retrieval.keyword import build_bm25_index

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────

class ShenKuoAgent:
    """沈括：素材匹配 + LLM 重排。"""

    def __init__(
        self,
        configs_provider: Optional[Callable[[], tuple[list, int]]] = None,
        *,
        keyword_top_k: int = 10,
        rerank_top_n: int = 5,
    ):
        """
        configs_provider: 返回 (configs, rr_start_index) 的函数。
        默认从 services.config_store 读，便于单测注入 fixture configs。
        """
        if configs_provider is None:
            from services.config_store import config_store
            self._configs_provider = config_store.get_configs_and_next_index
        else:
            self._configs_provider = configs_provider
        self.keyword_top_k = keyword_top_k
        self.rerank_top_n = rerank_top_n

    # ── match ──────────────────────────────────

    async def match(
        self,
        toc: list[Section],
        outline_matrix: dict[str, OutlineMatrixRow],
        chunks: list[dict],
    ) -> dict[str, list[Match]]:
        """为 toc 中每个 block 匹配 top-N 素材。

        - 空 toc → 返回 {}
        - 空 chunks → toc 中每个 section.id 对应空 list
        - 无 LLM 配置 → 仅 keyword 截断，score=0、reason 标识未重排
        - 单 block llm_rerank 抛错 → 该 block 置空，不影响其它 block

        返回的 dict 在 toc 非空时总是覆盖 toc 中每个 section.id。
        """
        if not toc:
            return {}

        # 空 chunks 短路：所有 block 返空匹配
        if not chunks:
            return {s.id: [] for s in toc}

        configs, rr_start = self._configs_provider()

        # 一次性构建 BM25 索引，多 block 复用
        bm25_index, _ = build_bm25_index(chunks)

        result: dict[str, list[Match]] = {}
        for idx, section in enumerate(toc):
            row = outline_matrix.get(section.id)
            requirement = (row.requirement if row else "") or ""
            query = f"{section.title} {requirement}".strip()

            # 关键词检索 top-k
            candidates = keyword_search(
                chunks,
                query=query,
                top_k=self.keyword_top_k,
                bm25_index=bm25_index,
            )

            if not candidates:
                result[section.id] = []
                continue

            if not configs:
                # 无 LLM 配置：直接把 keyword_search 的 top_k 截到 top_n 作为兜底
                # 给一个保守的 score=0 标识此结果未经 LLM 重排
                result[section.id] = [
                    Match(
                        chunk_id=c.get("id") or c.get("chunk_id"),
                        score=0.0,
                        reason="(未配置 LLM，仅关键词检索结果)",
                        hit_points=[],
                    )
                    for c in candidates[: self.rerank_top_n]
                ]
                continue

            # LLM 重排 top-n（每 block 错开 rr_index 散开 LLM 调用起点）
            try:
                matches = await llm_rerank(
                    candidates,
                    query=query,
                    requirement=requirement,
                    top_n=self.rerank_top_n,
                    configs=configs,
                    rr_start_index=(rr_start + idx) % max(len(configs), 1),
                )
            except Exception as e:
                logger.warning(
                    f"沈括：block {section.id} 重排异常，匹配置空：{e}"
                )
                matches = []

            result[section.id] = matches

        return result


__all__ = ["ShenKuoAgent"]
