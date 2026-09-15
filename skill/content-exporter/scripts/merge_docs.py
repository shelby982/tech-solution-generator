#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
整合文档脚本 - 按文件名顺序将所有章节草稿合并为完整应标文件

用法：
  uv run scripts/merge_docs.py [项目路径]
"""

import sys
import json
from pathlib import Path
from datetime import datetime


def merge_docs(项目路径: Path) -> bool:
    草稿目录 = 项目路径 / "20_应标草稿"
    输出目录 = 项目路径 / "30_最终输出"
    输出目录.mkdir(exist_ok=True)

    # 读取项目名称
    project_file = 项目路径 / "project.json"
    项目名称 = 项目路径.name
    if project_file.exists():
        项目信息 = json.loads(project_file.read_text(encoding="utf-8"))
        项目名称 = 项目信息.get("项目名称", 项目路径.name)

    # 按文件名排序获取所有章节文件
    章节文件列表 = sorted(草稿目录.glob("章节_*.md"))

    if not 章节文件列表:
        print("[错误] 未找到任何章节草稿文件", file=sys.stderr)
        print(f"请确认 {草稿目录} 目录下有「章节_XX_名称.md」格式的文件", file=sys.stderr)
        return False

    print(f"找到 {len(章节文件列表)} 个章节文件：")

    合并内容 = []
    合并内容.append(f"# {项目名称} 技术应标文件\n")
    合并内容.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    for 文件 in 章节文件列表:
        print(f"  合并: {文件.name}")
        章节内容 = 文件.read_text(encoding="utf-8")
        合并内容.append(章节内容)
        合并内容.append("\n\n")

    输出文件 = 输出目录 / "应标文件_草稿.md"
    输出文件.write_text("".join(合并内容), encoding="utf-8")

    字符数 = len("".join(合并内容))
    print(f"\n整合完成：{输出文件}")
    print(f"总字数：约 {字符数 // 2:,} 字")

    return True


def main():
    if len(sys.argv) < 2:
        print("用法：uv run scripts/merge_docs.py <项目路径>", file=sys.stderr)
        sys.exit(1)

    项目路径 = Path(sys.argv[1])
    if not 项目路径.exists():
        print(f"[错误] 项目路径不存在：{项目路径}", file=sys.stderr)
        sys.exit(1)

    成功 = merge_docs(项目路径)
    sys.exit(0 if 成功 else 1)


if __name__ == "__main__":
    main()
