# 单块 AI 生成：POST SSE + 文风/字数参数传递

**日期：** 2026-05-31  
**状态：** 待实现

---

## 背景

前端已有文风切换按钮（公文/技术/精简）和目标字数输入框，但点击「AI 生成」时调用的是 `EventSource GET /api/blocks/{id}/generate`，无法携带参数。后端端点定义为 `POST`，存在 method 不匹配。文风和字数参数均未传递给 LLM。

---

## 目标

1. 修复 method 不匹配：前端改为 `fetch POST + ReadableStream` 读取 SSE
2. 传递 `target_words` 和 `tone` 参数到 LLM 调用链
3. 后端按 tone 值使用不同 system_prompt，实现真实文风差异

---

## 接口设计

### POST /api/blocks/{id}/generate

**Request body（JSON）：**
```json
{
  "target_words": 800,
  "tone": "official"
}
```

- `target_words`：整数，范围 100–5000，默认 800
- `tone`：枚举，`"official"` | `"tech"` | `"concise"`，默认 `"official"`

**Response：** `text/event-stream`，事件格式不变

```
data: {"text": "..."}          // event: token（默认 event 名）
data: {"block_id": 1, "content": "...", "revision_no": 2}  // event: block_done
data: {"message": "..."}       // event: error
```

---

## 后端变更

### services/llm.py — stream_generate

新增参数 `tone: str = "official"`，在函数头按 tone 选 system_prompt：

| tone | system_prompt 人设 |
|------|--------------------|
| `official` | 政企方案撰写专家，正式书面文风（现有） |
| `tech` | 技术架构师，技术精准、逻辑严密、术语准确 |
| `concise` | 精简表达专家，简洁有力、去冗余、每句有信息量 |

user_prompt 构造逻辑不变。

### services/llm.py — dispatch_stream_generate

新增参数 `tone: str = "official"`，透传给 `stream_generate`。

### routes/blocks.py — _generate_stream / generate_block

- `generate_block` 接收 Pydantic request body（`target_words: int = 800`, `tone: str = "official"`）
- `_generate_stream` 新增 `target_words` 和 `tone` 参数，传给 `dispatch_stream_generate`
- 验证：`tone` 不在允许值时返回 error 事件；`target_words` 超出范围时 clamp 到 [100, 5000]

---

## 前端变更

### workbench.html — startSingleGenerate()

替换 `EventSource` 为 `fetch` + `ReadableStream`：

```js
async function startSingleGenerate() {
  const tone = document.querySelector('.ced-tone-btn.active')?.dataset.tone || 'official';
  const targetWords = parseInt(document.getElementById('ced-wordlimit').value) || 800;

  const resp = await fetch(`/api/blocks/${currentBlock.id}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tone, target_words: targetWords }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  // 解析 SSE 行，提取 data: {...} 并处理 token / block_done / error
  ...
}
```

停止逻辑：调用 `AbortController.abort()` 中止 fetch，替代原 `es.close()`。

---

## 不改动范围

- `dispatch_block_write`（批量生成路由用）暂不改动
- 批量生成的 generate-all 端点不变
- 其他 AI 功能（润色/扩写/缩写）不变

---

## 验收标准

1. 点击「AI 生成」，后端收到正确的 `tone` 和 `target_words`
2. 三种文风生成结果有可感知差异（公文：正式严谨；技术：精准术语；精简：短句去冗余）
3. 修改字数输入后生成内容长度相应变化
4. 停止按钮可中止流式生成
5. 旧的 `EventSource` 代码完全移除
