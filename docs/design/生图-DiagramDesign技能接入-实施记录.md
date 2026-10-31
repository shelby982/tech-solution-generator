# 生图接入 Diagram Design —— 实施记录（P0 + P1）

> 对应方案：[生图-DiagramDesign技能接入-方案](生图-DiagramDesign技能接入-方案.md)
> 上游：cathrynlavery/diagram-design，commit `0cf73c3`，skill `2.6.136`，MIT。

## 已完成（P0：手动设计版图表）

### 后端

- Vendor 技能子集到 `backend/skills/diagram_design/`：
  - `SKILL.md`、12 个常用图型 reference、输出规格/布局/图元规则/品牌 style-guide；
  - `scripts/self_check.py`、`export_png.py`、`export_svg.py`；
  - `LICENSE`、`NOTICE`、`VERSION`；
  - 产品品牌 profile：`references/style-guide.product.md`（白底/深灰/单一品牌蓝、系统中文字体、无 Google Fonts）。
- 新表：`diagram_assets`（HTML/PNG/JSON 留痕）、`diagram_jobs`（异步生成状态）。
- 新服务：
  - `services/diagram_design.py`：brief → 技能提示词 → LLM HTML → 校验/修复 → job 结束；
  - `services/diagram_assets.py`：资产 DB + 文件存储；
  - `services/diagram_render.py`：Playwright 渲染 PNG（本地开发自动用项目内 `.playwright-browsers/`）。
- 新路由：
  - `GET /api/blocks/{id}/studio/diagram-design/types`：图型/推荐；
  - `POST /api/blocks/{id}/studio/diagram-assets`：创建 job；
  - `GET /api/blocks/{id}/studio/diagram-assets/{job_id}`：状态；
  - `GET /api/studio/diagrams/{asset_id}/image`：PNG；
  - `GET /api/studio/diagrams/{asset_id}/preview`：HTML（CSP sandbox）；
  - `POST /api/studio/diagrams/{asset_id}/regenerate`。
- 校验：`self_check.py --offline` + 产品安全校验（无 script/iframe/外链）+ 中文/节点/数字提示。
- Word 导出：`infra/docx/document_model.py` 允许 `/api/studio/diagrams/{id}/image`；导出快照/旧导出入口都能取图表图片。
- 启动时恢复/标记中断 job。

### 前端

- 「插入内容 → 图片」改为：
  - **AI 生成图表（设计版）**（单一入口；架构图为默认，图型下拉含 12 种）
  - **快速结构图**（原 flow/layered/timeline 文本图，保留）
  - **从素材插入图片**
- 设计版弹窗：图名、图型、尺寸、内容来源 → 生成预览（轮询 job）→ 查看源/重新生成/插入正文。
- 插入格式：`:::figure 标题\n![标题](/api/studio/diagrams/{id}/image)\n:::`。
- 进度/图片审阅识别 `design` 图，显示“设计图”。

### 工程

- `requirements.txt` 增加 `playwright==1.64.1`；
- Dockerfile 安装 Noto CJK 字体 + Chromium（`playwright install --with-deps chromium`）；
- `.gitignore` 忽略本地 `.playwright-browsers/`；
- 前端资源版本统一升到 `20261016-v438`。

## 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装 Chromium（一次）
playwright install chromium
# 沙箱/无写权限环境可指定目录：
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" playwright install chromium

# 3. 启动
bash start.sh
```

未安装 Playwright 时，设计版 job 会明确失败（`设计版图表渲染需要 Playwright…`），不会插入破图；快速结构图不受影响。

## 验证

- 后端：`1031 passed, 1 deselected`
- 前端：`156 pass`
- 新增测试：`tests/test_diagram_design.py`（类型/清洗/校验/路由/失败路径/Word 图片白名单）
- 实测：Playwright 渲染 1280×720 @2 → 2560×1440 PNG；job 状态/资产读写/图片端点正常。

## 已知边界（P1/P2）

- 只做了**手动入口**；章节批量编写仍默认快速结构图，设计版自动批量（`diagram_quality`）尚未接线。
- 暂不导出 SVG（字段已预留）；Word 用 PNG。
- Prompt 约 90–100KB（SKILL.md + 参考 + 品牌样式），单图 token/耗时较高；P1 需要做静态前缀缓存/精简版技能提示。
- 生成质量依赖模型；视觉回归/几何校验只跑了上游 `self_check`，未接入 `verify-geometry.py`。
- 资产清理（项目删除/版本保留）未做。

## 关键文件

| 文件 | 作用 |
|---|---|
| `backend/skills/diagram_design/` | vendored 技能 + 品牌 profile |
| `backend/services/diagram_design.py` | 提示词/生成/校验/job |
| `backend/services/diagram_assets.py` | 资产存储 |
| `backend/services/diagram_render.py` | Playwright PNG |
| `backend/routes/diagram_assets.py` | API |
| `backend/db.py` | 新表/迁移 |
| `backend/infra/docx/document_model.py` | Word 图片白名单 |
| `backend/routes/chapter_studio.py` / `routes/export.py` | 导出取图 |
| `frontend/assets/chapter-studio.mjs` | 入口/弹窗/插入 |
| `frontend/assets/chapter-studio.css` | 预览样式 |


## P1 续做（已完成）

- **Prompt 缓存 / 资产复用**：
  - 技能静态提示词按 `(图型, 尺寸)` `lru_cache`，不再每次重读 ~100KB 文件；
  - `prompt_hash` 写入 `diagram_assets`；同项目、同 brief、同模型命中缓存直接复用资产，避免重复烧模型；
  - 「重新生成」显式带 `force`，绕过缓存。
- **几何审计**：
  - 渲染时用 Playwright 读取所有 `<text>` 的 `getBBox()`，检查文字越界和标签重叠（阈值 30%）；
  - 结果写 `checks.geometry`，前端预览显示「越界/标签重叠」提示，不阻断插入。
- **批量图表 opt-in**：
  - 新表 `diagram_settings(project_id, quality)`；`GET/PUT /api/projects/{id}/diagram-settings`；
  - 质量档：`fast`（默认快图）/ `design_architecture`（架构/分层图用设计版）/ `design_all`（所有图）；
  - 「编写设置」弹窗新增「图表质量」；章节 SSE 的图任务按设置自动走设计版，失败自动回退快速结构图；
  - 设计图以 `:::figure` 插入，`merge_charts`/Word 导出沿用图片链路。
- 新增测试：同 prompt 复用、质量设置 roundtrip、章节图任务走设计版分支。

## 仍未做

- 自动批量只接到章节 SSE 的 figure 任务；旧 job 编排（legacy）不接。
- 几何审计只报告越界/文本重叠，不检测连线遮挡/交叉；未接上游 `verify-geometry.py`。
- SVG 导出字段预留但未实现；Prompt 可进一步精简（当前仍是完整技能参考）。
- 资产清理（项目删除/版本保留）未做。

## P2 续做（本轮）

- **SVG 矢量导出**：vendor 的 `export_svg.py --system-fonts` 在生成后产出独立 SVG；资产表写 `svg` 字段；新增 `GET /api/studio/diagrams/{id}/svg`，预览弹窗加「下载 SVG」。
- **更强几何审计**：除文字越界/标签重叠外，增加 `rect/circle/path/line/polygon` 的图形越界；前端预览同时显示文字越界、图形越界、标签重叠数量。
- **交付要素覆盖**：生成校验比较本节 `elements` 与图中 `<text>`，缺失要素写 `checks.elements_missing`，预览提示「图中未出现本节交付要素」。
- 前端资源版本统一升到 `20261016-v440`。

### 验证

- 后端 `1031 passed, 1 deselected`；前端 `156 pass`。
- `tests/test_diagram_design.py` 增加 SVG 端点断言；工具单测覆盖几何/要素提示的数据结构。

## P2 续做（compact prompt）

- 新增 `references/prompt-core.product.md`：从 SKILL.md / 品牌 style / 输出规格 / 布局 / 图元规则中抽取必需章节，保留中文排版规则与连接线规则；约 49KB（原完整参考约 98KB）。
- 默认使用精简版；`DIAGRAM_FULL_PROMPT=1` 可切回完整版做质量对照。
- 实测同一架构 brief：`deepseek-flash` 约 20s 完成，PNG 2560×1440，`self_check` 通过，`elements_missing=[]`，几何审计无越界/重叠；未知数字（443/L4/L7）进入 `unknown_numbers` 提示。
- 仍建议对真实章节做 A/B：精简版 vs 完整版的质量/耗时/token。
