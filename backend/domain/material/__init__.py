"""素材聚合：Material、Chunk、MaterialEvidence、匹配总览。"""

from .models import (
    Chunk,
    Material,
    MaterialEvidence,
    Match,
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

__all__ = [
    "Material",
    "Chunk",
    "MaterialEvidence",
    "Match",
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
