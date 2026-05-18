# Demo + 生产端融合设计

**日期**：2026-05-18
**项目**：ge-solution（技术方案生成助手 → AiBidding 投标工作台）

---

## 背景与目标

当前生产端是**文档扩写引擎**：上传规范书 → AI 按章节扩写 → 下载 Word，单文档单次流程，无项目管理概念。

Demo（AiBidding）是**投标方案工作台**：多项目 Vault 管理、三阶段流程（项目信息 → 材料撰写 → 审核导出）、Block 级编辑器、评分上下文挂载、Revision 链与 Diff 审核。

**融合目标**：把生产端的 AI 生成引擎能力，完整嵌入 Demo 的全流程工作台中，三阶段一次性全部接通。

---

## 核心决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 融合范围 | 三阶段全部 | 一步到位，避免二次重构 |
| 存储方案 | SQLite 元数据 + 文件系统材料 | 零运维、职责分离、部署简单 |
| 后端架构 | 领域路由重构，Services 层保留 | 路由按业务域对齐，核心逻辑不动 |
| 前端策略 | Demo HTML 直接移入 frontend/，新增 api.js | 样式结构不变，只接入真实 API |

---

## 整体架构

### 后端结构

```
backend/
├── main.py
├── db.py                    ← 新：SQLite 初始化与连接
├── routes/
│   ├── projects.py          ← 新：Vault CRUD
│   ├── materials.py         ← 新：材料上传与解析
│   ├── blocks.py            ← 新：Block CRUD + AI 生成
│   ├── revisions.py         ← 新：版本历史与回溯
│   ├── export.py            ← 改自 download.py
│   └── config.py            ← 保留
├── services/
│   ├── parser.py            ← 保留（TOC 解析）
│   ├── llm.py               ← 保留（LLM 调用）
│   ├── docx_generator.py    ← 保留（Word 生成）
│   ├── config_store.py      ← 保留（API Key 管理）
│   └── block_store.py       ← 新：SQLite CRUD 封装
└── data/
    ├── app.db               ← 新：SQLite 数据库
    └── uploads/             ← 新：材料原始文件
```

### 前端结构

```
frontend/
├── index.html               ← 改为重定向到 projects.html
├── projects.html            ← Demo Vault Gallery
├── project-init.html        ← Demo 初始化页
├── workbench.html           ← Demo 材料撰写工作台
├── diff-review.html         ← Demo 审核导出
└── assets/
    ├── styles.css           ← Demo 样式（不动）
    ├── workbench.js         ← Demo JS（改数据绑定）
    └── api.js               ← 新：统一 API 封装层
```

### SQLite Schema

```sql
-- 项目 Vault
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    status      TEXT DEFAULT 'init',  -- init/authoring/reviewing/done
    deadline    TEXT,
    summary     TEXT,                 -- AI 生成的全局摘要
    base_snapshot_id INTEGER,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 导入材料
CREATE TABLE materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    filename    TEXT,
    type        TEXT,                 -- docx/pdf/xlsx 等
    file_path   TEXT,                 -- data/uploads/ 下的路径
    role        TEXT,                 -- main/spec/score/reference
    parsed_at   DATETIME
);

-- Block 树（大纲节点 + 内容块）
CREATE TABLE blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    block_id    TEXT,                 -- H-001, B-001 等 Demo 原始 ID
    kind        TEXT,                 -- heading / content
    level       INTEGER,
    title       TEXT,
    content     TEXT,
    domain      TEXT,                 -- 所属标题域（用于影响传播）
    parent_title TEXT,
    requirement TEXT,                 -- 评分要求
    score       TEXT,                 -- 分值说明
    source      TEXT,                 -- 来源文件条款
    order_idx   INTEGER,
    status      TEXT DEFAULT 'empty', -- empty/generating/done/needs_review
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Block 版本历史
CREATE TABLE block_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id    INTEGER REFERENCES blocks(id),
    revision_no INTEGER,
    content     TEXT,
    summary     TEXT,                 -- "AI 初稿" / "用户编辑" / "AI 润色" / "回溯至 rX"
    source      TEXT,                 -- generate/edit/polish/restore
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 项目快照（用于 diff 基线）
CREATE TABLE project_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    trigger     TEXT,                 -- lock_outline / apply_diff
    snapshot    TEXT,                 -- blocks 全量 JSON
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## API 路由设计

### projects.py

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/projects | 列表，支持 status 筛选 |
| POST | /api/projects | 创建 Vault |
| GET | /api/projects/{id} | 详情 |
| PATCH | /api/projects/{id} | 更新名称/截止日/状态 |
| POST | /api/projects/{id}/lock | 锁定大纲，生成快照 |

### materials.py

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/projects/{id}/materials | 上传材料文件，触发解析 |
| GET | /api/projects/{id}/materials | 材料列表与解析状态 |
| POST | /api/projects/{id}/outline | SSE：AI 生成大纲并写入 blocks 表 |

### blocks.py

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/projects/{id}/blocks | Block 树（含 requirement/score/source） |
| PUT | /api/blocks/{id} | 更新内容（自动保存，触发 revision） |
| POST | /api/blocks/{id}/generate | SSE：单块 AI 生成 |
| POST | /api/blocks/{id}/ai | AI 功能：润色/补充/风格/查漏/检索要求 |

### revisions.py

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/blocks/{id}/revisions | 版本历史列表 |
| POST | /api/blocks/{id}/revisions/{rn}/restore | 回溯到指定版本 |

### export.py

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/projects/{id}/diff | 计算 base snapshot vs current 的差异 |
| POST | /api/projects/{id}/diff/apply | 接受 diff 映射，合并写入 blocks |
| GET | /api/projects/{id}/export | 导出 Word（复用 docx_generator.py） |

---

## Block 级生成流程

```
用户点击「生成」
    ↓
POST /api/blocks/{id}/generate
    ↓
读取 block 元数据（requirement / score / source / domain）
    + projects.summary（全局上下文）
    ↓
构建 prompt（复用 llm.py prompt 模板，注入评分上下文）
    ↓
SSE 流式推送 → 前端 workbench.js 写入对应 Block DOM
    ↓
流结束 → 写入 blocks.content，插入 block_revisions（revision_no+1，summary="AI 初稿"）
    ↓
前端：block status → done，字数更新，历史列表 +1
```

**AI 功能面板**（POST /api/blocks/{id}/ai）通过 `action` 参数区分：
- `polish`：润色，保留结构改善表达
- `expand`：补充，基于 requirement 检查并补齐缺失内容
- `check`：查漏，对照评分项列出未覆盖要求
- `search`：检索要求，从已解析材料中找隐含要求
- `style`：风格调整

AI 建议先以 pending 状态返回，用户接受后才写入 blocks 并插 revision。

---

## Revision 链与 Diff 审核

### Block 级版本

- `blocks` 表只存当前最新 content
- 每次写入触发 `block_revisions` 插入（generate/edit/ai accept/restore）
- 回溯 = 把 `block_revisions` 的旧 content 写回 `blocks`，同时插一条 `summary="回溯至 rX"` 的新 revision

### Diff 审核流程

1. 上传新版材料（`?revision=true`）→ 解析生成 draft block 树，不覆盖现有 blocks
2. GET /api/projects/{id}/diff → 对比 draft vs current，返回 added/changed/removed
3. 影响传播：changed block 的 `domain` 找同域子块，标记 `status=needs_review`
4. POST /api/projects/{id}/diff/apply → 合并 draft 到正式 blocks，生成新 snapshot

### 项目级快照时机

- 锁定大纲时（`POST /api/projects/{id}/lock`）
- 接受 diff 映射时（`POST /api/projects/{id}/diff/apply`）

不做连续快照，控制存储量。

---

## 前端集成策略

### api.js（新增）

统一封装所有 fetch 调用，HTML 文件只改数据绑定，样式和结构完全不动：

```js
// 示例
export const api = {
  projects: {
    list: () => fetch('/api/projects').then(r => r.json()),
    create: (data) => fetch('/api/projects', { method: 'POST', body: JSON.stringify(data) }).then(r => r.json()),
  },
  blocks: {
    generate: (id) => new EventSource(`/api/blocks/${id}/generate`),
    update: (id, content) => fetch(`/api/blocks/${id}`, { method: 'PUT', body: JSON.stringify({ content }) }),
  },
  // ...
}
```

### 各页面改动范围

| 页面 | 结构/样式 | JS 改动 |
|------|-----------|---------|
| projects.html | 不动 | 页面加载时 GET /api/projects 渲染卡片 |
| project-init.html | 不动 | 文件上传、大纲生成、确认项接入 API |
| workbench.html | 不动 | Block 加载、SSE 生成、自动保存接入 API |
| diff-review.html | 不动 | diff 数据加载、接受映射、导出接入 API |

### 兼容过渡

- 旧 upload / generate / download 路由暂时保留，等前端全部迁移后删除
- demo/ 目录在集成完成后整体删除（HTML 已移入 frontend/）

---

## 关键复用点（不修改）

| 现有文件 | 复用方式 |
|---------|---------|
| `services/parser.py` | 材料解析、TOC 提取，由 materials.py 路由调用 |
| `services/llm.py` | Block 生成、AI 功能、摘要生成，prompt 模板扩展 |
| `services/docx_generator.py` | 导出时组装 blocks 数据传入，接口不变 |
| `services/config_store.py` | API Key 管理，完全不变 |
| `utils/sse.py` | SSE 事件格式化，复用于单块生成 |

---

## 不在本期范围内

- 多用户 / 权限管理
- 评论与协作
- 云存储（OSS/S3）
- 移动端适配
