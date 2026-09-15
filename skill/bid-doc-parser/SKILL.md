---
name: bid-doc-parser
description: >
  将招标文件、技术规格说明书等DOCX/PDF文件转换为Markdown格式，并初始化项目工作区。
  Use when: 用户需要"解析标书"、"转换文档"、"处理标书文件"、"读取招标文件"、"把标书转成可处理的格式"时使用。
  Do NOT use: 文件已经是MD格式时无需使用。
dependencies:
  - bid-setup
---

# Skill A1 - 标书文档转换器

## 角色定位

你是文档转换专家。将 DOCX/PDF 格式的招标文件转换为 Markdown 纯文本，初始化项目工作区，为后续所有 Skill 提供标准化的文件底座。

## 环境检查

**首次使用必读**：
> 使用 `python -c "import os; print(os.path.exists('pyproject.toml'))"` 
> 判断根目录pyproject.toml是否存在，否则就执行 `bid-setup` 进行环境初始化。

> **执行本 Skill 前，必须遵守 `bid-setup` SKILL.md 中的「生产环境 Shell 规范」。**
> 核心要点：禁止 mkdir -p、禁止直接 pip、用 Python 创建目录、含中文路径用双引号包裹。

## 前置条件

- 用户已提供待转换文件的路径（DOCX 或 PDF）
- markitdown 已安装

## 可用脚本

> 脚本目录：`scripts/`（相对于本 Skill 目录）
> 执行方式：项目使用 uv 管理时用 `uv run <脚本路径>`；依赖已安装时 `uv run <脚本路径>`。
| 脚本 | 功能 | 主要参数 |
|------|------|---------|
| `scripts/init_project.py` | 创建目录结构 + 生成 project.json | `[项目路径]` |
| `scripts/convert.py` | 将单个 DOCX/PDF 转换为 Markdown | `[源文件路径] -o [输出路径]` |

## 执行步骤

### Step 0：初始化项目工作区
调用本项目内的skills， `bid-setup` 进行初始化工作区


将创建：`00_原始文件/`（含子目录）、`10_解析结果/`、`20_应标草稿/`、`30_最终输出/`、`project_notes/`，并生成 `project.json` 项目状态文件。

### 前置：检查 file_map.json

若当前项目目录存在 `_state/file_map.json`（由 bid-pipeline 初始化生成），
从中自动读取文件路径，跳过手动输入步骤：

```json
{
  "招标正文": "[自动取此路径]",
  "技术规格书": "[自动取此路径，若为 null 则跳过]"
}
```

若 `_state/file_map.json` 不存在（用户直接调用本 Skill），按原流程继续：询问用户提供文件路径。

### Step 0：每一步都需要更新 project.json

读取 `[项目路径]/project.json`，更新后写回：

```json
{
  "当前阶段": "文档整理完成",
  "已完成": ["A1-文档转换"],
  "待执行": ["A2-章节索引", "A3-合规提取", "B1-角色分析", "B2-大纲规划", "C1-章节撰写", "D1-合规核验", "D2-合并校验"]
}
```


### Step 1：整理目录文件
项目初始化完毕后，根据用户提供的文档，移动到对应的文件夹内。

### Step 2：转换标书文件

对每个待转换文件执行，md 文件名可智能化调整。

```bash
# 仅有一个文件直接使用：
uv run scripts/convert.py "[源文件路径]" -o "[项目路径]/10_解析结果/<文件名>.md"

# 如果有多个文件，可合并转化，对应文件路径自行调整：
uv run scripts/convert.py "[源文件1]" -o "10_解析结果/<文件名1>.md" && uv run scripts/convert.py "[源文件2]" -o "10_解析结果/<文件名2>.md"
```

### Step 3：验证转换结果

使用 Python 统计行数：

```bash
python -c "print(len(open('[项目路径]/10_解析结果/<文件名>.md', encoding='utf-8').readlines()), '行')"
```

使用 Read 工具读取文件前 30 行，确认：中文正常显示、包含 Markdown 标题（`#`、`##` 等）。

### Step 4：再一次优化文档位置
根据对上面内容解析，移动对应文档到 `00_标书正文`的位置。

### Step 5：更新 project.json

读取 `[项目路径]/project.json`，更新后写回：

```json
{
  "当前阶段": "文档转换完成",
  "已完成": ["A1-文档转换"],
  "待执行": ["A2-章节索引", "A3-合规提取", "B1-角色分析", "B2-大纲规划", "C1-章节撰写", "D1-合规核验", "D2-合并校验"]
}
```

### Step 6：输出完成报告

```
=== 文档转换完成 ===
项目路径：[路径]
转换文件：
  - 标书全文.md（XX 行 / XX 字符）
  - 技术规格书全文.md（XX 行 / XX 字符）[如有]

下一步：运行 chapter-indexer 构建章节索引（可并行运行 compliance-extractor）
```

## 错误处理

### markitdown 未安装

调用 `bid-setup` 进行修复

### 转换结果为空或乱码

检查是否为扫描版 PDF（图片型）。扫描版 PDF 需先 OCR 处理，markitdown 不支持图片提取。

### Windows 中文路径问题

`convert.py` 已内置 UTF-8 修复，通常可自动处理。如仍失败，将文件复制到纯 ASCII 路径后重试。

## 输出规范

| 文件类型 | 输出路径 |
|----------|---------|
| 招标文件正文 | `10_解析结果/<项目名称>_标书全文.md` |
| 技术规格说明书 | `10_解析结果/<项目名称>_技术规格书全文.md` |
| 补充答疑文件 | `10_解析结果/补充答疑_{编号}.md` |
其他相关文档类似命名

## 与其他 Skill 的关系

- **后续调用**：`chapter-indexer`（构建章节索引）、`compliance-extractor`（若可 subagent 可并行，否则线性）

---

## 重复执行行为

本 Skill 可在流水线任意阶段单独触发，无需重启整个流程。

执行前检测关键输出文件是否已存在：

```
=== 检测到已有解析结果 ===

  10_解析结果/标书全文.md（生成时间：YYYY-MM-DD，约 XXXX 行）
  10_解析结果/技术规格书全文.md（如有）

  A. 保留已有文件，直接退出（推荐：后续步骤已就绪时选此项）
  B. 重新转换，覆盖旧文件（适用：原始文件有更新，或首次转换结果不完整）
```
