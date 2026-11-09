"""素材聚合的领域模型 — Material、Chunk（dataclass）。"""

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────
# Material
# ─────────────────────────────────────────────────────────

@dataclass
class Material:
    """素材文件元数据（对应 materials 表）。"""
    id: int
    project_id: int
    filename: str
    type: str          # docx / pdf / txt / ...
    file_path: str
    role: str          # spec / material / template …（业务约定的角色）
    file_size: int = 0
    parsed_at: Optional[str] = None
    parse_status: str = "pending"  # pending | parsing | done | failed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "filename": self.filename,
            "type": self.type,
            "file_path": self.file_path,
            "role": self.role,
            "file_size": self.file_size,
            "parsed_at": self.parsed_at,
            "parse_status": self.parse_status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Material":
        return cls(
            id=d["id"],
            project_id=d["project_id"],
            filename=d.get("filename", ""),
            type=d.get("type", ""),
            file_path=d.get("file_path", ""),
            role=d.get("role", ""),
            file_size=int(d["file_size"]) if d.get("file_size") is not None else 0,
            parsed_at=d.get("parsed_at"),
            parse_status=d.get("parse_status", "pending"),
        )


# ─────────────────────────────────────────────────────────
# Chunk
# ─────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """素材切片（对应 material_chunks 表 + 上层附加 filename）。"""
    id: int
    material_id: int
    chunk_index: int
    content: str
    filename: str = ""  # 来自 list_chunks_with_filename 的附加字段

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "material_id": self.material_id,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "filename": self.filename,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(
            id=d["id"],
            material_id=d["material_id"],
            chunk_index=int(d["chunk_index"]),
            content=d.get("content", ""),
            filename=d.get("filename", ""),
        )


# ─────────────────────────────────────────────────────────
# MaterialEvidence：匹配阶段的产物（章节/条目 → 素材依据）
# ─────────────────────────────────────────────────────────
#
# 设计约束（见 docs/superpowers/plans/2026-09-21-response-coverage-and-matching-plan.md §4.D）：
# - **必须带 content（素材原文）**：撰写阶段要引用原文，而不是"这段相关度 8 分"这类评语；
#   历史实现只留了 reason，导致正文和界面都拿不到素材原文（"最后一公里"缺口）。
# - 必须带出处：P0 精确到「文件名 + 第 N 段」，P1 再补页码/锚点。
# - 区分性质与来源：fact/solution/background；ours/tender（招标文件用途受限）。
# - by 记录归属方式，便于统计"零调用命中占比"与人工改判回收。

KIND_FACT = "fact"            # 事实性：参数/接口/标准条款（可核）
KIND_SOLUTION = "solution"    # 方案性：架构/方案论述/方法论（可引）
KIND_BACKGROUND = "background"  # 背景性：项目背景/需求理解（可溯）
VALID_KINDS = (KIND_FACT, KIND_SOLUTION, KIND_BACKGROUND)

SOURCE_OURS = "ours"          # 我方素材（「素材」窗口上传）
SOURCE_TENDER = "tender"      # 招标文件（「应标要求」窗口上传，用途受限）
VALID_SOURCE_KINDS = (SOURCE_OURS, SOURCE_TENDER)

BY_RULE = "rule"              # 规则/结构命中（0 调用）
BY_SEMANTIC = "semantic"      # 语义检索命中（0 调用）
BY_MODEL = "model"            # 模型归属（1 份素材 1 次调用）
BY_HUMAN = "human"            # 人工挂接（锁定）
VALID_BY = (BY_RULE, BY_SEMANTIC, BY_MODEL, BY_HUMAN)


@dataclass
class MaterialEvidence:
    """一段素材对某个章节（后续细化到条目）的依据。"""
    chunk_id: int
    material_id: int
    content: str                       # 素材原文（必须）
    filename: str = ""
    locator: str = ""                  # "P12 · 第 8 段 · 切片标题"（有什么给什么）
    page: int = 0                      # 出处页码（解析器能给才有；Word 无页码则为 0）
    kind: str = KIND_SOLUTION          # fact | solution | background
    source_kind: str = SOURCE_OURS     # ours | tender
    by: str = BY_RULE                  # rule | semantic | model | human
    confidence: float = 0.0
    score: float = 0.0                 # 检索/相似度原始分（便于排查与调阈值）
    item_ids: list[str] = field(default_factory=list)   # P0 为空；P1 挂到具体要求条目
    locked: bool = False               # 人工挂接/改判后为真，重算不覆盖

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "material_id": self.material_id,
            "content": self.content,
            "filename": self.filename,
            "locator": self.locator,
            "page": self.page,
            "kind": self.kind,
            "source_kind": self.source_kind,
            "by": self.by,
            "confidence": self.confidence,
            "score": self.score,
            "item_ids": list(self.item_ids),
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MaterialEvidence":
        return cls(
            chunk_id=d.get("chunk_id"),
            material_id=int(d.get("material_id") or 0),
            content=d.get("content", "") or "",
            filename=d.get("filename", "") or "",
            locator=d.get("locator", "") or "",
            page=int(d.get("page") or 0),
            kind=d.get("kind", KIND_SOLUTION) or KIND_SOLUTION,
            source_kind=d.get("source_kind", SOURCE_OURS) or SOURCE_OURS,
            by=d.get("by", BY_RULE) or BY_RULE,
            confidence=float(d.get("confidence") or 0.0),
            score=float(d.get("score") or 0.0),
            item_ids=list(d.get("item_ids") or []),
            locked=bool(d.get("locked")),
        )


# ─────────────────────────────────────────────────────────
# Match（检索结果；原 infra/retrieval/rerank.py，rerank 删除后迁入）
# ─────────────────────────────────────────────────────────

@dataclass
class Match:
    """单条素材检索结果。

    ``content`` 及其后的字段是"检索/匹配 → 撰写"契约的一部分：
    撰写阶段必须拿到素材原文，而不是只有一句评语。
    """
    chunk_id: object
    score: float
    reason: str
    hit_points: list[str] = field(default_factory=list)
    content: str = ""            # 素材原文（撰写阶段引用它）
    filename: str = ""           # 出处：文件名
    locator: str = ""            # 出处：第 N 段（页码/锚点）
    kind: str = ""               # fact | solution | background
    source_kind: str = ""        # ours | tender
    by: str = ""                 # rule | semantic | model | human
    confidence: float = 0.0
    locked: bool = False
