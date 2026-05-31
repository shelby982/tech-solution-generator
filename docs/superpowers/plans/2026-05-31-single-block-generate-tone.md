# Single-Block AI Generate: POST SSE + Tone/Target Words Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将单块 AI 生成从 EventSource GET 改为 fetch POST SSE，并将前端的文风（tone）和目标字数（target_words）参数传递给 LLM。

**Architecture:** 后端 `stream_generate` 新增 `tone` 参数，按三种 tone 值选择不同 system_prompt；调用链 `dispatch_stream_generate → _generate_stream → generate_block` 依次透传参数；前端 `startSingleGenerate` 替换 EventSource 为 fetch + ReadableStream，并从 toolbar 读取参数。

**Tech Stack:** FastAPI (Python), Pydantic, fetch API + ReadableStream (浏览器原生), text/event-stream SSE

---

## 文件变更地图

| 文件 | 变更类型 | 内容 |
|------|---------|------|
| `backend/services/llm.py` | Modify | `stream_generate` + `dispatch_stream_generate` 新增 `tone` 参数 |
| `backend/routes/blocks.py` | Modify | 新增 `GenerateRequest` 模型，`_generate_stream` / `generate_block` 接收并传递参数 |
| `frontend/workbench.html` | Modify | `startSingleGenerate` 替换 EventSource 为 fetch SSE，`finishGenerate` 接收 AbortController |

---

### Task 1: 后端 — stream_generate 新增 tone 参数

**Files:**
- Modify: `backend/services/llm.py`（`stream_generate` 函数，约第 208–270 行）

- [ ] **Step 1: 在 stream_generate 函数签名末尾新增 tone 参数**

找到：
```python
async def stream_generate(
    config: LLMConfig,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
    extra_prompt: str = "",
    doc_template: str = "",
) -> AsyncIterator[str]:
```

替换为：
```python
async def stream_generate(
    config: LLMConfig,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
    extra_prompt: str = "",
    doc_template: str = "",
    tone: str = "official",
) -> AsyncIterator[str]:
```

- [ ] **Step 2: 在函数体开头按 tone 选择 system_prompt**

找到现有 system_prompt 赋值：
```python
    system_prompt = (
        "你是一位专业的政企信息化项目方案撰写专家，擅长对技术规范书原文进行逐段忠实扩写。"
        "你的核心职责是：完全保留原文核心含义与逻辑框架，在此基础上补充背景释义、功能价值和应用场景，"
        "采用政企项目方案正式书面文风，不篡改原意，不新增无关内容。"
        "请使用 Markdown 格式输出。"
    )
```

替换为：
```python
    _SYSTEM_PROMPTS = {
        "official": (
            "你是一位专业的政企信息化项目方案撰写专家，擅长对技术规范书原文进行逐段忠实扩写。"
            "你的核心职责是：完全保留原文核心含义与逻辑框架，在此基础上补充背景释义、功能价值和应用场景，"
            "采用政企项目方案正式书面文风，不篡改原意，不新增无关内容。"
            "请使用 Markdown 格式输出。"
        ),
        "tech": (
            "你是一位资深技术架构师，擅长将技术规范书内容转化为精准的技术方案描述。"
            "你的核心职责是：保留原文逻辑框架，使用准确的技术术语和架构语言，"
            "突出系统设计、接口规范、性能指标等技术要素，逻辑严密、表述精准。"
            "请使用 Markdown 格式输出。"
        ),
        "concise": (
            "你是一位精简表达专家，擅长将技术规范书内容提炼为简洁有力的方案文字。"
            "你的核心职责是：保留原文核心信息，去除冗余修饰，每句话都有实际信息量，"
            "句式简短清晰，避免空泛表述和套话。"
            "请使用 Markdown 格式输出。"
        ),
    }
    system_prompt = _SYSTEM_PROMPTS.get(tone, _SYSTEM_PROMPTS["official"])
```

- [ ] **Step 3: 手动验证语法无误**

```bash
cd /Users/jianghe/projects/ge-solution
python -c "import backend.services.llm" 2>&1 || python -c "import sys; sys.path.insert(0,'backend'); import services.llm; print('OK')"
```

期望输出：`OK`（无报错）

- [ ] **Step 4: Commit**

```bash
git add backend/services/llm.py
git commit -m "feat(llm): add tone param to stream_generate with 3 system prompts"
```

---

### Task 2: 后端 — dispatch_stream_generate 透传 tone

**Files:**
- Modify: `backend/services/llm.py`（`dispatch_stream_generate` 函数，约第 336–382 行）

- [ ] **Step 1: 新增 tone 参数到 dispatch_stream_generate 签名**

找到：
```python
async def dispatch_stream_generate(
    configs: list[LLMConfig],
    rr_start_index: int,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
    extra_prompt: str = "",
    doc_template: str = "",
) -> AsyncGenerator[str, None]:
```

替换为：
```python
async def dispatch_stream_generate(
    configs: list[LLMConfig],
    rr_start_index: int,
    section_title: str,
    original_content: str,
    target_words: int = 500,
    doc_summary: str = "",
    extra_prompt: str = "",
    doc_template: str = "",
    tone: str = "official",
) -> AsyncGenerator[str, None]:
```

- [ ] **Step 2: 在调用 stream_generate 时透传 tone**

找到：
```python
            async for token in stream_generate(config, section_title, original_content, target_words, doc_summary, extra_prompt, doc_template):
```

替换为：
```python
            async for token in stream_generate(config, section_title, original_content, target_words, doc_summary, extra_prompt, doc_template, tone):
```

- [ ] **Step 3: 验证语法**

```bash
python -c "import sys; sys.path.insert(0,'backend'); import services.llm; print('OK')"
```

期望输出：`OK`

- [ ] **Step 4: Commit**

```bash
git add backend/services/llm.py
git commit -m "feat(llm): pass tone through dispatch_stream_generate"
```

---

### Task 3: 后端 — blocks 路由接收参数

**Files:**
- Modify: `backend/routes/blocks.py`

- [ ] **Step 1: 新增 GenerateRequest Pydantic 模型**

在文件中 `BlockUpdateRequest` 类定义之后（约第 42 行附近），现有模型结束处，插入：

```python
VALID_TONES = {"official", "tech", "concise"}

class GenerateRequest(BaseModel):
    target_words: int = Field(default=800, ge=100, le=5000)
    tone: str = Field(default="official")

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, v: str) -> str:
        if v not in VALID_TONES:
            raise ValueError(f"tone 必须为 official / tech / concise，收到：{v}")
        return v
```

- [ ] **Step 2: 修改 _generate_stream 接收 target_words 和 tone**

找到：
```python
async def _generate_stream(block_id: int, db) -> AsyncGenerator[str, None]:
    block = await get_block(db, block_id)
    if block is None:
        yield format_sse_event("error", {"message": f"Block 不存在：{block_id}"}); return

    if block.get("kind") != "content":
        yield format_sse_event("error", {"message": "仅 content 类型 block 支持生成"}); return

    project_id = block["project_id"]
    row = await (await db.execute("SELECT summary FROM projects WHERE id = ?", (project_id,))).fetchone()
    doc_summary = (row["summary"] or "") if row else ""

    await update_block_status(db, block_id, "generating")

    configs, rr_index = config_store.get_configs_and_next_index()
    parts: list[str] = []

    try:
        async for token in dispatch_stream_generate(
            configs, rr_index, block["title"], block.get("content", ""),
            doc_summary=doc_summary,
        ):
```

替换为：
```python
async def _generate_stream(block_id: int, db, target_words: int = 800, tone: str = "official") -> AsyncGenerator[str, None]:
    block = await get_block(db, block_id)
    if block is None:
        yield format_sse_event("error", {"message": f"Block 不存在：{block_id}"}); return

    if block.get("kind") != "content":
        yield format_sse_event("error", {"message": "仅 content 类型 block 支持生成"}); return

    project_id = block["project_id"]
    row = await (await db.execute("SELECT summary FROM projects WHERE id = ?", (project_id,))).fetchone()
    doc_summary = (row["summary"] or "") if row else ""

    await update_block_status(db, block_id, "generating")

    configs, rr_index = config_store.get_configs_and_next_index()
    parts: list[str] = []

    try:
        async for token in dispatch_stream_generate(
            configs, rr_index, block["title"], block.get("content", ""),
            doc_summary=doc_summary,
            target_words=target_words,
            tone=tone,
        ):
```

- [ ] **Step 3: 修改 generate_block 端点接收 GenerateRequest**

找到：
```python
@router.post("/blocks/{block_id}/generate")
async def generate_block(block_id: int):
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        if not config_store.is_configured():
            raise HTTPException(status_code=400, detail="未配置 API Key")

    async def stream():
        async with get_db() as db:
            async for chunk in _generate_stream(block_id, db):
                yield chunk
```

替换为：
```python
@router.post("/blocks/{block_id}/generate")
async def generate_block(block_id: int, req: GenerateRequest = None):
    if req is None:
        req = GenerateRequest()
    async with get_db() as db:
        block = await get_block(db, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"Block 不存在：{block_id}")
        if not config_store.is_configured():
            raise HTTPException(status_code=400, detail="未配置 API Key")

    async def stream():
        async with get_db() as db:
            async for chunk in _generate_stream(block_id, db, req.target_words, req.tone):
                yield chunk
```

- [ ] **Step 4: 验证语法**

```bash
python -c "import sys; sys.path.insert(0,'backend'); import routes.blocks; print('OK')"
```

期望输出：`OK`

- [ ] **Step 5: Commit**

```bash
git add backend/routes/blocks.py
git commit -m "feat(blocks): accept tone and target_words in POST generate endpoint"
```

---

### Task 4: 前端 — 替换 EventSource 为 fetch SSE

**Files:**
- Modify: `frontend/workbench.html`（`startSingleGenerate` 和 `finishGenerate` 函数）

- [ ] **Step 1: 替换 startSingleGenerate 函数**

找到整个 `startSingleGenerate` 函数（从 `function startSingleGenerate()` 到最后一个 `}`）：

```js
      // ── AI 生成（单块）：调用 api.blocks.generate 的 EventSource ──
      function startSingleGenerate() {
        if (!currentBlock || editorGenerating) return;
        editorGenerating = true;

        const editable = document.getElementById('ced-editable');
        const placeholder = document.getElementById('ced-placeholder');
        const hint = document.getElementById('ced-ai-hint');
        const banner = document.getElementById('ced-gen-banner');
        const pbar = document.getElementById('ced-gen-pbar');
        const pct = document.getElementById('ced-gen-pct');
        const stage = document.getElementById('ced-gen-stage');

        // 切到生成中状态
        placeholder.style.display = 'none';
        if (hint) hint.style.display = 'none';
        editable.style.display = '';
        editable.textContent = '';
        banner.classList.add('visible');
        let progress = 0;

        const stages = ['· 分析应标要求', '· 匹配原始素材', '· 生成正文内容', '· 整理输出'];
        let stageIdx = 0;
        const stageTimer = setInterval(() => {
          stageIdx = (stageIdx + 1) % stages.length;
          if (stage) stage.textContent = stages[stageIdx];
          progress = Math.min(95, progress + Math.random() * 8 + 2);
          if (pbar) pbar.style.width = progress + '%';
          if (pct) pct.textContent = Math.round(progress) + '%';
        }, 600);

        // 使用 api.blocks.generate EventSource
        const es = new EventSource(`/api/blocks/${currentBlock.id}/generate`);
        es.onmessage = e => {
          try {
            const data = JSON.parse(e.data);
            if (data.token) editable.textContent += data.token;
            if (data.done || data.finished) {
              finishGenerate(stageTimer, es, banner, pbar, pct);
            }
          } catch {}
        };
        es.onerror = () => finishGenerate(stageTimer, es, banner, pbar, pct);

        document.getElementById('ced-gen-stop')?.addEventListener('click', () => {
          finishGenerate(stageTimer, es, banner, pbar, pct);
        }, { once: true });
      }
```

替换为：

```js
      // ── AI 生成（单块）：POST SSE fetch ──
      async function startSingleGenerate() {
        if (!currentBlock || editorGenerating) return;
        editorGenerating = true;

        const tone = document.querySelector('.ced-tone-btn.active')?.dataset.tone || 'official';
        const targetWords = parseInt(document.getElementById('ced-wordlimit')?.value) || 800;

        const editable = document.getElementById('ced-editable');
        const placeholder = document.getElementById('ced-placeholder');
        const hint = document.getElementById('ced-ai-hint');
        const banner = document.getElementById('ced-gen-banner');
        const pbar = document.getElementById('ced-gen-pbar');
        const pct = document.getElementById('ced-gen-pct');
        const stage = document.getElementById('ced-gen-stage');

        placeholder.style.display = 'none';
        if (hint) hint.style.display = 'none';
        editable.style.display = '';
        editable.textContent = '';
        banner.classList.add('visible');
        let progress = 0;

        const stages = ['· 分析应标要求', '· 匹配原始素材', '· 生成正文内容', '· 整理输出'];
        let stageIdx = 0;
        const stageTimer = setInterval(() => {
          stageIdx = (stageIdx + 1) % stages.length;
          if (stage) stage.textContent = stages[stageIdx];
          progress = Math.min(95, progress + Math.random() * 8 + 2);
          if (pbar) pbar.style.width = progress + '%';
          if (pct) pct.textContent = Math.round(progress) + '%';
        }, 600);

        const abortCtrl = new AbortController();
        document.getElementById('ced-gen-stop')?.addEventListener('click', () => {
          abortCtrl.abort();
        }, { once: true });

        try {
          const resp = await fetch(`/api/blocks/${currentBlock.id}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tone, target_words: targetWords }),
            signal: abortCtrl.signal,
          });

          if (!resp.ok || !resp.body) {
            finishGenerate(stageTimer, null, banner, pbar, pct);
            return;
          }

          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buf = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();
            for (const line of lines) {
              if (!line.startsWith('data:')) continue;
              try {
                const data = JSON.parse(line.slice(5).trim());
                if (data.text) editable.textContent += data.text;
                if (data.block_id) finishGenerate(stageTimer, null, banner, pbar, pct);
              } catch {}
            }
          }
        } catch (err) {
          if (err.name !== 'AbortError') console.error('生成失败', err);
        }

        finishGenerate(stageTimer, null, banner, pbar, pct);
      }
```

- [ ] **Step 2: 替换 finishGenerate 函数**

找到：
```js
      function finishGenerate(stageTimer, es, banner, pbar, pct) {
        clearInterval(stageTimer);
        es.close();
        editorGenerating = false;
```

替换为：
```js
      function finishGenerate(stageTimer, _unused, banner, pbar, pct) {
        clearInterval(stageTimer);
        editorGenerating = false;
```

- [ ] **Step 3: 在浏览器验证**

启动后端：
```bash
cd /Users/jianghe/projects/ge-solution && python -m uvicorn backend.main:app --reload --port 8000
```

打开 `http://localhost:8000/workbench.html?projectId=1`，选择一个 content block，切换文风为「技术」，设置字数为 300，点击「AI 生成」，验证：
1. 控制台无报错
2. 内容流式出现在编辑器
3. 停止按钮可中止
4. 完成后 banner 消失

- [ ] **Step 4: Commit**

```bash
git add frontend/workbench.html
git commit -m "feat(workbench): replace EventSource with fetch SSE, pass tone and target_words"
```

---

## Self-Review

**Spec coverage 检查：**
- ✅ POST SSE 替换 EventSource → Task 4
- ✅ target_words 传递 → Task 3 + Task 4
- ✅ tone 传递 → Task 1 + Task 2 + Task 3 + Task 4
- ✅ 三种 tone system_prompt 差异 → Task 1
- ✅ 停止按钮中止 → Task 4（AbortController）
- ✅ 旧 EventSource 代码移除 → Task 4

**Placeholder 检查：** 无 TBD / TODO / "类似 Task N"。

**类型一致性检查：**
- `tone` 参数名在 llm.py / blocks.py / HTML 中一致
- `target_words` 在 Python 侧用下划线，JS body 也用 `target_words`（JSON key）
- SSE token 事件 key：后端发 `{"text": ...}`，前端读 `data.text` ✅
- block_done 事件：后端发 `{"block_id": ..., "content": ..., "revision_no": ...}`，前端检查 `data.block_id` ✅
