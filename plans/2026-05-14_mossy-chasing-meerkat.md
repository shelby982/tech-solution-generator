# 单章节编辑 & 重新生成方案

## Context

生成页面已有的章节卡片是静态的（只读 markdown 渲染），没有编辑或重新生成的入口。
用户需要：
1. **编辑**：每个章节卡片常驻 ✏ 按钮 → 原地切换为 textarea，修改后保存
2. **重新生成**：每个章节卡片常驻 ↺ 按钮 → 展开 inline 面板（选 API、填追加说明、设字数）→ 流式替换内容
3. 以上操作不影响其他章节的内容或整体下载流程

---

## 改动文件

| 文件 | 改动类型 |
|------|---------|
| `backend/services/llm.py` | `stream_generate` + `dispatch_stream_generate` 新增 `extra_prompt` 参数 |
| `backend/routes/generate.py` | 新增 `RegenStartRequest` 模型、`_run_regen()` 协程、2 个端点 |
| `frontend/index.html` | 章节卡片重构 + 单章节状态机 JS |

---

## 一、后端 — `backend/services/llm.py`

### `stream_generate()` 增加 `extra_prompt`（约第 195 行）

```python
async def stream_generate(
    config, section_title, original_content,
    target_words=500, doc_summary="",
    extra_prompt: str = "",       # ← 新增
) -> AsyncIterator[str]:
```

在 `user_prompt` 拼接完成后（约第 241 行之后）：
```python
if extra_prompt:
    parts.append(f"\n\n额外优化要求：{extra_prompt}")
```

### `dispatch_stream_generate()` 透传（约第 307 行）

函数签名和内部 `stream_generate(...)` 调用均增加 `extra_prompt: str = ""`。

---

## 二、后端 — `backend/routes/generate.py`

### 新增请求模型

```python
class RegenStartRequest(BaseModel):
    section_title: str = Field(..., min_length=1, max_length=200)
    section_content: str = Field(default="")       # 原文（给 LLM 参考）
    target_words: int = Field(default=500, ge=100, le=10000)
    extra_prompt: str = Field(default="", max_length=1000)
    config_id: Optional[str] = None  # None = 轮询；有值 = 用指定配置
```

### `_run_regen(task, regen_req)` 协程

轻量版单章节生成（无文档摘要步骤）：
- 若指定 `config_id`：`config_store.get_by_id(config_id)`，找不到则推 error
- 否则：`config_store.get_configs_and_next_index()`
- 调用 `dispatch_stream_generate(configs, idx, title, content, target_words, doc_summary="", extra_prompt=...)`
- 推送事件：`section_start` → `token` × N → `section_done` | `error`
- 最终推 `(None, None)` sentinel

### 新端点（追加到文件末尾）

```python
@router.post("/generate/regen/start")
async def start_regen(body: RegenStartRequest):
    # 校验已配置
    # 校验 config_id 若指定
    # task_store.create([{id, title, content}], target_words=body.target_words)
    # 注入 asyncio.Queue / Event
    # asyncio.create_task(_run_regen(task, body))
    # 返回 { task_id, stream_url: /api/generate/stream/{task_id} }

@router.get("/generate/regen/stream/{task_id}")
async def stream_regen_sse(task_id: str, request: Request):
    # 完全复用现有 _sse_event_generator() + StreamingResponse
```

> **注意**：重新生成 stream 复用现有 `/api/generate/stream/{task_id}`，无需新增端点。只需 `start_regen` 返回的 `stream_url` 指向现有端点即可。实际只需 1 个新 POST 端点。

---

## 三、前端 — `frontend/index.html`

### 3.1 模块级状态

```js
const sectionCards = new Map();
// secId → { rawContent, title, charCount, cardEl, regenTaskId, regenEs }

let currentConfigs = [];  // renderConfigList() 末尾同步赋值
```

### 3.2 `section_done` 事件改造（约第 1364 行）

原有直接拼 innerHTML → 改为调用 `createSectionCard(secId, title, rawContent, charCount)`

```js
function createSectionCard(secId, title, rawContent, charCount) {
  const card = document.createElement('div');
  card.id = `sec-card-${secId}`;
  card.className = 'pb-8 border-b border-gray-100';
  card.dataset.state = 'done';
  card.innerHTML = buildDoneHTML(secId, title, rawContent, charCount);
  $('completedSections').appendChild(card);
  sectionCards.set(secId, { rawContent, title, charCount, cardEl: card, regenTaskId: null, regenEs: null });
  bindCardEvents(secId);
}
```

### 3.3 卡片 HTML / 状态切换

**done 状态**（`buildDoneHTML`）：
```html
<div class="sec-header flex items-center gap-2 mb-3">
  <span class="text-green-600 text-xs">✓ 已完成</span>
  <span class="text-gray-400 text-xs">{charCount}字 ≈{pages}页</span>
  <div class="ml-auto flex gap-1">
    <button class="sec-edit-btn ...">✏ 编辑</button>
    <button class="sec-regen-btn ...">↺ 重新生成</button>
  </div>
</div>
<div class="sec-body">
  <h3 ...>{title}</h3>
  <div class="md prose sec-content">{marked.parse(rawContent)}</div>
</div>
```

**editing 状态**：header 换为 `✏ 编辑中  [保存] [取消]`；sec-body 换为高度自适应 textarea（内含 raw markdown）

**regen_panel 状态**：header 换为 `↺ 重新生成  [取消]`；在 header 与 sec-body 之间插入：
```html
<div class="regen-panel bg-gray-50 border border-gray-200 rounded-lg p-3 mb-3 space-y-2">
  <div class="flex gap-2 flex-wrap">
    <select class="regen-api-select ...">
      <option value="__auto__">自动（优先级顺序）</option>
      <!-- 从 currentConfigs 填充 -->
    </select>
    <input type="number" class="regen-words-input ..." value="{S.targetWords}" min="100" max="10000">
  </div>
  <textarea class="regen-extra-input ..." placeholder="可选：追加说明，如「更简洁」「增加案例」..."></textarea>
  <div class="flex justify-end gap-2">
    <button class="regen-cancel-btn ...">取消</button>
    <button class="regen-confirm-btn ...">确认重新生成</button>
  </div>
</div>
```

**regenerating 状态**：header 换为 `↺ 生成中... {words}字  [停止]`；sec-body 切换为流式渲染区（streaming-cursor）

### 3.4 核心 JS 函数

```js
function bindCardEvents(secId) { /* 委托给 cardEl，绑 edit/regen/save/cancel/confirm/stop */ }

function enterEditMode(secId) { /* 切换 header + body → textarea */ }
function saveEdit(secId) {
  // 读 textarea.value → rawContent
  // 重算 charCount → 更新 S.sectionWords → 更新 S.totalWords
  // 切回 done 状态
}
function cancelEdit(secId) { /* 恢复原 done HTML */ }

function openRegenPanel(secId) {
  // 填充 API select（currentConfigs + __auto__）
  // 切换到 regen_panel 状态
}

async function startRegen(secId) {
  const sel = card.querySelector('.regen-api-select').value;
  const words = +card.querySelector('.regen-words-input').value;
  const extra = card.querySelector('.regen-extra-input').value.trim();
  const configId = sel === '__auto__' ? null : sel;

  // POST /api/generate/regen/start
  const { task_id, stream_url } = await fetch('/api/generate/regen/start', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ section_title: title, section_content: originalContent,
                           target_words: words, extra_prompt: extra, config_id: configId })
  }).then(r => r.json());

  sectionCards.get(secId).regenTaskId = task_id;
  enterRegeneratingState(secId);

  const es = new EventSource(stream_url);
  sectionCards.get(secId).regenEs = es;
  let buffer = '';

  es.addEventListener('token', e => {
    buffer += JSON.parse(e.data).text;
    // 节流渲染（requestAnimationFrame）到 sec-body
  });
  es.addEventListener('section_done', e => {
    const d = JSON.parse(e.data);
    updateCardAfterRegen(secId, d.content);
    es.close();
  });
  es.addEventListener('all_done', () => es.close());
  es.addEventListener('error', () => { es.close(); restoreCardToDone(secId); });
}

function stopRegen(secId) {
  const { regenTaskId, regenEs } = sectionCards.get(secId);
  if (regenEs) regenEs.close();
  if (regenTaskId) fetch(`/api/generate/${regenTaskId}`, { method: 'DELETE' });
  restoreCardToDone(secId);  // 保留原 rawContent
}

function updateCardAfterRegen(secId, newContent) {
  const entry = sectionCards.get(secId);
  entry.rawContent = newContent;
  entry.charCount = (newContent.match(/[一-鿿]/g)||[]).length;
  S.sectionWords[secId] = entry.charCount;
  S.totalWords = Object.values(S.sectionWords).reduce((a,b)=>a+b,0);
  // 重建 done 状态卡片
  buildDoneState(secId);
  updateProgress(S.completedCount, S.totalSections);
}
```

### 3.5 `currentConfigs` 同步

在 `renderConfigList(configs)` 最后追加：
```js
currentConfigs = configs.slice();
```

`openConfig()` 已调 `refreshConfigList()`，所以打开 config 弹窗时自动同步。

---

## 关键约束

- **主任务不受影响**：`S.eventSource`（主 SSE）与 `regenEs`（单章节 SSE）完全独立
- **Word 下载**：重新生成后需把新内容写回 `task_store` 的 `task.results[secId].content`
  - 方案：`/api/generate/regen/start` 的 `_run_regen` 在 `section_done` 时，通过一个可选的 `parent_task_id` + `section_id` 将新内容写回主 task 的 results；前端在 `startRegen` 时把 `S.taskId` 和 `secId` 带上
  - 若不带 parent_task_id，则仅在前端内存更新，下载时走另一个 API（见下）
  - **简化方案（推荐）**：前端在 `updateCardAfterRegen` 完成后，调用 `PATCH /api/generate/{taskId}/section/{secId}` 更新内容，后端只需在 task.results 中覆盖

### 新增 PATCH 端点（约 5 行）

```python
class PatchSectionRequest(BaseModel):
    content: str

@router.patch("/generate/{task_id}/section/{section_id}")
async def patch_section(task_id: str, section_id: str, body: PatchSectionRequest):
    task = task_store.get(task_id)
    if not task: raise HTTPException(404)
    if section_id in task.results:
        task.results[section_id].content = body.content
    return {"ok": True}
```

---

## 验证方案

1. 完整生成一份多章节文档
2. 点 ✏ 编辑某章节，修改 markdown → 保存 → 字数/页数显示更新
3. 点 ✏ 编辑后点取消 → 原内容不变
4. 点 ↺ 重新生成，选择非默认 API，填写追加说明，确认 → 该章节流式替换，其他章节静止
5. 重新生成过程中点停止 → 流停止，原内容恢复
6. 所有章节完成后下载 Word → 内容为最新（含手动编辑和重新生成结果）
7. 在主流程生成过程中对已完成章节做操作 → 主进度条继续，互不干扰
