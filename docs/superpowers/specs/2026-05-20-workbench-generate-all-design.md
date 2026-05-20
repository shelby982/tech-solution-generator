# 材料编写模块设计：批量 AI 编写 + 素材联动

**日期：** 2026-05-20  
**范围：** workbench 材料编写模块的批量生成链路、素材实时检索、UI 交互拆分  

---

## 1. 背景与目标

用户上传应标要求和原始素材，经过大纲提炼后得到 blocks 表（每个 block 含 title、requirement）。当前 workbench 缺少将 block 内容实际写出来的能力。

本方案目标：
- 批量对所有 blocks 执行 AI 编写，每块编写时实时检索匹配素材作为上下文
- 流式写入，已完成的 block 可立即编辑，未完成的继续后台生成
- 右侧上下文面板展示当前 block 的要求文本 + 匹配素材片段 + 原文溯源入口

---

## 2. 整体架构

### 业务流程

```
上传应标要求 + 原始素材
       ↓
[project-init] 提炼大纲 → blocks 表（含 requirement 字段）
       ↓
[workbench] 点击「批量编写」
       ↓
后端 /generate-all SSE
  ├─ 对每个 block：
  │   ├─ 检索素材：从 material_chunks 中关键词匹配 Top-5 片段
  │   ├─ 构造 prompt：block.title + block.requirement + 素材片段
  │   └─ 流式写入：token 逐字推送 → blocks.content 更新
  └─ 结束：emit generate_done
       ↓
前端实时渲染：已完成 block 可立即编辑，未完成继续生成
```

### 模块划分

| 层 | 模块 | 职责 |
|---|---|---|
| 后端 Service | `services/retrieval.py`（新增） | 给定 block query，从 chunks 中检索相关素材片段 |
| 后端 Service | `services/llm.py`（扩展） | 新增 `dispatch_block_write`：接收 title+requirement+chunks，返回异步生成器 |
| 后端 Route | `routes/generate.py`（扩展） | 新增 `/generate-all` SSE 端点，编排检索→生成→写入 |
| 前端 | `assets/generate-session.js`（新增） | SSE 订阅、token 分发、生成状态机 |
| 前端 | `assets/context-panel.js`（新增） | 右侧面板渲染（要求 + 素材 + 溯源） |
| 前端 | `assets/workbench.js`（保留） | block 加载、outline 导航、编辑保存 |

---

## 3. 后端接口与数据流

### SSE 事件协议

`POST /api/projects/{id}/generate-all`

| 事件 | 字段 |
|---|---|
| `generate_start` | `{ total: N }` |
| `generate_block_start` | `{ block_id, title, index, total }` |
| `generate_block_token` | `{ block_id, token }` |
| `generate_block_done` | `{ block_id, content, sources: [{material_id, chunk_index, snippet}] }` |
| `generate_done` | `{ generated: N, skipped: M }` |
| `error` | `{ message, block_id? }` — `block_id` 有值表示单块失败，无值表示全局失败 |

### retrieval.py 接口

```python
def retrieve_chunks(
    chunks: list[dict],      # 项目全部 material_chunks（预加载，避免 N+1）
    query: str,              # block.title + " " + block.requirement
    top_k: int = 5,
) -> list[dict]:             # 返回按相关度排序的 chunks
```

当前使用关键词交集计分（复用 map-sources 逻辑）。接口签名稳定，后续换向量检索只改内部实现，调用方不感知。

### generate-all 端点执行流

```
1. 加载 blocks（按 order_idx 排序）
2. 加载项目全部 material_chunks（一次性，避免 N+1 查询）
3. 逐块循环：
   a. emit generate_block_start
   b. retrieve_chunks(all_chunks, query=title+" "+requirement, top_k=5)
   c. dispatch_block_write(config, title, requirement, chunks) → AsyncGenerator[token]
   d. 每个 token → emit generate_block_token
   e. 收集完整 content → UPDATE blocks SET content=?, source=? → emit generate_block_done
4. emit generate_done
```

### blocks 表

`source` 字段已存在，`generate_block_done` 直接覆写，格式保持：
```json
[{"material_id": 1, "chunk_index": 3, "snippet": "...前120字..."}]
```

---

## 4. 前端交互与 UI 模块拆分

### 状态机

```
Block 状态：  idle → generating → done | error
会话状态：    idle → running → done | partial_error
```

### UI 组件

**批量编写入口区**（workbench.html 工具栏扩展）
- 「✦ 批量编写」按钮，running 时变为「停止」
- 进度文字：「正在编写 3 / 12」
- running 期间禁用「提炼大纲」等破坏性操作

**Block 元素状态层**（每个 `.editor-block` 扩展）
- `generating`：标题旁闪烁光标动画，`contenteditable` 暂时 disabled
- `done`：移除动画，恢复 `contenteditable`，可立即编辑
- `error`：红色边框 + 「重试」按钮
- token 写入：`textContent +=` 逐字追加（不用 innerHTML，避免 XSS）

**右侧上下文面板**（选中某 block 时展示）

```
┌─────────────────────────────┐
│ 应标要求                     │
│  · 要求文本 1                │
│  · 要求文本 2                │
├─────────────────────────────┤
│ 匹配素材  (来自：文件名.pdf)  │
│  ┌──────────────────────┐   │
│  │ 片段摘要文字…         │   │
│  │              [原文 →] │   │
│  └──────────────────────┘   │
└─────────────────────────────┘
```

- 数据来源：`generate_block_done` 的 `sources` 写入 `block.dataset.source`，面板读取渲染
- 原文溯源：当前阶段点击「原文 →」toast 提示文件名 + chunk 位置；后续迭代再做 PDF 内联预览

### 前端模块解耦

三个 JS 文件通过 `CustomEvent` 通信，不直接互相调用：

| 模块 | dispatch 事件 | 监听事件 |
|---|---|---|
| `generate-session.js` | `block:token`、`block:done`、`block:error`、`session:done` | — |
| `context-panel.js` | — | `block:done`（更新面板素材数据） |
| `workbench.js` | — | `block:token`（追加 token）、`block:done`（解锁编辑） |

---

## 5. 关键设计决策

1. **检索与 LLM 解耦**：`retrieval.py` 独立模块，接口稳定，不耦合 LLM 调用
2. **SSE 格式复用**：事件结构与 `generate_outline` 保持一致，前端 SSE 处理逻辑可共用
3. **断线可恢复**：每块 `done` 后立即落库，前端重连后状态从 blocks 表恢复
4. **单块失败不阻断**：`error` 事件携带 `block_id`，其余块继续生成，前端显示重试入口
5. **source 字段复用**：不新增字段，`generate_block_done` 覆写现有 `source` 字段
