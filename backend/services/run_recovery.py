"""启动对账：把"进程中途死掉、却看起来像在等用户"的运行标记出来。

用户遇到的现象（2026-10-16）：某个标包的解析运行"断在半路"，页面上只剩
「当前显示的是招标文件原始目录（367 节），应答目录未派生」+ 一句指向不存在按钮的提示。

根因（用 checkpoints 表查证）：
  · 图的实际节点顺序：zhang_heng_parse → zhang_heng_outline_draft → gate_outline
  · 坏掉的 run 最新检查点是 **干活节点**（outline_draft），**没有 gate_outline 检查点**
    → 说明进程在"两个节点之间"被杀/重启
  · 而 workflow_runs.stage 停在 points_review，与"正等用户确认评分点"完全同形
    → 既不会自己往下走，也没有任何"我中断了"的落库信号

判据（精确，不靠时间/猜测）：
  finished_at 为空
  且 stage 不是终态（aborted）
  且 最新检查点的节点名存在且**不以 gate_ 开头**   → 判定为中途中断

注意：**不写 stage='aborted'** —— 那是"工作流已作废"的语义，会让 runner 直接拒绝后续操作，
      把已经可用的恢复路径（重新派生目录）也堵死。这里只记为事实，保留恢复路径。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 门节点 = graph 里显式等待用户的地方（最新检查点落在门上 = 正常运行中，别动）
GATE_PREFIX = "gate_"
# 终态 stage：已有明确结论，不参与对账
TERMINAL_STAGES = ("aborted",)
_WRITES_RE = re.compile(r'"writes"\s*:\s*\{?\s*"([a-z_]+)"')


def node_from_metadata(metadata) -> str:
    """从 LangGraph 检查点的 metadata 里取**最后一次写入的节点名**（取不到返回 ''）。"""
    text = metadata.decode("utf-8", "ignore") if isinstance(metadata, (bytes, bytearray)) else str(metadata or "")
    if not text:
        return ""
    m = _WRITES_RE.search(text)
    return m.group(1) if m else ""


async def mark_interrupted_runs(db) -> int:
    """标记中途中断的运行，返回标记数量。幂等：已标记过的不重复写。"""
    # checkpoints 由 LangGraph 的 saver 建表（不在 db._DDL 里）：表还不存在时直接跳过，
    # 否则首次启动/测试环境会因"no such table"而整段失效。
    has_cp = await (await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints' LIMIT 1")).fetchone()
    if not has_cp:
        return 0
    cur = await db.execute(
        """SELECT w.thread_id, w.project_id, w.stage, w.interrupted_at,
                  (SELECT c.metadata FROM checkpoints c WHERE c.thread_id = w.thread_id
                   ORDER BY c.rowid DESC LIMIT 1) AS metadata
           FROM workflow_runs w
           WHERE w.finished_at IS NULL"""
    )
    rows = await cur.fetchall()
    marked = 0
    for row in rows:
        get = (lambda k: row[k]) if hasattr(row, "keys") else (lambda k, r=row: r[{"thread_id": 0, "project_id": 1, "stage": 2, "interrupted_at": 3, "metadata": 4}[k]])
        if get("interrupted_at"):
            continue                                   # 已标记过：幂等
        stage = str(get("stage") or "")
        if stage in TERMINAL_STAGES:
            continue                                   # 用户取消是终态，不动
        node = node_from_metadata(get("metadata"))
        if not node or node.startswith(GATE_PREFIX):
            continue                                   # 等在门上 / 没有检查点 → 不动
        await db.execute(
            "UPDATE workflow_runs SET interrupted_at = CURRENT_TIMESTAMP, interrupted_node = ?, updated_at = updated_at "
            "WHERE thread_id = ?",
            (node, get("thread_id")),
        )
        marked += 1
        logger.warning("运行中途中断：project=%s thread=%s 停在节点 %s", get("project_id"), str(get("thread_id"))[:8], node)
    if marked:
        await db.commit()
    return marked
