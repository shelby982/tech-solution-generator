"""
生成任务内存状态管理

设计说明：
- asyncio.Queue 和 asyncio.Event 必须在 async 上下文中创建，
  因此由路由层（generate.py）在 async 路由函数内注入到 task 对象。
- TaskStore 仅负责 task 的 CRUD 和线程安全的字典管理。
- 支持最多 100 个任务的滚动保留（按创建顺序淘汰旧任务）。
"""

import asyncio
import uuid
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


# ─────────────────────────────────────────────
# 枚举与数据类
# ─────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    ERROR     = "error"
    CANCELLED = "cancelled"


@dataclass
class SectionResult:
    """单个章节的生成结果"""
    section_id: str
    title: str
    content: str = ""
    done: bool = False

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "content_length": len(self.content),
            "done": self.done,
        }


@dataclass
class GenerationTask:
    """
    一个生成任务的完整状态。

    关键字段说明：
    - sections:      输入章节列表（dict，含 id / title / content）
    - results:       section_id → SectionResult，记录已生成内容
    - queue:         asyncio.Queue，由路由层在 async 函数内创建并注入
    - cancel_event:  asyncio.Event，由路由层在 async 函数内创建并注入
    - status:        任务当前状态
    - target_words:  每章节目标字数
    """
    task_id: str
    sections: list          # list of {"id": str, "title": str, "content": str}
    target_words: int = 500
    status: TaskStatus = TaskStatus.PENDING
    error_message: str = ""
    doc_summary: str = ""
    results: Dict[str, SectionResult] = field(default_factory=dict)

    # 由路由层在 async 上下文注入，避免在模块导入时绑定到错误的事件循环
    queue: Optional[asyncio.Queue] = field(default=None, repr=False)
    cancel_event: Optional[asyncio.Event] = field(default=None, repr=False)

    def __post_init__(self):
        # 初始化每个章节的结果占位
        for sec in self.sections:
            self.results[sec["id"]] = SectionResult(
                section_id=sec["id"],
                title=sec["title"],
            )

    # ── 进度 ──────────────────────────────────

    @property
    def progress(self) -> float:
        if not self.results:
            return 0.0
        done_count = sum(1 for r in self.results.values() if r.done)
        return done_count / len(self.results)

    @property
    def completed_sections(self) -> int:
        return sum(1 for r in self.results.values() if r.done)

    # ── 序列化 ────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress": round(self.progress, 2),
            "total_sections": len(self.sections),
            "completed_sections": self.completed_sections,
            "error_message": self.error_message,
        }

    def to_detail_dict(self) -> dict:
        """包含每个章节结果概要的详细视图"""
        return {
            **self.to_dict(),
            "sections": [r.to_dict() for r in self.results.values()],
        }

    def get_all_content(self) -> str:
        """将所有已生成章节内容合并为 Markdown 字符串（按输入顺序，保留层级）"""
        parts = []
        if self.doc_summary:
            parts.append(f"## 项目概述\n\n{self.doc_summary}")
        for sec in self.sections:
            result = self.results.get(sec["id"])
            if result and result.done:
                level = sec.get("level", 1)
                heading = "#" * min(level + 1, 6)  # level 1 → ##，level 4 → #####
                if result.content:
                    parts.append(f"{heading} {result.title}\n\n{result.content}")
                else:
                    parts.append(f"{heading} {result.title}")
        return "\n\n---\n\n".join(parts)


# ─────────────────────────────────────────────
# TaskStore 单例
# ─────────────────────────────────────────────

class TaskStore:
    """
    线程安全的任务内存存储。
    保留最新的 MAX_TASKS 个任务，超出时淘汰最旧的。
    """
    MAX_TASKS = 100

    def __init__(self):
        self._tasks: Dict[str, GenerationTask] = {}
        self._order: list[str] = []          # 按创建顺序记录 task_id
        self._lock = threading.Lock()

    def create(self, sections: list, target_words: int = 500) -> "GenerationTask":
        """
        创建新任务并存储。
        注意：queue 和 cancel_event 由调用方在 async 上下文中设置。
        """
        task_id = str(uuid.uuid4())
        task = GenerationTask(task_id=task_id, sections=sections, target_words=target_words)
        with self._lock:
            self._tasks[task_id] = task
            self._order.append(task_id)
            self._evict_old()
        return task

    def get(self, task_id: str) -> Optional["GenerationTask"]:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """
        发送取消信号。仅对 RUNNING 状态的任务有效。
        返回是否成功发送了信号。
        """
        task = self.get(task_id)
        if task is None:
            return False
        if task.status != TaskStatus.RUNNING:
            return False
        if task.cancel_event is not None:
            task.cancel_event.set()
            return True
        return False

    def _evict_old(self):
        """保留最新的 MAX_TASKS 个任务（调用方已持有锁）"""
        while len(self._order) > self.MAX_TASKS:
            old_id = self._order.pop(0)
            old_task = self._tasks.pop(old_id, None)
            if old_task and old_task.cancel_event is not None:
                old_task.cancel_event.set()


# 全局单例
task_store = TaskStore()
