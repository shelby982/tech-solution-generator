"""方案聚合的领域模型 — Block / BlockOutput / Source。

对应 spec §5 ProposalState 中的产出类型，持久化到 blocks 表。
"""

import json
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────
# Source
# ─────────────────────────────────────────────────────────

@dataclass
class Source:
    """诸葛亮生成时引用的素材片段（对应 retrieve_chunks 输出）。

    持久化为 blocks.source 列的 JSON 数组元素。
    """
    material_id: int
    chunk_index: int
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "material_id": self.material_id,
            "chunk_index": self.chunk_index,
            "snippet": self.snippet,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        return cls(
            material_id=int(d["material_id"]),
            chunk_index=int(d["chunk_index"]),
            snippet=d.get("snippet", ""),
        )


# ─────────────────────────────────────────────────────────
# BlockOutput
# ─────────────────────────────────────────────────────────

@dataclass
class BlockOutput:
    """诸葛亮 agent 对单个 block 的产出（对应 spec §5）。

    持久化时：content / source 写到 blocks 表。outline / needs_diagram
    暂不持久化（Phase 2 仅持久化 final content；outline 是工作流中间态由
    LangGraph state 管理）。
    """
    block_id: str
    kind: str = "tech"           # tech | letter
    needs_diagram: bool = False
    outline: str = ""            # 写作大纲（工作流中间态）
    content: str = ""            # 最终正文
    sources: list[Source] = field(default_factory=list)
    material_requests: list[dict] = field(default_factory=list)  # [{query, reason}]，工作流中间态

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "kind": self.kind,
            "needs_diagram": self.needs_diagram,
            "outline": self.outline,
            "content": self.content,
            "sources": [s.to_dict() for s in self.sources],
            "material_requests": [dict(r) for r in self.material_requests],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BlockOutput":
        return cls(
            block_id=d["block_id"],
            kind=d.get("kind", "tech"),
            needs_diagram=bool(d.get("needs_diagram", False)),
            outline=d.get("outline", ""),
            content=d.get("content", ""),
            sources=[Source.from_dict(s) for s in d.get("sources", [])],
            material_requests=[
                dict(r) for r in d.get("material_requests", []) if isinstance(r, dict)
            ],
        )


# ─────────────────────────────────────────────────────────
# Block
# ─────────────────────────────────────────────────────────

@dataclass
class Block:
    """blocks 表的领域视图：toc 元数据 + outline_matrix + content + sources。

    完整反映 blocks 表的字段。Spec 阶段产出 toc + matrix 字段；
    Proposal 阶段在此基础上填充 content + sources + status。
    """
    id: int                          # blocks.id（数据库主键）
    project_id: int
    block_id: str                    # 业务 id，如 "s1"
    title: str
    level: int = 1
    kind: str = "tech"
    order_idx: int = 0
    # outline_matrix 8 字段（与 OutlineMatrixRow 对齐）
    requirement: str = ""
    key_points: str = ""
    veto_items: str = ""
    bonus_items: str = ""
    score_items: str = ""
    evidence_required: str = ""
    constraint_level: str = "recommended"
    indicators: str = ""
    # proposal 字段
    content: str = ""
    sources: list[Source] = field(default_factory=list)
    status: str = "empty"            # empty | done | failed
    # 其他暂存字段
    domain: str = ""
    parent_title: str = ""
    score: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "block_id": self.block_id,
            "title": self.title,
            "level": self.level,
            "kind": self.kind,
            "order_idx": self.order_idx,
            "requirement": self.requirement,
            "key_points": self.key_points,
            "veto_items": self.veto_items,
            "bonus_items": self.bonus_items,
            "score_items": self.score_items,
            "evidence_required": self.evidence_required,
            "constraint_level": self.constraint_level,
            "indicators": self.indicators,
            "content": self.content,
            "sources": [s.to_dict() for s in self.sources],
            "status": self.status,
            "domain": self.domain,
            "parent_title": self.parent_title,
            "score": self.score,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Block":
        """从 blocks 表 row dict 构造 Block，自动解析 source 列的 JSON。

        非法/空 source 列时 sources 退化为空列表，不抛错。
        """
        sources_raw = row.get("source") or ""
        sources: list[Source] = []
        if sources_raw:
            try:
                arr = json.loads(sources_raw)
                if isinstance(arr, list):
                    sources = [
                        Source.from_dict(s)
                        for s in arr
                        if isinstance(s, dict)
                    ]
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                sources = []

        return cls(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            block_id=row.get("block_id") or "",
            title=row.get("title") or "",
            level=int(row["level"]) if row.get("level") is not None else 1,
            kind=row.get("kind") or "tech",
            order_idx=(
                int(row["order_idx"]) if row.get("order_idx") is not None else 0
            ),
            requirement=row.get("requirement") or "",
            key_points=row.get("key_points") or "",
            veto_items=row.get("veto_items") or "",
            bonus_items=row.get("bonus_items") or "",
            score_items=row.get("score_items") or "",
            evidence_required=row.get("evidence_required") or "",
            constraint_level=row.get("constraint_level") or "recommended",
            indicators=row.get("indicators") or "",
            content=row.get("content") or "",
            sources=sources,
            status=row.get("status") or "empty",
            domain=row.get("domain") or "",
            parent_title=row.get("parent_title") or "",
            score=row.get("score") or "",
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
