"""规范书聚合的领域模型 — Section、OutlineMatrixRow（dataclass）。"""

from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────
# Section
# ─────────────────────────────────────────────────────────

@dataclass
class Section:
    """规范书章节（解析层产出，对应 infra/parser/toc.py:Section 的领域版本）。

    与 infra/parser 的 Section 区别：
    - infra 的 Section 是 parser 内部数据；
    - domain 的 Section 是领域对象，会被持久化到 blocks 表。
    """
    id: str                  # 章节 id，如 "s1"、"s1.1"
    level: int               # 1-4
    title: str
    raw_content: str = ""    # 章节原文（用于 LLM 上下文）
    special_marks: list[str] = field(default_factory=list)  # ★ ▲
    # 该章节来自哪份要求文件。多份文件拼接后**文件边界只能靠它还原**：
    # 按区段定位（segment.py）必须按文件分别切段，否则最后一个锚点到文末的那一段
    # 会把其后所有文件的正文整段吞进来（实测：选中段 20275 字符里只有 ~200 属实）。
    source_file: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "raw_content": self.raw_content,
            "special_marks": list(self.special_marks),
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Section":
        return cls(
            id=d["id"],
            level=int(d.get("level", 1)),
            title=d["title"],
            raw_content=d.get("raw_content", ""),
            special_marks=list(d.get("special_marks", [])),
            source_file=d.get("source_file", ""),
        )


# ─────────────────────────────────────────────────────────
# OutlineMatrixRow
# ─────────────────────────────────────────────────────────

@dataclass
class OutlineMatrixRow:
    """张衡 8 字段响应矩阵的一行（对应 blocks 表的同名列）。"""
    block_id: str            # 与 Section.id 一致
    title: str
    requirement: str = ""
    key_points: str = ""
    veto_items: str = ""
    bonus_items: str = ""
    score_items: str = ""
    evidence_required: str = ""
    constraint_level: str = "recommended"  # mandatory | recommended | optional
    indicators: str = ""
    error: str = ""          # 结果说明（失败原因 / "无原文"提示），会落库到 blocks.error
    # error_kind: '' 正常 | 'no_source' 无原文（正常，不必重试）| 'failed' 需要重试。
    # 有了它界面才能把"失败"和"这节本来就没内容"分开 —— 以前两者在页面上完全同形，
    # 失败章节被算成"已提炼"，页面照样显示「提炼完成」。
    error_kind: str = ""

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "title": self.title,
            "requirement": self.requirement,
            "key_points": self.key_points,
            "veto_items": self.veto_items,
            "bonus_items": self.bonus_items,
            "score_items": self.score_items,
            "evidence_required": self.evidence_required,
            "constraint_level": self.constraint_level,
            "indicators": self.indicators,
            "error": self.error,
            "error_kind": self.error_kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OutlineMatrixRow":
        return cls(
            block_id=d.get("block_id") or d.get("id", ""),
            title=d.get("title", ""),
            requirement=d.get("requirement", ""),
            key_points=d.get("key_points", ""),
            veto_items=d.get("veto_items", ""),
            bonus_items=d.get("bonus_items", ""),
            score_items=d.get("score_items", ""),
            evidence_required=d.get("evidence_required", ""),
            constraint_level=d.get("constraint_level", "recommended"),
            indicators=d.get("indicators", ""),
            error=d.get("error", ""),
            error_kind=d.get("error_kind", ""),
        )
