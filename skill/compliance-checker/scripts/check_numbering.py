#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
章节编号验证脚本 - 检查应标草稿的章节编号一致性

用法：
  uv run scripts/check_numbering.py [项目路径]       # 检查 20_应标草稿/ 下所有章节
  uv run scripts/check_numbering.py [文件路径]       # 检查单个文件
  uv run scripts/check_numbering.py [项目路径] --fix # 自动修正编号
  uv run scripts/check_numbering.py [项目路径] --report [报告路径]
"""

import re
import sys
from pathlib import Path
from datetime import datetime


class ChapterNumberValidator:
    """章节序号验证器"""

    def __init__(self):
        self.issues = []
        self.fixes = []

    def validate_file(self, file_path: Path) -> bool:
        """验证单个文件的章节序号"""
        print(f"\n检查文件: {file_path.name}")

        content = file_path.read_text(encoding="utf-8")
        lines = content.split('\n')

        filename_chapter = self._extract_chapter_from_filename(file_path.name)
        issues_found = []

        for i, line in enumerate(lines, 1):
            if line.startswith('#'):
                issue = self._check_heading(line, i, filename_chapter)
                if issue:
                    issues_found.append(issue)

        if issues_found:
            self.issues.extend(issues_found)
            print(f"  发现 {len(issues_found)} 个问题")
            for issue in issues_found:
                print(f"     行 {issue['line']}: {issue['desc']}")
            return False
        else:
            print(f"  序号正确")
            return True

    def _extract_chapter_from_filename(self, filename: str) -> str:
        """从文件名提取章节号（如 章节_01_名称.md -> '1'）"""
        match = re.match(r'章节_(\d+)_', filename)
        if match:
            return str(int(match.group(1)))  # 去掉前导零，03 -> '3'

        match = re.match(r'(\d+)_(\d+)_(\d+)\.md', filename)
        if match:
            return f"{int(match.group(1))}.{int(match.group(2))}.{int(match.group(3))}"

        match = re.match(r'第([一二三四五六七八九十]+)章', filename)
        if match:
            return self._chinese_to_number(match.group(1))

        return None

    def _chinese_to_number(self, chinese: str) -> str:
        mapping = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                   '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        return str(mapping.get(chinese, 0))

    def _check_heading(self, line: str, line_num: int, expected_chapter: str) -> dict:
        """检查单个标题行"""
        match = re.match(r'^(#{1,3})\s+(\d+(?:\.\d+)*)\s+(.+)', line)
        if not match:
            return None

        level = len(match.group(1))
        number = match.group(2)

        parts = number.split('.')

        if level == 1 and len(parts) != 1:
            return {'line': line_num,
                    'desc': f"一级标题应为单数字，实际是 {number}",
                    'current': number, 'expected': parts[0]}

        if level == 2 and len(parts) != 2:
            return {'line': line_num,
                    'desc': f"二级标题应为 X.X 格式，实际是 {number}",
                    'current': number, 'expected': None}

        if level == 3 and len(parts) != 3:
            return {'line': line_num,
                    'desc': f"三级标题应为 X.X.X 格式，实际是 {number}",
                    'current': number, 'expected': None}

        if expected_chapter and not number.startswith(expected_chapter.split('.')[0]):
            return {'line': line_num,
                    'desc': f"章节号 {number} 与文件名不符（应以 {expected_chapter.split('.')[0]} 开头）",
                    'current': number, 'expected': expected_chapter}

        return None

    def fix_file(self, file_path: Path) -> bool:
        """修正文件中的序号问题"""
        correct_chapter = self._extract_chapter_from_filename(file_path.name)
        if not correct_chapter:
            print(f"  无法从文件名确定章节号: {file_path.name}")
            return False

        content = file_path.read_text(encoding="utf-8")

        def replace_number(match):
            level = len(match.group(1))
            old_number = match.group(2) if match.group(2) else ""
            title = match.group(3)

            new_number = correct_chapter if level == 3 else correct_chapter.rsplit('.', 3 - level)[0]

            if old_number != new_number:
                self.fixes.append({'file': file_path.name, 'old': old_number,
                                   'new': new_number, 'title': title})

            return f"{'#' * level} {new_number} {title}"

        new_content = re.sub(
            r'^(#{1,3})\s+(\d+(?:\.\d+)*)\s+(.+)',
            replace_number,
            content,
            flags=re.MULTILINE
        )

        if new_content != content:
            file_path.write_text(new_content, encoding="utf-8")
            print(f"  已修正 {file_path.name}")
            return True

        return False

    def generate_report(self, output_path: Path = None) -> str:
        report = f"""# 章节编号验证报告

**验证时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## 结果总览

- 检查问题数: {len(self.issues)}
- 修正数量: {len(self.fixes)}

---

## 问题详情

"""
        if self.issues:
            for issue in self.issues:
                report += f"- 行 {issue['line']}: {issue['desc']}\n"
        else:
            report += "未发现问题\n"

        report += "\n---\n\n## 修正记录\n\n"
        if self.fixes:
            report += "| 文件 | 原编号 | 修正为 | 标题 |\n"
            report += "|------|--------|--------|------|\n"
            for fix in self.fixes:
                report += f"| {fix['file']} | {fix['old']} | {fix['new']} | {fix['title']} |\n"
        else:
            report += "无修正记录\n"

        if output_path:
            output_path.write_text(report, encoding="utf-8")
            print(f"\n报告已保存: {output_path}")

        return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description='验证和修正应标草稿的章节编号')
    parser.add_argument('path', nargs='?', default='.', help='项目路径或文件路径')
    parser.add_argument('--fix', action='store_true', help='自动修正问题')
    parser.add_argument('--report', help='生成报告的路径')

    args = parser.parse_args()

    validator = ChapterNumberValidator()
    path = Path(args.path)

    # 收集 Markdown 文件
    if path.is_file():
        files = [path]
    elif (path / "20_应标草稿").exists():
        # 项目路径：只检查应标草稿
        files = sorted((path / "20_应标草稿").glob("章节_*.md"))
    else:
        files = sorted(path.rglob('章节_*.md'))

    if not files:
        print(f"[警告] 未找到章节草稿文件（章节_*.md）")
        sys.exit(0)

    print(f"开始检查 {len(files)} 个文件...")
    print("=" * 60)

    for file in files:
        validator.validate_file(file)

    if args.fix and validator.issues:
        print("\n" + "=" * 60)
        print("开始修正...")
        for file in files:
            validator.fix_file(file)

    print("\n" + "=" * 60)
    if args.report:
        validator.generate_report(Path(args.report))
    else:
        print(validator.generate_report())

    return 0 if not validator.issues else 1


if __name__ == "__main__":
    sys.exit(main())
