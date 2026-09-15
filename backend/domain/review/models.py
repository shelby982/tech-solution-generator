"""评审聚合的数据模型 — Issue / Finding / GlobalReport（spec §5）。"""

from dataclasses import dataclass, field


@dataclass
class Issue:
    """评审发现的单个问题。

    needs_material / material_query 由评审 agent 在输出 JSON 里自行声明，
    而不是事后用 LLM 分类——包拯的视角本就包含「证明材料是否齐备」，
    它与王安石天然知道某条问题是"没写"还是"没料可写"。
    """
    severity: str            # critical | high | medium | low
    point: str               # 问题描述
    suggestion: str = ""     # 改进建议
    needs_material: bool = False    # 必须补外部素材才能修复，非重写可解决
    material_query: str = ""        # 缺什么，直接当检索 query

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "point": self.point,
            "suggestion": self.suggestion,
            "needs_material": bool(self.needs_material),
            "material_query": self.material_query,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        return cls(
            severity=d.get("severity", "medium"),
            point=d.get("point", ""),
            suggestion=d.get("suggestion", ""),
            needs_material=bool(d.get("needs_material", False)),
            material_query=d.get("material_query", ""),
        )


@dataclass
class Finding:
    """单 block 的评审结果（一位评审 agent 对一个 block 的判断）。

    持久化到 reviews 表的一行（issues、strengths 序列化为 JSON）。
    """
    block_id: str
    agent: str               # wang_anshi | bao_zheng
    score: int = 0           # 0-100
    issues: list[Issue] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    error: str = ""          # 评审失败时的错误描述

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "agent": self.agent,
            "score": self.score,
            "issues": [i.to_dict() for i in self.issues],
            "strengths": list(self.strengths),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            block_id=d.get("block_id", ""),
            agent=d.get("agent", ""),
            score=int(d["score"]) if d.get("score") is not None else 0,
            issues=[Issue.from_dict(i) for i in d.get("issues", [])],
            strengths=[str(s) for s in d.get("strengths", [])],
            error=d.get("error", ""),
        )


@dataclass
class GlobalReport:
    """全局评审汇总报告（spec §5 GlobalReport）。

    per_block: dict[block_id, {tech_score, comp_score, severity_counts}]
    本期不持久化（评审完成后由 routes 计算并直接返给前端，
    历史快照通过 reviews 表的明细在前端按需重组）。
    """
    per_block: dict = field(default_factory=dict)
    total_score: float = 0.0
    top_risks: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    missing_bonus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "per_block": dict(self.per_block),
            "total_score": float(self.total_score),
            "top_risks": list(self.top_risks),
            "missing_evidence": list(self.missing_evidence),
            "missing_bonus": list(self.missing_bonus),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GlobalReport":
        return cls(
            per_block=dict(d.get("per_block", {})),
            total_score=float(d.get("total_score", 0.0)),
            top_risks=list(d.get("top_risks", [])),
            missing_evidence=list(d.get("missing_evidence", [])),
            missing_bonus=list(d.get("missing_bonus", [])),
        )
