"""评审聚合的持久化层 — reviews 表 + workflow_runs 表。"""

import json
from typing import Optional

import aiosqlite

from .models import Finding, Issue


class ReviewRepository:
    """评审产出的持久化（reviews 表）。

    设计取舍：每条记录 = 一位 agent 对一个 block 的 finding。
    GlobalReport 不持久化（实时由 routes 用 list_findings_by_thread 重组）。
    """

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    # ─────────────────────────────────────────────────────
    # 写
    # ─────────────────────────────────────────────────────

    async def add_finding(self, thread_id: str, finding: Finding) -> int:
        """落地一条 finding，返回新行 id。"""
        issues_json = json.dumps(
            [i.to_dict() for i in finding.issues],
            ensure_ascii=False,
        )
        strengths_json = json.dumps(finding.strengths, ensure_ascii=False)
        cursor = await self.db.execute(
            """INSERT INTO reviews
               (thread_id, block_id, agent, score, issues, strengths, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                finding.block_id,
                finding.agent,
                finding.score,
                issues_json,
                strengths_json,
                finding.error,
            ),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def clear_thread(self, thread_id: str) -> None:
        """清空某次工作流的所有 findings（重新评审时使用）。"""
        await self.db.execute(
            "DELETE FROM reviews WHERE thread_id = ?", (thread_id,),
        )
        await self.db.commit()

    # ─────────────────────────────────────────────────────
    # 读
    # ─────────────────────────────────────────────────────

    async def list_findings_by_thread(self, thread_id: str) -> list[Finding]:
        """列出某次工作流的所有 findings，按 (block_id, agent) 升序。"""
        cursor = await self.db.execute(
            """SELECT * FROM reviews WHERE thread_id = ?
               ORDER BY block_id, agent""",
            (thread_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_finding(dict(r)) for r in rows]

    async def list_findings_by_block(
        self, thread_id: str, block_id: str,
    ) -> list[Finding]:
        cursor = await self.db.execute(
            """SELECT * FROM reviews
               WHERE thread_id = ? AND block_id = ?
               ORDER BY agent""",
            (thread_id, block_id),
        )
        rows = await cursor.fetchall()
        return [self._row_to_finding(dict(r)) for r in rows]

    @staticmethod
    def _row_to_finding(row: dict) -> Finding:
        try:
            issues_arr = json.loads(row.get("issues") or "[]")
            issues = [
                Issue.from_dict(i)
                for i in issues_arr
                if isinstance(i, dict)
            ]
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            issues = []
        try:
            strengths = json.loads(row.get("strengths") or "[]")
            if not isinstance(strengths, list):
                strengths = []
            strengths = [str(s) for s in strengths]
        except (json.JSONDecodeError, TypeError, ValueError):
            strengths = []
        return Finding(
            block_id=row.get("block_id", ""),
            agent=row.get("agent", ""),
            score=int(row["score"]) if row.get("score") is not None else 0,
            issues=issues,
            strengths=strengths,
            error=row.get("error", "") or "",
        )


# ─────────────────────────────────────────────────────────
# 合法 stage 集合（与 spec §5 一致）
# ─────────────────────────────────────────────────────────
_VALID_STAGES = {
    "idle", "parsing", "outline_review",
    "matching", "materials_review",
    "generating", "reviewing",
    "report_review", "done", "aborted",
}


class WorkflowRunRepository:
    """工作流元数据（workflow_runs 表）。

    本表存 LangGraph 一次工作流的元信息（thread_id 由 LangGraph 生成）。
    详细 state 由 LangGraph SQLite checkpointer 自管，不在这张表里。
    """

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(
        self, thread_id: str, project_id: int, stage: str = "idle",
    ) -> dict:
        if stage not in _VALID_STAGES:
            raise ValueError(f"非法 stage：{stage}")
        await self.db.execute(
            """INSERT INTO workflow_runs (thread_id, project_id, stage)
               VALUES (?, ?, ?)""",
            (thread_id, project_id, stage),
        )
        await self.db.commit()
        row = await self.get(thread_id)
        assert row is not None  # 刚插入，不会为空
        return row

    async def get(self, thread_id: str) -> Optional[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM workflow_runs WHERE thread_id = ?", (thread_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_by_project(
        self, project_id: int, *, only_active: bool = False,
    ) -> list[dict]:
        if only_active:
            cursor = await self.db.execute(
                """SELECT * FROM workflow_runs
                   WHERE project_id = ? AND stage NOT IN ('done', 'aborted')
                   ORDER BY created_at DESC""",
                (project_id,),
            )
        else:
            cursor = await self.db.execute(
                """SELECT * FROM workflow_runs
                   WHERE project_id = ? ORDER BY created_at DESC""",
                (project_id,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_stage(
        self, thread_id: str, stage: str,
    ) -> Optional[dict]:
        if stage not in _VALID_STAGES:
            raise ValueError(f"非法 stage：{stage}")
        if stage in ("done", "aborted"):
            await self.db.execute(
                """UPDATE workflow_runs
                   SET stage = ?,
                       updated_at = CURRENT_TIMESTAMP,
                       finished_at = CURRENT_TIMESTAMP
                   WHERE thread_id = ?""",
                (stage, thread_id),
            )
        else:
            await self.db.execute(
                """UPDATE workflow_runs
                   SET stage = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE thread_id = ?""",
                (stage, thread_id),
            )
        await self.db.commit()
        return await self.get(thread_id)
