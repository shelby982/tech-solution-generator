"""素材聚合的领域模型 — Material、Chunk（dataclass）。"""

from dataclasses import dataclass
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
