"""infra/retrieval 公开 API：

- keyword_search：BM25 + jieba 关键词检索（移自 services/retrieval.py）
- assign_chunks_to_sections：反向分配（chunk → section_idx）
- assign_evidence：**素材 → 章节 归属**（匹配阶段 P0 主线；规则 + 语义，零模型调用），
  产出 domain.material.MaterialEvidence（带原文/出处/性质/来源）
"""

from .assign import (
    AssignResult,
    allows_tender_evidence,
    assign_evidence,
    build_section_texts,
    classify_kind,
    is_tender_role,
    normalize_title,
)
from .keyword import keyword_search, build_bm25_index, assign_chunks_to_sections

__all__ = [
    "keyword_search",
    "build_bm25_index",
    "assign_chunks_to_sections",
    "assign_evidence",
    "AssignResult",
    "build_section_texts",
    "classify_kind",
    "is_tender_role",
    "allows_tender_evidence",
    "normalize_title",
]
