# 章节提炼完成后展示匹配原始素材 — 设计方案

## 背景

材料撰写工作台（workbench）目前左中右三栏：
- 左：文档大纲
- 中：编辑器（含应标要求/重点/否决/加分四项提炼卡）
- 右：context-pane，已有"匹配素材"面板骨架，但只展示 source 文件列表

需求：章节提炼完成后，对原始素材进行匹配分析，在右侧面板展示该章节匹配到的原始素材文件、以及素材中对应模块的内容。

## 设计目标

1. 用户选中任意章节，右侧立即看到该章节匹配的 Top-3 原始素材片段
2. 提炼过程中实时累积匹配数据，零额外网络请求
3. 支持上传新素材后手动刷新匹配
4. 保留现有"+ 添加原始素材"入口和素材管理能力

## 方案选型

### 触发时机：嵌入 outline SSE 流（推荐）

**对比：**

| 方案 | 首章节延迟 | 网络成本 | 实现复杂度 |
|------|------------|----------|------------|
| 全部完成后 map-sources | 1-2 分钟（等全部） | 1 次额外请求 | 已有 |
| 选中章节按需匹配 | 200ms RTT | N 次（每次切换） | 新端点 |
| **outline 时随流推送** | **0ms** | **0 次额外** | **改 SSE payload** |

匹配是关键词交集打分，纯内存 <5ms，没必要拆成独立请求。每个章节提炼完毕的瞬间在后端顺手匹配并随 SSE 一起推送，前端缓存即可。

### 后端改动

**`backend/routes/materials.py` `generate_outline`**

每完成一个 block，多做两步：
1. 调 `retrieve_chunks(all_chunks, query=title+requirement, top_k=3)`
2. 在 `outline_block` 事件 payload 增加 `matched_sources`：
   ```json
   {
     "block_id": "outline-0",
     "title": "...",
     "requirement": "...",
     "key_points": "...",
     "matched_sources": [
       { "material_id": 5, "filename": "技术方案.docx",
         "chunk_index": 12, "content": "完整段落内容...",
         "score": 4 }
     ]
   }
   ```
3. matched_sources 同步序列化写入 `blocks.source`，作为后续刷新的兜底

**`backend/routes/blocks.py` 新增 `GET /api/blocks/{id}/matched-sources`**

返回该 block 实时计算的 Top-3 chunks。供前端"刷新匹配"按钮调用，应对用户提炼完后再上传素材的场景。

### 前端改动

**`workbench.html` 右侧 context-pane 重构：**

```
┌──────────────────────────────┐
│ 匹配素材   [本章 Tab][全部]   │  ← 头部 Tab 切换
├──────────────────────────────┤
│ Tab=本章 ──────────────────  │
│  按当前章节自动匹配  ⟳刷新    │
│ ┌────────────────────────┐   │
│ │ 📄 技术方案.docx · #12  │   │
│ │ 项目实施分三阶段...      │   │
│ │ [展开▾] [+ 引用]         │   │
│ └────────────────────────┘   │
│ ┌────────────────────────┐   │
│ │ 📄 公司资质.pdf · #03   │   │
│ │ 我司持有 CMMI L5 认证... │   │
│ └────────────────────────┘   │
│                              │
│ Tab=全部 ──────────────────  │
│  [现有文件列表 + 上传按钮]    │
└──────────────────────────────┘
```

**卡片信息层级：**
- 顶部：文件类型徽标 + 文件名 + chunk 序号
- 主体：内容摘要前 100 字（点击展开看完整段落）
- 底部：[+ 引用] 按钮（M2 阶段实现）

**逻辑：**
- 全局 `Map<blockId, matchedSources[]>` 缓存（从 outline_block 事件累积）
- `__onBlockSelected(b)` 时读取缓存渲染
- 缓存为空时，从 `b.source`（已存 DB）反序列化兜底
- 刷新按钮 → 请求新端点 → 更新缓存 → 重渲染

## 数据契约

### outline_block 事件（变更）

```typescript
{
  block_id: string,
  title: string,
  requirement: string,
  key_points: string,
  veto_items: string,
  bonus_items: string,
  order_idx: number,
  matched_sources: Array<{
    material_id: number,
    filename: string,
    chunk_index: number,
    content: string,    // 完整段落，前端自行截断
    score: number,
  }>
}
```

### blocks.source 字段（变更）

之前：`[{material_id, chunk_index, snippet}]`
之后：`[{material_id, filename, chunk_index, content, score}]`

向后兼容：旧数据 snippet 字段 fallback 到 content。

## 边界情况

1. **无 source 素材** → matched_sources 返回 `[]`，前端展示"暂无原始素材，请先上传"
2. **打分全为 0** → 仍返回 Top-3，但前端可显示"无强相关素材"提示
3. **chunks 数量 < 3** → 返回实际数量
4. **后期追加素材** → 用户点刷新，按需请求

## 不在此范围

- 引用按钮的具体行为（M2）
- chunk 高亮跳转到原文（M2）
- LLM 语义匹配（当前关键词足够，待 chunks 量级超过 100 时再升级）
