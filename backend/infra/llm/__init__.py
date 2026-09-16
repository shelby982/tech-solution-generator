"""
infra/llm 公开 API：
- LLMConfig / OPENAI_COMPATIBLE_PROVIDERS（从 services.config_store 转出）
- verify_api_key
- dispatch_stream_generate / dispatch_doc_summary / dispatch_outline_json / dispatch_block_write
- dispatch_outline_draft_json
- generate_section_outline / generate_letter_content
- is_letter_section / LETTER_KEYWORDS

Phase 1 阶段，LLMConfig 仍由 services/config_store.py 维护；
Phase 7 会把存储层迁过来后再断开此处依赖。
"""

from services.config_store import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS

from .dispatcher import (
    LETTER_KEYWORDS,
    dispatch_block_write,
    dispatch_doc_summary,
    dispatch_outline_draft_json,
    dispatch_outline_json,
    dispatch_stream_generate,
    generate_letter_content,
    generate_section_outline,
    is_letter_section,
    verify_api_key,
)

__all__ = [
    "LLMConfig",
    "OPENAI_COMPATIBLE_PROVIDERS",
    "LETTER_KEYWORDS",
    "verify_api_key",
    "dispatch_stream_generate",
    "dispatch_doc_summary",
    "dispatch_outline_draft_json",
    "dispatch_outline_json",
    "dispatch_block_write",
    "generate_section_outline",
    "generate_letter_content",
    "is_letter_section",
]
