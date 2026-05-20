# 批量编写模块实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 workbench 实现「批量 AI 编写」——点击按钮后，系统对每个 block 实时检索匹配素材 + 流式写入内容，完成的 block 可立即编辑。

**Architecture:** 后端新增 `services/retrieval.py`（素材检索）和 `/generate-all` SSE 端点；前端新增 `generate-session.js`（SSE 状态机）和 `context-panel.js`（右侧面板），三个 JS 模块通过 CustomEvent 解耦通信。

**Tech Stack:** FastAPI SSE、aiosqlite、AsyncGenerator、原生 JS CustomEvent

---

## 文件清单

| 操作 | 文件 |
|---|---|
| 新增 | `backend/services/retrieval.py` |
| 修改 | `backend/services/llm.py`（新增 `dispatch_block_write`） |
| 修改 | `backend/routes/generate.py`（新增 `/generate-all` 端点） |
| 新增 | `frontend/assets/generate-session.js` |
| 新增 | `frontend/assets/context-panel.js` |
| 修改 | `frontend/workbench.html`（按钮 + 进度区 + 引入新 JS） |
| 修改 | `frontend/assets/workbench.js`（监听 block:token / block:done） |
| 新增 | `tests/test_generate_all.py` |

---

## Task 1：retrieval.py — 素材检索模块

**Files:**
- Create: `backend/services/retrieval.py`
- Test: `tests/test_generate_all.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_generate_all.py
"""批量编写模块测试 — pytest tests/test_generate_all.py -v"""
from services.retrieval import retrieve_chunks

def _make_chunks(texts: list[str]) -> list[dict]:
    return [
        {"material_id": 1, "chunk_index": i, "content": t}
        for i, t in enumerate(texts)
    ]

def test_retrieve_returns_top_k():
    chunks = _make_chunks(["技术方案 架构设计", "价格报价", "技术方案 实施计划", "售后服务", "技术方案 测试"])
    result = retrieve_chunks(chunks, query="技术方案", top_k=3)
    assert len(result) == 3
    # 包含"技术方案"的片段应排在前面
    contents = [r["content"] for r in result]
    assert all("技术方案" in c for c in contents)

def test_retrieve_returns_all_when_less_than_k():
    chunks = _make_chunks(["A", "B"])
    result = retrieve_chunks(chunks, query="A", top_k=5)
    assert len(result) == 2

def test_retrieve_empty_chunks():
    result = retrieve_chunks([], query="技术方案", top_k=3)
    assert result == []
```

- [ ] **Step 2: 运行确认失败**

```
pytest tests/test_generate_all.py -v
```
期望：`ModuleNotFoundError: No module named 'services.retrieval'`

- [ ] **Step 3: 实现 retrieval.py**

```python
# backend/services/retrieval.py
def retrieve_chunks(
    chunks: list[dict],
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """关键词交集计分，返回 Top-K 相关 chunks。"""
    if not chunks:
        return []
    query_words = set(query.lower().split())

    def score(chunk: dict) -> int:
        cw = set(chunk["content"].lower().split())
        return len(query_words & cw)

    ranked = sorted(chunks, key=score, reverse=True)
    return ranked[:top_k]
```

- [ ] **Step 4: 运行确认通过**

```
pytest tests/test_generate_all.py -v
```
期望：3 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/retrieval.py tests/test_generate_all.py
git commit -m "feat(retrieval): add retrieve_chunks for block-level material matching"
```

---

## Task 2：llm.py — 新增 dispatch_block_write

**Files:**
- Modify: `backend/services/llm.py`
- Test: `tests/test_generate_all.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_generate_all.py` 末尾追加：

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_dispatch_block_write_yields_tokens():
    from services.llm import dispatch_block_write
    from services.config_store import LLMConfig

    fake_config = LLMConfig(
        provider="openai", model="gpt-4o-mini",
        api_key="sk-test", base_url="https://api.openai.com/v1",
    )

    async def fake_stream(*args, **kwargs):
        for token in ["技", "术", "方", "案"]:
            yield token

    with patch("services.llm.stream_generate", side_effect=fake_stream):
        tokens = []
        async for t in dispatch_block_write(
            configs=[fake_config], rr_start_index=0,
            title="技术方案", requirement="需满足 ISO 标准",
            chunks=[{"content": "参考案例：某项目采用…"}],
        ):
            tokens.append(t)

    assert tokens == ["技", "术", "方", "案"]
```

- [ ] **Step 2: 运行确认失败**

```
pytest tests/test_generate_all.py::test_dispatch_block_write_yields_tokens -v
```
期望：`ImportError: cannot import name 'dispatch_block_write'`

- [ ] **Step 3: 在 llm.py 末尾追加**

```python
async def dispatch_block_write(
    configs: list["LLMConfig"],
    rr_start_index: int,
    title: str,
    requirement: str,
    chunks: list[dict],
    target_words: int = 600,
) -> "AsyncGenerator[str, None]":
    """
    为单个 block 流式生成正文内容。
    将 requirement + chunks 拼入 extra_prompt，复用 dispatch_stream_generate。
    """
    snippets = "\n".join(
        f"- {c['content'][:200]}" for c in chunks
    )
    extra_prompt = ""
    if requirement:
        extra_prompt += f"【应标要求】\n{requirement}\n\n"
    if snippets:
        extra_prompt += f"【参考素材】\n{snippets}"

    async for token in dispatch_stream_generate(
        configs=configs,
        rr_start_index=rr_start_index,
        section_title=title,
        original_content="",
        target_words=target_words,
        extra_prompt=extra_prompt,
    ):
        yield token
```

- [ ] **Step 4: 运行确认通过**

```
pytest tests/test_generate_all.py -v
```
期望：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/llm.py tests/test_generate_all.py
git commit -m "feat(llm): add dispatch_block_write for block-level stream generation"
```

---

## Task 3：generate.py — /generate-all SSE 端点

**Files:**
- Modify: `backend/routes/generate.py`
- Test: `tests/test_generate_all.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_generate_all.py` 末尾追加：

```python
import json as _json
from fastapi.testclient import TestClient
from main import app

def _parse_sse_events(text: str) -> list[dict]:
    events, cur = [], {}
    for line in text.splitlines():
        if line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            cur["data"] = _json.loads(line[5:].strip())
        elif line == "" and cur:
            events.append(cur); cur = {}
    if cur:
        events.append(cur)
    return events

def test_generate_all_no_blocks(tmp_path):
    """项目无 blocks 时返回 generate_done(generated=0)"""
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/projects", json={"name": "gen-test"})
    pid = r.json()["id"]
    resp = client.post(f"/api/projects/{pid}/generate-all")
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    names = [e["event"] for e in events]
    assert "generate_start" in names
    assert "generate_done" in names
    done = next(e for e in events if e["event"] == "generate_done")
    assert done["data"]["generated"] == 0
```

- [ ] **Step 2: 运行确认失败**

```
pytest tests/test_generate_all.py::test_generate_all_no_blocks -v
```
期望：404 或路由不存在导致失败

- [ ] **Step 3: 在 generate.py 中新增端点**

在文件末尾（`router` 已存在）追加：

```python
import json as _json
from db import get_db
from services.block_store import list_blocks as _list_blocks, list_chunks_by_project, update_block_content
from services.retrieval import retrieve_chunks
from services.llm import dispatch_block_write
from services.config_store import config_store
from utils.sse import format_sse_event

@router.post("/projects/{project_id}/generate-all")
async def generate_all_blocks(project_id: int):
    """SSE：批量为每个 block 检索素材 + 流式生成正文内容"""

    async def _event_gen():
        async with get_db() as db:
            from services.block_store import get_project
            project = await get_project(db, project_id)
            if project is None:
                yield format_sse_event("error", {"message": f"项目不存在：{project_id}"})
                return
            blocks = await _list_blocks(db, project_id)
            all_chunks = await list_chunks_by_project(db, project_id)

        total = len(blocks)
        yield format_sse_event("generate_start", {"total": total})

        if total == 0:
            yield format_sse_event("generate_done", {"generated": 0, "skipped": 0})
            return

        configs, rr_index = config_store.get_configs_and_next_index()
        generated = 0
        skipped = 0

        for idx, block in enumerate(blocks):
            block_id = block["id"]
            title = block.get("title", "")
            requirement = block.get("requirement", "") or ""

            yield format_sse_event("generate_block_start", {
                "block_id": block_id, "title": title,
                "index": idx, "total": total,
            })

            try:
                query = f"{title} {requirement}".strip()
                matched_chunks = retrieve_chunks(all_chunks, query=query, top_k=5)
                sources = [
                    {"material_id": c["material_id"], "chunk_index": c["chunk_index"],
                     "snippet": c["content"][:120]}
                    for c in matched_chunks
                ]

                content_parts = []
                async for token in dispatch_block_write(
                    configs=configs,
                    rr_start_index=(rr_index + idx) % len(configs),
                    title=title,
                    requirement=requirement,
                    chunks=matched_chunks,
                ):
                    content_parts.append(token)
                    yield format_sse_event("generate_block_token", {
                        "block_id": block_id, "token": token,
                    })

                full_content = "".join(content_parts)
                async with get_db() as db:
                    await db.execute(
                        "UPDATE blocks SET content=?, source=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (full_content, _json.dumps(sources, ensure_ascii=False), block_id),
                    )
                    await db.commit()

                yield format_sse_event("generate_block_done", {
                    "block_id": block_id,
                    "content": full_content,
                    "sources": sources,
                })
                generated += 1

            except Exception as exc:
                logger.exception(f"block {block_id} 生成失败: {exc}")
                yield format_sse_event("error", {"message": str(exc), "block_id": block_id})
                skipped += 1

        yield format_sse_event("generate_done", {"generated": generated, "skipped": skipped})

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

注意：`generate.py` 顶部已有 `import logging; logger = logging.getLogger(__name__)` 和 `StreamingResponse` 导入，无需重复添加。

- [ ] **Step 4: 运行确认通过**

```
pytest tests/test_generate_all.py -v
```
期望：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/routes/generate.py tests/test_generate_all.py
git commit -m "feat(generate): add /generate-all SSE endpoint for batch block writing"
```

---

## Task 4：前端 generate-session.js

**Files:**
- Create: `frontend/assets/generate-session.js`

- [ ] **Step 1: 创建文件**

```javascript
// frontend/assets/generate-session.js
// SSE 订阅 + token 分发 + 生成状态机
// 通过 CustomEvent 向外广播，不直接操作 DOM

export class GenerateSession {
  constructor(projectId) {
    this.projectId = projectId;
    this._sse = null;
    this._running = false;
  }

  get running() { return this._running; }

  start() {
    if (this._running) return;
    this._running = true;
    this._sse = new EventSource(`/api/projects/${this.projectId}/generate-all`, { withCredentials: false });

    // 用 POST 替代 EventSource（EventSource 只支持 GET）
    // 改用 fetch + ReadableStream
    this._sse = null;
    this._startFetch();
  }

  stop() {
    this._running = false;
    if (this._reader) this._reader.cancel();
  }

  async _startFetch() {
    const resp = await fetch(`/api/projects/${this.projectId}/generate-all`, { method: "POST" });
    if (!resp.ok) { this._running = false; return; }

    this._reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (this._running) {
      const { done, value } = await this._reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop(); // 保留未完整行
      this._processLines(lines);
    }
    this._running = false;
    window.dispatchEvent(new CustomEvent("session:done"));
  }

  _processLines(lines) {
    let eventName = "", dataStr = "";
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataStr = line.slice(5).trim();
      else if (line === "" && eventName) {
        try {
          const data = JSON.parse(dataStr);
          this._dispatch(eventName, data);
        } catch (_) {}
        eventName = ""; dataStr = "";
      }
    }
  }

  _dispatch(eventName, data) {
    switch (eventName) {
      case "generate_start":
        window.dispatchEvent(new CustomEvent("session:start", { detail: data }));
        break;
      case "generate_block_start":
        window.dispatchEvent(new CustomEvent("block:start", { detail: data }));
        break;
      case "generate_block_token":
        window.dispatchEvent(new CustomEvent("block:token", { detail: data }));
        break;
      case "generate_block_done":
        window.dispatchEvent(new CustomEvent("block:done", { detail: data }));
        break;
      case "generate_done":
        window.dispatchEvent(new CustomEvent("session:done", { detail: data }));
        this._running = false;
        break;
      case "error":
        window.dispatchEvent(new CustomEvent("block:error", { detail: data }));
        break;
    }
  }
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/assets/generate-session.js
git commit -m "feat(frontend): add GenerateSession SSE state machine"
```

---

## Task 5：前端 context-panel.js

**Files:**
- Create: `frontend/assets/context-panel.js`

- [ ] **Step 1: 创建文件**

```javascript
// frontend/assets/context-panel.js
// 右侧上下文面板：要求文本 + 匹配素材 + 原文溯源

export function initContextPanel() {
  const reqSection = document.querySelector("[data-panel-requirement]");
  const srcSection = document.querySelector("[data-panel-sources]");

  function renderRequirement(requirement) {
    if (!reqSection) return;
    if (!requirement) { reqSection.innerHTML = "<p class='panel-empty'>暂无应标要求</p>"; return; }
    const items = requirement.split(/[；;。\n]/).map(s => s.trim()).filter(Boolean);
    reqSection.innerHTML = items.map(s => `<p class="req-item">· ${s}</p>`).join("");
  }

  function renderSources(sources, materials) {
    if (!srcSection) return;
    if (!sources || sources.length === 0) {
      srcSection.innerHTML = "<p class='panel-empty'>暂无匹配素材</p>"; return;
    }
    srcSection.innerHTML = sources.map(s => {
      const matName = materials?.[s.material_id] ?? `素材 #${s.material_id}`;
      return `
        <div class="source-card">
          <div class="source-meta">来自：${matName}</div>
          <p class="source-snippet">${s.snippet}</p>
          <button class="source-origin-btn" type="button"
            data-material-id="${s.material_id}"
            data-chunk-index="${s.chunk_index}">原文 →</button>
        </div>`;
    }).join("");

    srcSection.querySelectorAll(".source-origin-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const mid = btn.dataset.materialId;
        const cidx = btn.dataset.chunkIndex;
        // 当前阶段：toast 提示；后续可升级为 PDF 内联预览
        const name = materials?.[mid] ?? `素材 #${mid}`;
        alert(`原文位置：${name}，第 ${cidx} 段`);
      });
    });
  }

  // 选中 block 时更新面板
  document.addEventListener("click", e => {
    const block = e.target.closest(".editor-block");
    if (!block) return;
    renderRequirement(block.dataset.requirement || "");
    try {
      const sources = JSON.parse(block.dataset.source || "[]");
      // materials map 需外部注入
      renderSources(sources, window.__materialsMap);
    } catch (_) {
      renderSources([], null);
    }
  });

  // block:done 时更新 dataset.source，如果当前面板显示的正是该 block 则刷新
  window.addEventListener("block:done", e => {
    const { block_id, sources } = e.detail;
    const blockEl = document.getElementById(`block-${block_id}`);
    if (blockEl) {
      blockEl.dataset.source = JSON.stringify(sources);
    }
  });
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/assets/context-panel.js
git commit -m "feat(frontend): add context-panel for requirement + sources display"
```

---

## Task 6：workbench.html + workbench.js 接入

**Files:**
- Modify: `frontend/workbench.html`
- Modify: `frontend/assets/workbench.js`

- [ ] **Step 1: 在 workbench.html 的 `editor-toolbar` 区添加批量编写按钮和进度文字**

找到：
```html
<div class="editor-toolbar">
  <nav class="breadcrumb" data-breadcrumb aria-label="当前位置"></nav>
</div>
```

改为：
```html
<div class="editor-toolbar">
  <nav class="breadcrumb" data-breadcrumb aria-label="当前位置"></nav>
  <div class="generate-controls">
    <button type="button" id="btn-generate-all" class="btn-generate-all">✦ 批量编写</button>
    <span class="generate-progress" id="generate-progress" hidden></span>
  </div>
</div>
```

- [ ] **Step 2: 在 workbench.html 右侧 context-pane 中添加面板数据区**

找到 `<section class="context-section">` 中 `<h4>当前标题域</h4>` 前插入：

```html
<section class="context-section" id="panel-requirement-section">
  <h4>应标要求</h4>
  <div data-panel-requirement></div>
</section>
<section class="context-section" id="panel-sources-section">
  <h4>匹配素材</h4>
  <div data-panel-sources></div>
</section>
```

- [ ] **Step 3: 在 workbench.html 末尾 `</body>` 前引入新 JS**

```html
<script type="module">
  import { GenerateSession } from './assets/generate-session.js';
  import { initContextPanel } from './assets/context-panel.js';
  initContextPanel();

  const params = new URLSearchParams(location.search);
  const projectId = params.get('projectId');
  let session = null;

  const btnGen = document.getElementById('btn-generate-all');
  const progress = document.getElementById('generate-progress');

  window.addEventListener('session:start', e => {
    btnGen.textContent = '■ 停止';
    progress.hidden = false;
    progress.textContent = `正在编写 0 / ${e.detail.total}`;
  });

  let doneCount = 0;
  window.addEventListener('block:done', e => {
    doneCount++;
    if (progress) progress.textContent = `正在编写 ${doneCount} / ...`;
  });

  window.addEventListener('session:done', e => {
    btnGen.textContent = '✦ 批量编写';
    progress.hidden = true;
    doneCount = 0;
    session = null;
  });

  btnGen.addEventListener('click', () => {
    if (session?.running) { session.stop(); return; }
    doneCount = 0;
    session = new GenerateSession(projectId);
    session.start();
  });
</script>
```

- [ ] **Step 4: 在 workbench.js 中监听 block:token 和 block:done**

在 `loadBlocks()` 函数末尾追加：

```javascript
// 监听批量生成事件
window.addEventListener('block:start', e => {
  const el = document.getElementById(`block-${e.detail.block_id}`);
  if (!el) return;
  el.dataset.generating = 'true';
  const editable = el.querySelector('[contenteditable]');
  if (editable) { editable.contentEditable = 'false'; editable.textContent = ''; }
});

window.addEventListener('block:token', e => {
  const el = document.getElementById(`block-${e.detail.block_id}`);
  if (!el) return;
  const editable = el.querySelector('[contenteditable]');
  if (editable) editable.textContent += e.detail.token;
});

window.addEventListener('block:done', e => {
  const el = document.getElementById(`block-${e.detail.block_id}`);
  if (!el) return;
  delete el.dataset.generating;
  const editable = el.querySelector('[contenteditable]');
  if (editable) editable.contentEditable = 'true';
});

window.addEventListener('block:error', e => {
  if (!e.detail.block_id) return;
  const el = document.getElementById(`block-${e.detail.block_id}`);
  if (!el) return;
  el.dataset.error = 'true';
  const editable = el.querySelector('[contenteditable]');
  if (editable) editable.contentEditable = 'true';
});
```

- [ ] **Step 5: 提交**

```bash
git add frontend/workbench.html frontend/assets/workbench.js
git commit -m "feat(workbench): wire generate-all button, progress, context panel"
```

---

## Task 7：主路由注册 + 手动冒烟测试

**Files:**
- Check: `backend/main.py`

- [ ] **Step 1: 确认 generate router 已注册**

```bash
grep -n "generate" backend/main.py
```

期望：看到 `include_router` 引用 generate。若无则在 main.py 中添加：
```python
from routes.generate import router as generate_router
app.include_router(generate_router, prefix="/api")
```

- [ ] **Step 2: 启动服务，冒烟测试**

```bash
cd backend && uvicorn main:app --reload --port 8000
```

新终端：
```bash
# 创建项目并上传应标要求（替换为真实文件路径）
curl -s -X POST http://localhost:8000/api/projects -H "Content-Type: application/json" -d '{"name":"测试"}' | python3 -m json.tool
# 记录 project_id，然后测试 generate-all（无 blocks 场景）
curl -s -X POST http://localhost:8000/api/projects/1/generate-all
```

期望输出包含：
```
event: generate_start
data: {"total": 0}

event: generate_done
data: {"generated": 0, "skipped": 0}
```

- [ ] **Step 3: 运行全量测试**

```bash
pytest tests/test_generate_all.py -v
```

期望：全部 PASS

- [ ] **Step 4: 提交**

```bash
git add backend/main.py
git commit -m "chore: verify generate router registration"
```
