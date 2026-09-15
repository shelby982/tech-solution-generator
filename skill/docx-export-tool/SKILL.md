---
name: docx-export-tool
description: >
  T2 将合并后的 Markdown 应标文件通过 pandoc 导出为 DOCX 格式。
  手动触发的独立工具，不在流水线内自动执行。
  Use when: 用户说“导出”、“生成docx”、“转word”、“输出最终文件”时使用。
  Do NOT use: 尚未完成 D2 合并校验时。
---

# Skill T2 - DOCX 导出工具

## 角色定位

你是应标文件的格式导出工具。将合并并校验后的 Markdown 文件通过 pandoc 转换为 DOCX。

## 前置检查

- 仅在 D2 合并校验完成后使用
- 必需：`{项目路径}/30_最终输出/应标文件_合并稿.md`
- 推荐：`{项目路径}/00_原始文件/02_应标模板/`（模板 DOCX）
- 外部依赖：`pandoc`

## 执行步骤

### Step 1：确认合并稿存在

检查 `30_最终输出/应标文件_合并稿.md` 是否存在。  
如不存在，提示先运行 D2 `doc-exporter`。

### Step 2：执行导出

```bash
uv run .claude/skill/docx-export-tool/scripts/export_docx.py [项目路径]
```

### Step 3：输出完成提示

```
=== DOCX 导出完成 ===

文件位置：30_最终输出/应标文件_{项目名}.docx

请打开文件检查以下重点：
1. 章节标题层级是否与甲方模板一致
2. 表格格式是否正常显示
3. 页眉页脚是否正确
4. 页码和目录是否需要更新（在 Word 中右键目录 -> 更新域）
```

## 常见问题

- pandoc 未安装：提示安装 pandoc（https://pandoc.org/installing.html）
- 模板文件缺失：可不带模板导出，但版式可能不符合甲方模板
