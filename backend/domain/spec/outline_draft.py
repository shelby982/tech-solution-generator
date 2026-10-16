"""应答文件目录派生 —— 纯函数层（模型输出的扁平 nodes → 规整后的 Section 列表）。

不依赖 LLM / DB / IO，便于单测覆盖边界：level 越级、孤儿 parent、乱序、
深层嵌套、同级重名、超出节点上限、半截输出。

模型输出采用「扁平数组 + 显式 parent 下标」而非嵌套 children，原因是
``extract_json_object`` 要求 JSON 严格闭合，嵌套结构一旦被 max_tokens 截断会整棵
子树丢失；扁平数组截断后前缀仍是合法 JSON，可降级为「部分成功」。
"""

import re
from dataclasses import dataclass, field

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
    # 来源链等自定义字段（如 review_point_id / spec_requirement_ids / score_weight /
    # criterion_text / spec_topic）。目录节点不只是标题：它是"评分点/规范要求 → 章节"
    # 这条链的落点，链路必须能透传到 blocks（用户确定的核心数据关系）。
    extra: dict = field(default_factory=dict)


def _coerce_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_nodes(
    raw_nodes,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_level: int = MAX_LEVEL,
    preserve_duplicates: bool = False,
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

    # 模型给的 ``parent`` 是**原始 nodes 数组**里的下标（prompt 里就是这么承诺的），
    # 而 accepted 会因为空标题 / 非 dict / 同级重名而变短。直接把 explicit 当 accepted
    # 下标用，一旦有节点被丢弃，其后每个 parent 都会指向更早的另一个节点 ——
    # 实测：中间一个空标题节点会让其后 parent=1 的节点被降级重挂并留下假告警。
    raw_to_accepted: dict[int, int] = {}

    for raw_idx, raw in enumerate(raw_nodes or []):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue

        raw_level = _coerce_int(raw.get("level")) or 1
        raw_level = max(1, min(max_level, raw_level))

        parent_idx: int | None = None
        level = raw_level

        explicit = _coerce_int(raw.get("parent"))
        mapped = raw_to_accepted.get(explicit) if explicit is not None else None
        if (
            mapped is not None
            and 0 <= mapped < len(accepted)
            and accepted[mapped].level == raw_level - 1
        ):
            parent_idx, level = mapped, raw_level
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
        if key in seen_siblings and not preserve_duplicates:
            dropped_dup += 1
            continue
        seen_siblings.add(key)

        raw_to_accepted[raw_idx] = len(accepted)
        extra = {k: v for k, v in raw.items()
                 if k not in ("level", "title", "parent", "children")}
        accepted.append(NormalizedNode(level=level, title=title, parent=parent_idx,
                                       extra=extra))

    if dropped_dup:
        warnings.append(f"丢弃同级重名节点 {dropped_dup} 个")
    if rehung:
        warnings.append(f"重挂层级不自洽的节点 {rehung} 个")

    if len(accepted) > max_nodes:
        warnings.append(f"节点数超过上限 {max_nodes}，已截断")
        accepted = accepted[:max_nodes]

    return accepted, warnings


def stable_chapter_key(chain: dict, title_path: str) -> str:
    """章节的**稳定键**：重出目录后靠它把正文与素材挂接迁移到新章节。

    为什么需要：``block_id`` 是**位置派生**的（s1 / s1.1 / s1.1.1），重拆目录换了位置
    就换 id；而应答条目 id 里嵌着 block_id（``{block_id}#R1``），素材挂接也一起失效。
    前后端都不解析 id 的格式（层级靠 level 与标题推断），所以不动 id 格式，
    另给一个内容派生的键做"跨版本认亲"。

    键的取法（越靠前越稳）：
    1. ``review_point_id + 标题路径`` —— 链条给的锚点，重排也认得出；
    2. 只有标题路径时用它（同一次派生的标题通常稳定）。
    """
    norm = re.sub(r"\s+", "", title_path or "")
    point = str(chain.get("review_point_id") or "").strip()
    return f"{point}|{norm}" if point else norm


def build_sections(nodes: list[NormalizedNode], *, max_level: int = MAX_LEVEL) -> list[Section]:
    """给规整后的节点分配 ``s1 / s1.1 / s1.1.1`` 形式的 id，转成 Section。

    id 格式沿用 ``Section.id`` 的既有约定，前端 ``inferTocLevel`` / ``buildToc``
    的启发式与 ``workspace-model.projectChapters`` 无需任何改动。

    ``raw_content`` / ``special_marks`` 留空，由调用方做 grounding 时填充。
    """
    top_counter = 0
    # 子序号按**父 id** 记账，而不是按 level 记一个全局计数器。
    # 旧实现用 ``last_id_at_level`` + 按 level 的计数器：换一级时只清深层的
    # ``last_id_at_level``，浅层记录留着，于是「上一棵子树遗留的父 id + 归零的计数」
    # 会拼出与更早节点**完全相同**的 id（实测合法模型输出即可撞出两个 ``s1.2.1``，
    # 且 normalize 零告警）—— 重复 id 会让一节被静默覆盖丢失。
    child_counters: dict[str, int] = {}
    sections: list[Section] = []
    used_ids: set[str] = set()
    title_paths: list[str] = []          # 与 sections 对齐，用于算稳定键

    for node in nodes:
        parent = node.parent
        parent_section = (
            sections[parent]
            if isinstance(parent, int) and 0 <= parent < len(sections)
            else None
        )
        if parent_section is None:
            # 一级节点（normalize_nodes 保证 level>1 必有父；这里对脏输入兜底）
            top_counter += 1
            level = 1
            section_id = f"s{top_counter}"
        else:
            # 层级以父级为准：parent 是权威关系，避免 level 与 parent 不一致
            level = max(1, min(max_level, parent_section.level + 1))
            used = child_counters.get(parent_section.id, 0) + 1
            child_counters[parent_section.id] = used
            section_id = f"{parent_section.id}.{used}"

        if section_id in used_ids:
            # 构造上不可能（父 id 唯一 + 每个父的子序号自增）；留一道防线，宁可改 id 也不撞号
            suffix = 1
            while f"{section_id}-{suffix}" in used_ids:
                suffix += 1
            section_id = f"{section_id}-{suffix}"
        used_ids.add(section_id)

        chain = dict(getattr(node, "extra", {}) or {})
        parent_path = (
            title_paths[sections.index(parent_section)]
            if parent_section is not None and parent_section in sections else ""
        )
        path = f"{parent_path} > {node.title}" if parent_path else node.title
        title_paths.append(path)
        chain["stable_key"] = stable_chapter_key(chain, path)
        sections.append(Section(id=section_id, level=level, title=node.title, chain=chain))

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
