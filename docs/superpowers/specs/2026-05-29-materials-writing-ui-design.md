# 材料撰写页面 UI 改造设计

_Created 2026-05-29_

## 背景

当前 `workbench.html` 的中间区只展示 block 详情（应标要求/重点/否决/加分四个模块），没有编辑器。设计稿 `材料撰写.html` 引入了完整的撰写工作台，需要将其移植到现有项目中。

## 实施方式

**方案 1：直接改造 workbench.html**（已确认）

- 在现有文件上修改，不新增独立文件
- 保留现有所有 API 调用、outline 树形渲染、generate-session 逻辑
- 编辑器新增样式写入页内 `<style>`，编辑器交互逻辑写成内联 `<script>` 挂在 workbench.js 之后

---

## 布局结构

三栏不变：**左侧大纲（264px）+ 中间编辑工作台（flex:1）+ 右侧匹配素材（280px）**

### 左侧：文档大纲树

现有实现保持不变，补充以下调整：
- 大纲头部加进度计数徽章 `3 / 10`
- 底部加撰写进度条（从 blockList 统计已有内容的 block 数）

### 中间：撰写工作台

自上而下三段：

**① 章节标题头（flex:none）**
- 面包屑（`data-breadcrumb`，现有实现）+ 章节大标题
- 右侧：标记完成按钮 + 批量编写主按钮

**② 提炼参考区（flex:none，默认折叠）**
- 一行小标题 `提炼参考 ── 点击展开`
- 4 列横排折叠卡片：应标要求（蓝）/ 应标重点（琥珀）/ 否决项（红）/ 加分项（绿）
- 每张卡点击展开，展开高度 max 160px + 内滚动
- 数据来源：`block.requirement`、`block.score`（JSON 解析 key_points / hard_constraints / special_marks）

**③ 正文编辑器（flex:1，滚动）**

编辑器卡片结构：
```
┌─ 编辑器卡 ──────────────────────────────────────────┐
│ 头部：[04] 撰写正文  [基于四项提炼自动生成]  [版本] [保存时间] │
│ 生成Banner（仅生成中显示）：spinner + 阶段 + 进度条 + 停止    │
│ 工具栏：B/I/U · H1/H2/列表 · [目标字数输入] · 文风分段 · AI生成│
│ 编辑区（contenteditable，flex:1）                          │
│ 底栏：字数/限制 · 模型 · 润色/扩写/缩写 · 保存草稿             │
└──────────────────────────────────────────────────────┘
```

**编辑器三种状态：**

| 状态 | 触发 | 表现 |
|------|------|------|
| 空白 | 未生成内容 | 斜体 placeholder + 右上角浮动 AI 提示卡 |
| 生成中 | 点击 AI 生成 | 顶部蓝色 Banner（spinner + 阶段 + 进度条 + 停止）+ 内容实时流式输出 |
| 已生成 | 生成完成或手动输入 | 正常 contenteditable 富文本 |

**目标字数控件：**
- `<input type="number">` 内嵌在胶囊样式容器中
- 实时显示当前字数；超限时字数变红

**文风切换：**三段式分段按钮：公文 / 技术 / 精简

**AI 生成按钮：** accent 蓝色主按钮，带 sparkle 图标

### 右侧：匹配素材面板

替换现有 `context-pane` 中的"匹配素材"部分：
- 面板头：`匹配素材` + 数量徽章 + 刷新按钮
- 副标题：`按当前章节的要求 / 重点自动匹配`
- 素材卡列表（mock 数据，4条）：
  - 文件类型徽章（PDF/DOCX/PPTX，各有专属配色）
  - 文件名（溢出省略）
  - 匹配度进度条 + 百分比（≥85% 绿，≥70% 琥珀，其余灰）
  - 摘要片段
  - 查看 / + 引用 按钮

> 注：匹配度数据暂用 mock，后续对接后端真实数据。

---

## 数据流

```
后端 API
  └─ api.blocks.list(projectId)
       └─ block { id, title, level, content, requirement, score, source }
            ├─ score (JSON): { key_points[], hard_constraints[], special_marks[] }
            ├─ requirement → 应标要求卡
            └─ source (JSON array): [{ material_id, chunk_index, snippet }]
                  └─ 右侧匹配素材（当前 mock，score/match 字段待后端支持）
```

---

## 保留的现有逻辑

| 模块 | 保留 | 改动 |
|------|------|------|
| `workbench.js` outline 树 | ✅ 完整保留 | 无 |
| `workbench.js` block 详情渲染 | ✅ 保留逻辑 | 改为渲染到折叠卡片 |
| `generate-session.js` 批量生成 | ✅ 完整保留 | 无 |
| `context-panel.js` requirement 渲染 | ⚠️ 部分保留 | 匹配素材改为新样式 |
| `block:token` 流式输出事件 | ✅ 保留 | 流入 contenteditable 编辑区 |

---

## 不在本次范围内

- 编辑器富文本格式化（B/I/U 工具栏按钮只做样式，不接 execCommand）
- 保存草稿到后端（contenteditable blur 已有，不改）
- 匹配素材真实数据对接
- 右侧面板生成状态卡（gen-status-card）保留现有实现不改
