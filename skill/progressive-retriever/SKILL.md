---
name: progressive-retriever
description: >
  T1 渐进式标书检索工具：最多5轮智能检索，通过章节索引+ripgrep+行号切片定位目标内容，
  避免全文加载。被 C1（撰写）、C2（润色）、D1（核验）等步骤按需调用，非独立流水线步骤。
  Use when: 需要从标书中检索特定章节内容、查找相关技术要求、定位不可偏离条款时使用。
  也被其他Skill模块内部调用。
  Do NOT use: 需要读取整个标书全文时（那不是检索，请直接读文件）。
---

# Skill T1 - 渐进式标书检索工具

## 角色定位

你是标书内容检索专家。通过最多 5 轮智能检索，从标书中精准定位目标内容，为撰写、核验等环节提供所需的标书原文片段。

**核心原则：永远不全量加载标书，通过索引导航 + 关键词粗筛 + 行号切片精读逐步缩小范围。**

## 前置条件

以下文件必须存在（由 `chapter-indexer` 生成）：
- `10_解析结果/章节索引.json`
- `10_解析结果/章节摘要.json`
- `10_解析结果/标书全文.md`（仅用于按行号切片读取）

## 可用脚本

> 脚本目录：`scripts/`（相对于本 Skill 目录）
> 执行方式：项目使用 uv 管理时用 `uv run <脚本路径>`；依赖已在全局环境时可直接 `python <脚本路径>`。
| 脚本 | 功能 |
|------|------|
| `scripts/search.py` | 5种模式的标书检索工具 |

使用方式：

```bash
# 读取所有章节摘要（Round 1 用）
uv run scripts/search.py [项目路径] --mode summary

# 列出章节目录
uv run scripts/search.py [项目路径] --mode chapters

# 提取指定章节内容（Round 3 用）
uv run scripts/search.py [项目路径] --mode section --chapter "3.2"

# 关键词检索（Round 2 用）
uv run scripts/search.py [项目路径] --mode keyword --query "关键词1,关键词2" --context 5

# 按行号提取（Round 3 精确切片用）
uv run scripts/search.py [项目路径] --mode lines --start 100 --end 200
```

**输出上限：每次输出约 4500 字符（约 3000 token）。超出时会给出截断提示和继续读取的命令。**

## 检索轮次设计（核心流程）

### Round 1：索引导航 —— 确定目标范围

```bash
uv run scripts/search.py [项目路径] --mode summary
```

读取章节摘要，将检索目标拆解为关键词，匹配最相关的 3-5 个章节，记录起止行号。

**输出：** 目标章节列表（编号 + 行号范围）

### Round 2：关键词粗筛 —— 快速定位

```bash
uv run scripts/search.py [项目路径] --mode keyword --query "关键词1,关键词2" --context 5
```

（search.py 内部优先使用 ripgrep，不可用时自动降级 Python 搜索）

如果已知章节编号，可跳过 Round 2，直接进入 Round 3。

**输出：** 命中行列表（行号 + 匹配内容 + 上下文）

### Round 3：精确切片 —— 加载目标内容

结合 Round 1 的章节范围和 Round 2 的命中行，确定精读区间：

```bash
# 按章节提取（首选）
uv run scripts/search.py [项目路径] --mode section --chapter "3.2"

# 按行号精确切片
uv run scripts/search.py [项目路径] --mode lines --start 120 --end 180
```

或直接使用 Read 工具按行号读取：
```
file_path: "[项目路径]/10_解析结果/标书全文.md"
offset: [起始行]
limit: [行数]
```

**输出：** 相关内容片段（含行号标注）

### Round 4：交叉验证 —— 不可偏离项核查

如果 `不可偏离项.json` 存在，读取并交叉比对已提取内容，补充相关约束。

**输出：** 补充了合规约束的内容片段

### Round 5（可选）：技术规格书补充

仅当检索目标涉及技术参数时执行：

```bash
# 在技术规格书中补充检索
uv run scripts/search.py [项目路径] --mode keyword --query "关键词" --context 5
```

（需在 search.py 中指定技术规格书文件，或直接用 Read 工具读取）

## 结果汇总输出格式

```
=== 检索结果：<检索目标> ===

--- 来源：第X章 <章节标题>（第XX-XX行）---
<标书原文片段>

--- 来源：第Y章 <章节标题>（第YY-YY行）---
<标书原文片段>

--- 相关约束（来自不可偏离项）---
- [硬性] <约束描述>（来源：第Z章）

共检索 X 轮，覆盖 Y 个章节，提取 Z 个片段。
```

## 检索策略选择

| 检索场景 | 建议策略 | 执行轮次 |
|----------|----------|----------|
| 已知章节名称 | Round 1 定位行号 → Round 3 提取 | 1, 3 |
| 模糊关键词检索 | 完整 5 轮 | 1-5 |
| 不可偏离项相关 | 重点执行 Round 4 | 1, 2, 3, 4 |
| 技术参数查找 | 重点执行 Round 5 | 1, 2, 3, 5 |

## 错误处理

### 索引文件不存在

```
[错误] 章节索引文件不存在，请先运行 chapter-indexer。
```

### ripgrep 不可用

search.py 自动降级为 Python 内置搜索，功能完全一致，仅速度较慢。

### 检索无结果

5 轮检索后仍无结果时：列出所有一级章节供用户选择，或建议用同义词重试。

## 与其他 Skill 的关系

- **前置依赖**：`chapter-indexer`（A2）
- **调用方**：`section-writer`（C1）、`content-polisher`（C2）、`content-expander`（C3）、`compliance-extractor`（A3）、`compliance-checker`（D1）、`draft-polisher`（T3）、`hint-generator`（B3）、`outline-builder`（B2）
- **定位**：工具层，不在流水线步骤状态中，被各步骤按需调用
