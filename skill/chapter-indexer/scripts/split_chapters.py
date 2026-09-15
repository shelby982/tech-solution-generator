#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
章节分割脚本 - 解析标书全文 Markdown，提取章节结构

用法：
  uv run scripts/split_chapters.py <标书全文.md路径>

输出：
  同目录下生成 章节索引.json（含平铺列表 章节列表 和嵌套树 章节树）
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime


def 提取章节结构(md内容: str) -> list:
    """从 Markdown 内容提取章节结构（平铺列表）"""
    章节列表 = []
    行列表 = md内容.splitlines()

    标题模式列表 = [
        (re.compile(r'^(#{1,4})\s+(.+)$'), "markdown"),
        (re.compile(r'^(第[一二三四五六七八九十百\d]+[章节篇部分])\s*(.*)$'), "中文章节"),
        (re.compile(r'^(\d+(?:\.\d+)*)[.、]\s+(.+)$'), "数字编号"),
        (re.compile(r'^([一二三四五六七八九十]+)[、.]\s*(.+)$'), "中文序号"),
    ]

    当前章节 = None

    for 行号, 行内容 in enumerate(行列表, 1):
        行内容_stripped = 行内容.strip()
        if not 行内容_stripped:
            continue

        for 模式, 类型 in 标题模式列表:
            匹配 = 模式.match(行内容_stripped)
            if 匹配:
                if 类型 == "markdown":
                    层级 = len(匹配.group(1))
                    标题文字 = 匹配.group(2).strip()
                elif 类型 == "数字编号":
                    层级 = len(匹配.group(1).split("."))
                    标题文字 = f"{匹配.group(1)} {匹配.group(2).strip()}"
                else:
                    层级 = 1
                    标题文字 = f"{匹配.group(1)}{匹配.group(2).strip()}"

                if 当前章节:
                    当前章节["结束行"] = 行号 - 1
                    当前章节["字数"] = sum(
                        len(行列表[i]) for i in range(当前章节["起始行"] - 1, 行号 - 1)
                    )
                    章节列表.append(当前章节)

                当前章节 = {
                    "编号": str(len(章节列表) + 1),
                    "标题": 标题文字,
                    "层级": 层级,
                    "起始行": 行号,
                    "结束行": len(行列表),
                    "字数": 0
                }
                break

    if 当前章节:
        当前章节["结束行"] = len(行列表)
        当前章节["字数"] = sum(
            len(行列表[i]) for i in range(当前章节["起始行"] - 1, len(行列表))
        )
        章节列表.append(当前章节)

    return 章节列表


def build_hierarchy(flat_chapters: list) -> list:
    """将平铺章节列表构建为嵌套树结构（每个节点含 子章节 列表）"""
    if not flat_chapters:
        return []

    result = []
    stack = []  # (层级, 节点引用)

    for chapter in flat_chapters:
        node = {k: v for k, v in chapter.items()}
        node["子章节"] = []
        level = chapter["层级"]

        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            stack[-1][1]["子章节"].append(node)
        else:
            result.append(node)

        stack.append((level, node))

    return result


def main():
    if len(sys.argv) < 2:
        print("用法：uv run scripts/split_chapters.py <标书全文.md>", file=sys.stderr)
        sys.exit(1)

    md文件路径 = Path(sys.argv[1])

    if not md文件路径.exists():
        print(f"[错误] 文件不存在：{md文件路径}", file=sys.stderr)
        sys.exit(1)

    print(f"正在解析章节结构：{md文件路径.name}")

    md内容 = md文件路径.read_text(encoding="utf-8")
    章节列表 = 提取章节结构(md内容)

    if not 章节列表:
        print("[警告] 未识别到任何章节，可能标书格式特殊")
        print("建议检查标书全文.md 的格式，或手动添加章节标题")
        sys.exit(1)

    索引数据 = {
        "来源文件": md文件路径.name,
        "总行数": len(md内容.splitlines()),
        "总字数": len(md内容),
        "章节数量": len(章节列表),
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "章节列表": 章节列表,
        "章节树": build_hierarchy(章节列表)
    }

    输出路径 = md文件路径.parent / "章节索引.json"
    with open(输出路径, "w", encoding="utf-8") as f:
        json.dump(索引数据, f, ensure_ascii=False, indent=2)

    print(f"\n识别到 {len(章节列表)} 个章节：\n")
    for 章节 in 章节列表:
        缩进 = "  " * (章节["层级"] - 1)
        print(f"{缩进}[{章节['编号']:>3}] 第{章节['起始行']}-{章节['结束行']}行 "
              f"({章节['字数']}字) {章节['标题']}")

    print(f"\n章节索引已保存：{输出路径}")
    print("下一步：运行 gen_summary.py 生成章节摘要")


if __name__ == "__main__":
    main()
