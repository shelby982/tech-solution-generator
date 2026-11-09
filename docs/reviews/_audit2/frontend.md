# 前端审计

- 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`，HEAD `a91b1e9`（2026-11-09 01:15 +0800）
- 审计对象：**当前工作区**（4 个 M 在后端，前端无未提交改动；`frontend/`、`tests/frontend/` 与 HEAD 一致）
- 只读审计：未修改/删除任何项目文件，未 `git checkout/stash/reset`，未 `rm`，未启动服务。**唯一写入**：本报告
- 审计时间：2026-11-09
- 规模基线：`frontend/*.html` 17,431 行 · `frontend/assets/*.{js,mjs}` 20,241 行 · `*.css` 5,244 行 · `tests/frontend/*.test.mjs` 1,764 行
- 旧审计：`docs/reviews/_audit/frontend.md`（2026-10-19，426 行）。本轮**逐条复核**，行号全部按当前工作区重取；结论标 `[仍存在]` / `[已修复]` / `[扩大]`

---

## 0 摘要

### 0.1 三类数量与可删行数

| 类别 | 项数 | 可删行数（估算） |
|---|---|---|
| **死代码**（不可达页 / 死函数 / 死 CSS / 空壳） | **29 项**（D01–D29） | 约 **13,180 行**＝两个不可达整页 10,542 + 页外死 CSS 2,446 + 死函数/死变量 188 + 垃圾 2 |
| **无用代码**（零调用 API / 零引用导出 / 未用导入 / no-op 模块 / 重复实现） | **11 项**（U01–U11） | 约 **330 行**（不含「仅测试维持」的 8 个导出，那些符号本身是活函数、只多一个 `export` 关键字） |
| **逻辑不一致**（路由/契约/版本号/孤儿页/双样式源） | **8 项**（I01–I08） | —（配置与架构项，非纯行数） |
| **合计** | **48 项** | **不删旧整页的保守清理 ≈ 2,960 行；若产品确认删两个旧整页，合计 ≈ 13,500 行（占前端 42,916 行的 31%）** |

> 行数口径：`wc -l`。CSS 死规则按「规则块」计（块内换行计入）；D05/D06 的两页内联 CSS（2,932 行）已含在 10,542 里，不重复计。

### 0.2 严重度分布

| 严重度 | 死代码 | 无用代码 | 逻辑不一致 | 小计 |
|---|---|---|---|---|
| 高 | 4 | 3 | 3 | **10** |
| 中 | 11 | 2 | 4 | **17** |
| 低 | 14 | 6 | 1 | **21** |
| 合计 | 29 | 11 | 8 | **48** |

> 高危 10 项：D01、D02、D03、D04（两个不可达整页 + 两份快照）· U01、U02、U07（零调用 API 群、context-panel no-op、410 端点客户端）· I01、I02、I03（版本契约盲区、diff-review 孤儿页、bench.html 路由错位）。

### 0.3 最高危 5 条

1. **`frontend/bench.html`（310 行）不属于产品路由，却放在产品 `frontend/` 目录里。**
   `grep -rn "bench" backend/ --include=*.py` 只有 3 条命中，**没有一条是 `/bench` 路由**：`main.py:275`（注释里提到“workbench”）、`main.py:337-338`（`/workbench` 的 307 桩）。唯一服务方是 **未跟踪**的 `scripts/bench_server.py:86`（`git ls-files scripts/bench_server.py` → 空）。
   → 部署后该页 404；它现在靠一个不在版本库里的脚本才能跑。且它读 `marketing/*.mp4`，与投标系统无业务关系。

2. **`frontend/assets/context-panel.js`（70 行）是彻底 no-op，但每次进编辑器都执行一遍。**
   `mode-editor.js:25` 顶层调用 `initContextPanel()`；其唯一 DOM 挂点 `[data-panel-requirement]` / `[data-panel-sources]` 全仓命中 **仅定义处 2 行**（`context-panel.js:13,14`），`window.__materialsMap` 全仓 **1 处**（`context-panel.js:57`，从未赋值），`.editor-block` 在 `three-mode.html` 命中 **0 次**（`grep -c editor-block frontend/three-mode.html` → 0），它输出的 6 个 class（`panel-empty/source-card/source-meta/source-snippet/source-origin-btn/req-item`）在任何 CSS/HTML 里也 **零引用**。
   → 它注册的 `document` click 委托与 `block:done` 监听永远命中 null，纯噪声。

3. **`?v=` 版本号契约有 5 个盲区，而版本错配是本仓库已发生过的“整页脚本静默哑掉”事故根因。**
   `tests/frontend/module-imports.test.mjs:111-117,141-147` 的文件白名单漏掉仍在维护 `?v=` 的 5 个文件：
   `chapter-studio.mjs`、`chapter-tree.mjs`、`intake.js`、`project-intake.html`、`workspace-model.mjs`（见 §5 命令 R6）。
   同时白名单里仍留着两个**不可达页** `project-init.html`、`workbench.html`。

4. **`frontend/diff-review.html`（331 行）仍是孤儿页：路由存在，但活页面零入口。**
   `backend/main.py:353-357` 有 `/diff-review`（200），但 `grep -rn "diff-review" frontend/` 除**自身** `diff-review.html:17` 的自链接外，只有 `workspace.js:57` 一句注释。`review.js`、`mode-editor.js`、`workspace.js` 生成的所有跳转都指向 `/review` 或 `/three-mode`，**没有任何一条指向 `/diff-review`**。[旧审计 F5 仍存在]

5. **`api.js:136 materials.outline` 仍在向一个明确返回 410 的端点发请求。**
   `backend/routes/materials.py:674-683`：`POST /api/projects/{project_id}/outline` 函数体只有 `raise HTTPException(410, "该接口已废弃，请改用 /api/workflow/start")`。前端该方法 **零调用者**（`grep -rn "materials.outline" frontend/` → 0，排除 `api.js` 自身）。死客户端 + 死端点双份留存。

---

## 1 逐项清单

> 证据均为本次在当前工作区执行的命令结果；`file:line` 对应当前工作区。
> 「可达性」依据：`backend/main.py` 只 `app.mount("/assets", ...)`（`:292`），HTML 只能由 `FileResponse` 路由给出。

### 1.1 死代码（29 项）

| ID | 文件:行 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|
| D01 | `frontend/workbench.html` 全文件 | 不可达整页 | 5,580 行 / 245,006 B | `/workbench` → `main.py:337-342` `RedirectResponse(..., status_code=307)`；全仓无 `FileResponse(workbench_path)`；前端只挂 `/assets`（`main.py:292`） | 高 | 产品确认后整页删除 `[旧 A1 仍存在]` |
| D02 | `frontend/project-init.html` 全文件 | 不可达整页 | 4,962 行 / 239,409 B | `/project-init` → `main.py:360-365` 307；同上 | 高 | 随 D01 删除 `[旧 A2 仍存在]` |
| D03 | `workbench.html:2504-5224` | 历史快照（JS） | 2,721 行 | 与 `mode-editor.js` 对比：inline 顶层 `function` **55 个，全部**在 `mode-editor.js` 中存在（55/55=100%）；**函数体逐字相同 42/55 = 76%**；去重后非空/非结构行 **1,840/1,917 = 96.0%** 逐行字面同现。`mode-editor.js` 另有 5 个 inline 没有的函数（`insertNotice/isMergedPage/loadPointMeta/renderChapterPoints/switchBlockDiscarding`）→ 快照已落后 | 高 | 随 D01 `[旧 A4，漂移扩大]` |
| D04 | `project-init.html:1099-4959` | 历史快照（JS） | 3,861 行 | inline 顶层 `function` **138 个，全部**在 `mode-outline.js` 中（138/138）；函数体逐字相同 **94/138 = 68%**；逐行字面同现 **2,394/2,567 = 93.3%**；`mode-outline.js` 另有 **67 个**函数 inline 没有 | 高 | 随 D02 `[旧 A5，漂移扩大]` |
| D05 | `workbench.html:8-2055` | 历史快照（CSS） | 2,048 行 / 429 选择器 | 与 `three-mode.html:10-3352` 内联样式对比：workbench 429 个选择器中 **427 个（99.5%）已在 three-mode 内联**，仅 74 个与 `assets/*.css` 重合（17%）→ 同一份 CSS 的第二份拷贝，主副本在 three-mode 内联 | 中 | 随 D01 `[旧 A6 仍存在]` |
| D06 | `project-init.html:8-891` | 历史快照（CSS） | 884 行 / 327 选择器 | 327 个选择器**全部（100%）**已在 `three-mode.html:10-3352` 内联；与 `assets/*.css` 仅重合 45 个（14%） | 中 | 随 D02 `[旧 A6 仍存在]` |
| D07 | `workbench.html:5326-5577` | 重复实现（模型/API 弹窗 IIFE） | 252 行 | 功能由 `mode-outline.js:514-615`（`loadModels/renderDefaultModels/bindModelOptions/openApiModal/closeApiModal/resetApiForm`）承担；`escModelHtml` 在活代码中 0 命中 | 低 | 随 D01 `[旧 A7 仍存在]` |
| D08 | `workbench.html:2061-2071`；`project-init.html:895-905` | 失效逃生口 | 22 行 | 两段 IIFE 在 `?legacy===1` 时 `return` 否则 `location.replace('/three-mode?…')`。但页面已被服务端 307 拦下，脚本永不执行；`grep -rn "legacy=1" frontend/` → 仅这 2 处注释 + 2 处判断，**无任何链接/代码会带该参数** → 逃生口双重失效 | 低 | 随 D01/D02 `[旧 A3 仍存在]` |
| D09 | `workbench.html:5228` | 空壳函数 | 1 行 | `function toggleRefCard(id) {}`；全仓 1 处（定义本身） | 低 | 随 D01 `[旧 A8 仍存在]` |
| D10 | `workbench.html:2057` | 外部 CDN 残留 | 1 行 | `<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js">`；`grep -rn "cdn.jsdelivr\|unpkg\|cdnjs" frontend/` → 仅此 1 处；活页无外网依赖 | 低 | 随 D01 `[旧 A9 仍存在]` |
| D11 | `workbench.html:2708-2710,2854,2874`；`project-init.html:1468-1472,4666,4694` | 失效链接生成 | 10 处 | 死页内仍拼 `/workbench?…`、`/project-init?…`、`/review?…`。对照：活模块已全部改 `/three-mode`（`mode-editor.js:228-232`、`mode-outline.js:488-489`、`workspace.js:39-41`），`grep -rn "/project-init\|/workbench" frontend/assets/*.js` → 仅注释 | 低 | 随 D01/D02 `[旧 A10 仍存在，已隔离在死页内]` |
| D12 | `mode-outline.js:2833-2839` `readConfirmedPkg` | 死函数 | 7 行 | `grep -rn "\breadConfirmedPkg\b" frontend/ tests/` → **1**（定义）。同族 `rememberConfirmedPkg` 有 5 处调用，说明是遗留的旧读取路径 | 中 | 删除 `[旧 B1 仍存在]` |
| D13 | `mode-outline.js:2981-3010` `renderPackageCards` | 死函数 | 30 行 | 全仓 **1** 处（定义）。其独占依赖 `pkgMergeNote`（`:2817`）除本函数 `:3009` 外只剩赋值 `:5396,5406` → 随之变死 | 中 | 删除；连带清 `pkgMergeNote` `[旧 B1 仍存在]` |
| D14 | `mode-outline.js:3013-3025` `renderPointsAlignment` | 死函数 | 13 行 | 全仓 **1** 处（定义）。`CAT_NAME`（`:2821`）仅本函数用 → 随之变死。注意活代码里有 `api.projects.syncPoints`（`api.js:77`）但面板不再渲染该行 | 中 | 删除；连带清 `CAT_NAME` `[旧 B1 仍存在]` |
| D15 | `mode-outline.js:4056-4140`(85) `:4142-4145`(4) `:4147-4148`(2) `:4218-4220`(3) `:4229-4230`(2) `:4273-4281`(9) | 死局部 helper | 105 行 | 六个 `openSplitReport()` 内局部箭头函数，全仓各 **1** 处（定义）。`:4056` `confItem` 85 行是其中最大一块 | 中 | 删除 `[旧 B2 仍存在]` |
| D16 | `mode-outline.js:200` `currentReader`；`:2153` `pendingRenameId`；`:5856-5860` `lastRunErrorMessage` | 死变量/函数 | 7 行 | 三者在本模块内**零引用**（`grep -rn "\bcurrentReader\b" frontend/assets/*.js frontend/assets/*.mjs tests/frontend/` → 各 1 处=定义） | 低 | 删除 |
| D17 | `workspace.js:47-51` `currentView()`；`:83` `const inPlace` | 死函数/死变量 | 6 行 | 各全仓 **1** 处（定义）。`currentView` 的空壳化源于导航整块移除（`workspace.js:84-85 nav.innerHTML=''; nav.hidden=true`） | 低 | 删除 `[旧 B3 仍存在]` |
| D18 | `diff-review.html:156-164` `fmtTime` | 死函数 | 9 行 | 同文件 `timeKey`(`:166`)`groupByTime`(`:184`) 均在用；`fmtTime` 仅定义 1 处 | 低 | 删除 `[旧 B6 仍存在]` |
| D19 | `mode-dispatch.js:98-103` | 死事件委托 | 6 行 | `document.getElementById('workspace-nav')?.addEventListener('click', … e.target.closest('a[data-mode]') …)`；但 `workspace.js:86-87` 把 `#workspace-nav` 清空并隐藏，**永不产生 `a[data-mode]`**；全仓 `data-mode` 仅命中 `three-mode.html:3837-3838` 的 `button[data-mode]`（编辑器内部 编辑/预览 开关，语义无关） | 中 | 删除委托；或导航重做时一并恢复 `[旧 C7 部分仍存在]` |
| D20 | `styles.css` | 死 CSS 规则块 | **208 块 / 1,217 行** | 规则级扫描（语料 = HTML 去 `<style>` 后 + JS/MJS 的全部标识符 token；见 §5 R4）。最大块：`.confirm-row`(L1421,15行)、`.icon-button`(L555,14)、`.floating-selection-toolbar`(L1971,14)、`.history-item`(L2054,14)、`.search-shell`(L102,12)、`.rail-button`(L838,12)。旧审计点名的 `.page-shell`(64)、`.gallery-page`(68)、`.gallery-hero`(324)、`.brand-lockup`(188)、`.list-*`(137-194)、`.view-toggle`(115) 全部**仍在且仍死** | 中 | 分批删；先删「历史项目列表页」整段 `[旧 C1，规模自 177 块/1,394 行收缩为 208 块/1,217 行]` |
| D21 | `review.css` | 死 CSS 规则块 | **54 块 / 276 行** | 最大：`.review-recover`(L89,12)、`.outline-item`(L416,12)、`.review-banner`(L37,11)、`.review-actions`(L104,11)、`.review-outline`(L380,11)、`.review-content`(L18,10)、`.score-pill`(L237,10)、`.review-progress`(L317,10)、`.banner-list`(L69,9)、`.severity-tag`(L287,9) | 中 | 删除 `[旧 C2，从 55 块/381 行收缩为 54 块/276 行]` |
| D22 | `chapter-studio.css` | 死 CSS 规则块 | **354 块 / 368 行** | 该文件是**压缩格式**（多规则同行，故块多行少）。最大块 `.cs-tab-badge`(L606)、`.cs-plan-badge`(L499)、`.cs-mat-add`(L505)、`.cs-mat-title`(L512)、`.cs-ins-*`(L557-668 一串) | 中 | 与 D23/D24 一并清理 |
| D23 | `response-outline.css` | 死 CSS 规则块 | **26 块 / 33 行** | `.sr-mount`(L33,3)、`.sr-two`(L49)、`.sr-two-foot`(L61)、`.sr-check`(L98)、`.sr-echo`(L103)、`.sr-btn/.sr-btn-primary`(L2-4)。注意 `.sr-mount-cell` 是活的、`.sr-mount` 是死的 —— 名字近似 | 低 | 删除 `[旧 C3，从 26 块/63 行收缩]` |
| D24 | `workspace.css` | 死 CSS 规则块 | **14 块 / 14 行** | `.ws-count`(L6)、`.ws-status`(L7)、`.ws-state-copy`(L8)、`.ws-status-actions`(L10-12)、`.ws-outline-tools`(L46-47)、`.ws-editor-review`(L48-51) —— 共享壳状态条已移除（`workspace.js:96-97` 注释「共享壳状态条已按原型移除」） | 低 | 删除 `[旧 C4 仍存在]` |
| D25 | `three-mode.html:10-3352` 内联 `<style>` | 死 CSS 规则块 | **83 块 / 467 行** | 最大：`.mat-tab`(L2527,16)、`.btn-mark-done`(L1341,14)、`.ai-action-btn`(L2426,13)、`.history-item`(L2495,13)、`.ced-step-badge`(L1912,12)、`.suggestion-btn`(L2470,12)、`.ref-section-label`(L1494,11)、`.ai-toggle`(L2397,11)、`.mat-panel-hint`(L2578,11)、`.block-detail-points li`(L1746,10) | 中 | 删除（或先把 3,343 行内联 CSS 抽成 `assets/three-mode.css` 再清） `[旧 C5，从 85 块/648 行收缩为 83 块/467 行]` |
| D26 | `projects.html:8-615` 内联 `<style>` | 死 CSS 规则块 | **13 块 / 70 行** | `.modal-footer`(L367,10)、`.upload-count-badge`(L258,9)、`.drop-zone-icon`(L287,9)、`.btn-add-config-api`(L501,7)、`.upload-section-*`(L238-249)、`.footer-hint-*`(L403-409)。注意 `.api-modal-footer`/`.edit-modal-footer` 是活的，`.modal-footer` 是死的 | 低 | 删除 |
| D27 | `ui.css:95` | 死 CSS 规则块 | 1 块 / 1 行 | `.ui-card--flat`，全仓零引用；`ui.css` 其余 23 块全部命中 | 低 | 删除 |
| D28 | `frontend/.DS_Store` | 垃圾文件 | 1 文件 / 6,148 B | `git ls-files frontend \| grep -i ds_store` → 空（未跟踪）；`.gitignore:16` 已含 `.DS_Store` | 低 | 删除 `[旧 B12 仍存在]` |
| D29 | `backend/main.py:272` | 死变量（前端路由相关） | 1 行 | `_index_path = os.path.join(frontend_dir, "index.html")`；`grep -n "_index_path" backend/main.py` → 仅此 1 处；`ls frontend/index.html` → No such file | 低 | 删除 `[旧 B11 仍存在，行号 246→272]` |

### 1.2 无用代码（11 项）

| ID | 文件:行 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|
| U01 | `api.js:44,95,101,136,164,173,210,216,225,227,287,441` | 零调用客户端方法 | 12 方法 / 约 40 行 | 按 `api.<group>.<method>(` 精确扫描（排除 `api.js` 自身）：`projects.createPackage`(:44)、`projects.patch`(:95)、`projects.lock`(:101)、`materials.outline`(:136)、`materials.mapSources`(:164)、`blocks.update`(:173)、`blocks.ai`(:210)、`blocks.updateRequirement`(:216)、`revisions.list`(:225)、`revisions.restore`(:227)、`workflow.getSplitAnswers`(:287)、`review.get`(:441) → 前端命中 **0**，测试命中 **0**。**但后端路由全部健在**：`packages.py:188`、`projects.py:164/177`、`materials.py:674/687`、`blocks.py:197/330`、`revisions.py:28/68`、`materials.py:280`、`review.py:19` | 高 | 与后端契约一起决策：前端补用，或前后端同步删 `[旧 B9，11→12 项，新增 materials.outline]` |
| U02 | `context-panel.js:1-70`；调用点 `mode-editor.js:8,25` | no-op 模块 | 70 行 + 2 处调用 | 见 §0.3 第 2 条。补充：`grep -rn "data-panel-requirement\|data-panel-sources" frontend/ tests/ backend/` → 2 行，均在 `context-panel.js` 自身；`grep -c editor-block frontend/three-mode.html` → **0**；`window.__materialsMap` 全仓 1 处 | 高 | 删除模块 + `mode-editor.js` 的 import/调用；若产品要恢复右栏上下文面板，需先补 DOM 挂点 `[旧 B10 仍存在]` |
| U03 | `mode-editor.js:19`（4 个）；`mode-outline.js:13`（4 个）；`workbench.js:3`（1 个） | 未使用具名导入 | 9 个符号 | 静态扫描「import 后全文再无该标识符」：`mode-editor.js:19` → `evidenceSourceLineHtml`、`evidenceSnippet`、`chapterStage`、`CHAPTER_STAGE_LABELS`；`mode-outline.js:13` → `constraintLevelLabel`、`groupEvidenceByItem`、`itemCode`、`chapterBadges`；`workbench.js:3` → `chapterBadges` | 低 | 从 import 列表移除 `[旧 B7 仍存在]` |
| U04 | `chapter-tree.mjs:146`；`mode-dispatch.js:48`；`workbench-modes.mjs:26`；`workspace-model.mjs:6,6,541,849` | 零外部引用导出（产品与测试皆 0） | 7 个导出 | 全仓 grep 各 1 处（定义本身）：`setSelectedRow`(chapter-tree.mjs:146)、`currentMode`(mode-dispatch.js:48)、`REVIEW_PATH`(workbench-modes.mjs:26)、`materialRoleLabels`/`materialRoleHints`(workspace-model.mjs:6，`export { ROLE_LABEL as … }`)、`EVIDENCE_SOURCE_LABELS`(workspace-model.mjs:541，模块内 `:669` 自用)、`CHAPTER_STAGES`(workspace-model.mjs:849) | 低 | 删导出（`setSelectedRow` 若为预留 API 需加注释） `[旧 B4/B5/B8 部分仍存在]` |
| U05 | `chapter-studio.mjs:105`；`workbench-modes.mjs:19,46,58,75,89`；`workspace-model.mjs:559,621` | 仅测试维持的导出 | 8 个导出 | 「frontend 零引用 + 模块内零自用 + tests 有引用」三联筛：`exportScope`(chapter-studio.mjs:105)、`MODE_LABEL`(workbench-modes.mjs:19)、`parseMode`(:46)、`modeUrl`(:58)、`resolveSelectedBlock`(:75)、`panelVisible`(:89)、`evidenceSliceTitle`(workspace-model.mjs:559)、`evidenceByFile`(:621)。这些导出在产品代码里不产生任何行为 | 低 | 与 `workbench-modes.test.mjs` 一起决策：删函数+删测试，或接进 `mode-dispatch.js` `[旧 B8/F3 仍存在]` |
| U06 | `workspace.js:336` `openWritingSpecs`；`:467` `openMaterialsHub` | 多余 `export` 关键字 | 2 处 | 函数**是活的**（`workspace.js:102,104` / `:103` 绑定 `#ws-specs`/`#ws-materials`，两者在 `three-mode.html:3374-3375` 存在），但**全仓无外部 import** → `export` 无意义 | 低 | 去掉 `export`，保留函数 |
| U07 | `api.js:136-163` | 已废弃端点客户端 | 28 行 | 见 §0.3 第 5 条；`materials.py:674-683` 唯一函数体是 `raise HTTPException(410, ...)` | 高 | 删除该方法（唯一 410 端点） |
| U08 | `intake.js:8-12`（`request`）；`:93`（`save`） | 重复实现 | 约 10 行 | `request()` 与 `api.js:26-36 parseJsonResponse` 语义重复（都是「非 2xx 抛 detail」）；`save()` 用裸 `fetch` 拼 `PATCH/POST /projects/{pid}/packages…`，绕过 `api.projects.createPackage`/`patch` —— 这正是 U01 中两个方法“零调用”的原因之一 | 低 | `request` 直接复用 `api.js` 的错误处理；`save` 走 `api.projects.*` |
| U09 | `projects.html:939-1058`（内联） | 与模块重复实现 | 约 120 行 / 8 个同名函数 | `projects.html` 内联与 `mode-outline.js` 同名 8 个：`loadModels`、`renderDefaultModels`、`bindModelOptions`、`openApiModal`、`closeApiModal`、`resetApiForm`、`escHtml`、`fmtSize`。两处已漂移（非逐字相同） | 中 | 抽 `assets/model-config.js` 共用；活代码里最大一处重复 `[旧 D3 仍存在]` |
| U10 | `chapter-studio.css` / `styles.css` / `review.css` / `response-outline.css` / `ui.css` / `workspace.css` | 同文件内同选择器重复定义 | 268 次多定义 | `chapter-studio.css` 94 个选择器重复（121 次多定义，如 `.cs-toolbar` @ [2,6,12,527]）；`styles.css` 89 个（137 次，`card`/`panel` @ [300,315,595]）；`review.css` 8 个；`response-outline.css` 4 个；`ui.css` 3 个；`workspace.css` 2 个 | 中 | 合并为单条声明（后者覆盖前者，字段合并），不改视觉 `[旧 C6，规模扩大]` |
| U11 | 6 个 CSS 文件之间 | 跨文件重复选择器 | 27 个选择器 | 例：`.v-pill{,.ok,.part,.bad,.none}` 在 `review.css:618-623` 与 `ui.css:117,130-133` 双份；`.workspace-shell` 在 `review.css:8` 与 `styles.css:472,2359`；`.ws-dialog{,::backdrop}` 在 `ui.css:160,168` 与 `workspace.css:14,15`；`.context-pane` 在 `styles.css` **5 处** + `ui.css:171`；`.sr-btn/.sr-btn-primary` 在 `response-outline.css:2-4` 与 `ui.css:33,60` | 低 | 确立 `ui.css` 为组件单源；页面级文件不再重复 `[旧 C10 仍存在]` |

### 1.3 逻辑不一致（8 项）

| ID | 文件:行 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|
| I01 | `tests/frontend/module-imports.test.mjs:111-117,141-147` | 版本号契约盲区 | 5 个文件 | 见 §0.3 第 3 条。测试白名单 = `{three-mode,projects,project-init,workbench,review,diff-review}.html` + `{mode-outline,mode-editor,mode-dispatch,workspace,workbench,context-panel,review,generate-session,api}.js`；实际含 `?v=` 的 18 个文件里，**5 个不在名单**：`chapter-studio.mjs`、`chapter-tree.mjs`、`intake.js`、`project-intake.html`、`workspace-model.mjs`。当前 68 处 token **全部**为 `20261016-v503`（`grep -rhoE '\?v=[A-Za-z0-9._-]+' frontend/ \| sort -u` → 1 个值），所以测试此刻是绿的 —— 是**覆盖率漏洞**而非当前违反 | 高 | 补 5 个文件进两个数组；同时移除 `project-init.html`/`workbench.html` `[旧 F2 仍存在，字面一致]` |
| I02 | `frontend/diff-review.html` 全文件 + `backend/main.py:353-357` | 孤儿页 | 331 行 | 见 §0.3 第 4 条 | 高 | 二选一：在 `/review` 版本区补入口，或删路由+文件 `[旧 F5 仍存在]` |
| I03 | `frontend/bench.html` 全文件 + `scripts/bench_server.py:86` | 非产品页 / 依赖未跟踪服务 | 310 行 | `backend/main.py` 无 `/bench` 路由；`scripts/bench_server.py` **未跟踪**（`git ls-files` → 空），第 86 行 `@app.get('/bench') → FileResponse(FRONTEND/'bench.html')`；该页 12 处调用 `/bench/*`（`bench.html:110,118,119,131,201,250,253,290,298,301`）；读 `marketing/*.mp4`；设计文档 `docs/design/视频逐帧工作台-*.md` 明确它是**独立本地工具**（端口 5199），与投标系统无关 | 高 | 移出 `frontend/`（如 `tools/bench/`）并把 `bench_server.py` 纳入版本库；或明确标注为 dev-only 并从产品资源树分离 |
| I04 | `workbench.html` + `project-init.html` | 死页仍在维护 `?v=` | 15 处引用 | `grep -c "?v=" frontend/workbench.html` → **9**（行 `7, 2058, 2503, 2505-2508, 2514, 5578`）；`project-init.html` → **6**（行 `7, 892, 1100, 1107, 1121, 4960`）。每次 bump 版本号必须同步这 15 处，而它们不产生任何运行时效果 | 中 | 随 D01/D02；bump 点从 68 → 53 `[旧 A11/E7 仍存在]` |
| I05 | `mode-dispatch.js:29` `setMode`、`:48` `currentMode`、`:54-56` `renderNav` | 半失效机制 | 约 30 行 | `mode-dispatch` 的**核心目的是顶栏原地切模式**，但唯一入口 `a[data-mode]` 已被 `workspace.js:86-87` 移除 → `setMode` 只剩 `popstate` 内部自用（`:106`），`currentMode` 全仓 0 调用。**仍活着的部分**是 `init()` 把 URL `?view=` 落到 `body[data-view]`（`:84-96`）—— 这个必须保留，因为 `[data-view="matching"]` 有 25 条、`[data-view="editor"]` 13 条、`[data-view="outline"]` 2 条 CSS 依赖它 | 中 | 保留 `init()` 的 data-view 落地 + popstate；删 `currentMode`，`setMode` 去 `export`，删 D19 委托。若导航重做则一并恢复 |
| I06 | `workspace.css:2-5` | 契约失效的样式 | 4 条 | `#workspace-nav a` / `a:hover` / `a[aria-current="page"]` —— `renderChrome()`（`workspace.js:86-87`）保证 `#workspace-nav` 永远 `innerHTML=''` 且 `hidden`，故 `a` 子元素永不存在 | 低 | 删这 4 条（与 I05 同一根因） `[旧 C7 仍存在]` |
| I07 | `three-mode.html:10-3352` vs `assets/*.css` | 双样式源 / 真实样式表错位 | 3,343 行内联 | 页面的**实际样式主要活在内联 `<style>`**：three-mode 内联 803 个选择器中，**713 个（89%）在任何 `assets/*.css` 里都不存在**，仅 90 个与 6 个 CSS 文件重合（`styles/workspace/ui/chapter-studio/response-outline/review`）。再加上 `mode-outline.js:158,175,4678` 三处**运行时注入** `<style>` → 一份页面有 **4 个 CSS 投递位置**。这使 D20-D26 的死规则清理与视觉回归强耦合 | 中 | 先把内联 CSS 抽成 `assets/three-mode.css`，再统一清死规则；运行时注入的三段迁入该文件 `[旧 C5/E3 的根因描述，本轮量化]` |
| I08 | `tests/` 全目录 | 页面路由零回归测试 | 0 条断言 | `grep -rn "307\|redirect" tests/routes/*.py` → **0**；`grep -rln "workbench\|project-init\|three-mode\|project-intake\|diff-review\|/lp" tests/ --include=*.py` → 仅 `tests/routes/test_leads.py`（引用 `/lp` 作 referer 字符串，非路由断言）。`/workbench`、`/project-init` 的 307、`/lp` 的 200 全无保护 | 中 | 新增 `tests/routes/test_frontend_routes.py`：断言 6 条页面路由状态码 + `/workbench?projectId=12` 的 `Location` `[旧 F1 仍存在]` |

---

## 2 高危逐项详述

### 2.1 D01/D02/D03/D04：两个不可达整页 + 两份历史快照（10,542 行）

**不可达性是硬结论，不是推测。** `backend/main.py` 只有两处静态挂载相关代码：

- `:292` `app.mount("/assets", NoCacheStaticFiles(directory=.../assets), name="assets")` —— 只挂 `assets/` 子目录，`frontend/*.html` 不在 URL 空间里；
- `:315-380` 九个 `@app.get`，其中 `serve_workbench`(`:337-342`) 与 `serve_project_init`(`:360-365`) 只 `return RedirectResponse(target, status_code=307)`。

所以 `GET /workbench.html`、`GET /project-init.html` 均 404；`GET /workbench`、`GET /project-init` 均 307。**这两个文件在当前部署下没有任何代码路径能被执行。**

**它们的内部 JS 是活模块的冻结快照，且已明显落后。** 逐函数比对（§5 R1）：

| 对照 | inline 顶层 function | 全部存在于活模块 | 函数体逐字相同 | 逐行字面同现 | 活模块独有（inline 已落后） |
|---|---|---|---|---|---|
| `workbench.html:2504-5224` ↔ `mode-editor.js` | 55 | **55 / 55 = 100%** | **42 / 55 = 76%** | **1,840 / 1,917 = 96.0%** | 5 个 |
| `project-init.html:1099-4959` ↔ `mode-outline.js` | 138 | **138 / 138 = 100%** | **94 / 138 = 68%** | **2,394 / 2,567 = 93.3%** | **67 个** |

「函数名 100% 重合」+「逐行 93-96% 同现」+「活模块独有 5/67 个函数」三条同时成立，只有一种解释：这是机械抽取模块**之前**的一份快照，抽取后活模块继续演进，快照停在原地。它们没有任何“保留作参考”以外的价值，而参考价值已被 `mode-editor.js`/`mode-outline.js` 完全覆盖。

**维护成本还在持续支付。** `?v=` token 在 18 个文件共 68 处；这两个死页占其中 15 处。任何一次版本 bump 都要动它们，而它们不会产生任何运行时效果（I04）。

**阻塞点（需产品决策）**：删除是不可逆的产品动作。建议给一个明确判据 —— 「两次发版内无人 `git log` 引用这两个文件 + `docs/design/UI一致性审计.md:306,730` 里承诺的『旧页保留重定向』改为承认已 307」即可删。

### 2.2 U02：`context-panel.js` 是一个每次进编辑器都执行的 no-op

模块只做三件事，三件全部无效：

1. `document.querySelector("[data-panel-requirement]")` / `"[data-panel-sources]"`（`:13-14`）→ **两个选择器全仓只在这里出现**。`renderRequirement`/`renderSources` 第一行都是 `if (!reqSection) return;`，所以永远立即返回。
2. `document.addEventListener("click", … closest(".editor-block") …)`（`:51-61`）→ `grep -c "editor-block" frontend/three-mode.html` 是 **0**：活页根本没有 `.editor-block` 元素（编辑器正文块用的是 `.matched-card`，见 `mode-editor.js:1927`）。
3. `window.addEventListener("block:done", …)`（`:63-69`）→ 依赖 `blockEl.dataset.source` 与 `window.__materialsMap`，而 `__materialsMap` **全仓只有这里读、从未被写**。

它渲染的 6 个 class（`panel-empty`、`source-card`、`source-meta`、`source-snippet`、`source-origin-btn`、`req-item`）在全部 HTML/JS/CSS 里**零引用**（逐个 `grep -rl` 结果都只有 `context-panel.js` 自己；`chapter-studio.css` 里只有同前缀不同名的 `.cs-cp-req-item`，不是同一个类）。

调用点 `mode-editor.js:25` 是**顶层** `initContextPanel();` —— 不是惰性、不是条件，每次进编辑器模式都注册两个永久监听器并做两次注定失败的 `querySelector`。删除收益：70 行 + 每次页面加载两个空转监听。

### 2.3 I01：`?v=` 契约的 5 个盲区

这个测试的存在理由写在它自己的注释里（`:1-11`、`:103-108`）：ES 模块具名导入不存在是**链接期错误**，整个模块不执行，页面只剩骨架转圈；而 `?v=` 不统一会让浏览器命中缓存里的旧模块，即使代码已修好也照样哑。这是**已经发生过**的事故（`:105` 提到 `mode-outline.js` 曾挂 `?v=20261013-noconfirm`）。

现在测试的两个数组（`:112-113`、`:142-143`）是硬编码白名单。实测（§5 R6）：

```
实际含 ?v= 的文件 18 个
测试名单 16 个名字，其中 13 个确实被覆盖；另 3 个名字（context-panel.js、generate-session.js、
material-roles.mjs）本身不含 ?v=，是无害的冗余项
未被覆盖 5 个：
  chapter-studio.mjs      ← 3 处相对导入（:1,:2,:3）
  chapter-tree.mjs        ← 1 处相对导入（:18 起）
  intake.js               ← 1 处（:1）
  project-intake.html     ← 唯一真入口页，不在任何数组里
  workspace-model.mjs     ← 被 6 个活模块导入的核心模块（mode-editor/mode-outline/review/workbench/workspace/chapter-tree）
```

今天是绿的（68 处 token 全为 `20261016-v503`），所以这是**覆盖率漏洞**而非当前失败。修法就是把这 5 个名字加进两个数组、把两个死页名字删掉 —— 纯测试文件改动，零产品风险。

### 2.4 I02 + I03：两个“路由错位”的页面

**`diff-review.html`** 是「路由在、入口无」：`main.py:353-357` 给它 200，`diff-review.html:17` 自己给自己一个 `<a href="/diff-review">审核导出</a>`，除此之外全仓只有 `workspace.js:57` 的一句注释提到它。而 `revisions.py:40` 的 `GET /projects/{id}/revisions`、`export.py` 的 diff 接口都健在 —— 也就是说后端能力可用、页面可达、但**没有任何用户路径能走到它**。要么在 `/review` 补一个「查看修订链/导出审核」入口，要么连路由一起删。

**`bench.html`** 是「页面在、路由无」，方向恰好相反：它在 `frontend/` 里（一个会被整体部署的目录），但 `backend/main.py` 里没有任何 `/bench` 路由，唯一服务它的是 `scripts/bench_server.py:86`，而那个文件 **`git ls-files` 返回空 —— 未跟踪**。它读 `marketing/*.mp4`（`bench_server.py:26` `FILMS_DIRS = [ROOT/'marketing']`），跑 ffmpeg 抽帧，是「视频逐帧工作台」这个**独立本地工具**的 UI（`docs/design/视频逐帧工作台-使用说明.md:4` 明确「独立页面…保留为不依赖 Harness 的备用入口」）。结论：它不是投标系统的产品页；它出现在 `frontend/` 里是目录归属错误，并且它当前的可运行性依赖一个不在版本库中的脚本。

### 2.5 U01 + U07：`api.js` 与后端的契约错位

`api.js` 是「客户端方法 = 后端契约」的事实清单（各页面都从它取）。当前 63 个方法里：

- **12 个零调用**（前端 0、测试 0），但**后端路由全部健在** → 前端单方面不再使用，后端仍在服务。这类不能只删前端，否则清单失真。
- **其中 1 个（`materials.outline`，`:136-163`，28 行 SSE 解析逻辑）后端已明确废弃**：`materials.py:674-683` 的 `generate_outline()` 函数体只有 `raise HTTPException(410, "该接口已废弃，请改用 /api/workflow/start 启动 LangGraph 工作流")`，且这是全后端**唯一**的 410（`grep -rn "410" backend/routes/*.py` → 只有 `:678,:682` 两行注释/代码）。

也就是说 `materials.outline` 是「唯一一个连后端都判定为死的客户端方法」，可以单独、立即删除，不需要产品决策；其余 11 个要跟后端一起决策。

### 2.6 D20-D26：2,446 行死 CSS，且清理与视觉回归强耦合

扫描方法（§5 R4）：语料 = 所有 HTML（**先剥掉 `<style>` 块**）+ 所有 JS/MJS 的全部标识符 token；规则 = 选择器里的每一个 class/id 都不在语料中，则整块判死。

结果：6 个 assets CSS 文件 + 2 处活页内联样式，**共 753 个整块死规则 / 2,446 行**（不含 D05/D06 两个死页里的重复 CSS —— 那 2,932 行已随整页计入）：

| 位置 | 死块 | 死行 |
|---|---|---|
| `styles.css` | 208 | 1,217 |
| `chapter-studio.css` | 354 | 368 |
| `review.css` | 54 | 276 |
| `response-outline.css` | 26 | 33 |
| `workspace.css` | 14 | 14 |
| `ui.css` | 1 | 1 |
| `three-mode.html` 内联 | 83 | 467 |
| `projects.html` 内联 | 13 | 70 |
| **合计** | **753** | **2,446** |

为什么必须和 I07 一起看：`three-mode.html` 内联 `<style>`（3,343 行）共 803 个选择器，其中 **713 个（89%）在任何 `assets/*.css` 中都不存在**，仅 90 个与 6 个 CSS 文件重合 —— 说明这个页面的样式主体从来就没进过 CSS 文件；再加上 `mode-outline.js:158,175,4678` 的三处**运行时 `insertAdjacentHTML('<style>')`**，一份页面有 4 个 CSS 投递位置。在这种结构下删 `assets/*.css` 的死规则，回归风险最高的是「你以为是死的，其实某处运行时注入/内联里同名的活规则被一起改掉」。所以：**先抽内联 CSS 成文件、再统一清死规则**，顺序不能反。

---

## 3 已核实「看起来死其实活」的排除项（附引用）

以下都曾被怀疑或容易被误判，逐一核实后**确认是活的**，不要删。

| 对象 | 为什么看起来像死的 | 实际引用（证据） |
|---|---|---|
| `frontend/assets/` 下**没有任何**整文件零引用 | 目录里 24 个文件看着杂乱 | 从 7 个路由页出发做**传递闭包**（含 `src=`、`<link>`、静态 `import`、`import()`、`./assets/*` 字面量数组），31 个文件全被覆盖 = 7 个 HTML + `assets/` 下 24 个 js/mjs/css 文件，**无一例外**都在闭包里。另有 `assets/landing/` 5 张 PNG，被 `landing.html` 5 处 `<img src="/assets/landing/…">` 引用。→ 不存在“零引用 assets 文件”这一项 |
| `ai-revision.mjs`（34 行） | 新文件、名字不像被用到 | `chapter-studio.mjs:1` 导入；3 个真实调用点：`rebaseSelection`(`chapter-studio.mjs:3537`)、`selectedRange`(`:5207`)、`reverseAIChange`(`:5975`)；`tests/frontend/ai-revision.test.mjs` 覆盖 |
| `chapter-live.mjs`（129 行） | 独立小模块 | `chapter-studio.mjs:2` 导入，`:mountLiveWriting` 有 2 处使用；`tests/frontend/chapter-live.test.mjs`、`manual-content.test.mjs` 覆盖 |
| `spec-content.mjs`（23 行） | 23 行太小像残留 | `chapter-studio.mjs:3` + `mode-outline.js:7` 双处导入，`specContentHtml` 在 `chapter-studio.mjs` 2 处、`mode-outline.js:4260,4266,5367` 三处使用 |
| `generate-session.js`（78 行） | 只在 `mode-editor.js` 出现 | `mode-editor.js:5` 导入、`:554` `new GenerateSession(projectId)` 实例化 —— 是流式生成会话的唯一实现 |
| `workbench-modes.mjs` 的 `DEFAULT_MODE`/`isValidMode` | 模块里一半导出是死的（见 U04/U05） | `mode-dispatch.js:13` 导入二者；`isValidMode` 用于 `:20,23,30,87`、`DEFAULT_MODE` 用于 `:25,30` —— 共 6 处。这是模式判定的唯一来源，不能整体删 |
| `material-roles.mjs` | 名字像纯常量 | 3 个活模块导入：`workspace.js`、`workspace-model.mjs`、`mode-outline.js`；`workspace.js` 还再导出 `SELECTABLE_REQUIREMENT_ROLES`，被 `module-imports.test.mjs:94-101` 专门守护 |
| `landing.html`（835 行） | `/lp` 不在导航里，像没接线 | `main.py:345-350` 有 200 路由；`:818` `fetch('/api/leads')`；`backend/routes/leads.py:52` `@router.post("/leads", status_code=201)` 存在且被 `tests/routes/test_leads.py` 覆盖。**完全接线** |
| `review.js`（277 行） | `grep -c "api\."` 返回 **0** | 它不直连 `api.js`，而是从 `workspace.js` 取 `request/statusLabels/esc/...`（`review.js:1`），再加 `workspace-model.mjs` 的 `summarize/diffHtml`（`:2`）；`:228-250` 有真实 `addEventListener`/`onclick` 绑定。是活页 |
| `intake.js`（135 行） | 只被一个 35 行的小页引用 | `project-intake.html:35` `<script src="/assets/intake.js">`；文件内 6 处 `api.*` 调用（`api.projects.intake`×2、`identifyPackages`、`confirmPackage`、`packageWorkspace`、`api.materials.upload`），对应后端路由实测存在（`packages.py:119,163,188,213,240`） |
| `three-mode.html` 的 `body[data-view]` 机制 | 导航被整块移除，像整套模式切换都死了 | URL `?view=` 落地（`mode-dispatch.js:84-96`）是**活**的：CSS 里 `[data-view="matching"]` **25 条**、`[data-view="editor"]` **13 条**、`[data-view="outline"]` **2 条**规则依赖它。进入 `/three-mode?view=editor`（`diff-review.html:109`、`review.js:17`、`mode-editor.js:228-230,374,393`、`intake.js:87`）全靠它。**只有** 顶栏 `a[data-mode]` 点击那条路死了（D19/I05） |
| `chapter-studio.css` / `response-outline.css` | 各只有 1 个引用方 | 都被 `three-mode.html` 直接 `<link>`：`response-outline.css` 在 `three-mode.html:7`、`chapter-studio.css` 在 `:8` —— 单引用，但引用方是**唯一主页面**，是活的 |
| `styles.css` / `ui.css` / `workspace.css` | 死规则占比高（D20/D24/U11），像可以整体弃用 | 被**活页** `<link>` 的次数：`styles.css` 5 个（projects/project-intake/three-mode/review/diff-review）、`ui.css` 3 个（projects/review/three-mode）、`workspace.css` 3 个（three-mode/review/diff-review）；`styles.css` 还承载 `:root` 变量（跨文件重复定义中唯一合法的一处）。**只能清死规则，不能删文件** |
| `d05/d06` 之外的 `.sr-*` 体系 | `response-outline.css` 死块 26 个 | 但 `.sr-note`(8 处)、`.sr-mini`、`.sr-align`、`.sr-pkg-card`、`.sr-mount-cell` 等在 `mode-outline.js` 里大量拼装，且 `mode-outline.js:4678` 运行时注入的样式表也用同一前缀 → 逐块确认后再删 |
| `mode-outline.js` 体量（7,722 行） | 像应该拆分 | 它是 `/three-mode` 两个模式之一的**唯一实现**（`three-mode.html:4071` 动态 import）。拆分是重构议题，不是清理项 |

---

## 4 不确定 / 需产品决策项

| 项 | 不确定的点 | 需要谁定 |
|---|---|---|
| D01/D02 两个旧整页（10,542 行） | `docs/design/UI一致性审计.md:306` 承诺「旧页保留重定向，不删文件…`?legacy=1` 可绕过」；`:730` 又说「真正下线需要删文件」。文档自相矛盾，且 `?legacy=1` 实测已被 307 抢占（D08）。删文件是产品动作 | 产品 + 前端负责人 |
| I02 `diff-review.html` 去留 | 后端 diff/修订能力健在，页面也 200，只是没入口。是「该补入口」还是「该报废」取决于“审核导出”是否还在路线图上 | 产品 |
| I03 `bench.html` 归属 | 它是「视频逐帧工作台」独立工具页，与投标系统无业务关系，但目录在 `frontend/`。移目录会影响 `bench_server.py:86` 的路径与既有文档；保留则污染产品资源树 | 工具作者 / 产品 |
| U01 其余 11 个零调用 API 方法 | 后端路由全在。单独删前端会让「客户端方法 = 后端契约」的清单失真；一起删后端则要确认无外部调用方（该后端可能被脚本/第三方调用） | 后端负责人 |
| U05 8 个「仅测试维持」导出 | `parseMode/modeUrl/resolveSelectedBlock/panelVisible` 是「URL 与模式的完整语义模型」，产品代码里已被 `mode-dispatch.js` 的简化版内联实现取代。是「接线」还是「删码+删测试」是设计选择 | 前端负责人 |
| I05 顶栏导航 | `workspace.js:84-85` 注释写「四模式导航整块移除（用户要求）…逻辑需要重新梳理，暂时先不梳理」。这是**已知待重做**项，不是遗漏。删 D19/I06 等于承认“暂时不重做” | 产品（导航体验） |
| I07 内联 CSS 抽取 | 3,343 行内联 + 3 处运行时注入，抽取是纯结构改造但需要三模式逐屏视觉回归，工作量大 | 前端负责人排期 |
| D20-D26 CSS 死规则分批删 | 我的判死基于「标识符 token 语料」，对**运行时拼装的 class 名**（如 `` `cs-todo-ignored-${x}` ``）做了保守容忍；但仍有理论漏网。每批删完需 5 个活页截图对比 | 前端 + QA |

> 基线说明：`node --test tests/frontend/*.test.mjs` → **161 pass / 0 fail**。上述 48 项全部落在测试覆盖之外，删改前后都应保持 161 全绿作为最小回归网。

---

## 5 只读验证命令

全部命令在仓库根 `/Users/jianghe/projects/ge-solution` 执行，均为只读。

**R1 — 不可达页 + 内联脚本/样式重合度**

```bash
# 路由事实
grep -n "FileResponse\|app.mount\|status_code=307" backend/main.py
# → :292 只挂 /assets；:342 /:365 两处 307；无任何 FileResponse 指向 workbench.html / project-init.html

# 边界
grep -n "<script\|</script>\|<style\|</style>" frontend/workbench.html frontend/project-init.html

# 逐函数 / 逐行重合度（python3，输出见 §2.1 表格）
python3 - <<'PY'
import re, pathlib
def L(p,a,b): return pathlib.Path(p).read_text(encoding='utf-8').splitlines()[a-1:b]
wb="\n".join(L('frontend/workbench.html',2504,5224)); pi="\n".join(L('frontend/project-init.html',1099,4959))
me=pathlib.Path('frontend/assets/mode-editor.js').read_text(encoding='utf-8')
mo=pathlib.Path('frontend/assets/mode-outline.js').read_text(encoding='utf-8')
def bodies(t):
    o={}
    for m in re.finditer(r'(?m)^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{',t):
        i=m.end()-1;d=0;j=i
        while j<len(t):
            if t[j]=='{':d+=1
            elif t[j]=='}':
                d-=1
                if d==0:break
            j+=1
        o.setdefault(m.group(1),[]).append(re.sub(r'\s+',' ',t[i:j+1]))
    return o
for lab,inline,mod in [('wb',wb,me),('pi',pi,mo)]:
    a,b=bodies(inline),bodies(mod); sh=set(a)&set(b)
    print(lab,len(a),len(b),len(sh),sum(1 for n in sh if a[n][0]==b[n][0]))
def stats(inline, asset):
    s=set(re.sub(r'\s+',' ',l.strip()) for l in asset.splitlines())
    skip=re.compile(r'^(|\{|\}|\);?|\}\);?|\)|else \{|try \{|\} catch.*|//.*|/\*.*)$')
    c=[re.sub(r'\s+',' ',l.strip()) for l in inline.splitlines()]
    c=[x for x in c if not skip.match(x)]
    return len(c), sum(1 for x in c if x in s)
print(stats(wb.splitlines(), me)); print(stats(pi.splitlines(), mo))
PY
```

**R2 — 每个 assets 文件的引用方（确认无整文件零引用）**

```bash
for f in frontend/assets/*.js frontend/assets/*.mjs frontend/assets/*.css; do
  b=$(basename "$f")
  echo "### $b"
  grep -rlF "$b" frontend/ tests/ --include=*.html --include=*.js --include=*.mjs --include=*.css | grep -v "^frontend/assets/$b$"
done
# 结论：24 个文件各至少 1 个引用方；传递闭包（含 src/link/import()/字面量数组）覆盖全部 24 个
```

**R3 — 零引用导出（定义 1 处 + 全仓 grep）**

```bash
for s in setSelectedRow currentMode REVIEW_PATH materialRoleLabels materialRoleHints \
         EVIDENCE_SOURCE_LABELS CHAPTER_STAGES exportScope MODE_LABEL parseMode modeUrl \
         resolveSelectedBlock panelVisible evidenceSliceTitle evidenceByFile; do
  echo "=== $s ==="; grep -rn "\b$s\b" frontend/ tests/ --include=*.html --include=*.js --include=*.mjs
done
# setSelectedRow / currentMode / REVIEW_PATH / materialRoleLabels / materialRoleHints /
# EVIDENCE_SOURCE_LABELS / CHAPTER_STAGES → 各 1 处（定义）
# exportScope / MODE_LABEL / parseMode / modeUrl / resolveSelectedBlock / panelVisible /
# evidenceSliceTitle / evidenceByFile → 定义 1 处 + tests/frontend/*.test.mjs
```

**R4 — CSS 死规则（规则块扫描 + 动态 class 前缀容忍）**

```bash
# 关键点：语料必须用「HTML 剥掉 <style> + JS/MJS 标识符」，否则内联 CSS 的选择器会被误当成元素引用
python3 - <<'PY'
import re, pathlib, bisect
tokens=set()
for p in pathlib.Path('frontend').rglob('*'):
    if not (p.is_file() and p.suffix in ('.html','.js','.mjs')): continue
    s=p.read_text(encoding='utf-8'); s=re.sub(r'<style[\s\S]*?</style>',' ',s)
    for t in re.findall(r'[A-Za-z_][\w-]*', s): tokens.add(t)
CLASS=re.compile(r'\.(-?[_a-zA-Z][\w-]*)'); ID=re.compile(r'#(-?[_a-zA-Z][\w-]*)')
def parse(path):
    src=path.read_text(encoding='utf-8')
    src=re.sub(r'/\*[\s\S]*?\*/', lambda m:'\n'*m.group(0).count('\n'), src)
    nl=[i for i,c in enumerate(src) if c=='\n']; L=lambda p: bisect.bisect_left(nl,p)+1
    out=[];d=0;ss=0;cur=None
    for i,c in enumerate(src):
        if c=='{':
            if d==0:
                cur=src[ss:i].strip();k=ss
                while k<i and src[k] in ' \t\r\n':k+=1
                cur=(cur,L(k))
            d+=1
        elif c=='}':
            d-=1
            if d==0: out.append((cur[0],cur[1],L(i)-cur[1]+1));ss=i+1
    return out
TOT=0
for f in sorted(pathlib.Path('frontend/assets').glob('*.css')):
    rs=parse(f);dead=[]
    for sel,ln,n in rs:
        if not sel or sel.startswith('@'): continue
        sels=[x.strip() for x in sel.split(',') if x.strip()]
        ok=True
        for s in sels:
            tk=CLASS.findall(s)+ID.findall(s)
            if not tk or any(t in tokens for t in tk): ok=False;break
        if ok: dead.append((ln,n))
    TOT+=sum(x[1] for x in dead)
    print(f"{f.name:24} dead={len(dead):<4} lines={sum(x[1] for x in dead)}")
print("TOTAL", TOT)
PY
# 输出：chapter-studio.css 354/368 · response-outline.css 26/33 · review.css 54/276
#       styles.css 208/1217 · ui.css 1/1 · workspace.css 14/14 · TOTAL 1909
```

**R5 — `api.js` 方法与后端路由对照**

```bash
for m in createPackage projects.patch projects.lock materials.outline mapSources \
         "blocks.update(" "blocks.ai" updateRequirement "revisions.list" "revisions.restore" \
         getSplitAnswers "review.get"; do
  echo -n "$m : "; grep -rnF "$m" frontend/ --include=*.html --include=*.js --include=*.mjs | grep -v "assets/api.js" | wc -l
done
# 全部 0（"revisions.list" 的唯一命中是 revisions.listByProject 的子串，非本方法）

grep -n "410" backend/routes/*.py            # → materials.py:678,682 唯一一处
sed -n '674,684p' backend/routes/materials.py # → 函数体只有 raise HTTPException(410, ...)
grep -n "@router" backend/routes/{packages,projects,blocks,revisions,review}.py
grep -n "@router" backend/routes/review.py   # → :19 @router.get("/{thread_id}")，prefix /review
```

**R6 — `?v=` 版本契约盲区**

```bash
grep -rhoE '\?v=[A-Za-z0-9._-]+' frontend/ --include=*.html --include=*.js --include=*.mjs | sort | uniq -c
# → 68 处，全部 20261016-v503（一致性 OK）

python3 - <<'PY'
import re, pathlib
tc=pathlib.Path('tests/frontend/module-imports.test.mjs').read_text(encoding='utf-8')
listed=set(re.findall(r"'([\w.-]+\.(?:html|js|mjs|css))'", tc))
have=set()
for p in list(pathlib.Path('frontend').glob('*.html'))+list(pathlib.Path('frontend/assets').glob('*.js'))+list(pathlib.Path('frontend/assets').glob('*.mjs')):
    if re.search(r'\.(?:js|mjs|css)\?v=', p.read_text(encoding='utf-8')): have.add(p.name)
print("未覆盖:", sorted(have-listed))
PY
# → ['chapter-studio.mjs','chapter-tree.mjs','intake.js','project-intake.html','workspace-model.mjs']
```

**R7 — 旧审计已知项复核**

```bash
# workbench/project-init 不可达
grep -n "status_code=307" backend/main.py                       # :342, :365

# context-panel no-op
grep -rn "data-panel-requirement\|data-panel-sources" frontend/ tests/ backend/
grep -rn "__materialsMap" frontend/ tests/
grep -c "editor-block" frontend/three-mode.html                  # 0
grep -rn "initContextPanel" frontend/                            # mode-editor.js:8,25 + 定义

# mode-outline 死函数
for s in readConfirmedPkg renderPackageCards renderPointsAlignment \
         confItem statusCell rpRows notGenerated unitRows scopeRows \
         currentReader pendingRenameId lastRunErrorMessage; do
  echo -n "$s="; grep -rn "\b$s\b" frontend/ tests/ --include=*.html --include=*.js --include=*.mjs | wc -l
done
# 全部 1（仅定义）

# styles.css 死规则（旧审计点名的块逐条确认）
for c in page-shell gallery-page search-shell view-toggle list-view brand-lockup \
         gallery-hero context-pane; do
  echo -n "$c="; grep -rn "\b$c\b" frontend/ --include=*.html --include=*.js --include=*.mjs | wc -l
done
# 全部 0

# diff-review 孤儿页
grep -rn "diff-review" frontend/ --include=*.html --include=*.js --include=*.mjs
# → diff-review.html:17（自链接）、workspace.js:57（注释）

# 死页生成 /workbench、/project-init 链接
grep -rn "/workbench\|/project-init" frontend/ --include=*.html --include=*.js --include=*.mjs
# → 生成链接只在两个死页内；活模块仅注释

# bench
grep -rn "bench" backend/routes/*.py backend/main.py             # 0 条真实路由
git ls-files scripts/bench_server.py                             # 空（未跟踪）
sed -n '84,88p' scripts/bench_server.py                          # @app.get('/bench') → FileResponse(bench.html)

# 页面路由零测试
grep -rn "307\|redirect" tests/routes/*.py                       # 0
grep -rln "workbench\|project-init\|three-mode\|project-intake\|diff-review" tests/ --include=*.py
# → 无（仅 test_leads.py 里 "/lp" 作 referer 字符串）
```

**R8 — 测试基线（只读）**

```bash
node --test tests/frontend/*.test.mjs
# → tests 161 / pass 161 / fail 0（node v26，本轮实测）
```
> 基线全绿说明上述 48 项里**没有一项**表现为测试失败 —— 它们全部落在测试覆盖之外的缝隙（不可达页、零调用导出、CSS、路由、契约白名单）。这正是「测试绿 ≠ 代码干净」的直接证据。

---

## 附：与旧审计（2026-10-19）的差分

| 旧 ID | 旧结论 | 本轮 |
|---|---|---|
| A1/A2 | 两个旧整页不可达 | **仍存在**，行号不变（文件未改动） |
| A3 | `?legacy=1` 逃生口失效 | **仍存在**，且证实全仓无任何 URL 会带 `legacy=1` |
| A4/A5 | inline 是活模块快照 | **仍存在，漂移扩大**：函数体重合 76% / 68%；活模块独有函数 5 / **67** |
| A6 | 死页内联 CSS 是 three-mode 的拷贝 | **仍存在**：选择器重合 427/429、327/327 |
| A7/A8/A9 | 弹窗 IIFE / `toggleRefCard` 空壳 / CDN | **仍存在**（`workbench.html:5326-5577`、`:5228`、`:2057`） |
| A10 | 死页生成旧链接 | **仍存在**，但已完全隔离在死页内（活模块仅剩注释） |
| A11 | 版本号维护成本 | **仍存在**：68 处，其中 15 处在死页 |
| B1 | mode-outline 3 个死函数 | **仍存在**（`:2833,:2981,:3013`）；旧行号 `2827/2975/3007` 已漂 |
| B2 | 6 个死局部 helper | **仍存在**（`:4056,:4142,:4147,:4218,:4229,:4273`） |
| B3 | `currentView` / `inPlace` 死 | **仍存在**（`workspace.js:47,83`） |
| B4 | `setSelectedRow` 零引用导出 | **仍存在**（`chapter-tree.mjs:146`；旧审计记 `:146-151`） |
| B5 | `currentMode` / `setMode` 导出无外部 import | **仍存在**（`mode-dispatch.js:29` `setMode`、`:48` `currentMode`） |
| B6 | diff-review `fmtTime` 死 | **仍存在**（`diff-review.html:156`） |
| B7 | 9 个未使用具名导入 | **仍存在**，逐符号一致 |
| B8 | 测试维持的 API | **仍存在**，且新增 `exportScope` / `evidenceSliceTitle` / `evidenceByFile` 三个 |
| B9 | 11 个零调用 api 方法 | **仍存在，11→12**（新增 `materials.outline`，且它是唯一指向 410 的） |
| B10 | context-panel no-op | **仍存在**，证据加强（`.editor-block` 在活页 0 次、`__materialsMap` 从未赋值） |
| B11 | `_index_path` 死变量 | **仍存在**，`main.py:246` → `:272` |
| B12 | `frontend/.DS_Store` | **仍存在**（未跟踪，`.gitignore:16` 已忽略） |
| C1-C5 | 死 CSS 块 | **仍存在**，规模收缩：styles 177→208 块（行 1,394→1,217）、review 55→54、response-outline 26→26、workspace 14→14、three-mode 内联 85→83 |
| C6 | 同文件重复选择器 | **仍存在，扩大**：268 次多定义（旧审计称 43 条冗余） |
| C7 | `#workspace-nav` 契约失效 | **仍存在**（`workspace.css:2-5` + `mode-dispatch.js:98-103`） |
| C10 | 跨文件重复 class | **仍存在**：27 个完整选择器跨文件重复 |
| D3 | projects.html 与 mode-outline 模型弹窗重复 | **仍存在**：8 个同名函数 |
| E1/E2/E3 | God module / 内联巨量 CSS | **仍存在**（mode-outline 7,722 行、mode-editor 2,848 行、three-mode 内联 CSS 3,343 行） |
| E7 | `?v=` 维护成本 | **仍存在**（68 处） |
| F1 | 页面路由零测试 | **仍存在**（`tests/routes/` 下无任何 307/redirect 断言） |
| F2 | 版本契约数组含死页、漏活页 | **仍存在，5 个盲区字面一致** |
| F3 | workbench-modes 测试保护假行为 | **仍存在**（8 个测试围绕 4 个产品零引用导出） |
| F5 | diff-review 孤儿页 | **仍存在** |
| **新增** | — | **I03 `bench.html` 不属产品路由且依赖未跟踪脚本**（旧审计未列） |
