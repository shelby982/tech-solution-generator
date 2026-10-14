"""目录结构编辑：用户增删改 / 排序后的校验与层级自洽化（纯函数，便于单测）。

背景：闸门 1 的目录树允许用户新增章节、删除章节、改标题、上下移动、缩进/取消缩进。
前端按「**有序列表 + level**」推导树形（与 ``workspace-model.mjs`` 的
``buildOutlineTree``、后端 ``normalize_nodes`` 同一套规则：父节点是前面出现过的、
level 更小的最近前驱）—— 所以保存一份结构只需要保证三件事：

1. ``block_id`` 非空且唯一；
2. 标题非空（空标题在一棵目录树里没有意义，也会让卡片无法渲染）；
3. ``level`` 落在 1..4，且**不跨级加深**（level 3 紧跟 level 1 时并没有 level 2
   的父节点，存成 2 才与树上展示一致）。

这里刻意不引入 parent 字段：层级由顺序推导，顺序就是唯一权威。这样「上下移动」
只是数组换位，不需要重算任何下标，也不会出现 parent 指向已删节点的悬空引用。
"""

from typing import Iterable

MAX_NODES = 300
MIN_LEVEL = 1
# **不是产品限制**：层级可以一直往下嵌（每次只能降一级，见 clamp_levels）。
# 这个上限只是挡住异常数据（例如前端算错导致 level=9999 把界面/DB 撑坏）。
MAX_LEVEL = 12


class StructureError(ValueError):
    """结构非法。消息直接面向用户，会原样出现在 400 响应里。"""


def validate_structure(nodes) -> list[dict]:
    """校验并规整 ``[{block_id, level, title}]``，返回清洗后的列表。

    非法时抛 :class:`StructureError`。合法返回的列表已过 :func:`clamp_levels`。
    """
    if not isinstance(nodes, list) or not nodes:
        raise StructureError("目录不能为空")
    if len(nodes) > MAX_NODES:
        raise StructureError(f"目录节点过多（{len(nodes)} 个，上限 {MAX_NODES}）")

    clean: list[dict] = []
    seen: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, dict):
            raise StructureError("节点格式不正确")
        block_id = str(raw.get("block_id") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not block_id:
            raise StructureError("存在缺少 block_id 的节点")
        if block_id in seen:
            raise StructureError(f"节点 id 重复：{block_id}")
        if not title:
            raise StructureError("章节标题不能为空")
        # 目录 id 只有两种合法来源：派生（s1 / s1.1 / s1.2.3）与新增（n + base36）。
        # **纯数字**只可能来自"把数据库行 id 当成 block_id 传"（实测发生过一次：
        # 121 个节点被当成新节点、116 个原占位被删）。这里直接拒，别让破坏性请求走完。
        if block_id.isdigit():
            raise StructureError(
                f"节点 id 是纯数字（{block_id}）：这看着像数据库行 id，不是目录 id（如 s1.1）。"
                "已拒绝本次提交，目录未做任何改动。"
            )
        seen.add(block_id)
        clean.append({
            "block_id": block_id,
            "title": title,
            "level": _coerce_level(raw.get("level")),
        })
    return clamp_levels(clean)


def clamp_levels(nodes: Iterable[dict]) -> list[dict]:
    """层级自洽化：clamp 到 1..4，且不允许比前一个节点深两级以上。

    文档顺序是层级的最终权威（同 ``buildOutlineTree``）。第一条节点若给了 level 3，
    树上它本来就是顶层（没有更浅的前驱）—— 这里一并压到 1，避免「存 3 但显示在顶层」
    这种表里不一的目录。
    """
    out: list[dict] = []
    prev_level = 0
    for node in nodes:
        level = max(MIN_LEVEL, min(MAX_LEVEL, int(node.get("level") or MIN_LEVEL)))
        if prev_level == 0:
            level = MIN_LEVEL
        elif level > prev_level + 1:
            level = prev_level + 1
        out.append({**node, "level": level})
        prev_level = level
    return out


def subtree_range(nodes: list[dict], block_id: str) -> tuple[int, int]:
    """返回节点及其子树在列表中的下标区间 ``[start, end)``（**仅测试与诊断用**）。

    子树 = 该节点之后、直到出现 level 不大于它的节点为止的所有节点。
    找不到返回 ``(-1, -1)``。
    """
    start = next((i for i, n in enumerate(nodes) if n.get("block_id") == block_id), -1)
    if start < 0:
        return -1, -1
    level = int(nodes[start].get("level") or MIN_LEVEL)
    end = start + 1
    while end < len(nodes) and int(nodes[end].get("level") or MIN_LEVEL) > level:
        end += 1
    return start, end


def _coerce_level(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return MIN_LEVEL
