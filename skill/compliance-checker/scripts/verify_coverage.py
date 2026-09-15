#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
草稿覆盖核验脚本 - 双轨核验：定量指标精确匹配 + 定性条款结构化输出（供 AI 语义核验）

用法：
  uv run scripts/verify_coverage.py [项目路径] --mode quantitative
  uv run scripts/verify_coverage.py [项目路径] --mode full

注：从项目根目录运行，或使用相对路径指向脚本所在位置
"""

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Windows 终端 UTF-8 输出
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def load_json(path: Path) -> dict:
    if not path.exists():
        print(f"[警告] 文件不存在：{path}")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def grep_in_drafts(draft_dir: Path, keyword: str) -> list[dict]:
    """在草稿目录中搜索关键词，返回命中位置列表"""
    results = []
    for md_file in sorted(draft_dir.glob("章节_*.md")):
        lines = md_file.read_text(encoding="utf-8").split("\n")
        for lineno, line in enumerate(lines, 1):
            if keyword in line:
                results.append({
                    "文件": md_file.name,
                    "行号": lineno,
                    "原文": line.strip()[:150]
                })
    return results


def verify_quantitative(project_path: Path) -> dict:
    """轨道 A：定量指标精确匹配（纯脚本，不交 AI）"""
    qt_file = project_path / "10_解析结果" / "定量指标清单.json"
    draft_dir = project_path / "20_应标草稿"

    qt_data = load_json(qt_file)
    indicators = qt_data.get("指标列表", [])

    if not indicators:
        # 兜底：从原始需求库读取定量指标（Layer 1 未完成时也能运行）
        raw_file = project_path / "10_解析结果" / "原始需求库.json"
        raw = load_json(raw_file)
        for item in raw.get("定量指标", []):
            # 自动生成搜索关键词（数值 + 单位的组合）
            num_str = str(int(item["数值"])) if item["数值"] == int(item["数值"]) \
                else str(item["数值"])
            unit = item.get("单位", "")
            item["搜索关键词"] = [
                f"{num_str}{unit}",
                f"{num_str} {unit}",
                item["运算符"] + num_str,
            ]
            item["约束"] = f"{item['运算符']} {num_str} {unit}".strip()
            item["是否不可偏离"] = item.get("有明确废标后果", False)
            indicators.append(item)

    results = []
    未承诺_红线 = []

    for qt in indicators:
        keywords = qt.get("搜索关键词", [])
        found = False
        evidence = []

        for kw in keywords:
            if not kw.strip():
                continue
            hits = grep_in_drafts(draft_dir, kw)
            if hits:
                found = True
                evidence.extend(hits)

        status = "已明文承诺" if found else "未明文承诺"
        entry = {
            "id": qt.get("id", ""),
            "指标名": qt.get("指标名", ""),
            "约束": qt.get("约束", ""),
            "来源章节": qt.get("来源章节", ""),
            "是否不可偏离": qt.get("是否不可偏离", False),
            "状态": status,
            "证据": evidence[:3]  # 最多保留3条命中
        }
        results.append(entry)

        if not found and qt.get("是否不可偏离", False):
            未承诺_红线.append(entry)

    return {
        "核验时间": datetime.now().strftime("%Y-%m-%d"),
        "定量指标核验": results,
        "统计": {
            "总数": len(results),
            "已明文承诺": sum(1 for r in results if r["状态"] == "已明文承诺"),
            "未明文承诺": sum(1 for r in results if r["状态"] == "未明文承诺"),
            "未承诺且为不可偏离项": len(未承诺_红线)
        },
        "未承诺红线条款": 未承诺_红线
    }


def build_chapter_density(project_path: Path, qt_results: list[dict]) -> dict:
    """按章节汇总覆盖密度（供 Layer 3 热力图使用）"""
    density = {}
    for item in qt_results:
        chapter = item.get("来源章节", "未知章节")
        if chapter not in density:
            density[chapter] = {"要求数": 0, "已响应": 0}
        density[chapter]["要求数"] += 1
        if item["状态"] == "已明文承诺":
            density[chapter]["已响应"] += 1

    for chapter, d in density.items():
        d["覆盖率"] = round(d["已响应"] / d["要求数"], 2) if d["要求数"] > 0 else 0.0

    return density


def print_quantitative_report(qt_result: dict):
    stats = qt_result["统计"]
    print(f"\n=== 定量指标核验结果 ===")
    print(f"  总数：{stats['总数']} 条")
    print(f"  已明文承诺：{stats['已明文承诺']} 条 [OK]")
    print(f"  未明文承诺：{stats['未明文承诺']} 条")
    print(f"  其中不可偏离项未承诺：{stats['未承诺且为不可偏离项']} 条 [FAIL]")

    if qt_result["未承诺红线条款"]:
        print(f"\n【必须处理】以下不可偏离定量指标在草稿中无明文数字：")
        for item in qt_result["未承诺红线条款"]:
            print(f"  {item['id']} | {item['指标名']} {item['约束']} | 来自：{item['来源章节']}")


def main():
    parser = argparse.ArgumentParser(description="草稿覆盖核验")
    parser.add_argument("project_path", nargs="?", default=".", help="项目路径")
    parser.add_argument("--mode", choices=["quantitative", "full"],
                        default="quantitative", help="核验模式")
    parser.add_argument("--output", help="输出路径（默认：10_解析结果/覆盖核验结果.json）")
    args = parser.parse_args()

    project_path = Path(args.project_path)
    output_path = Path(args.output) if args.output else \
        project_path / "10_解析结果" / "覆盖核验结果.json"

    # 前置检查
    draft_dir = project_path / "20_应标草稿"
    if not draft_dir.exists() or not list(draft_dir.glob("章节_*.md")):
        print("[错误] 未找到应标草稿（20_应标草稿/章节_*.md），请先生成章节内容")
        sys.exit(1)

    qt_result = verify_quantitative(project_path)
    density = build_chapter_density(project_path, qt_result["定量指标核验"])

    output_data = {
        **qt_result,
        "章节覆盖密度": density
    }

    if args.mode == "full":
        print("[提示] full 模式：定量核验已完成。")
        print("       定性条款的 AI 语义核验请由 compliance-checker Skill 按章节分批执行。")
        print("       脚本将在 覆盖核验结果.json 中预留 定性条款核验 字段供 AI 填写。")
        output_data["定性条款核验"] = []  # AI 填写

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print_quantitative_report(qt_result)
    print(f"\n[完成] 核验结果已写入：{output_path}")


if __name__ == "__main__":
    main()
