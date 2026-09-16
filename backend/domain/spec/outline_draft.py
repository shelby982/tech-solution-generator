"""应答文件目录派生 —— 纯函数层（模型输出的扁平 nodes → 规整后的 Section 列表）。

不依赖 LLM / DB / IO，便于单测覆盖边界：level 越级、孤儿 parent、乱序、
深层嵌套、同级重名、超出节点上限、半截输出。

模型输出采用「扁平数组 + 显式 parent 下标」而非嵌套 children，原因是
``extract_json_object`` 要求 JSON 严格闭合，嵌套结构一旦被 max_tokens 截断会整棵
子树丢失；扁平数组截断后前缀仍是合法 JSON，可降级为「部分成功」。
"""

from dataclasses import dataclass

from .models import Section

MAX_LEVEL = 4
DEFAULT_MAX_NODES = 120
# 一级章节少于此数视为目录结构不可信，调用方应整体降级回 source_toc。
MIN_TOP_LEVEL_NODES = 3


@dataclass
class NormalizedNode:
    """规整后的一个目录节点。``parent`` 是它在**本列表中的下标**，一级节点为 None。"""
    level: int
    title: str
    parent: int | None


def _coerce_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_nodes(
    raw_nodes,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> tuple[list[NormalizedNode], list[str]]:
    """把模型输出的 nodes 数组规整成父子自洽的序列。

    规整规则：
    - 标题去空白；空标题丢弃。
    - 显式 ``parent`` 仅在「指向更早的节点」且「level 恰好比 parent 大 1」时才采信。
    - 否则重挂到**最近一个 level 更小的前驱**，level 取该前驱 level + 1；
      找不到前驱则为一级节点。文档顺序是层级的最终权威。
    - 同级（同 parent）重名只保留第一条。
    - 超过 ``max_nodes`` 截断——截断取前缀，而前缀天然自洽（parent 永远指向更早的节点）。

    返回 ``(nodes, warnings)``。warnings 供调用方写入 ``spec.outline_error``。
    """
    accepted: list[NormalizedNode] = []
    warnings: list[str] = []
    seen_siblings: set[tuple[int | None, str]] = set()
    dropped_dup = 0
    rehung = 0

    for raw in raw_nodes or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue

        raw_level = _coerce_int(raw.get("level")) or 1
        raw_level = max(1, min(MAX_LEVEL, raw_level))

        parent_idx: int | None = None
        level = raw_level

        explicit = _coerce_int(raw.get("parent"))
        if (
            explicit is not None
            and 0 <= explicit < len(accepted)
            and accepted[explicit].level == raw_level - 1
        ):
            parent_idx, level = explicit, raw_level
        else:
            # 回退：最近一个 level 更小的前驱（保证 level = parent.level + 1 ≤ MAX_LEVEL）
            for j in range(len(accepted) - 1, -1, -1):
                if accepted[j].level < raw_level:
                    parent_idx, level = j, accepted[j].level + 1
                    break
            else:
                parent_idx, level = None, 1
            if parent_idx is not None and raw_level > 1:
                rehung += 1

        key = (parent_idx, title)
        if key in seen_siblings:
            dropped_dup += 1
            continue
        seen_siblings.add(key)

        accepted.append(NormalizedNode(level=level, title=title, parent=parent_idx))

    if dropped_dup:
        warnings.append(f"丢弃同级重名节点 {dropped_dup} 个")
    if rehung:
        warnings.append(f"重挂层级不自洽的节点 {rehung} 个")

    if len(accepted) > max_nodes:
        warnings.append(f"节点数超过上限 {max_nodes}，已截断")
        accepted = accepted[:max_nodes]

    return accepted, warnings


def build_sections(nodes: list[NormalizedNode]) -> list[Section]:
    """给规整后的节点分配 ``s1 / s1.1 / s1.1.1`` 形式的 id，转成 Section。

    id 格式沿用 ``Section.id`` 的既有约定，前端 ``inferTocLevel`` / ``buildToc``
    的启发式与 ``workspace-model.projectChapters`` 无需任何改动。

    ``raw_content`` / ``special_marks`` 留空，由调用方做 grounding 时填充。
    """
    counters = [0] * (MAX_LEVEL + 1)
    last_id_at_level: dict[int, str] = {}
    sections: list[Section] = []

    for node in nodes:
        level = max(1, min(MAX_LEVEL, node.level))
        if level == 1:
            counters[1] += 1
            for lv in range(2, MAX_LEVEL + 1):
                counters[lv] = 0
            section_id = f"s{counters[1]}"
        else:
            parent_id = last_id_at_level.get(level - 1)
            if parent_id is None:
                # 理论上不会发生（normalize_nodes 保证有一级前驱），兜底降为一级
                counters[1] += 1
                for lv in range(2, MAX_LEVEL + 1):
                    counters[lv] = 0
                section_id = f"s{counters[1]}"
                level = 1
            else:
                counters[level] += 1
                for lv in range(level + 1, MAX_LEVEL + 1):
                    counters[lv] = 0
                section_id = f"{parent_id}.{counters[level]}"

        last_id_at_level[level] = section_id
        sections.append(Section(id=section_id, level=level, title=node.title))

    return sections


def count_top_level(sections: list[Section]) -> int:
    """一级章节数量，供调用方判断目录是否可信。"""
    return sum(1 for s in sections if s.level == 1)


__all__ = [
    "MAX_LEVEL",
    "DEFAULT_MAX_NODES",
    "MIN_TOP_LEVEL_NODES",
    "NormalizedNode",
    "normalize_nodes",
    "build_sections",
    "count_top_level",
]
