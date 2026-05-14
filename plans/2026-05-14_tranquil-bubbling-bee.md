# 生成流程优化：文档摘要作为全局上下文

## Context

当前生成流程直接逐章节调用 LLM，每个章节只传入该章节自身的 content_hint。用户要求：

1. **生成前先提炼整篇文档的背景、要求和工作内容摘要（~500字）**
2. **将摘要展示在生成内容的最上方**
3. **后续每个章节生成时，将此摘要作为基础上下文一并传入 LLM**

这样每个章节的生成都能理解整体项目背景，生成内容更连贯。

## 改动范围

### 1. `backend/utils/sse.py` — 新增 SSE 事件类型

新增 `sse_doc_summary(summary: str)` 函数，事件类型为 `doc_summary`，数据格式：
```json
{"summary": "...500字摘要..."}
```

### 2. `backend/services/llm.py` — 新增摘要生成 + 改造章节生成

**新增 `generate_doc_summary(config, all_sections) -> str`**：
- 将所有章节的 title + content_hint 拼接为输入
- 调用 LLM（非流式，单次调用），prompt 要求提炼文档的背景、核心要求、工作内容，限制 500 字
- 返回摘要文本

**改造 `stream_generate()` 签名**：
- 新增参数 `doc_summary: str = ""`
- 在 user_prompt 中注入文档摘要作为全局上下文（放在章节内容之前）

### 3. `backend/routes/generate.py` — 生成流程添加摘要步骤

**改造 `_run_generation()` 协程**：
- 在 `for` 循环之前，先调用 `generate_doc_summary()` 生成摘要
- 通过 queue 发送 `("doc_summary", {"summary": text})` 事件
- 将摘要存入 `task.doc_summary`
- 在后续每个章节的 `stream_generate()` 调用中传入 `doc_summary=task.doc_summary`

**改造 `_sse_event_generator()`**：
- 处理 `doc_summary` 事件类型，格式化为 SSE 输出
- 晚连接重播时先发送 `doc_summary` 事件

### 4. `backend/services/task_store.py` — 扩展数据结构

- `GenerationTask` 新增字段 `doc_summary: str = ""`
- `get_all_content()` 在输出 Markdown 最前面添加摘要段

### 5. `frontend/index.html` — 渲染摘要卡片

- 在 `connectSSE()` 中新增 `doc_summary` 事件监听
- 收到摘要后在 `#completedSections` 最顶部插入一个固定的摘要卡片（蓝色背景，标题"项目概述"）
- 摘要卡片在"正在连接"状态消失后立即显示，在所有章节卡片之前

## 执行任务分解

| # | 任务 | 文件 |
|---|------|------|
| 1 | 新增 `sse_doc_summary` 事件函数 | `backend/utils/sse.py` |
| 2 | 新增 `generate_doc_summary()` 摘要生成函数 | `backend/services/llm.py` |
| 3 | 改造 `stream_generate()` 接受 `doc_summary` 参数并注入 prompt | `backend/services/llm.py` |
| 4 | `GenerationTask` 新增 `doc_summary` 字段 + `get_all_content()` 输出摘要 | `backend/services/task_store.py` |
| 5 | `_run_generation()` 添加摘要步骤 + 事件发送 | `backend/routes/generate.py` |
| 6 | `_sse_event_generator()` 处理 `doc_summary` 事件 + 晚连接重播 | `backend/routes/generate.py` |
| 7 | 前端新增 `doc_summary` 事件监听 + 渲染摘要卡片 | `frontend/index.html` |

## 验证方式

1. 启动服务，上传文档，开始生成
2. 确认生成开始后先出现"项目概述"摘要卡片（~500字）
3. 确认后续章节正常流式生成
4. 检查生成的章节内容是否体现了对项目背景的理解（相比之前更连贯）
5. 刷新页面（晚连接），确认摘要卡片仍然显示在最上方
6. 下载 Word/MD 文件，确认摘要在文档开头
