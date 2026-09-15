# 执行计划：章节提炼完成后展示匹配原始素材

设计文档：`docs/superpowers/specs/2026-06-03-source-material-matching-panel-design.md`

## Phase 1 — 后端：outline SSE 推送匹配素材

**目标**：每个章节提炼完成时，后端附带 Top-3 匹配 chunks 推送给前端。

**改动文件**：`backend/routes/materials.py`

**Tasks**：
1. `generate_outline` 函数顶部预加载所有 chunks 并 JOIN materials 拿 filename
2. 每完成一个 block 时，调 `retrieve_chunks` 取 Top-3，构造 matched_sources 数组（含 material_id/filename/chunk_index/content/score）
3. `outline_block` SSE 事件 payload 新增 `matched_sources` 字段
4. 同步序列化写入 `blocks.source`（替换原 snippet 结构为完整 content）

**验收**：手动触发 outline 接口，curl 观察 SSE 流中 outline_block 事件包含 matched_sources。

## Phase 2 — 后端：单 block 匹配端点

**目标**：支持用户后续上传素材后单独刷新某章节的匹配。

**改动文件**：`backend/routes/blocks.py`

**Tasks**：
1. 新增 `GET /api/blocks/{block_id}/matched-sources`
2. 复用 retrieval 逻辑，返回 Top-3 matched_sources
3. 同步更新 `blocks.source` 字段

**验收**：curl 端点返回结构与 outline_block.matched_sources 一致。

## Phase 3 — 前端：右侧面板 Tab 切换骨架

**目标**：context-pane 改造为"本章匹配 / 全部素材"双 Tab。

**改动文件**：`frontend/workbench.html`

**Tasks**：
1. 头部加 Tab 切换（本章匹配 / 全部素材）
2. 现有"+ 添加原始素材"和文件列表移到"全部素材" Tab
3. "本章匹配" Tab 添加占位容器和刷新按钮
4. CSS：Tab 切换样式与现有 ref-card 视觉对齐

**验收**：浏览器打开 workbench，Tab 切换正常，原文件列表在"全部素材"下可见。

## Phase 4 — 前端：消费 outline_block.matched_sources

**目标**：从 SSE 流累积匹配数据，章节切换时实时展示。

**改动文件**：`frontend/workbench.html`（内联脚本）+ 可能新增 `frontend/assets/matched-sources-panel.js`

**Tasks**：
1. 全局 `blockMatchedCache` Map<blockId, matched_sources[]>
2. 监听 SSE 中 outline_block 事件累积到 cache（GenerateSession 内）
3. `__onBlockSelected(b)` 触发 `renderMatchedSources(blockId)`
4. 渲染：文件徽标 + filename + chunk_index + 内容摘要（100 字）+ 展开/收起
5. 缓存为空时从 b.source 反序列化兜底

**验收**：跑一遍提炼流程，切换章节看到对应匹配卡片；刷新页面后切章节仍显示（DB 兜底生效）。

## Phase 5 — 前端：刷新匹配按钮

**目标**：用户上传新素材后能手动刷新当前章节匹配。

**改动文件**：`frontend/workbench.html`

**Tasks**：
1. 刷新按钮接 `GET /api/blocks/{id}/matched-sources`
2. 成功后更新 cache 重渲染
3. loading 态 + 错误提示

**验收**：上传一个新 source 文件，点刷新，看到新匹配出现。

## Phase 6 — 边界与空状态

**Tasks**：
1. 无 source 素材：展示"先上传原始素材"提示 + 跳转"全部素材" Tab
2. 全部 score=0：展示"无强相关素材"
3. matched_sources 长度 < 3：按实际渲染

## Out of Scope

- [+ 引用] 按钮行为（下个迭代）
- chunk 跳转原文位置（下个迭代）
- LLM 语义匹配（chunks 量级达到 100+ 时再升级）
