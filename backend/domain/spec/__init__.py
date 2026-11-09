"""规范书聚合：Section、OutlineMatrixRow、目录派生纯函数。"""

from .models import OutlineMatrixRow, Section
from .outline_draft import (
    DEFAULT_MAX_NODES,
    MAX_LEVEL,
    MIN_TOP_LEVEL_NODES,
    NormalizedNode,
    build_sections,
    count_top_level,
    normalize_nodes,
)
__all__ = [
    "Section",
    "OutlineMatrixRow",
    "NormalizedNode",
    "normalize_nodes",
    "build_sections",
    "count_top_level",
    "MAX_LEVEL",
    "DEFAULT_MAX_NODES",
    "MIN_TOP_LEVEL_NODES",
]
