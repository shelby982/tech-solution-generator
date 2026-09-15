#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目快速初始化脚本 v2.1 - 专注于目录结构与基础文件生成
用法: python scripts/init_project.py "项目路径" --name "项目名称"
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

def fix_windows_encoding():
    if sys.platform == "win32":
        import io
        if hasattr(sys.stdout, 'buffer'):
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            except: pass
        try: os.system('chcp 65001 >nul 2>&1')
        except: pass

fix_windows_encoding()

PROJECT_DIRS = [
    "00_原始文件/01_标书正文",
    "00_原始文件/02_应标模板",
    "00_原始文件/03_补充答疑",
    "00_原始文件/04_参考资料",
    "00_原始文件/05_商务文件",
    "10_解析结果",
    "20_应标草稿",
    "30_最终输出",
    "project_notes",
    "_state",
]

def create_structure(base_path: Path):
    print(f"  正在初始化项目目录: {base_path}")
    for d in PROJECT_DIRS:
        (base_path / d).mkdir(parents=True, exist_ok=True)
    print("  [OK] 目录结构创建完成")

def create_bid_md(base_path: Path, name: str):
    bid_path = base_path / "BID.md"
    if bid_path.exists():
        return
    content = f"""# 项目名称: {name}

> 创建时间: {datetime.now().strftime('%Y-%m-%d')}
> 状态: 💡 初始化完成

## 1. 项目概况
- **招标人**: 
- **截止时间**: 

## 2. 核心任务清单
- [ ] 1. 文档转换 (markitdown)
- [ ] 2. 需求拆解
- [ ] 3. 编写草稿
"""
    bid_path.write_text(content, encoding='utf-8')
    print("  [OK] 生成 BID.md")

def create_project_json(base_path: Path, name: str):
    json_path = base_path / "project.json"
    if json_path.exists():
        return
    data = {
        "project_name": name,
        "created_at": datetime.now().isoformat(),
        "status": "initialization",
        "current_phase": "A1-文档转换",
        "progress": 0
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("  [OK] 生成 project.json")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--name", help="项目显示名称")
    args = parser.parse_args()

    base_path = Path(args.path).resolve()
    project_name = args.name or base_path.name

    create_structure(base_path)
    create_bid_md(base_path, project_name)
    create_project_json(base_path, project_name)
    print("\n项目初始化成功！")

if __name__ == "__main__":
    main()