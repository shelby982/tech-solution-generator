"""沈括 — 应标素材匹配专家 agent。

职责（spec §3）：
对 toc 中每个 block，用 (requirement + title) 关键词检索 top-10 候选 chunks，
再用 LLM 重排取 top-5，输出 {block_id: list[Match]}。

策略：
- 全 corpus BM25 索引一次性构建，避免每 block 重建
- 单 block 重排失败由 llm_rerank 内部兜底为 [] — 不影响其它 block
- chunks 为空 / toc 为空 / 无 LLM 配置 → 返回空 dict 或全空 list，不抛错
"""

import inspect
import logging
from typing import Awaitable, Callable, Optional

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
        *,
        on_section: Optional[
            Callable[[Section, list[Match]], Optional[Awaitable[None]]]
        ] = None,
        on_section_start: Optional[
            Callable[[Section], Optional[Awaitable[None]]]
        ] = None,
    ) -> dict[str, list[Match]]:
        """为 toc 中每个 block 匹配 top-N 素材。

        - 空 toc → 返回 {}
        - 空 chunks → toc 中每个 section.id 对应空 list
        - 无 LLM 配置 → 仅 keyword 截断，score=0、reason 标识未重排
        - 单 block llm_rerank 抛错 → 该 block 置空，不影响其它 block

        on_section / on_section_start：让节点层逐 block 流式推 SSE 事件。

        返回的 dict 在 toc 非空时总是覆盖 toc 中每个 section.id。
        """
        if not toc:
            return {}

        async def _fire(section: Section, matches: list[Match]) -> None:
            if on_section is None:
                return
            try:
                rv = on_section(section, matches)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"沈括：on_section 回调失败（{section.id}）：{e}")

        async def _fire_start(section: Section) -> None:
            if on_section_start is None:
                return
            try:
                rv = on_section_start(section)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"沈括：on_section_start 回调失败（{section.id}）：{e}")

        # 空 chunks 短路：所有 block 返空匹配
        if not chunks:
            result: dict[str, list[Match]] = {}
            for s in toc:
                await _fire_start(s)
                result[s.id] = []
                await _fire(s, [])
            return result

        configs, rr_start = self._configs_provider()

        # 一次性构建 BM25 索引，多 block 复用
        bm25_index, _ = build_bm25_index(chunks)

        result: dict[str, list[Match]] = {}
        for idx, section in enumerate(toc):
            await _fire_start(section)
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
                await _fire(section, [])
                continue

            if not configs:
                # 无 LLM 配置：直接把 keyword_search 的 top_k 截到 top_n 作为兜底
                # 给一个保守的 score=0 标识此结果未经 LLM 重排
                fallback = [
                    Match(
                        chunk_id=c.get("id") or c.get("chunk_id"),
                        score=0.0,
                        reason="(未配置 LLM，仅关键词检索结果)",
                        hit_points=[],
                    )
                    for c in candidates[: self.rerank_top_n]
                ]
                result[section.id] = fallback
                await _fire(section, fallback)
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
            await _fire(section, matches)

        return result

    # ── retrieve_for ───────────────────────────

    async def retrieve_for(
        self,
        requests_by_block: dict[str, list[dict]],
        chunks: list[dict],
    ) -> dict[str, list[Match]]:
        """按补料请求定向检索素材 —— 收集角色的按需服务入口。

        requests_by_block: {block_id: [{"query": str, "reason": str}, ...]}
        chunks:            全语料切片

        返回 {block_id: list[Match]}，只为真正检索到结果的 block 建键。
        空入参 → 返回 {}。BM25 索引对本批次只建一次。

        单条请求检索/重排失败只跳过该请求，不影响其它请求与其它 block。
        """
        if not requests_by_block or not chunks:
            return {}

        configs, rr_start = self._configs_provider()
        bm25_index, _ = build_bm25_index(chunks)

        result: dict[str, list[Match]] = {}
        idx = 0

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
                rr_index = (rr_start + idx) % max(len(configs), 1)
                idx += 1

                if not candidates:
                    continue

                if not configs:
                    # 与 match() 的降级一致：关键词 top_n 截断，score=0 标识未重排
                    for c in candidates[: self.rerank_top_n]:
                        cid = c.get("id") or c.get("chunk_id")
                        if cid not in merged:
                            merged[cid] = Match(
                                chunk_id=cid,
                                score=0.0,
                                reason="(未配置 LLM，仅关键词检索结果)",
                                hit_points=[],
                            )
                    continue

                try:
                    matches = await llm_rerank(
                        candidates,
                        query=query,
                        requirement=(req.get("reason") or query),
                        top_n=self.rerank_top_n,
                        configs=configs,
                        rr_start_index=rr_index,
                    )
                except Exception as e:
                    logger.warning(
                        f"沈括：补料请求 {query!r}（{block_id}）重排异常，跳过：{e}"
                    )
                    continue

                # 同一 chunk 被多条请求命中时保留高分
                for m in matches:
                    existing = merged.get(m.chunk_id)
                    if existing is None or m.score > existing.score:
                        merged[m.chunk_id] = m

            if merged:
                result[block_id] = sorted(
                    merged.values(), key=lambda x: x.score, reverse=True,
                )

        return result


__all__ = ["ShenKuoAgent"]
