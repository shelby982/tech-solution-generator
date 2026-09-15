#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
标书检索工具 - 5种模式精准定位目标内容

用法：
  uv run scripts/search.py [项目路径] --mode summary
  uv run scripts/search.py [项目路径] --mode chapters
  uv run scripts/search.py [项目路径] --mode section --chapter "3.2"
  uv run scripts/search.py [项目路径] --mode keyword --query "关键词1,关键词2" --context 5
  uv run scripts/search.py [项目路径] --mode lines --start 100 --end 200

输出上限：每次输出约 4500 个字符（约 3000 token），超出会给出截断提示。
"""

import sys
import json
import subprocess
import argparse
from pathlib import Path

MAX_CHARS = 4500  # 输出字符上限，对应约 3000 token


def check_ripgrep() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def truncate_output(text: str, header: str = "") -> str:
    """超出上限时截断并提示"""
    full = header + text
    if len(full) <= MAX_CHARS:
        return full
    truncated = full[:MAX_CHARS]
    return truncated + f"\n\n[截断] 输出已达 {MAX_CHARS} 字符上限。请缩小检索范围或使用 --mode lines 分段读取。"


def mode_summary(项目路径: Path):
    """模式1：读取所有章节摘要"""
    摘要文件 = 项目路径 / "10_解析结果" / "章节摘要.json"
    if not 摘要文件.exists():
        print("[错误] 章节摘要.json 不存在，请先运行 chapter-indexer", file=sys.stderr)
        sys.exit(1)

    data = load_json(摘要文件)
    lines = [f"=== 章节摘要（共 {data.get('章节数', 0)} 个章节）===\n"]
    for item in data.get("摘要列表", []):
        lines.append(f"### {item['编号']} {item['标题']}（第{item['起始行']}-{item['结束行']}行）")
        lines.append(item.get("摘要", "（无摘要）"))
        lines.append("")

    print(truncate_output("\n".join(lines)))


def mode_chapters(项目路径: Path):
    """模式2：列出所有章节目录"""
    索引文件 = 项目路径 / "10_解析结果" / "章节索引.json"
    if not 索引文件.exists():
        print("[错误] 章节索引.json 不存在，请先运行 chapter-indexer", file=sys.stderr)
        sys.exit(1)

    data = load_json(索引文件)
    print(f"=== 章节列表（共 {data.get('总行数', '?')} 行）===\n")
    for 章节 in data.get("章节列表", []):
        缩进 = "  " * (章节.get("层级", 1) - 1)
        print(f"{缩进}[{章节['编号']:>3}] 第{章节['起始行']}-{章节['结束行']}行 "
              f"({章节.get('字数', '?')}字) {章节['标题']}")


def mode_section(项目路径: Path, chapter_query: str):
    """模式3：提取指定章节内容"""
    索引文件 = 项目路径 / "10_解析结果" / "章节索引.json"
    if not 索引文件.exists():
        print("[错误] 章节索引.json 不存在", file=sys.stderr)
        sys.exit(1)

    data = load_json(索引文件)
    章节列表 = data.get("章节列表", [])

    目标章节 = None
    for 章节 in 章节列表:
        if chapter_query in 章节.get("编号", "") or chapter_query in 章节.get("标题", ""):
            目标章节 = 章节
            break

    if not 目标章节:
        print(f"[错误] 未找到匹配章节：{chapter_query}", file=sys.stderr)
        print("可用章节（编号 标题）：")
        for c in 章节列表:
            print(f"  [{c['编号']}] {c['标题']}")
        sys.exit(1)

    标书文件 = 项目路径 / "10_解析结果" / "标书全文.md"
    if not 标书文件.exists():
        print("[错误] 标书全文.md 不存在", file=sys.stderr)
        sys.exit(1)

    all_lines = 标书文件.read_text(encoding="utf-8").splitlines()
    start = 目标章节["起始行"] - 1
    end = 目标章节["结束行"]
    内容 = "\n".join(all_lines[start:end])

    header = f"=== 来源：{目标章节['编号']} {目标章节['标题']}（第{目标章节['起始行']}-{目标章节['结束行']}行）===\n\n"
    output = truncate_output(内容, header)
    print(output)

    if len(header) + len(内容) > MAX_CHARS:
        mid = 目标章节["起始行"] + (目标章节["结束行"] - 目标章节["起始行"]) // 2
        print(f"\n如需后续内容：--mode lines --start {mid} --end {目标章节['结束行']}")


def mode_keyword(项目路径: Path, query: str, context: int):
    """模式4：关键词检索（优先 ripgrep，降级 Python）"""
    标书文件 = 项目路径 / "10_解析结果" / "标书全文.md"
    if not 标书文件.exists():
        print("[错误] 标书全文.md 不存在", file=sys.stderr)
        sys.exit(1)

    关键词列表 = [kw.strip() for kw in query.split(",")]
    use_rg = check_ripgrep()
    print(f"检索关键词：{关键词列表}（上下文 {context} 行）\n")

    if use_rg:
        pattern = "|".join(关键词列表)
        cmd = ["rg", "-n", f"-C{context}", "--no-heading", pattern, str(标书文件)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        结果 = proc.stdout.strip()
    else:
        lines = 标书文件.read_text(encoding="utf-8").splitlines()
        output_lines = []
        shown = set()
        for i, line in enumerate(lines):
            if any(kw in line for kw in 关键词列表):
                start = max(0, i - context)
                end = min(len(lines), i + context + 1)
                for j in range(start, end):
                    if j not in shown:
                        prefix = ">" if j == i else " "
                        output_lines.append(f"{j+1}{prefix}{lines[j]}")
                        shown.add(j)
                output_lines.append("--")
        结果 = "\n".join(output_lines)

    if not 结果:
        print(f"[未找到] 关键词 {关键词列表} 在标书中无匹配")
        return

    print(truncate_output(结果))


def mode_lines(项目路径: Path, start: int, end: int):
    """模式5：按行号范围提取内容"""
    标书文件 = 项目路径 / "10_解析结果" / "标书全文.md"
    if not 标书文件.exists():
        标书文件 = 项目路径 / "10_解析结果" / "技术规格书全文.md"
    if not 标书文件.exists():
        print("[错误] 未找到标书文件", file=sys.stderr)
        sys.exit(1)

    all_lines = 标书文件.read_text(encoding="utf-8").splitlines()
    提取行 = all_lines[start - 1:end]
    内容 = "\n".join(提取行)

    header = f"=== 来源：{标书文件.name}（第{start}-{end}行，共{len(提取行)}行）===\n\n"
    print(truncate_output(内容, header))

    if len(header) + len(内容) > MAX_CHARS:
        mid = start + len(提取行) // 2
        print(f"\n如需后续内容：--mode lines --start {mid} --end {end}")


def main():
    parser = argparse.ArgumentParser(description="标书内容检索工具（5种模式）")
    parser.add_argument("项目路径", help="项目根目录路径")
    parser.add_argument("--mode", required=True,
                        choices=["summary", "chapters", "section", "keyword", "lines"],
                        help="检索模式")
    parser.add_argument("--chapter", help="章节编号或名称关键词（mode=section 使用）")
    parser.add_argument("--query", help="检索关键词，多个用逗号分隔（mode=keyword 使用）")
    parser.add_argument("--context", type=int, default=5, help="关键词上下文行数（默认5）")
    parser.add_argument("--start", type=int, help="起始行号（mode=lines 使用）")
    parser.add_argument("--end", type=int, help="结束行号（mode=lines 使用）")

    args = parser.parse_args()
    项目路径 = Path(args.项目路径)

    if not 项目路径.exists():
        print(f"[错误] 项目路径不存在：{项目路径}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "summary":
        mode_summary(项目路径)
    elif args.mode == "chapters":
        mode_chapters(项目路径)
    elif args.mode == "section":
        if not args.chapter:
            print("[错误] --mode section 需要 --chapter 参数", file=sys.stderr)
            sys.exit(1)
        mode_section(项目路径, args.chapter)
    elif args.mode == "keyword":
        if not args.query:
            print("[错误] --mode keyword 需要 --query 参数", file=sys.stderr)
            sys.exit(1)
        mode_keyword(项目路径, args.query, args.context)
    elif args.mode == "lines":
        if not args.start or not args.end:
            print("[错误] --mode lines 需要 --start 和 --end 参数", file=sys.stderr)
            sys.exit(1)
        mode_lines(项目路径, args.start, args.end)


if __name__ == "__main__":
    main()
