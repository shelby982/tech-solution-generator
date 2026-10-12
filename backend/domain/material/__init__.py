"""素材聚合：Material、Chunk、MaterialEvidence、MaterialRepository、匹配总览。"""

from .models import (
    Chunk,
    Material,
    MaterialEvidence,
    KIND_FACT,
    KIND_SOLUTION,
    KIND_BACKGROUND,
    SOURCE_OURS,
    SOURCE_TENDER,
    BY_RULE,
    BY_SEMANTIC,
    BY_MODEL,
    BY_HUMAN,
)
from .overview import build_match_overview, evidence_from_chunk
from .repository import MaterialRepository

__all__ = [
    "Material",
    "Chunk",
    "MaterialEvidence",
    "MaterialRepository",
    "KIND_FACT",
    "KIND_SOLUTION",
    "KIND_BACKGROUND",
    "SOURCE_OURS",
    "SOURCE_TENDER",
    "BY_RULE",
    "BY_SEMANTIC",
    "BY_MODEL",
    "BY_HUMAN",
    "build_match_overview",
    "evidence_from_chunk",
]
