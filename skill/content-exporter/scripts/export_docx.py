#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
导出文档脚本 - 使用 pandoc 将整合后的 Markdown 导出为 docx

用法：
  uv run scripts/export_docx.py [项目路径]
  uv run scripts/export_docx.py [项目路径] --template [模板文件路径]
"""

import sys
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# Windows UTF-8 编码修复
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def check_pandoc() -> bool:
    """检查 pandoc 是否可用"""
    try:
        result = subprocess.run(
            ["pandoc", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    except subprocess.TimeoutExpired:
        print("[警告] pandoc 检查超时", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[错误] pandoc 检查异常：{e}", file=sys.stderr)
        return False


    except FileNotFoundError:
        return False


def find_template(项目路径: Path) -> Path:
    """在应标模板目录中查找 docx 模板（取第一个）"""
    模板目录 = 项目路径 / "00_原始文件" / "02_应标模板"
    try:
        docx文件列表 = list(模板目录.glob("*.docx"))
        return docx文件列表[0] if docx文件列表 else None
    except FileNotFoundError:
        return None


    except Exception:
        return None


def export_docx(项目路径: Path, template_path: Path = None) -> bool:
    if not check_pandoc():
        print("[错误] pandoc 未安装", file=sys.stderr)
        print("请安装 pandoc：", file=sys.stderr)
        print("  Windows：前往 https://pandoc.org/installing.html 下载 .msi 安装", file=sys.stderr)
        print("  Mac：brew install pandoc", file=sys.stderr)
        return False

    输入文件 = 项目路径 / "30_最终输出" / "应标文件_草稿.md"
    if not 输入文件.exists():
        print("[错误] 未找到整合后的草稿文件（应标文件_草稿.md）", file=sys.stderr)
        print("请先运行 merge_docs.py", file=sys.stderr)
        return False
    project_file = 项目路径 / "project.json"
    项目名称 = 项目路径.name
    if project_file.exists():
        try:
            项目信息 = json.loads(project_file.read_text(encoding="utf-8"))
            项目名称 = 项目信息.get("项目名称", 项目路径.name)
        except json.JSONDecodeError as e:
            print(f"[警告] project.json 解析失败，使用默认名称：{e}", file=sys.stderr)
        except FileNotFoundError:
            pass
    时间戳 = datetime.now().strftime("%Y%m%d")
    输出文件 = 项目路径 / "30_最终输出" / f"应标文件_{项目名称}_{时间戳}.docx"
    模板文件 = template_path or find_template(项目路径)
    pandoc命令 = [
        "pandoc",
        str(输入文件),
        "-o", str(输出文件),
        "--toc",
        "--toc-depth=3",
    "--no-highlight"
    ]
    if 模板文件 and 模板文件.exists():
        pandoc命令.extend(["--reference-doc", str(模板文件)])
        print(f"使用模板：{模板文件.name}")
    else:
        print("[提示] 未找到应标模板文件，将使用默认格式")
        print("       建议将甲方提供的 docx 模板放入：00_原始文件/02_应标模板/")
    print(f"正在导出：{输出文件.name}")
    try:
        result = subprocess.run(
            pandoc命令,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60
        )
        if result.returncode != 0:
            print(f"[错误] pandoc 导出失败：", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return False
        if result.stderr:
            print(f"[警告] {result.stderr}")
        print(f"\n导出完成：{输出文件}")
        print("\n请打开文件检查格式，重点检查：")
        print("  1. 章节标题层级是否和甲方模板一致")
        print("  2. 表格格式是否正常显示")
        print("  3. 页眉页脚是否正确")
        print("  4. 字体和段落格式是否符合要求")
        return True
    except subprocess.TimeoutExpired:
        print("[错误] pandoc 执行超时", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[错误] 执行失败：{e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="使用 pandoc 导出应标 docx")
    parser.add_argument("项目路径", help="项目根目录路径")
    parser.add_argument("--template", help="指定 docx 模板文件路径（可选）")
    args = parser.parse_args()
    项目路径 = Path(args.项目路径)
    if not 项目路径.exists():
        print(f"[错误] 项目路径不存在：{项目路径}", file=sys.stderr)
        sys.exit(1)
    template = Path(args.template) if args.template else None
    成功 = export_docx(项目路径, template)
    sys.exit(0 if 成功 else 1)


if __name__ == "__main__":
    main()
