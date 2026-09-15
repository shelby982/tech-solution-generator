#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
章节覆盖热力图生成脚本

用法：
  uv run scripts/coverage_map.py [项目路径]
  uv run scripts/coverage_map.py [项目路径] --threshold 80
  uv run scripts/coverage_map.py [项目路径] --output [路径]

注：从项目根目录运行，或使用相对路径指向脚本所在位置
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Windows 终端 UTF-8 输出
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


LEVEL_ICONS = {
    "green":  "[OK]",  # >= 90%
    "yellow": "[WARN]",  # 70-89%
    "orange": "[ALERT]",  # 50-69%
    "red":    "[FAIL]",  # < 50%
}


def get_level(rate: float, threshold: int) -> tuple[str, str]:
    """返回 (level_key, icon)"""
    if rate >= 0.90:
        return "green", LEVEL_ICONS["green"]
    if rate >= threshold / 100:
        return "yellow", LEVEL_ICONS["yellow"]
    if rate >= 0.50:
        return "orange", LEVEL_ICONS["orange"]
    return "red", LEVEL_ICONS["red"]


def load_coverage(project_path: Path) -> dict:
    coverage_file = project_path / "10_解析结果" / "覆盖核验结果.json"
    if not coverage_file.exists():
        print(f"[错误] 未找到覆盖核验结果.json，请先运行 verify_coverage.py")
        sys.exit(1)
    return json.loads(coverage_file.read_text(encoding="utf-8"))


def load_uncovered_details(coverage_data: dict) -> dict[str, list]:
    """按章节整理未响应条款"""
    chapter_issues: dict[str, list] = {}

    for item in coverage_data.get("定量指标核验", []):
        if item["状态"] == "未明文承诺":
            ch = item.get("来源章节", "未知章节")
            chapter_issues.setdefault(ch, []).append(
                f"{item['id']} {item['指标名']} {item['约束']}（定量，草稿无明文数字）"
            )

    for item in coverage_data.get("定性条款核验", []):
        if item.get("状态") in ("未响应", "已提及但不充分"):
            ch = item.get("来源章节", "未知章节")
            tag = "未响应" if item["状态"] == "未响应" else "内容不充分"
            chapter_issues.setdefault(ch, []).append(
                f"{item.get('id','')} {item.get('原文摘要','')[:40]}（{tag}）"
            )

    return chapter_issues


def generate_heatmap(coverage_data: dict, threshold: int) -> str:
    density = coverage_data.get("章节覆盖密度", {})
    if not density:
        return "\n> 暂无章节覆盖密度数据（需先完成 verify_coverage.py）\n"

    uncovered = load_uncovered_details(coverage_data)

    lines = [
        f"\n## 章节覆盖热力图\n",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"> 覆盖率 >= 90% [OK]  /  {threshold}-89% [WARN]  /  50-{threshold-1}% [ALERT]  /  < 50% [FAIL]\n",
        "| 标书章节 | 要求总数 | 已响应 | 未响应/不充分 | 覆盖率 | 状态 |",
        "|----------|----------|--------|---------------|--------|------|",
    ]

    danger_chapters = []

    for chapter, d in sorted(density.items()):
        rate = d.get("覆盖率", 0.0)
        responded = d.get("已响应", 0)
        total = d.get("要求数", 0)
        not_covered = total - responded
        level, icon = get_level(rate, threshold)
        lines.append(
            f"| {chapter} | {total} | {responded} | {not_covered} | {rate*100:.0f}% | {icon} |"
        )
        if level in ("orange", "red"):
            danger_chapters.append((chapter, rate, not_covered, level, uncovered.get(chapter, [])))

    if danger_chapters:
        lines.append(f"\n### 危险区域（覆盖率 < {threshold}%）\n")
        for chapter, rate, not_covered, level, issues in sorted(danger_chapters, key=lambda x: x[1]):
            icon = LEVEL_ICONS[level]
            lines.append(f"#### {icon} {chapter}（覆盖率 {rate*100:.0f}%）")
            if issues:
                lines.append(f"未响应/不充分条款（{len(issues)}条）：")
                for issue in issues[:10]:  # 最多展示10条
                    lines.append(f"  - {issue}")
                if len(issues) > 10:
                    lines.append(f"  - ...（共 {len(issues)} 条，查看 覆盖核验结果.json 获取完整列表）")
            else:
                lines.append(f"未响应条款：{not_covered} 条（详见 覆盖核验结果.json）")
            # 空行分隔
            lines.append("")
    else:
        lines.append(f"\n> 所有章节覆盖率均达到 {threshold}% 以上，无危险区域。[OK]\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成章节覆盖热力图")
    parser.add_argument("project_path", nargs="?", default=".", help="项目路径")
    parser.add_argument("--threshold", type=int, default=70, help="危险区阈值，默认 70")
    parser.add_argument("--output", help="输出文件路径（默认：追加到最新核验报告）")
    args = parser.parse_args()

    project_path = Path(args.project_path)
    coverage_data = load_coverage(project_path)
    heatmap_md = generate_heatmap(coverage_data, args.threshold)

    if args.output:
        out = Path(args.output)
        out.write_text(heatmap_md, encoding="utf-8")
        print(f"[完成] 热力图已写入：{out}")
    else:
        # 追加到最新核验报告
        draft_dir = project_path / "20_应标草稿"
        reports = sorted(draft_dir.glob("核验报告_*.md"), reverse=True)
        if reports:
            report = reports[0]
            existing = report.read_text(encoding="utf-8")
            # 避免重复追加
            if "章节覆盖热力图" not in existing:
                report.write_text(existing + "\n\n---\n" + heatmap_md, encoding="utf-8")
                print(f"[完成] 热力图已追加到：{report.name}")
            else:
                # 替换旧热力图
                before = existing.split("\n## 章节覆盖热力图")[0]
                report.write_text(before + "\n\n---\n" + heatmap_md, encoding="utf-8")
                print(f"[完成] 热力图已更新：{report.name}")
        else:
            # 无报告则单独输出
            out = draft_dir / f"热力图_{datetime.now().strftime('%Y%m%d')}.md"
            out.write_text(heatmap_md, encoding="utf-8")
            print(f"[完成] 热力图已写入：{out.name}")

    # 终端简要打印
    print(heatmap_md[:1000])


if __name__ == "__main__":
    main()
