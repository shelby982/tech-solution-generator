# 生图接入 Diagram Design 技能 —— 产品与技术方案

> **P0/P1/P2 已实施**：见 [实施记录](生图-DiagramDesign技能接入-实施记录.md)（单入口、校验、Playwright PNG/Word 导出、prompt 缓存与精简、质量档批量、SVG、几何/要素校验）。

> 目标：把 [cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design)（MIT，45 种图型，输出单文件 HTML + SVG）包装进产品「插入内容 → 图片」的生图链路，生成能直接进标书正文、能导出 Word 的高质量图示。
>
> 已核对的仓库版本：HEAD `0cf73c3`，skill 版本 `2.6.136`；技能许可证 MIT。
>
> 一句话结论：**不直接替换现有文本图示，新增一条「设计版图表」资产链路**——LLM 按技能规范生成 HTML/SVG → 校验 → Chromium 渲染 PNG → 作为图片插入正文/导出；现有 `:::diagram` 快速结构图保留，批量编写默认走快图，设计版按需开启。

---

## 一、结论先行（8 条）

1. **定位**：设计版图表是**高质量图片资产**（PNG 进正文，HTML 留源），不是可编辑的文本图；现有 `:::diagram` 快速结构图继续保留。
2. **技能包装方式**：把 skill 的 `SKILL.md + references + templates + 校验脚本` 作为**受控提示词与校验资产** vendor 进后端，由专用「图示设计 Agent」调用，不在产品里跑外部 Agent。
3. **生成链路**：节计划/素材证据 → 图型选择 → 品牌样式约束 → 单文件 HTML/SVG → `self_check` + 产品校验 → PNG/SVG → 预览/插入。
4. **渲染**：Playwright + Chromium；PNG @2～@3；Word 导出只用 PNG，矢量 SVG 可选。
5. **插入方式**：`:::figure` + 新图片端点 `/api/studio/diagrams/{id}/image`；扩展 Word 导出的图片白名单与快照。
6. **人工入口**：把截图里的「AI 生成信息图 / AI 生成架构图」**合并为一个「设计版」入口**（架构图作为图型选项，不再单列），增加图型/尺寸/内容来源选择 + 预览 + 重新生成；「快速结构图」作为次级选项。
7. **自动入口**：章节批量编写默认仍用快图，避免每张图 30–90 秒 + 高 token；项目设置可对架构/分层图开启「设计版」。
8. **合规**：保留 MIT 版权声明、pin commit、不自动升级；HTML 沙箱预览、禁用脚本/外网、素材内容按不可信数据处理。

---

## 二、为什么不是「直接把技能塞进现有 `/studio/diagram`」

| | 现有快速结构图 | Diagram Design |
|---|---|---|
| 输入 | 节点 JSON（flow/layered/timeline） | 技能提示词 + 内容 brief + 品牌样式 |
| 输出 | `:::diagram` 文本 + Pillow PNG | 单文件 HTML + inline SVG + PNG/SVG |
| 生成耗时 | 1–5s | 30–90s（LLM 长输出 + 浏览器渲染） |
| 质量 | 稳定、简单、可编辑 | 设计感强、图型多、可做架构/矩阵/组织图 |
| 可编辑性 | 文本可 diff/改 | 图片；源 HTML 可重生成，不适合逐字编辑 |
| 校验 | 节点数/来源 | 技能 self_check + 产品内容/安全校验 |
| 成本 | 低 | 高（提示词长、输出大、渲染重） |
| 导出 | 现有 Pillow PNG | Playwright PNG，质量高 |

**结论**：两条链路并存。设计版解决"这张图要好看、要像专业标书配图"；快图解决"自动批量、简单结构、可编辑"。

---

## 三、产品形态与入口

### 3.1 插入内容 → 图片

```
图片
├─ AI 生成图表（设计版）      ← 唯一技能入口
│    · 图型：推荐（默认架构图）/ 手动选 10 种
│    · 内容来源：本节要点 / 选中文字 / 素材证据
│    · 尺寸：文档内（推荐）/ 整页宽 / 打印
│    · [生成预览]
├─ 快速结构图               ← 现有 flow/layered/timeline，可编辑
└─ 从素材插入图片            ← 现有素材图
```

**不单列「AI 生成架构图」**：设计版链路只有**一个入口**，架构图是图型选择里的一个选项（且根据本节内容默认推荐）。
原来的「AI 生成信息图 / AI 生成架构图」是两个不同算法（自动抽结构 vs 强制分层），现在都走同一个技能，只是 `type` 参数不同——再拆成两个菜单项会重复状态、重复请求、让用户以为生成结果会有本质差别。

### 3.2 生成后的预览弹窗

```
┌ 图表预览 ─────────────────────────────────────── × ┐
│ [PNG 预览 / HTML 预览]  图型：Architecture · 文档宽  │
│                                                    │
│              （渲染后的图）                         │
│                                                    │
│ 检查：✓ 单文件  ✓ 无脚本  ✓ 无外网  ✓ 9 节点内      │
│ 内容：✓ 节点来自本节要点  ⚠ 1 处数字待核对           │
│                                                    │
│ [换图型] [重新生成] [查看源]      [取消] [插入正文] │
└────────────────────────────────────────────────────┘
```

- 插入后正文里是一张图片（`:::figure`），带图名；点击图可打开同一弹窗「重新设计 / 查看源 / 替换」。
- 不直接改正文文字；如需文本可编辑图，走「快速结构图」。
- 生成中可离开页面（后台 job），完成后通知/在素材库可见。

### 3.3 自动编写中的图

- 默认：`deliverables.kind=figure` 仍走现有 `_generate_chart` 快图（2–5s/张）。
- 项目设置 `diagram_quality`：
  - `fast`（默认）：全部快图；
  - `design_for_architecture`：仅图型为 architecture/layers/high-level 时走设计版；
  - `design_all`：全部走设计版（明确提示耗时与费用）。
- 设计版失败 → 自动回退快图，并在待办标"设计图生成失败，已用结构图代替"。

---

## 四、只开放适合标书的 10 种图型

| 业务场景 | 技能图型 | 备注 |
|---|---|---|
| 总体/技术/集成架构 | `architecture` | 首选；含 containment 变体 |
| 分层架构 | `layers` | 对应现有 layered |
| 流程/实施步骤 | `process` / `flowchart` | 流程分叉用 flowchart |
| 里程碑/计划 | `timeline` / `gantt` | 日期必须有依据 |
| 目标/能力分解 | `tree` / `pyramid` | 目标分解、层级 |
| 对比/优先级 | `quadrant` | 两轴矩阵；轴定义要写清 |
| 闭环/飞轮 | `loop` | 机制闭环 |
| 组织/职责 | `org-chart` | 项目组织、分工 |
| 数据流 | `data-flow` | 数据流向、角色 |
| 现状→目标 | `architecture-delta` | 两态对比，需两态都有依据 |

**V1 不开放**：sketchy、terminal、axonometric、exploded、social、story-map、UML、DB schema、甘特之外的数据图表等。理由：标书风格不匹配、数据依据不足、图型预算复杂。

映射：

```
节计划 type=layered → design_type=layers（或 architecture）
节计划 type=flow    → design_type=process（分叉多则 flowchart）
节计划 type=timeline→ design_type=timeline
未指定 → 小型分类器/关键词判断；UI 显示推荐，可改
```

---

## 五、技能包如何包装进后端

### 5.1 Vendor 目录（pin 版本）

```
backend/skills/diagram_design/
├─ VERSION                      # upstream commit 0cf73c3, skill 2.6.136
├─ LICENSE                      # MIT 原文
├─ NOTICE                       # 来源、版权、修改说明
├─ SKILL.md
├─ references/
│   ├─ style-guide.product.md   # 产品品牌版（非上游默认）
│   ├─ output-spec.md
│   ├─ layout-budget.md
│   ├─ primitives-core.md
│   ├─ semantic-patterns.md     # 需要时
│   └─ type-{architecture,layers,process,flowchart,timeline,
│            tree,pyramid,quadrant,loop,org-chart,data-flow,
│            architecture-delta}.md
├─ assets/
│   ├─ template.html / template-full.html
│   └─ template-motion.html（不启用，仅保留给校验）
└─ scripts/
    ├─ self_check.py            # 无第三方依赖，生成后必跑
    ├─ export_svg.py
    └─ export_png.py            # Playwright
```

- 不 vendor 45 个 example HTML（5.7MB 大头），只保留模板 + 需要的引用；同步脚本按 allowlist 更新。
- 不把 skill 装到用户机器/运行时 npm；后端只读文件。
- 版本升级：人工跑 `scripts/sync_diagram_skill.py`，更新 VERSION + NOTICE，跑回归测试；不自动跟随上游。

### 5.2 提示词组装（Diagram Agent）

```
System:
  角色：标书图示设计师；只输出一个自包含 HTML 文件
  + SKILL.md（设计原则、选择、反模式、输出契约）
  + 选中图型的 type-*.md（只加载一个）
  + style-guide.product.md（品牌/中文字体/打印）
  + output-spec.md（尺寸/type ramp）
  + layout-budget.md（节点/箭头预算）
  + primitives-core.md（连接线/箭头/节点规则）

User:
  JSON brief：
  {
    "title": "总体技术架构图",
    "section": "总体技术方案",
    "type": "architecture",
    "size": "doc-wide",
    "elements": ["接入层", "服务层", "数据层"],
    "evidence": [{"label": "...", "source": "..."}],
    "caption": "...",
    "constraints": ["只用给定事实", "不编数字", "中文标签", "无动画/脚本/外网"]
  }
```

硬约束（写进 system，生成后校验）：

- 只输出 HTML，不要解释、不要 Markdown 围栏；
- 单文件；内联 SVG；**无 script / 无 iframe / 无外链**（字体走 system）；
- 中文标题与节点；节点 ≤9（技能预算）；
- 禁止新增 brief/evidence 之外的数字、名称、承诺；
- 图型预算按 type reference；超预算就拆图；
- 不输出空图、占位图、Mermaid。

### 5.3 模型与调用

- 角色：复用 `diagram` 模型设置；设计版默认建议 `pro`（质量优先），可在编写设置里改回 flash。
- 调用：新增 `generate_diagram_html()`：
  - `max_tokens=16000`、`timeout=240s`、`temperature=0.3`；
  - 复用 `config_store` 的模型解析、`run_context` 记账；
  - 输入超长时只截取 brief/evidence，不截断技能规则。
- 输出长度实测：技能示例 HTML 约 2–4k tokens，16k 上限足够；若 provider 输出上限更低，只生成 `<svg>` 主体 + 产品固定 HTML 壳（见风险）。
- 修复：校验失败时把错误 + 原 HTML 回给模型"只修这些问题"，最多 1 次；再失败回退快图。

### 5.4 品牌样式（标书专用 profile）

`style-guide.product.md` 从上游完整复制后改 token，不修改图型规则：

| Token | 建议值 | 说明 |
|---|---|---|
| paper | `#ffffff` | 白底，打印/Word 干净 |
| paper-2 | `#f7f8fa` | 分区/卡片 |
| ink | `#1f2937` | 主文字/描边 |
| muted | `#4b5563` | 次级文字/箭头 |
| soft | `#6b7280` | 边界标签 |
| accent | 项目品牌色（默认 `#1d4ed8`） | 仅 1–2 个焦点 |
| link | `#2563eb` | 接口/外部调用 |
| 标题字体 | `'Noto Serif SC', 'Songti SC', serif` | 正式感 |
| 节点字体 | `'Noto Sans SC', 'PingFang SC', sans-serif` | 中文可读 |
| 等宽 | `'Geist Mono', ui-monospace` | 只用于英文/编号/端口 |
| Font source | `system` | 不请求 Google Fonts；离线/内网可用 |
| 默认尺寸 | `doc-wide`（1280×720） | 正文整宽 |
| 打印尺寸 | `print-a4-landscape` @3 | 导出高清 |

- 技能要求"首次生成必须自定义 style guide"；产品直接使用固定 profile，跳过交互 gate，提示词中声明"已品牌化"。
- v1 一个「正式投标」profile；后续做项目品牌 profile（颜色/字体/logo）。

---

## 六、生成、校验、渲染链路

### 6.1 流程

```
POST /api/blocks/{id}/studio/diagram-assets  (202, job)
  → 1. 组装 brief（图型/元素/证据/尺寸/品牌）
  → 2. Diagram Agent → HTML（30–90s）
  → 3. 校验：
       a. 技能 self_check.py --offline
       b. 产品安全/结构校验
       c. 内容溯源校验
  → 4. 失败 → 修复一次 → 仍失败 → 回退快图/报错
  → 5. Playwright 渲染 PNG（@2/@3）+ 可选 SVG
  → 6. 落库 + 文件；job done
  → 7. 前端预览 → 插入正文
```

### 6.2 校验规则

**技能自带（必跑）**

- `python3 scripts/self_check.py --offline <html>`：单文件安全、无远程资源、可访问 SVG 契约、文档完整性、内容在 viewBox 内、motion 结构。
- 可选 `verify-geometry.py`（repo 脚本）：连接线/标签遮挡；v1 先不引入，避免依赖。

**产品补充（v1 必做）**

| 检查 | 规则 |
|---|---|
| 文件形态 | 单个 `.html`，含一个顶层 `<svg>`，有 `viewBox` |
| 安全 | 无 `<script>` / `<iframe>` / `<object>` / `on*=`；无 `http(s)://` 外链；无 `<img src>` 远程图 |
| 可访问 | `role="img"` + `<title>`/`<desc>` 且非空 |
| 尺寸 | viewBox 与所选 size preset 一致 |
| 图型预算 | 节点/箭头不超过所选 type reference 上限（V1 统一 ≤9 节点） |
| 中文 | 标题/节点有中文；无字体缺字替代（渲染后检查） |
| 内容 | 节点核心词能在 brief/evidence 找到；数字/日期必须在 evidence 出现；不出现 brief 外的新事实 |
| 空白/截断 | PNG 非空白；边缘内容不越界（self_check 已覆盖大部分） |

失败处理：修复一次；仍失败 → 不插入，回退快图（若用户选"允许回退"），否则报错并保留 HTML 供排查。

### 6.3 渲染

- **Playwright + Chromium**（技能导出脚本同款）：`page.goto(file://...?motion=static)`，断网，`wait_until=domcontentloaded` + `document.fonts.ready`，截 SVG。
- `device_scale_factor`：正文图 2；打印图 3。
- 输出：PNG + 可选 SVG；都存项目目录。
- 运行方式：`asyncio.to_thread` + 浏览器单例 + 并发 1–2；超时 30s；失败重试 1 次。
- 部署：Docker 安装 `playwright` + `playwright install chromium` + Noto CJK 字体；镜像增大约 300MB，可接受；本地开发同样需要。
- PNG 尺寸上限：单图 ≤ 8MB；超限降 scale 或提示。

### 6.4 插入与导出

- 插入 markdown：
  ```
  :::figure 总体技术架构图
  ![总体技术架构图](/api/studio/diagrams/{asset_id}/image)
  :::
  ```
- 后端新增 `GET /api/studio/diagrams/{asset_id}/image`，校验项目归属，返回 PNG。
- 修改 Word 导出白名单：
  - `infra/docx/document_model.py` 的 `image_urls`、`model_html`、`render_blocks` 正则增加 `/api/studio/diagrams/\d+/image`；
  - 导出快照抓图逻辑：按 URL 类型调用素材或 diagram 图片接口；
  - 现有 `:::diagram` 文本图不动。
- 前端 `parseFigures` 识别 diagram 图片（`kind='design'`），显示"设计图"；图片审阅可打开源 HTML。
- 快照/导出失败时，回退到该图的 PNG 缓存或提示重新生成，不静默丢图。

### 6.5 数据模型

```sql
CREATE TABLE diagram_assets (
  id            TEXT PRIMARY KEY,
  project_id    INTEGER NOT NULL,
  block_id      INTEGER,
  section       TEXT DEFAULT '',
  title         TEXT NOT NULL,
  skill_type    TEXT NOT NULL,
  size_preset   TEXT NOT NULL,
  html          TEXT NOT NULL,
  svg           TEXT DEFAULT '',
  png_path      TEXT NOT NULL,
  png_width     INTEGER,
  png_height    INTEGER,
  brief_json    TEXT NOT NULL,
  checks_json   TEXT DEFAULT '{}',
  model         TEXT DEFAULT '',
  prompt_hash   TEXT DEFAULT '',
  version       INTEGER DEFAULT 1,
  status        TEXT DEFAULT 'done',     -- generating|done|failed
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  deleted_at    TEXT
);

CREATE TABLE diagram_jobs (
  id          TEXT PRIMARY KEY,
  project_id  INTEGER,
  block_id    INTEGER,
  asset_id    TEXT,
  status      TEXT,                     -- queued|running|done|failed|cancelled
  stage       TEXT,
  error       TEXT,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
```

- 文件：`backend/data/diagrams/{project_id}/{asset_id}.html/.png/.svg`；删除项目时清理。
- 缓存：`prompt_hash` 相同可复用（图型/品牌/内容一致时）；重新生成创建新 version。
- 一个图可被多章引用？v1 绑定 block；跨章复用后续做。

### 6.6 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/blocks/{id}/studio/diagram-assets` | 建 job（brief）→ 202 `{job_id}` |
| GET | `/api/blocks/{id}/studio/diagram-assets/{job_id}` | 状态/进度/错误 |
| GET | `/api/studio/diagrams/{asset_id}/preview` | HTML（CSP sandbox，无脚本） |
| GET | `/api/studio/diagrams/{asset_id}/image` | PNG |
| POST | `/api/studio/diagrams/{asset_id}/regenerate` | 新 version（可改图型/尺寸） |
| DELETE | `/api/studio/diagrams/{asset_id}` | 软删，正文引用提示失效 |

---

## 七、前端交互与状态

### 7.1 入口

- 「AI 生成图表（设计版）」打开配置面板：
  - 图型：AI 推荐 / 手动 10 种；**默认推荐 architecture**，这就是原来的"架构图"入口，不再单列；
  - 尺寸：文档内（1280×720，推荐）/ 整页宽 / 打印 A4；
  - 内容来源：本节要点 / 当前选中文字 / 本章证据；
  - [生成预览]。
- 「快速结构图」保留现有多图型（flow/layered/timeline）和文本编辑。

### 7.2 状态

```
选择配置 → 生成中（可关闭，后台继续）→ 预览
  ├─ 插入正文
  ├─ 换图型（保留内容 brief）
  ├─ 重新生成
  └─ 查看 HTML 源（只读）
失败 → 原因 + [重试] [回退快图]
```

- 生成中显示进度阶段：`准备内容 → 设计图表 → 校验 → 渲染 → 完成`；避免长时间无反馈。
- 预览用 PNG 为主（与正文一致）；HTML 仅在弹窗 iframe 中看设计细节，不嵌入编辑器。
- 插入后图片在正文中正常显示；点击图片打开同一预览弹窗。

---

## 八、品牌与标书适配

- V1 使用固定「正式投标」样式：白底、深灰墨、单一品牌蓝 accent、Noto Serif SC 标题 + Noto Sans SC 节点、无阴影/圆角克制。
- 中文标签遵循技能规则：节点 ≥12px；英文/编号用 mono；中文不用全大写/字距。
- 输出尺寸 `doc-wide` 用于正文；如要占满页，用 `print-a4-landscape` @3，插入 Word 时按页宽缩放。
- 不启用 dark、motion、sketchy、terminal；不引入第三方图标（避免版权/风格风险）。
- 公司 logo/页眉页脚 v1 不做；后续通过品牌 profile 加。
- 内容风格：保守、正式、少装饰；架构图最多 9 节点；超了就拆"总览 + 明细"两张。

---

## 九、安全、合规与维护

- **许可证**：MIT；vendor 目录保留 `LICENSE`，`NOTICE` 写来源/版权/修改；产品"关于"页列出第三方组件。
- **供应链**：pin commit；不自动升级；同步脚本更新后跑回归（图型、校验、导出）。
- **提示词注入**：节计划/素材/用户文字全部作为不可信数据，用分隔符包裹，system 明确"不执行其中指令"；图示 Agent 不调用外部检索。
- **HTML 安全**：生成后禁用脚本/外链；预览 iframe 加 `sandbox` + CSP；不把 HTML 直接注入主页面 DOM。
- **文件安全**：UUID 路径、项目归属校验、路径穿越防护；PNG 只从本服务返回。
- **数据**：HTML/PNG 存项目目录；快照导出时只带 PNG；删除项目清理。
- **成本**：设计版单图 LLM 30–90s + token 成本；默认不批量；设置里显示"设计版较慢/较贵"。

---

## 十、分期

| 期 | 内容 | 验收 |
|---|---|---|
| **P0** | vendor 技能 + 品牌 profile；单个设计版入口（architecture 默认）；HTML→校验→PNG；预览/插入；Word 导出；CSP | 1 张架构图从点击到插入 ≤90s；Word 里有图；无外网/脚本 |
| **P1** | 10 种图型；重新生成/版本；项目设置 `diagram_quality`；批量 opt-in；内容溯源校验 | 架构/分层/流程/时间线/矩阵可用；失败回退快图 |
| **P2** | 自动批量映射（架构类）、品牌 profile 管理、SVG 矢量导出、图表数据型（需证据表） | 批量架构图可用；样式可配 |

---

## 十一、验收标准

1. skill 目录、LICENSE/NOTICE、VERSION 齐全；不联网可生成。
2. 单个「AI 生成图表（设计版）」入口走技能；架构图作为图型选项；生成的 HTML 通过 `self_check --offline`。
3. 预览与插入：PNG 清晰（≥1920px 宽），正文显示图名；点击可查看/重生成。
4. Word 导出包含该图；`:::figure` 白名单扩展；现有 `:::diagram` 导出不受影响。
5. 中文标签正常、无方框/缺字；无 Google Fonts 请求；系统字体缺失时启动检查报错。
6. 无 script/iframe/外链；自检拒绝不安全 HTML；修复/回退路径有效。
7. 内容：节点来自 brief/证据；数字在证据中；无编造；超出预算拆图或拒绝。
8. 失败时给出原因，可回退快图；不静默插入破图。
9. 生成 job 可离开页面继续；状态可查询；并发限制生效。
10. 目录/文件清理、项目归属校验、HTML 预览 CSP 生效。
11. 现有快速结构图、素材图、其他导出链路回归通过。
12. 1 张图耗时/费用有记录；批量默认关闭。

---

## 十二、风险与待确认

| 风险 | 对策 |
|---|---|
| 技能输出 HTML 不稳定/截断 | 16k 输出上限；self_check + 一次修复；仍失败回退快图 |
| 模型输出 SVG 坐标错误/遮挡 | 技能几何规则 + self_check；v2 引入 `verify-geometry.py`；预览人工核对 |
| 中文缺字/字体回退 | 安装 Noto CJK；`font-source: system`；渲染后检查 |
| Playwright 镜像变大/部署复杂 | 单浏览器实例、并发 1–2；Docker 安装依赖；只设计版用 |
| 生成慢（30–90s） | 后台 job + 进度；默认手动/架构类；批量 opt-in |
| token 成本高 | 只加载选中 type reference；缓存；默认 pro 但可切 flash |
| 风格像"AI 设计稿"不符合标书 | 固定正式 profile；禁用 playful 图型；人工预览确认 |
| 素材注入 | 数据分隔 + 不执行指令 + 输出校验 |

**需要你确认：**

1. **品牌样式**：公司/项目的主色、标题字体、是否允许 logo？（没有的话先用"白底 + 深灰 + 单一蓝色 accent"）。
2. **入口合并**：把「AI 生成信息图 / AI 生成架构图」合并为一个「AI 生成图表（设计版）」，架构图作为默认图型；快速结构图放"更多"。你同意吗？
3. **默认模型**：设计版默认用 pro（质量优先）还是 flash（快/便宜）？我建议 pro，单张约 30–60s。
4. **自动批量**：是否允许章节批量编写时对架构类图自动走设计版？我建议默认关，项目设置开启。
5. **部署**：是否接受后端引入 Playwright/Chromium（镜像约 +300MB）？如果不接受，需要改用外部渲染服务或只输出 HTML（Word 无法直接用）。

---

## 十三、代码落点

| 改动 | 位置 |
|---|---|
| vendor 技能/同步脚本 | 新增 `backend/skills/diagram_design/`、`scripts/sync_diagram_skill.py` |
| 品牌 profile | `backend/skills/diagram_design/references/style-guide.product.md` |
| Diagram Agent/校验/渲染 | 新增 `backend/services/diagram_design/`（brief、prompt、validate、render、store） |
| 任务/接口 | 新增 `backend/routes/diagram_assets.py`；`backend/db.py` 新表/迁移 |
| 模型调用 | `backend/agents/chapter_team.py` 或新 `services/llm_diagram.py`（max_tokens/timeout） |
| 图片端点/导出白名单 | `backend/routes/chapter_studio.py`、`backend/infra/docx/document_model.py` |
| 前端入口/预览/插入 | `frontend/assets/chapter-studio.mjs`、`chapter-studio.css`、`three-mode.html` |
| 编写设置 | `diagram_quality` 项目/章节配置（复用 writing settings） |
| 测试 | 技能校验、HTML 安全、渲染、导出、前端状态、CSP、回退 |

---

## 十四、一句话

**把 Diagram Design 当"设计规范 + 校验器 + HTML 模板"vendor 进后端，做成一个异步的「设计版图表」资产链路：LLM 产 HTML、脚本校验、Chromium 出 PNG、PNG 进正文、HTML 留源。** 快图继续负责批量与可编辑，设计版负责标书里真正需要"好看、像人做的"那几张图。
