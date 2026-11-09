"""沈括 — 应标素材检索专家 agent。

职责（spec §3；2026-11-09 起只保留按需检索）：
- ``retrieve_for``：按补料请求做 BM25 关键词检索，返回 ``Match`` 列表。
- 旧的「逐章 BM25 top-k + LLM 重排」匹配路径已删除；生产匹配默认走
  ``assign`` 归属（规则 + 语义，零模型调用，见 ``infra/retrieval/assign.py``）。

本 agent 在编排里仍被 ``collect_gaps`` 节点用于「按补料请求定向检索素材」。
"""

import logging

from domain.material.models import Match
from infra.retrieval.keyword import build_bm25_index, keyword_search

logger = logging.getLogger(__name__)


class ShenKuoAgent:
    """沈括：素材按需检索（BM25 关键词，无模型调用）。"""

    def __init__(self, *, keyword_top_k: int = 10, top_n: int = 5):
        self.keyword_top_k = keyword_top_k
        self.top_n = top_n

    # ── retrieve_for ───────────────────────────

    async def retrieve_for(
        self,
        requests_by_block: dict[str, list[dict]],
        chunks: list[dict],
    ) -> dict[str, list[Match]]:
        """按补料请求定向检索素材 —— 收集角色的按需服务入口。

        ``requests_by_block``: ``{block_id: [{"query": str, "reason": str}, ...]}``
        ``chunks``:             全语料切片

        返回 ``{block_id: list[Match]}``，只为真正检索到结果的 block 建键。
        空入参 → 返回 ``{}``。BM25 索引对本批次只建一次；同一 chunk 被多条
        请求命中时只保留一条（关键词路径没有分数，取首次命中）。
        """
        if not requests_by_block or not chunks:
            return {}

        bm25_index, _ = build_bm25_index(chunks)

        result: dict[str, list[Match]] = {}
        for block_id, requests in requests_by_block.items():
            merged: dict[object, Match] = {}
            for req in (requests or []):
                query = (req.get("query") or "").strip()
                if not query:
                    continue

                candidates = keyword_search(
                    chunks, query=query, top_k=self.keyword_top_k,
                    bm25_index=bm25_index,
                )
                for c in candidates[: self.top_n]:
                    cid = c.get("id") or c.get("chunk_id")
                    if cid is None or cid in merged:
                        continue
                    merged[cid] = Match(
                        chunk_id=cid,
                        score=0.0,
                        reason="(仅关键词检索结果)",
                        hit_points=[],
                    )

            if merged:
                result[block_id] = list(merged.values())

        return result


__all__ = ["ShenKuoAgent"]
