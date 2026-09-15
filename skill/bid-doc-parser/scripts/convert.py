#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = ["markitdown[docx,pdf,pptx]"]
# ///
"""
convert.py - 将 DOCX/PDF 转换为 Markdown（使用 markitdown）

用法：
  uv run scripts/convert.py <输入文件> -o <输出文件路径>

示例：
  uv run scripts/convert.py 标书.docx -o 10_解析结果/标书全文.md
"""
import sys
import argparse
from pathlib import Path

# Windows UTF-8 修复
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def convert(input_path: Path, output_path: Path) -> bool:
    try:
        from markitdown import MarkItDown
    except ImportError:
        print("[错误] markitdown 未安装，请运行：uv sync", file=sys.stderr)
        return False

    if not input_path.exists():
        print(f"[错误] 文件不存在：{input_path}", file=sys.stderr)
        return False

    print(f"正在转换：{input_path.name} → {output_path.name}")
    try:
        md = MarkItDown()
        result = md.convert(str(input_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.text_content, encoding="utf-8")
        lines = result.text_content.count("\n")
        chars = len(result.text_content)
        print(f"[OK] 转换完成：{lines} 行 / {chars} 字符")
        return True
    except Exception as e:
        print(f"[错误] 转换失败：{e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="将 DOCX/PDF 转换为 Markdown")
    parser.add_argument("input", help="输入文件路径（.docx 或 .pdf）")
    parser.add_argument("-o", "--output", required=True, help="输出 .md 文件路径")
    args = parser.parse_args()

    success = convert(Path(args.input), Path(args.output))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
