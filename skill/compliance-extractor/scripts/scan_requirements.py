#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
标书需求全文扫描脚本 - 不依赖关键词触发，逐章提取所有强制性条款、定量指标、评分细则

用法：
  uv run scripts/scan_requirements.py [项目路径]
  uv run scripts/scan_requirements.py [项目路径] --no-spec
  uv run scripts/scan_requirements.py [项目路径] --output [路径]

注：从项目根目录运行，或使用相对路径指向脚本所在位置
"""

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime


# ── 触发词配置 ───────────────────────────────────────────────
MANDATORY_TRIGGERS = re.compile(
    r'(必须|应当|须|不得|禁止|严禁|不可|不允许)'
)
CONSEQUENCE_TRIGGERS = re.compile(
    r'(否则|视为无效|废标|否决|不予通过|视为不满足|不符合要求)'
)

# 定量指标正则：匹配"指标名 运算符 数值 单位"
QUANTITATIVE_PATTERN = re.compile(
    r'([\u4e00-\u9fa5a-zA-Z]{2,10})'
    r'\s*'
    r'(≥|≤|>|<|不低于|不超过|不少于|不得超过|达到|不得低于)'
    r'\s*'
    r'(\d+\.?\d*)'
    r'\s*'
    r'([%％秒s ms个人年月天小时次]?)'
)

# 评分细则正则
SCORING_PATTERN = re.compile(
    r'.{0,20}(满分|得分|评分|加分)\s*[：:为是]?\s*(\d+)\s*分'
)


def load_chapter_index(project_path: Path) -> list:
    """读取章节索引，获取各章节行号范围"""
    index_file = project_path / "10_解析结果" / "章节索引.json"
    if not index_file.exists():
        return []
    data = json.loads(index_file.read_text(encoding="utf-8"))
    return data.get("章节列表", [])


def read_lines(md_file: Path) -> list[str]:
    return md_file.read_text(encoding="utf-8").split("\n")


def scan_mandatory(lines: list[str], start: int, end: int, chapter_name: str) -> list[dict]:
    """扫描强制性句子（按行扫描，不跨行合并）"""
    results = []
    for lineno in range(start - 1, min(end, len(lines))):
        line = lines[lineno].strip()
        if not line or line.startswith("#"):
            continue
        if MANDATORY_TRIGGERS.search(line):
            has_consequence = bool(CONSEQUENCE_TRIGGERS.search(line))
            results.append({
                "id": f"REQ-PLACEHOLDER-{lineno+1}",
                "原文": line[:200],
                "行号": lineno + 1,
                "触发词": MANDATORY_TRIGGERS.search(line).group(0),
                "后果词": CONSEQUENCE_TRIGGERS.search(line).group(0) if has_consequence else "",
                "有明确废标后果": has_consequence,
                "来源章节": chapter_name,
                "类型": "强制性条款"
            })
    return results


def scan_quantitative(lines: list[str], start: int, end: int, chapter_name: str) -> list[dict]:
    """扫描定量指标"""
    results = []
    for lineno in range(start - 1, min(end, len(lines))):
        line = lines[lineno].strip()
        if not line or line.startswith("#"):
            continue
        for m in QUANTITATIVE_PATTERN.finditer(line):
            op_raw = m.group(2)
            op_map = {"不低于": "≥", "不少于": "≥", "达到": "≥",
                      "不超过": "≤", "不得超过": "≤", "不得低于": "≥"}
            op = op_map.get(op_raw, op_raw)
            results.append({
                "id": f"QT-PLACEHOLDER-{lineno+1}",
                "指标名": m.group(1),
                "运算符": op,
                "数值": float(m.group(3)),
                "单位": m.group(4).strip(),
                "行号": lineno + 1,
                "原文": line[:200],
                "来源章节": chapter_name,
                "搜索关键词": []
            })
    return results


def scan_scoring(lines: list[str], start: int, end: int, chapter_name: str) -> list[dict]:
    """扫描评分细则"""
    results = []
    for lineno in range(start - 1, min(end, len(lines))):
        line = lines[lineno].strip()
        if not line:
            continue
        m = SCORING_PATTERN.search(line)
        if m:
            results.append({
                "id": f"SC-PLACEHOLDER-{lineno+1}",
                "原文": line[:200],
                "行号": lineno + 1,
                "评分类型": m.group(1),
                "分值": int(m.group(2)),
                "来源章节": chapter_name,
                "类型": "评分细则"
            })
    return results


def scan_all(source_files: list[Path], chapters: list[dict]) -> dict:
    """整合扫描：逐文件、逐章节扫描三类内容"""
    all_mandatory = []
    all_quantitative = []
    all_scoring = []
    source_names = [f.name for f in source_files]

    for src_file in source_files:
        lines = read_lines(src_file)
        total_lines = len(lines)

        if chapters:
            for i, chapter in enumerate(chapters):
                start = chapter.get("起始行", 1)
                end = chapters[i + 1].get("起始行", total_lines + 1) - 1 \
                    if i + 1 < len(chapters) else total_lines
                name = chapter.get("标题", f"第{i+1}章")
                all_mandatory.extend(scan_mandatory(lines, start, end, name))
                all_quantitative.extend(scan_quantitative(lines, start, end, name))
                all_scoring.extend(scan_scoring(lines, start, end, name))
        else:
            chunk = 500
            for start in range(1, total_lines + 1, chunk):
                end = min(start + chunk - 1, total_lines)
                name = f"行{start}-{end}"
                all_mandatory.extend(scan_mandatory(lines, start, end, name))
                all_quantitative.extend(scan_quantitative(lines, start, end, name))
                all_scoring.extend(scan_scoring(lines, start, end, name))

    # 去重（同行号+触发词）并全局重新编号
    seen = set()
    deduped_mandatory = []
    for item in all_mandatory:
        key = (item["行号"], item["触发词"])
        if key not in seen:
            seen.add(key)
            deduped_mandatory.append(item)

    for i, item in enumerate(deduped_mandatory, 1):
        item["id"] = f"REQ-{i:03d}"
    for i, item in enumerate(all_quantitative, 1):
        item["id"] = f"QT-{i:03d}"
    for i, item in enumerate(all_scoring, 1):
        item["id"] = f"SC-{i:03d}"

    return {
        "扫描时间": datetime.now().strftime("%Y-%m-%d"),
        "来源文件": source_names,
        "统计": {
            "强制性条款": len(deduped_mandatory),
            "定量指标": len(all_quantitative),
            "评分细则": len(all_scoring)
        },
        "强制性条款": deduped_mandatory,
        "定量指标": all_quantitative,
        "评分细则": all_scoring
    }


def print_summary(result: dict):
    stats = result["统计"]
    print(f"\n  强制性条款：{stats['强制性条款']} 条")
    print(f"  定量指标：  {stats['定量指标']} 条")
    print(f"  评分细则：  {stats['评分细则']} 条")
    print(f"\n请使用 compliance-extractor Skill 进行 Layer 1 检索确认。")


def main():
    parser = argparse.ArgumentParser(description="标书需求全文扫描")
    parser.add_argument("project_path", nargs="?", default=".", help="项目路径")
    parser.add_argument("--no-spec", action="store_true", help="不扫描技术规格书")
    parser.add_argument("--output", help="输出路径（默认：10_解析结果/原始需求库.json）")
    args = parser.parse_args()

    project_path = Path(args.project_path)
    output_path = Path(args.output) if args.output else \
        project_path / "10_解析结果" / "原始需求库.json"

    source_files = []
    bid_full = project_path / "10_解析结果" / "标书全文.md"
    spec_full = project_path / "10_解析结果" / "技术规格书全文.md"

    if bid_full.exists():
        source_files.append(bid_full)
    if spec_full.exists() and not args.no_spec:
        source_files.append(spec_full)

    if not source_files:
        print("[错误] 未找到任何可扫描文件（标书全文.md / 技术规格书全文.md），请先运行 bid-doc-parser")
        sys.exit(1)

    chapters = load_chapter_index(project_path)
    result = scan_all(source_files, chapters)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[完成] 原始需求库已写入：{output_path}")
    print_summary(result)


if __name__ == "__main__":
    main()
