"""infra/retrieval 公开 API：

- keyword_search：BM25 + jieba 关键词检索（移自 services/retrieval.py）
- llm_rerank：沈括 agent 用的 LLM 重排
- Match：重排结果 dataclass（Phase 2 会移到 domain/material/models.py）
"""

from .keyword import keyword_search, build_bm25_index
from .rerank import llm_rerank, Match

__all__ = [
    "keyword_search",
    "build_bm25_index",
    "llm_rerank",
    "Match",
]
