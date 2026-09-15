#!/usr/bin/env python3
"""
清除测试数据脚本

用法：
  python scripts/clear_data.py          # 交互确认后清除全部
  python scripts/clear_data.py --yes    # 跳过确认直接清除
  python scripts/clear_data.py --keep-db  # 只删上传文件，保留数据库
"""

import argparse
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "backend", "data", "ge_solution.db")
UPLOADS_DIR = os.path.join(ROOT, "backend", "data", "uploads")

TABLES = [
    "material_chunks",
    "block_revisions",
    "project_snapshots",
    "blocks",
    "materials",
    "projects",
]


def count_summary(conn: sqlite3.Connection) -> str:
    lines = []
    for table in reversed(TABLES):  # 从顶层表开始展示
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        n = cur.fetchone()[0]
        lines.append(f"  {table:<22} {n:>5} 行")
    return "\n".join(lines)


def upload_summary() -> str:
    if not os.path.isdir(UPLOADS_DIR):
        return "  uploads/  (目录不存在)"
    dirs = [d for d in os.listdir(UPLOADS_DIR)
            if os.path.isdir(os.path.join(UPLOADS_DIR, d))]
    total_files = sum(
        len(files)
        for _, _, files in os.walk(UPLOADS_DIR)
    )
    return f"  uploads/  {len(dirs)} 个项目目录，共 {total_files} 个文件"


def clear_db(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
    # 重置自增序列
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    print("  ✓ 数据库已清空，自增 ID 已重置")


def clear_uploads() -> None:
    if not os.path.isdir(UPLOADS_DIR):
        print("  ✓ uploads/ 目录不存在，跳过")
        return
    for name in os.listdir(UPLOADS_DIR):
        path = os.path.join(UPLOADS_DIR, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
    print("  ✓ uploads/ 上传文件已清除")


def main():
    parser = argparse.ArgumentParser(description="清除 ge-solution 测试数据")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    parser.add_argument("--keep-db", action="store_true", help="只删上传文件，保留数据库")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"数据库不存在：{DB_PATH}")
        print("没有需要清除的数据。")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)

    print("=== 当前数据概况 ===")
    print(count_summary(conn))
    print(upload_summary())
    print()

    if not args.yes:
        answer = input("确认清除以上全部数据？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消。")
            conn.close()
            sys.exit(0)

    print("\n=== 开始清除 ===")
    if not args.keep_db:
        clear_db(conn)
    else:
        print("  - 跳过数据库（--keep-db）")

    clear_uploads()
    conn.close()
    print("\n清除完成。")


if __name__ == "__main__":
    main()
