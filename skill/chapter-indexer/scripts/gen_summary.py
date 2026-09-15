#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
摘要生成脚本 - Bottom-Up 分批模式

处理流程（重复运行，自动跳过已完成章节）：
  Phase 1  叶子节点：输出原文 → AI 生成摘要 → append 到 章节摘要.json
  Phase 2  内部节点：输出子摘要 → AI 汇总   → append 到 章节摘要.json
  Phase 3  全部完成：生成标书总摘要.md

用法：
  uv run scripts/gen_summary.py <项目路径> [--profile compact|standard|extended|unlimited]

--profile 按上下文容量选档（默认 standard），与具体模型无关：
  compact   ~32k 可用
  standard  ~128k-200k 可用
  extended  ~500k-1M 可用
  unlimited 一次处理所有章节（one-shot）
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

CONTEXT_PROFILES = {
    "compact":   {"leaf_batch": 10,  "rollup_batch": 15},
    "standard":  {"leaf_batch": 20,  "rollup_batch": 30},
    "extended":  {"leaf_batch": 60,  "rollup_batch": 80},
    "unlimited": {"leaf_batch": 9999, "rollup_batch": 9999},
}
DEFAULT_PROFILE = "standard"

# 叶子章节长度阈值：超过此行数时改用头尾采样
HEAD_LINES = 200
TAIL_LINES = 50
SAMPLE_THRESHOLD = HEAD_LINES + TAIL_LINES


def get_leaves(tree: list) -> list:
    """递归提取所有叶子节点（子章节为空的节点）"""
    leaves = []
    for node in tree:
        if not node.get("子章节"):
            leaves.append(node)
        else:
            leaves.extend(get_leaves(node["子章节"]))
    return leaves


def get_ready_internals(tree: list, done_set: set) -> list:
    """
    提取所有"可汇总"内部节点：
      - 自身未完成
      - 所有直接子节点已完成
    按深度从深到浅排序，确保深层先处理。
    """
    candidates = []  # [(depth, node)]

    def traverse(nodes, depth=0):
        for node in nodes:
            children = node.get("子章节", [])
            if not children:
                continue  # 叶子节点，Phase 1 处理，跳过
            traverse(children, depth + 1)
            if node["编号"] in done_set:
                continue  # 已完成，跳过
            if all(c["编号"] in done_set for c in children):
                candidates.append((depth, node))

    traverse(tree)
    candidates.sort(key=lambda x: -x[0])  # 深度降序
    return [node for _, node in candidates]


def load_existing(summary_path: Path):
    """加载已有摘要，返回 (data_dict, done_set, summary_map)"""
    if not summary_path.exists():
        empty = {"摘要列表": []}
        return empty, set(), {}
    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("摘要列表", [])
    done_set = {item["编号"] for item in items}
    summary_map = {item["编号"]: item["摘要"] for item in items}
    return data, done_set, summary_map


def extract_leaf_content(chapter: dict, all_lines: list) -> tuple[str, bool, int]:
    """
    提取叶子章节正文。
    长章节（超过阈值）改用头尾采样，避免撑爆上下文。
    返回 (content, is_sampled, total_lines)
    """
    start = chapter["起始行"] - 1
    end = chapter["结束行"]
    lines = all_lines[start:end]
    total = len(lines)

    if total <= SAMPLE_THRESHOLD:
        return "\n".join(lines), False, total

    head = lines[:HEAD_LINES]
    tail = lines[-TAIL_LINES:]
    omitted = total - HEAD_LINES - TAIL_LINES
    content = (
        "\n".join(head)
        + f"\n\n[... 省略中间 {omitted} 行（详细规格/表格），摘要应覆盖章节整体意图而非细节 ...]\n\n"
        + "\n".join(tail)
    )
    return content, True, total


def sep(char="─", width=60):
    print(char * width)


def print_write_instructions(summary_path: Path, is_first_batch: bool):
    print("[写入说明]")
    if is_first_batch:
        print(f"  文件尚不存在，请用 Write 工具创建：{summary_path}")
        print("""  初始结构：
  {
    "生成时间": "<当前时间>",
    "章节数": <本批数量>,
    "摘要列表": [ <新条目> ]
  }""")
    else:
        print(f"  1. 用 Read 工具读取：{summary_path}")
        print(f"  2. 将新条目追加到 摘要列表 数组")
        print(f"  3. 更新 章节数 字段，用 Write 工具写回整个文件")
    print("""  每条新条目格式：
  {
    "编号":  "章节编号（字符串）",
    "标题":  "章节标题",
    "层级":   层级数字,
    "起始行": 起始行号,
    "结束行": 结束行号,
    "摘要":  "2-3 句摘要"
  }""")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Bottom-Up 章节摘要生成（重复运行直到完成）"
    )
    parser.add_argument("项目路径", help="项目根目录路径")
    parser.add_argument(
        "--profile",
        choices=list(CONTEXT_PROFILES.keys()),
        default=DEFAULT_PROFILE,
        help=f"上下文档案（默认 {DEFAULT_PROFILE}）",
    )
    args = parser.parse_args()

    cfg = CONTEXT_PROFILES[args.profile]
    项目路径 = Path(args.项目路径)
    解析目录 = 项目路径 / "10_解析结果"
    index_path = 解析目录 / "章节索引.json"
    md_path = 解析目录 / "标书全文.md"
    summary_path = 解析目录 / "章节摘要.json"
    total_summary_path = 解析目录 / "标书总摘要.md"

    for p in [index_path, md_path]:
        if not p.exists():
            print(f"[错误] 文件不存在：{p}", file=sys.stderr)
            print("请先运行 split_chapters.py 生成章节索引", file=sys.stderr)
            sys.exit(1)

    with open(index_path, encoding="utf-8") as f:
        index_data = json.load(f)

    chapter_tree = index_data.get("章节树", [])
    flat_list = index_data.get("章节列表", [])
    total_count = len(flat_list)

    # 兼容无层级结构（章节树为空）时退化为全叶子模式
    if not chapter_tree:
        chapter_tree = [dict(c, 子章节=[]) for c in flat_list]

    all_lines = md_path.read_text(encoding="utf-8").splitlines()
    existing_data, done_set, summary_map = load_existing(summary_path)
    done_count = len(done_set)

    # ── 阶段判断 ────────────────────────────────────────────────

    all_leaves = get_leaves(chapter_tree)
    pending_leaves = [c for c in all_leaves if c["编号"] not in done_set]
    pending_internals = get_ready_internals(chapter_tree, done_set)

    # ── Phase 3：全部完成，生成总摘要 ───────────────────────────

    if not pending_leaves and not pending_internals:
        if total_summary_path.exists():
            print(f"✅ 全部 {total_count} 个章节摘要已完成，标书总摘要已存在。")
            return

        sep("=")
        print("  标书总摘要生成任务（Phase 3 / Final）")
        print(f"  进度：{done_count}/{total_count} 章节摘要已完成")
        sep("=")
        print()
        print("[任务] 基于以下所有章节摘要，生成标书总摘要。")
        print(f"[写入] 用 Write 工具写入：{total_summary_path}\n")
        print("输出格式：\n")
        print(
            "# 标书总摘要\n"
            "\n## 项目背景与建设目标\n"
            "\n## 核心技术需求概览\n"
            "\n## 评分项分布\n"
            "（列出各大项及分值；如无则注明「标书未包含评分细则」）\n"
            "\n## 截标时间与应标要求\n"
            "（投标截止时间、开标时间、资质要求）\n"
            "\n## 需要特别关注的事项\n"
            "（风险点、特殊要求、易遗漏条款）\n"
        )
        sep()
        print("已完成的章节摘要（按编号顺序）：\n")

        sorted_items = sorted(
            existing_data["摘要列表"],
            key=lambda x: int(x["编号"]) if x["编号"].isdigit() else 9999
        )
        for item in sorted_items:
            print(f"[{item['编号']}] 层级{item['层级']} {item['标题']}")
            print(f"  {item['摘要']}\n")
        return

    # ── Phase 1：叶子节点 ────────────────────────────────────────

    if pending_leaves:
        batch = pending_leaves[:cfg["leaf_batch"]]
        remaining_after = len(pending_leaves) - len(batch)

        sep("=")
        print("  叶子章节摘要（Phase 1）")
        print(f"  进度：已完成 {done_count}/{total_count} | "
              f"本批 {len(batch)} 个 | 处理后剩余叶子 {remaining_after} 个")
        sep("=")
        print()
        print(f"[任务] 为以下 {len(batch)} 个叶子章节各生成 2-3 句摘要，要求：")
        print("  - 概括章节主要内容与核心要求")
        print("  - 标注关键技术指标、时间节点、评分分值（如有）")
        print("  - 指出与应标响应密切相关的重点\n")
        print_write_instructions(summary_path, done_count == 0)

        if remaining_after > 0:
            print(f"[续接] 本批完成后，再次运行本命令处理剩余 {remaining_after} 个叶子节点。\n")
        elif pending_internals:
            print(f"[续接] 叶子节点处理完后，再次运行进入 Phase 2（汇总 {len(pending_internals)} 个父章节）。\n")
        else:
            print(f"[续接] 完成后再次运行，进入 Phase 3 生成标书总摘要。\n")

        sep()
        for i, chapter in enumerate(batch, 1):
            content, sampled, total_lines = extract_leaf_content(chapter, all_lines)
            tag = f"头尾采样，原文 {total_lines} 行" if sampled else f"{total_lines} 行"
            print(f"\n【{i}/{len(batch)}】编号:{chapter['编号']} | 层级:{chapter.get('层级',1)} | {tag}")
            print(f"标题: {chapter['标题']} | 行号: {chapter['起始行']}-{chapter['结束行']}")
            sep("·", 40)
            print(content)
        return

    # ── Phase 2：内部节点汇总 ────────────────────────────────────

    batch = pending_internals[:cfg["rollup_batch"]]
    remaining_after = len(pending_internals) - len(batch)

    sep("=")
    print("  父章节汇总（Phase 2）")
    print(f"  进度：已完成 {done_count}/{total_count} | "
          f"本批 {len(batch)} 个 | 处理后剩余汇总节点 {remaining_after} 个")
    sep("=")
    print()
    print(f"[任务] 根据各父章节的子摘要，为以下 {len(batch)} 个章节生成 2-4 句汇总摘要。")
    print("  汇总摘要应覆盖所有子章节的核心要点，不重复罗列细节。\n")
    print_write_instructions(summary_path, done_count == 0)

    if remaining_after > 0:
        print(f"[续接] 本批完成后，再次运行处理剩余 {remaining_after} 个汇总节点。\n")
    else:
        print(f"[续接] 完成后再次运行，进入 Phase 3 生成标书总摘要。\n")

    sep()
    for i, node in enumerate(batch, 1):
        print(f"\n【{i}/{len(batch)}】编号:{node['编号']} | 层级:{node.get('层级',1)}")
        print(f"标题: {node['标题']} | 行号: {node['起始行']}-{node['结束行']}")
        print("子章节摘要：")
        for child in node.get("子章节", []):
            child_summary = summary_map.get(child["编号"], "（摘要缺失，请检查 Phase 1 是否完整）")
            print(f"  [{child['编号']}] {child['标题']}")
            print(f"       {child_summary}")


if __name__ == "__main__":
    main()
