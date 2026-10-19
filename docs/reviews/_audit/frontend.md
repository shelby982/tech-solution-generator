# 前端审计

- 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`
- 审计对象：**当前工作区**（19 个未提交修改文件，见 §0.3）
- 只读审计：未修改任何项目文件；仅写入本报告
- 审计时间：2026-10-19
- 基线测试：`node --test tests/frontend/*.test.mjs` → **160 passed, 0 failed**（`node v26.5.1`）

## 0. 结论摘要

### 0.1 数量与规模

| 类别 | 数量 | 规模估算 |
|---|---|---|
| DEAD（整页不可达 / 活模块内死函数 / 死 CSS 块 / 空壳） | 15 项 | 约 2,900 行（不含旧页）+ 10,542 行（两个旧整页） |
| LEGACY（已被新实现取代但仍留仓库） | 4 项 | 约 6,600 行（含在两个旧整页里） |
| DUPLICATE（内联 ↔ 模块、模块 ↔ 模块、CSS 规则重复） | 10 项 | 约 3,000 行重复；可收敛约 200–300 行 |
| UNUSED-API（导出/导入/客户端方法无产品引用） | 5 项 | 约 100 行 + 11 个 API 方法 |
| JUNK（空壳、外部 CDN 遗留、.DS_Store、无引用变量、体积异常） | 12 项 | 约 10 行 + 1 个垃圾文件；God module 7,725 行 / 内联 CSS 3,342 行 |
| INVALID-CONTRACT / TEST-CONTRACT（失效路由、失效测试契约） | 6 项 | — |

**总发现数：52 项**（§1 表格逐项列出；同一根因合并成一项，涉及文件/行号明细见 §2）。

### 0.2 风险分布与可删行数

| 风险 | 项数 | 可删行数（估算） |
|---|---|---|
| `safe` | 19 | 约 450 行（死函数 174 + 未用导入/导出约 40 + 死 API 方法 30 + JUNK 约 10 + 测试契约修正） |
| `needs-check` | 21 | 约 2,450 行（CSS 死规则 2,476 行中扣掉需视觉回归确认的部分；含 `context-panel.js` 70 行、api 方法） |
| `product-decision` | 12 | 10,542 行（两个旧整页）+ `diff-review.html` 331 行 |

**总量估算**：
- 若**保守清理**（只删 safe/needs-check 且不动旧页）：约 **2,600–3,000 行**（前端 34,284 行的 8–9%）。
- 若**产品确认删除两个旧整页**：约 **13,100–13,500 行**（前端 34,284 行的 38–39%），同时 `?v=` 版本号维护点从 68 处降到约 53 处。

### 0.3 当前工作区状态（审计基于此）

```
git status --short  → 19 个 M（全部在 frontend/）
git diff --stat     → 70 insertions(+), 100 deletions(-)
```
- 15 个文件的改动仅为 `?v=20261016-v228` → `?v=20261016-v229`（68 处引用现已统一为 v229，`module-imports.test.mjs` 的版本一致性测试通过）。
- `frontend/assets/chapter-studio.mjs`（-33 行）、`frontend/assets/chapter-studio.css`（-7 行）是**实质性回滚**：把 v228 的「章节类型行右侧篇幅」(`kindBudgetHtml/paintKindBudget` + `.cs-kind-budget*` 规则) 删掉了。
- `frontend/workbench.html`、`frontend/project-init.html` 的未提交差异**全部是版本号**，没有任何逻辑变更 —— 这是 §1 A11 的维护成本证据。

### 0.4 最重要的一句话结论

`frontend/workbench.html`（5,580 行）与 `frontend/project-init.html`（4,962 行）在**当前路由下已 100% 不可达**（`backend/main.py:301-307`、`:316-322` 均为 307 跳转，且前端目录只静态挂载了 `/assets`，见 `main.py:256`），但两者仍以**整页内联脚本 + 内联 CSS** 的形式，各自重复保存了一份 `mode-editor.js` / `mode-outline.js` 的历史快照，并且**每次发版都要跟着改版本号**（最近 4 个功能提交均如此）。删除这两个文件是本次审计收益最大的一项，需产品确认。

---

## 1. 逐项清单

> 证据均为本次在当前工作区实际执行的 `grep -rn` / Python 静态分析 / `git log` / `wc -l` 结果；行号对应当前工作区文件。
> 「规模」= 涉及行数/文件大小；「风险」：`safe` = 可直接删（有测试或引用为零）；`needs-check` = 需浏览器/视觉或耦合确认；`product-decision` = 需产品/负责人拍板。

| ID | 文件:行 | 类别 | 规模 | 证据（引用检查方式与结果） | 风险 | 建议动作 | 验证方式 |
|---|---|---|---|---|---|---|---|
| A1 | `frontend/workbench.html` 全文件 | DEAD | 5,580 行 / 245,006 B | 全仓路由表无任何 `FileResponse` 指向它：`grep -n "@app.get" backend/main.py` = `/`、`/three-mode`、`/projects`、`/workbench`、`/diff-review`、`/project-init`、`/project-intake`、`/review`；`/workbench` 是 307→`/project-intake`（main.py:301-307）；`grep -c "app.mount" main.py` = 1，只挂 `/assets`（:256） | product-decision | 整页删除（产品确认后）；同时删测试引用（F2）与文档说明（F4/F6） | 起服务后 `curl -I /workbench` 应 307；`grep -rn "workbench.html" frontend/ tests/ backend/` 归零；`node --test tests/frontend/*.test.mjs` |
| A2 | `frontend/project-init.html` 全文件 | DEAD | 4,962 行 / 239,409 B | 同上；`/project-init` 307→`/project-intake`（main.py:316-322） | product-decision | 整页删除 | 同上；`curl -I /project-init` 应 307 |
| A3 | `workbench.html:2061-2071`；`project-init.html:895-905` | LEGACY | 22 行 | 页内自跳 `/three-mode`（`location.replace('/three-mode?'+q)`），并留 `?legacy=1` 绕过。但页面本身已不可达，脚本永不执行；`?legacy=1` 已被后端 307 抢占 → **逃生口失效** | safe（随 A1/A2） | 随整页删除 | 删除后 `grep -rn "legacy=1" frontend/` 归零 |
| A4 | `workbench.html:2504-5224` | LEGACY + DUPLICATE | 2,721 行 | 与 `mode-editor.js` 对比：inline 55 个 `function` 声明**全部**存在于 mode-editor.js（重叠 55/55），而 mode-editor.js 另有 5 个（`insertNotice`、`isMergedPage`、`loadPointMeta`、`renderChapterPoints`、`switchBlockDiscarding`）；非空行逐行比对 **2,186/2,278 行完全一致（94.4%）** → 机械抽取前的历史快照，且已落后 | product-decision（随 A1） | 随整页删除；模块侧 `mode-editor.js` 为唯一实现 | 删除后 `grep -rn "GenerateSession\|initContextPanel" frontend/` 仅剩 `mode-editor.js` 与模块自身（见 §3 排除项） |
| A5 | `project-init.html:1099-4959` | LEGACY + DUPLICATE | 3,861 行 | inline 138 个 `function` 声明全部存在于 `mode-outline.js`（138/138），mode-outline.js 另有 67 个；138 个同名函数中 **94 个函数体逐字相同** → 历史快照 | product-decision（随 A2） | 随整页删除 | 同 A4 |
| A6 | `workbench.html:8-2055`（2,048 行）；`project-init.html:8-891`（884 行） | DUPLICATE | 2,932 行 | CSS 选择器扫描：workbench 429 个选择器中 **427 个**已在 `three-mode.html` 内联 `<style>`（10-3352）；project-init **327/327** 全部已在 three-mode | product-decision（随 A1/A2） | 随整页删除；`three-mode.html` 为唯一合并实现 | 删除后 `three-mode.html` 视觉回归（三模式各截一张） |
| A7 | `workbench.html:5326-5577` | DUPLICATE + DEAD | 252 行 | 「模型选择器 / API 配置弹窗」IIFE，功能已由 `mode-outline.js:514-615` 承担（`loadModels/renderDefaultModels/bindModelOptions/openApiModal/closeApiModal/resetApiForm`） | product-decision（随 A1） | 随整页删除 | `grep -n "escModelHtml" frontend/` 仅剩旧页（当前已是 0 处活引用） |
| A8 | `workbench.html:5226-5229` | JUNK | 4 行 | `function toggleRefCard(id) {}`，注释「保留空壳避免旧代码报错」；`grep -rn "toggleRefCard" frontend/ tests/` = 1（仅定义） | safe（随 A1） | 随整页删除 | 同上 |
| A9 | `workbench.html:2057` | JUNK | 1 行 | 外部 CDN `marked@12.0.0`；`grep -rn "marked" frontend/` 仅在旧页。三模式页无预览 Markdown 依赖 | safe（随 A1） | 随整页删除，前端不再有外网 CDN 依赖 | `grep -rn "cdn.jsdelivr" frontend/` 归零 |
| A10 | `project-init.html:1468-1470, 4666, 4694`；`workbench.html:2708-2710, 2854, 2874` | INVALID-CONTRACT | 10 行 | 死页内仍生成 `/project-init?...`、`/workbench?...` 链接（旧契约）；活代码已全部改 `/three-mode`：`mode-outline.js:482-483`、`mode-editor.js:228-230,374,393`、`workspace.js:39-41` | product-decision（随 A1/A2） | 随整页删除；不留半旧契约 | `grep -rn "/project-init\|/workbench" frontend/ --include=*.js --include=*.mjs` 应为 0 |
| A11 | 两个旧页 + 测试 | JUNK（维护成本） | 15 处 `?v=` | `git show --name-only` 显示最近 4 个功能提交（`4a6f627`、`fea5868`、`65e0407`、`98a2ade`）**都同时改** `workbench.html`、`project-init.html`、`three-mode.html`、`mode-editor.js`、`mode-outline.js`；`git show 4a6f627 -- frontend/workbench.html` 的 18 行差异**全部是 `?v=` 版本号**，0 行逻辑 | safe（随 A1/A2） | 删页后版本号维护点 68→53 | 统计 `grep -rc "?v=" frontend/*.html` |
| B1 | `mode-outline.js:2827-2833, 2975-3004, 3007-3019` | DEAD | 50 行 | `readConfirmedPkg` / `renderPackageCards` / `renderPointsAlignment`：`grep -rn "readConfirmedPkg\|renderPackageCards\|renderPointsAlignment" frontend/ tests/` 各仅 1 处（定义本身） | safe | 删除三函数（标包分析 UI 已迁 `project-intake`/`/three-mode` 的对应实现） | `node --test tests/frontend/*.test.mjs`；`node --check` 不可用（.js + ESM），用 `grep` 复核零引用 |
| B2 | `mode-outline.js:4049-4133, 4135-4138, 4140-4141, 4211-4213, 4222-4223, 4266-4274` | DEAD | 105 行 | `openSplitReport()`（3892-5616，1,725 行）内定义的局部 helper：`confItem`(85) `statusCell`(4) `rpRows`(2) `notGenerated`(3) `unitRows`(2) `scopeRows`(9)；全仓 grep 各仅 1 处 | safe | 删除这 6 个局部 helper（渲染分支已不在函数内被引用；建议顺带用 eslint `no-unused-vars` 兜底） | `grep -rn "confItem\|rpRows\|unitRows\|scopeRows" frontend/ tests/` 归零 |
| B3 | `workspace.js:47-51, 83` | DEAD | 6 行 | `currentView()` 与 `const inPlace = ...`：全仓 grep 各 1 处（定义本身）；且 `canvas` 分支/**四模式导航已被用户要求整块移除**（workspace.js:84-92 `nav.innerHTML=''; nav.hidden=true`） | safe | 删除两处；`inPlace` 相关分支若随导航重做再引入 | grep 归零；顶栏渲染回归（review/three-mode/diff-review 各一张） |
| B4 | `chapter-tree.mjs:146-151` | UNUSED-API | 6 行 | `export function setSelectedRow(...)`：`grep -rn "setSelectedRow" frontend/ tests/` = 1（定义）；同模块 `chapterNode/renderChapterTree/chapterBadgesHtml` 均被 3 个活模块使用 | safe | 删除导出（或若确为预留 API，加注释并补使用点） | grep 归零 |
| B5 | `mode-dispatch.js:48-50`（及 `setMode` 的导出） | UNUSED-API | 3 行 | `currentMode` 全仓 1 处；`setMode` 作为导出无外部 import（仅模块内 `init()` 的 popstate 用，:106） | safe | `currentMode` 删除；`setMode` 去掉 `export`（保留内部调用） | grep；浏览器前进/后退切模式回归 |
| B6 | `diff-review.html:156-164` | DEAD | 9 行 | `fmtTime(iso)` 在全页零调用（同文件 `timeKey`、`groupByTime` 都在用，只有它没有） | safe | 删除函数 | grep；页面「修订链」渲染回归 |
| B7 | `mode-editor.js:23-24`；`workbench.js:3`；`mode-outline.js:24,30,32` | UNUSED-API | 9 个符号 | 静态扫描「import 后全文再无该标识符」：`evidenceSourceLineHtml`、`evidenceSnippet`、`chapterStage`、`CHAPTER_STAGE_LABELS`（mode-editor）；`chapterBadges`（workbench.js）；`constraintLevelLabel`、`groupEvidenceByItem`、`itemCode`、`chapterBadges`（mode-outline）→ 均只有 import 行 1 处命中 | safe | 从 import 列表删除这 9 个符号 | grep 每个符号应只剩定义/其它无关命中；`node --test tests/frontend/*.test.mjs` |
| B8 | `workbench-modes.mjs:19,26,29,46,58,75,89`；`workspace-model.mjs:6,541,559,621,849` | UNUSED-API | 约 35 行 | **产品零引用、仅测试引用**：`parseMode/modeUrl/resolveSelectedBlock/panelVisible` 只出现在 `tests/frontend/workbench-modes.test.mjs`；`MODE_LABEL`、`MODE_PANEL`、`REVIEW_PATH` 全仓仅定义；`CHAPTER_STAGES` 全仓仅定义（定义处之外 0 命中）；`materialRoleLabels/materialRoleHints` 仅再导出行；`EVIDENCE_SOURCE_LABELS` 仅模块内部 :669 用（导出多余）；`evidenceSliceTitle/evidenceByFile` 只被 `tests/frontend/evidence-view.test.mjs` 用 | needs-check | 分两类：①产品真死（`REVIEW_PATH`、`CHAPTER_STAGES`、两个 role 别名）直接删；②测试维持的 API（4 个 mode 函数、2 个 evidence 函数）要么补产品调用，要么删函数+测试（见 F3） | 删后 `node --test`；`grep -rn "REVIEW_PATH\|CHAPTER_STAGES\|materialRoleLabels" frontend/ tests/` 归零 |
| B9 | `api.js:44,95,101,164,173,210,216,225,227,287,441` | UNUSED-API | 11 个方法 / 约 30 行 | 按 `api.<group>.<method>(` 精确引用扫描（排除 api.js 自身）：`projects.createPackage/patch/lock`、`materials.mapSources`、`blocks.update/ai/updateRequirement`、`revisions.list/restore`、`workflow.getSplitAnswers`、`review.get` 全仓零调用。注意：**后端对应路由仍存在**（`blocks.py:196/312`、`revisions.py:28/68`、`materials.py:180/587`、`projects.py:164/177`、`packages.py:188`、`review.py:19`） | needs-check | 与后端契约一起决策：要么前端补用，要么前后端同步删除；单独删前端会让「客户端方法 = 后端契约」清单失真 | 逐个 `grep -rn "api\.<group>\.<method>(\?" frontend/ tests/`；后端路由 `grep` 对照 |
| B10 | `context-panel.js:1-70`；调用点 `mode-editor.js:8,25` | DEAD（运行时 no-op） | 70 行 | 模块只做 `document.querySelector("[data-panel-requirement]")` / `"[data-panel-sources]"`，但 `grep -rn "data-panel-requirement\|data-panel-sources" frontend/` **只在 context-panel.js 内命中**；`git log -S "data-panel-requirement" -- frontend/` 显示最后一次出现是 `2026-05-31 31393a5`（之后 DOM 被删）。`initContextPanel()` 在 mode-editor.js:25 顶层调用 → 每次都执行、每次都拿到 null 直接 return | needs-check | 删除 `context-panel.js` + `mode-editor.js` 的 import/调用（三模式右栏上下文由 mode-editor/mode-outline 自己渲染）；若产品要恢复该面板，则补回 DOM 挂点 | 删除后三模式页 `--editor` 右栏回归；`grep -rn "initContextPanel" frontend/` 归零；浏览器 console 无报错 |
| B11 | `backend/main.py:246` | JUNK | 1 行 | `_index_path = os.path.join(frontend_dir, "index.html")`：`grep -rn "_index_path" --include=*.py .` 仅此 1 处（定义后从未使用）；且 `ls frontend/index.html` = No such file | safe | 删除该变量（不新增 index.html；`/` 已由 `projects.html` 承担，main.py:279-283） | `grep -rn "_index_path" backend/` 归零；`GET /` 仍返回项目列表 |
| B12 | `frontend/.DS_Store` | JUNK | 1 文件 / 6,148 B | `git ls-files frontend \| grep -i ds_store` 为空（未跟踪）；`ls -la frontend/.DS_Store` 存在 | safe | 删除；确认 `.gitignore` 已含 `.DS_Store` | `ls frontend/.DS_Store` 不存在；`git status --short` 无变化 |
| C1 | `styles.css` 177 个整块死规则 | DEAD | 约 1,394 行 | 规则级扫描（HTML 内联 CSS 与 JS 均计入引用语料）：完全无引用的整块如 `.page-shell`(:62-66)、`.gallery-page`(:66-70)、`.search-shell`(:100-113)、`.view-toggle`(:113-124)、`.list-view`(:135-139)、`.brand-lockup`(:186-192)、`.gallery-hero`(:322-329)、`.project-card`(:353-388)；`.context-pane` 在 :1773/:1990/:2099/:2114/:2391/:2398/:2419 **共 7 次定义，全部无元素引用** | needs-check | 分批删：先删「历史项目列表页」整段（`.page-shell/.gallery-*/.list-*/.search-*`），再删 `.context-pane/.editor-*/.block-*` 等旧 workbench 布局段；每批做 5 个活页视觉回归 | `grep -rn "class=\"[^\"]*page-shell"` 等逐类确认；浏览器截图对比（projects/three-mode/review/project-intake/diff-review） |
| C2 | `review.css` 55 个整块死规则 | DEAD | 约 381 行 | 示例：`.review-content`(:17-27)、`.thread-id-display`(:27-33)、`.review-banner`(:33-47)、`.banner-*`(:47-79)、`.review-recover`(:85-100)、`.review-actions/.actions-*`(:100-124)、`.review-stage/.stage-*`(:159-176)、`.review-grid`(:176-184)、`.card-head/.card-checkbox/.card-title/.card-scores`(:207-235)、`.score-pill*`(:235-260)、`.card-section*`(:260-273)、`.issue-list/.severity-tag`(:273-295) | needs-check | 删除死块；review.css 从 628 行可降到约 250 行。删前确认 review.html/review.js 实际使用的 71 个 class（扫描结果已列） | 浏览器打开 `/review?...` 截图对比；`grep -rn "review-banner\|review-grid\|score-pill" frontend/` |
| C3 | `response-outline.css` 26 个整块死规则 | DEAD | 约 63 行 | `.sr-btn/.sr-btn-primary`(:1-4，注意 `.sr-btn` 在 `ui.css` 也定义但同样未被使用)、`.sr-mount`(:32-36)、`.sr-two*/.sr2-*`(:48-63)、`.sr-checks/.sr-check`(:95-99)、`.sr-echo*`(:102-105)、`.sr-struct-row/.sr-struct-title`(:107-112)、`.sr-req-table .sr-group/.sr-req-*`(:117-129)、`.spec-attention-hint`(:127-128) | needs-check | 删除死块（141 行 → 约 78 行）；与 chapter-studio.css/ui.css 的 `.spec-source-text/.spec-table-scroll` 重复定义一并合并 | `three-mode.html` 的「要求与大纲 / 素材库」截图对比 |
| C4 | `workspace.css` 14 个整块死规则 | DEAD | 约 29 行 | `.ws-count`(:5-6)、`.ws-status`(:6-7)、`.ws-state-copy`(:7-8)、`.ws-status-actions`(:9-12)、`.ws-outline-tools`(:45-47)、`.ws-editor-review`(:47-51，:59-62 重复定义) —— 共享壳状态条已被移除（workspace.js:96-101 注释「共享壳状态条已按原型移除」） | needs-check | 删除死块；保留 `#workspace-nav` 的处置见 C7 | `grep -rn "ws-status\|ws-state-copy\|ws-outline-tools" frontend/`；5 个活页顶栏截图 |
| C5 | `three-mode.html:10-3352` 内联 CSS 中 85 个整块死规则 | DEAD | 约 648 行 | 规则级扫描，示例：`:101-109 .status-bar-tools`（源码注释自己写着「已挪走，保留选择器以防残留引用」）、`:109-135 .materials-panel-header/.app-logo-sm/.app-title/.project-name-sm`、`:181-193 .mat-role-badge*`、`:932-946 .init-sidebar/.sidebar-progress`、`:1337-1356 .btn-mark-done`、`:1483-1516 .ref-section*`、`:1690-1765 .block-detail-*` 等 | needs-check | 删除死块（先把 3,342 行内联 CSS 迁出为 `assets/three-mode.css` 再清理，可读性收益更大） | `three-mode.html` 三模式 + 编辑器抽屉逐屏截图比对 |
| C6 | `styles.css` / `three-mode.html` / `review.css` / `response-outline.css` / `workspace.css` | DUPLICATE | 55 条冗余声明 | 同一文件内选择器被重复声明：`styles.css` 35 个选择器（`.editor-block`×3、`.editor-block h1`×3、`.tiptap-shell`×3、`.block-history`×3、`.history-item`×3…共 43 条冗余）；`three-mode.html` 9 个（`.btn-add-mat`、`.extract-hint`、`.model-selector-wrap`、`.api-modal-backdrop`、`.stage-progress`、`.doc-outline-item`…）；`review.css` 2（`.card-head/.card-title`）；`response-outline.css` 1（`.sr-struct-edit`）；`workspace.css` 1（`.ws-editor-review`） | safe（合并同选择器声明，不改视觉） | 合并为单条声明（后者覆盖前者的字段合并）；每文件一次提交 | 合并前后 CSS 文本 diff 只应删除重复属主；截图对比 |
| C7 | `workspace.css:2-5`（`#workspace-nav` 及 `a` 的 4 条）、`:29`、`:45`、`:56`；`styles.css:470-480`（`.workspace-nav`） | DEAD（契约失效） | 约 10 行 | `workspace.js:84-92` 主动 `nav.innerHTML=''` + `nav.hidden=true`，注释「四模式导航**整块移除**（用户要求）」；唯一会输出 `a[data-mode]` 的 `renderWorkspaceChrome()`（workspace.js:77-92）已不产出链接；`mode-dispatch.js:98-103` 的点击委托因此永不触发 | needs-check（导航「逻辑待重新设计」，见 §4） | 二选一：①保留 `#workspace-nav` 容器但删 `a` 相关样式与 `mode-dispatch` 点击委托；②导航重做时一起恢复。先记录为「已知死代码，等导航重做」 | `grep -rn "data-mode\]" frontend/`；三模式页顶栏截图 |
| C8 | `styles.css` 的 `.context-pane`（7 处）、`ui.css:171`、`workspace.css:61-62` | DEAD | 含在 C1/C4 计数内 | `grep -rn "context-pane" frontend/` 仅命中 CSS 与模块文件名 `context-panel.js`（不是同类名）；HTML/JS 无任何元素带 `.context-pane` | needs-check | 删 `.context-pane` 规则（若 B10 不恢复该面板） | `grep -rn "context-pane" frontend/*.html frontend/assets/*.js` 归零 |
| C9 | `chapter-studio.css:2,6`（5 块） | DEAD | 5 行 | `.cs-insert/.cs-suggestion/.ct-steps` 无引用；另当前未提交 diff 已删 `.cs-kind-budget*` 7 行（回滚 v228 功能） | safe | 删除 3 个类对应规则 | `grep -rn "cs-insert\|cs-suggestion\|ct-steps" frontend/` |
| C10 | 6 个 CSS 文件之间 | DUPLICATE | 40 个 class | 跨文件重复定义：`.sr-btn/.sr-btn-primary`（response-outline.css + ui.css）、`.ws-count/.ws-dialog/.ws-button/.ws-chapter-status/.ws-primary`（ui.css + workspace.css）、`.workspace-shell/.editor-pane/.outline-pane/.project-header/.context-pane`（styles.css + workspace.css / review.css）、`.v-*/.quality-*/.bad/.btn-primary/.btn-danger`（review.css + ui.css） | needs-check | 确立单一来源：`ui.css` 作为组件层，页面级文件不再重复定义；分批合并 | 逐 class `grep -rn "^\.ws-count"` 等；截图对比 |
| D1 | 16 处 `escHtml`/`escapeHtml`/`esc` 定义 | DUPLICATE | 约 80 行 | `escHtml` 7 处：`project-init.html:1498`、`diff-review.html:178`、`projects.html:811`、`workbench.html:3359`、`mode-editor.js:915`、`context-panel.js:5`、`mode-outline.js:512`；`escapeHtml` 4 处：`project-init.html:1669`、`workbench.html:4939`、`mode-editor.js:2563`、`mode-outline.js:716`；`esc` 5 处：`chapter-team.mjs:1`、`intake.js:5`、`workspace-model.mjs:158`（唯一 `export`）、`chapter-studio.mjs:5`、`chapter-live.mjs:1` | needs-check | 活代码统一 import `workspace-model.mjs:158 escapeHtml`（或新建 `assets/html.mjs` 提供 `esc` 并 re-export）；旧页随 A1/A2 消失。注意各实现转义范围不同（部分不转单引号），收敛前需核对 | 收敛后浏览器输入含 `<>&"'` 的文本，检查渲染与属性注入 |
| D2 | `project-init.html:4708-4712` vs `mode-outline.js:7380-7384` | DUPLICATE | 5 行 | `escapeHtmlForRole` 两处**逐字相同**（`String(t==null?'':t).replace(/[&<>"']/g,...)`）；测试注释仍引用旧页（见 F4） | product-decision（随 A2） | 随旧页删除；保留 mode-outline 一份 | 删除后 `grep -rn "escapeHtmlForRole" frontend/` = 1 |
| D3 | `projects.html:939-1058` vs `mode-outline.js:514-615` | DUPLICATE | 约 120 行 | 同名 8 个函数重复实现（模型选择器 + API 配置弹窗）：`loadModels`(projects:939-966)、`renderDefaultModels`(968-981)、`bindModelOptions`(983-999)、`openApiModal`(1034-1038)、`closeApiModal`(1039-1041)、`resetApiForm`(1043-1058)、`escHtml`(811)、`fmtSize`(860)；mode-outline 对应 `:514/:535/:549/:599/:604/:605/:512/:726`。两处已出现实现漂移（逐函数 diff 非逐字相同） | needs-check | 抽成 `assets/model-config.js`（DOM + api 逻辑，纯浏览器），projects.html 与 mode-outline.js 共用；这是当前**活代码里最大的一处重复** | 两页各自打开「添加模型 / API 配置」弹窗，验证列表、验证连接、保存全流程 |
| D4 | `projects.html:860-863` / `mode-outline.js:726-729`；`diff-review.html:156-164` | DUPLICATE + DEAD | 8 行 | `fmtSize` 重复实现两份；`fmtTime` 死（见 B6） | safe / needs-check | `fmtSize` 合并到共享工具模块；`fmtTime` 删除 | grep + 弹窗/修订链 UI 回归 |
| D5 | `workbench.js:92-127` vs `mode-editor.js:2568-2622` | DUPLICATE（同宿主双渲染） | 约 120 行 | 两模块都往**同一宿主** `#blocks-list` 写章节树：`workbench.js:99 renderOutline()`（由 `mode-dispatch.js:77 mode==='editor'→window.__renderEditorTree?.()` 触发）与 `mode-editor.js:2603 renderExtractOutline()`（SSE 流式期间写同一宿主，并在 :2615-2623 绑自己的点击、`rowData:{idx}`）；`workbench.js` 用 `rowData:{blockId}` 且 `selectedClass:'active'` —— 两种行标记/选中语义并存 | needs-check | 明确所有权：编辑器模式左栏树只由 `workbench.js` 渲染，`mode-editor` 只做数据写入并调用 `window.__renderEditorTree()`；否则合并成共享渲染入口 | 编辑器模式：流式拆分过程中观察左栏是否被覆盖、点击是否选中正确章节；`grep -n "renderChapterTree" frontend/assets/*.js` |
| D6 | `window.__*` 全局通道 | DUPLICATE（架构债） | 约 15 个全局 | `window.__renderEditorTree`(workbench.js:197)、`__reloadOutlineTree`(mode-outline.js:7696)、`__extractBlockMap`(mode-editor.js:2597/workbench.js:94)、`__allBlocks`、`__selectedModel`、`__editorDirty`、`__discardEditorChanges`、`__onBlockSelected` 等跨模块以 window 通信；`document.addEventListener('workspace:mode')` 与 `CustomEvent` 混用 | needs-check | 中期改成显式事件/共享 store（`workspace-model` 或新建 `assets/app-store.mjs`），先列清单再逐步替换 | `grep -rn "window\.__" frontend/` 建立清单；替换一处跑一次三模式回归 |
| E1 | `mode-outline.js` 全文件；`openSplitReport()` 3892-5616 | JUNK（God module/function） | 7,725 行 / 430,920 B；单函数 1,725 行 | 顶层函数 205 个；`openSplitReport` 单函数 1,725 行（占文件 22%），内含 B2 的 6 个死 helper；`panel-helpers.test.mjs` 已专门为「整段替换误删函数」写守卫（:1-13 事故说明） | product-decision（大重构） | 中期按阶段拆：`split-report/`（拆分面板）→ `materials/`→`outline/`→`matching/`；先删 B1/B2 死码降低体量，再做拆分。不要在本轮清理中做 | 拆分前后 `node --test tests/frontend/*.test.mjs` + 三模式手工回归 |
| E2 | `mode-editor.js` 全文件 | JUNK（God module） | 2,848 行 / 120,274 B；60 个函数 | 编辑器 + 流式生成 + 素材抽屉 + 评审入口全在一个模块；与 `workbench.js` 存在 D5 双渲染 | product-decision | 与 E1 同批规划拆分 | 同上 |
| E3 | `three-mode.html` 全文件；内联 `<style>` 10-3352 | JUNK（巨量内联） | 4,068 行，其中 CSS 3,342 行 | 191 个 `id`、5 个 `<script>`（含 4 个 module 标签 + 1 段看门狗内联脚本 4023-4055）；C5 中 648 行内联 CSS 是死规则 | needs-check | 把内联 CSS 抽成 `assets/three-mode.css`（随 C5 一起清死规则）；HTML 只留结构 | 抽取后页面截图与 `grep -c "<style" three-mode.html` |
| E4 | `workbench.html` / `project-init.html` 内联 | JUNK | 9,770 行内联（JS 6,582 + CSS 2,932 + 其他） | 见 A4/A5/A6 | product-decision（随 A1/A2） | 随旧页删除 | 见 A1/A2 |
| E5 | `styles.css` 全文件 | JUNK（历史样式堆积） | 2,727 行 / 824 个 class 选择器（245 unique） | C1 中 177 块死规则；同时包含「项目列表页 / 旧 workbench 布局 / 旧 project-init 布局」三代页面的样式残骸 | needs-check | 拆分为 `base.css`（变量/重置/通用）+ `components.css`（ui.css 已有）+ 页面样式；先清 C1 | 拆分后 5 个活页视觉回归 |
| E6 | `.js` / `.mjs` 混用；无 `package.json` | JUNK（结构） | 10 `.js` + 8 `.mjs` | `ls frontend/assets/*.js \| wc -l`=10、`*.mjs`=8；仓库根/`frontend/` 均无 `package.json`（`find . -maxdepth 2 -name package.json` 为空）→ 浏览器侧靠 `type="module"`，Node 侧靠 `.mjs` + Node 26 的 ESM 探测（`tests/frontend/api-stream.test.mjs:14` 用 `await import('../../frontend/assets/api.js')` 能跑通纯属 Node 26 行为） | safe（低优先） | 统一 `.mjs`（或补 `package.json {"type":"module"}` 后统一 `.js`），并把 `api-stream.test.mjs` 改为显式契约 | 改后 `node --test tests/frontend/*.test.mjs`；浏览器 5 页 smoke |
| E7 | `?v=` 版本号机制 | JUNK（维护成本） | 68 处 | `grep -rc "?v=" frontend/*.html frontend/assets/*`：HTML 40 处（7 个文件）+ 模块 28 处；每次发版需同步 18 个文件 | needs-check | 中期改为构建期 hash 或去掉 `?v=`（后端已对 `/assets` 设 `no-cache`，`main.py:264-275`，缓存问题已有服务端兜底） | 去掉后强刷/普通刷新各验证一次模块加载 |
| F1 | `tests/` 全目录 vs 路由 | TEST-CONTRACT（缺失） | 0 处 | `grep -rn "workbench\|project-init\|three-mode\|project-intake\|diff-review" tests/ --include=*.py` 返回 **0**：`/workbench`、`/project-init` 的 307 行为**没有任何测试**（这次路由变更零回归保护） | needs-check | 新增 `tests/routes/test_frontend_routes.py`：断言 `/workbench`、`/project-init` 307 且 Location 含 `/project-intake?projectId=`；`/three-mode`、`/review`、`/projects`、`/project-intake`、`/diff-review` 200 | `pytest tests/routes/test_frontend_routes.py -q` |
| F2 | `tests/frontend/module-imports.test.mjs:111-117, 141-147` | TEST-CONTRACT（错误/不完整） | 2 个数组 | ① 两个版本一致性数组仍包含死页 `'project-init.html'`、`'workbench.html'`（:112-113、:142-143）→ **测试是旧页"活着"的唯一原因**；② 活页 `project-intake.html` 与 `intake.js`、`chapter-studio.mjs`、`chapter-tree.mjs`、`workspace-model.mjs`（均含 `?v=` 相对导入）**不在数组里** → 版本号漂移检查有 5 个盲区（实际引用：`intake.js:1`、`chapter-studio.mjs:1-3`、`chapter-tree.mjs:23`、`workspace-model.mjs:8`） | safe（测试文件可改） | 删旧页两项；补 `project-intake.html` + 上述 4 个模块；跑全量前端测试 | `node --test tests/frontend/*.test.mjs`；故意改一个模块版本号验证测试能红 |
| F3 | `tests/frontend/workbench-modes.test.mjs` | TEST-CONTRACT（假覆盖） | 83 行 / 8 个测试 | 该文件 8 个测试全部围绕 `parseMode/modeUrl/resolveSelectedBlock/panelVisible/MODE_PANEL`；这 5 个导出在产品代码里**零引用**（`grep` 确认），只有测试自己调用 → 覆盖率高但保护的是无产品行为的 API | needs-check | 与 B8 一起决策：删函数+删测试，或把 `parseMode/panelVisible` 接进 `mode-dispatch.js`（它现在自己内联实现同类逻辑的简化版） | 删除后 `node --test`；或接线后三模式 URL 切换回归 |
| F4 | `tests/frontend/material-roles.test.mjs:72`；`panel-helpers.test.mjs:80` | TEST-CONTRACT（陈旧注释/依赖） | 2 处 | material-roles 测试注释仍写「文件名由 project-init.html 的 escapeHtmlForRole 转义后拼接」；panel-helpers 正确读取 `three-mode.html`。旧页删除后首条注释失真 | safe | 更新注释为 `mode-outline.js`；panel-helpers 保持 | 注释校对 |
| F5 | `frontend/diff-review.html` 全文件 + `main.py:309-313` | LEGACY（孤儿页） | 331 行 | 路由存在（main.py:309-313），但**全前端无任何链接指到 `/diff-review`**：`grep -rn "diff-review" frontend/ --include=*.html --include=*.js --include=*.mjs` 只命中文件自身与 `workspace.js:57` 注释；`revisions.py:49` 注释称「diff-review.html 直接渲染」但无入口 | product-decision | 二选一：①在 `/review` 章节版本区补入口（产品保留）；②删除路由+文件（产品确认无人用）。不要长期留孤儿页 | `grep -rn "href=\"/diff-review" frontend/`；`curl -I /diff-review` |
| F6 | `docs/design/UI一致性审计.md:306, 730` | INVALID-CONTRACT（文档与现实不符） | 2 处 | 文档写「旧页保留重定向（不删文件）…`?legacy=1` 可绕过」；现实：`backend/main.py:301/316` 的 307 已使页面不可达，`?legacy=1` **完全无效**（前端目录只挂 `/assets`）。同一文档 §730 又写「旧页文件仍在磁盘…真正下线需要删文件」 | safe | 删页时同步更新该文档；若保留回退期，应给一个**可用**的 legacy 路由（如 `/legacy/workbench`）而不是留死文件 | 文档与 `curl` 行为对照 |

---

## 2. 逐项详述

### 2.1 两个整页死实现（A1–A11、E4）

**它是什么**：`frontend/workbench.html`（5,580 行）与 `frontend/project-init.html`（4,962 行）是 2026-10-13 三模式合并前的两个整页：前者是方案编辑器，后者是「要求与大纲 + 素材库」。合并后唯一入口是 `/three-mode`，两页被保留了「按 `view` 参数重定向」的脚本（`workbench.html:2061-2071`、`project-init.html:895-905`），并留 `?legacy=1` 逃生口。

**为什么判定无用**：
1. **HTTP 层不可达**。`backend/main.py` 的路由表里没有任何 `FileResponse` 指向这两个文件（`grep -n "@app.get" backend/main.py` 共 8 条前端路由，见 §1 A1 证据）；`/workbench`（:301-307）与 `/project-init`（:316-322）直接 `RedirectResponse(status_code=307)` 到 `/project-intake`/`/projects`。前端目录只 `app.mount("/assets", ...)`（:256），所以 HTML 文件本身没有静态访问路径。
2. **页内重定向永不执行**。既然页面不会被浏览器拿到，`location.replace('/three-mode?...')` 也不会有机会跑；`?legacy=1` 分支必然到不了 —— 逃生口已经在后端改造时被破坏（F6）。文档 `docs/design/UI一致性审计.md:730` 也自己写着「真正下线需要删文件」。
3. **内联代码是历史快照**。
   - `workbench.html:2504-5224` 的 `<script type="module">` 与 `assets/mode-editor.js`：55 个 `function` 声明全部重合（55/55），mode-editor 另有 5 个后来新增的（`insertNotice`/`isMergedPage`/`loadPointMeta`/`renderChapterPoints`/`switchBlockDiscarding`）；2,278 行非空行中 2,186 行逐行一致（94.4%）。
   - `project-init.html:1099-4959` 与 `assets/mode-outline.js`：138 个 `function` 声明全部重合（138/138），mode-outline 另有 67 个；138 个同名函数中 94 个函数体逐字相同。
   - `workbench.html:5326-5577` 的模型选择器 IIFE 已在 `mode-outline.js:514-615` 重写一份。
   - 两页的内联 CSS 与 `three-mode.html` 内联 CSS 选择器重合率 427/429、327/327。
4. **维护成本是真实的**。最近 4 个功能提交（`4a6f627`/`fea5868`/`65e0407`/`98a2ade`）都同时修改 5 个文件（旧页×2 + three-mode.html + mode-editor.js + mode-outline.js），但旧页的改动**只是 `?v=` 版本号**（`git show 4a6f627 -- frontend/workbench.html`：18 行差异全是版本 token，0 行逻辑）。也就是说：旧页不产生任何功能，却在每次发版时强制多改 15 处版本号、多跑一遍 2 个文件的测试检查。

**删了会影响什么（已逐一核实）**：
- `workbench.html`/`project-init.html` 的 import 列表引用了 `generate-session.js`、`context-panel.js`、`workspace.js`、`workspace-model.mjs`、`api.js`、`material-roles.mjs`（经 workspace.js）——其中 `generate-session.js`、`context-panel.js` **还被活代码 `mode-editor.js:5,8` 引用**，其余都被活模块引用（§3 排除项）。所以删页不会让任何 assets 文件变成孤儿。
- 测试引用：只有 `tests/frontend/module-imports.test.mjs` 的两个数组（F2）需要同步；`material-roles.test.mjs:72` 是注释（F4）。
- 文档引用：`docs/design/UI一致性审计.md`、`docs/superpowers/**` 属历史设计记录，不必改，但 `UI一致性审计.md:306,730` 应更新状态（F6）。
- **产品回退价值**：理论上旧页是回退路径，但后端 307 已使其不可用；若产品坚持保留回退期，正确做法是加一个真实可达的 `/legacy/workbench` 路由，而不是留两个死文件。**这是本报告唯一需要产品拍板的大项。**

**具体做法**（按顺序）：
1. 先做 F2：从 `module-imports.test.mjs` 两个数组删去 `'project-init.html'`、`'workbench.html'`，并补齐 `project-intake.html` + 4 个漏检模块；跑测试确认仍 160 passed。
2. `git rm frontend/workbench.html frontend/project-init.html`。
3. 跑 `grep -rn "workbench.html\|project-init.html" frontend/ tests/ backend/` —— 期望只剩历史注释（`three-mode.html:1076,3530`、`mode-editor.js:1`、`workbench.js:43` 等「原 workbench.html」说明文字，可保留）。
4. 更新 `docs/design/UI一致性审计.md:730` 的「旧页文件仍在磁盘」条目为已删除。
5. 浏览器访问 `/workbench`、`/project-init` 确认 307 目标正确（F1 建议顺手补测试）。

### 2.2 活模块里的死函数（B1–B6）

**B1** `mode-outline.js:2827-2833 readConfirmedPkg`、`:2975-3004 renderPackageCards`、`:3007-3019 renderPointsAlignment`
- 是什么：2026 年「标包分析」功能的渲染层。`readConfirmedPkg` 从 URL/localStorage 读确认标包；`renderPackageCards` 渲染标包卡片（含原文出处）；`renderPointsAlignment` 渲染评分点对齐提示。
- 为什么无用：三者全仓各只有 1 处命中（定义行）。标包确认流程后来迁到 `/project-intake`（`intake.js` 与 `projects.py`/`packages.py`），`/three-mode` 里的标包选择由 `mode-outline.js` 其它函数（`confirmedPkg` 状态 + 下拉）负责。三个函数从未被任何调用点使用 —— 典型的「重写 `openSplitReport()` 时替换掉的旧渲染分支」。
- 怎么做：整函数删除（共 50 行）。若担心未来恢复标包卡片，git 历史里可找回，不必留在工作区。
- 注意：`panel-helpers.test.mjs` 只断言一组关键函数存在（`openSplitReport` 等），不包含这 3 个，删除不会破坏测试。

**B2** `mode-outline.js:4049-4133 confItem`、`:4135-4138 statusCell`、`:4140-4141 rpRows`、`:4211-4213 notGenerated`、`:4222-4223 unitRows`、`:4266-4274 scopeRows`
- 是什么：`openSplitReport()` 内部的局部箭头函数（模板片段生成器）：确认项控件、状态单元格、评分点行、未生成提示、单位行、范围行。
- 为什么无用：每个名字全仓只有定义处 1 次出现。`openSplitReport` 现版本的分支已经不再调用它们（说明该函数某次整段替换后，旧的渲染 helper 被留在函数体内）。这正是 `panel-helpers.test.mjs` 文件头记录的事故类型（整段替换误删/遗留函数）的「遗留侧」。
- 怎么做：删除这 6 个 helper（105 行）。建议顺手在 `package.json`（或临时脚本）里加 `eslint --rule 'no-unused-vars:error'` 防回归（E6 提到仓库目前无 package.json，可先加最小配置）。

**B3** `workspace.js:47-51 currentView()`、`:83 const inPlace`
- 是什么：`currentView()` 按 pathname 猜当前视图（review/editor/outline），`inPlace` 判断页面是否三模式合并页。
- 为什么无用：两者全仓各 1 处（定义）。`currentView` 是合并前「按 URL 判断页面」的残留（现在视图由 `body[data-view]` 表达）；`inPlace` 所在分支的「四模式导航」被用户要求整块移除（`workspace.js:84-92`：`nav.innerHTML=''` + `nav.hidden=true`，注释「逻辑待重新设计」）。注意同文件 `:39-41` 的 `workspaceUrl()` 是**活的**（被 workbench.js/review.js 使用），别误删。
- 怎么做：删两处；`inPlace` 若导航重做需要，重新引入即可。

**B4** `chapter-tree.mjs:146-151 setSelectedRow`
- 是什么：给章节行设选中的导出函数。
- 为什么无用：`grep -rn "setSelectedRow" frontend/ tests/` = 1（定义）。选中逻辑实际由 `chapterNode({selected})` + `renderChapterTree` 的 `selectedClass` 参数完成（`workbench.js:99-110`、`mode-editor.js:2603-2611`）。
- 怎么做：删除导出；若保留为公共 API，则在文件头说明「预留给 X」。

**B5** `mode-dispatch.js:48-50 currentMode()`（+ `setMode` 的 export）
- 是什么：模式调度的两个导出。
- 为什么无用：`currentMode` 全仓 1 处；`setMode` 无外部 import，只在模块内部被 `init()` 的 nav 点击（:102，而 nav 已被清空，见 C7）与 `popstate`（:106）调用。
- 怎么做：删 `currentMode`；`setMode` 去 `export`。这样 `mode-dispatch.js` 的对外契约只剩「被 `<script type="module">` 加载后初始化」，与其实际角色一致。

**B6** `diff-review.html:156-164 fmtTime`
- 是什么：把 ISO 时间格式化成 `MM-DD HH:mm`。
- 为什么无用：同文件 `timeKey`(:166)、`groupByTime`(:184)、`renderChain`(:201) 都在用 `timeKey`/`slice` 方案，`fmtTime` 零调用（重构时替换成 `timeKey` 后忘了删）。
- 怎么做：删除 9 行。

### 2.3 未用/死导出与导入（B7–B9）

**B7 9 个未使用 import**：`mode-editor.js:23-24` 的 `evidenceSourceLineHtml`、`evidenceSnippet`、`chapterStage`、`CHAPTER_STAGE_LABELS`；`workbench.js:3` 的 `chapterBadges`；`mode-outline.js:24,30,32` 的 `constraintLevelLabel`、`groupEvidenceByItem`、`itemCode`、`chapterBadges`（后者的第二处命中的是注释 `:1326`）。
- 为什么判定：逐符号全文计数 = 1（仅 import 行）。
- 怎么做：从 import 列表删除；这些符号在 `workspace-model.mjs` 里仍被其他模块使用（例如 `chapterBadges` 被 `mode-editor.js` 通过不同路径使用？实际 `grep -rn "chapterBadges"`：`workspace-model.mjs` 定义、`project-init.html`(死页)、`mode-outline.js:1326` 注释 —— 删除 import 不影响模块本身）。

**B8 未用/仅测试用导出**：
- `workbench-modes.mjs`：`MODES`(:17) 被内部 `MODE_SET` 使用，活；`DEFAULT_MODE`(:38)/`isValidMode`(:94) 被 `mode-dispatch.js:13` 使用，活；**`MODE_LABEL`(:19)、`REVIEW_PATH`(:26)、`MODE_PANEL`(:29)、`parseMode`(:46)、`modeUrl`(:58)、`resolveSelectedBlock`(:75)、`panelVisible`(:89) 产品零引用**。其中前 4 个只被 `workbench-modes.test.mjs` 调用（F3），`REVIEW_PATH`/`MODE_PANEL` 只在测试断言里被间接读取。
- `workspace-model.mjs`：`CHAPTER_STAGES`(:849) 全仓仅定义；`materialRoleLabels`/`materialRoleHints`(:6) 是 `ROLE_LABEL/ROLE_HINT` 的死别名；`EVIDENCE_SOURCE_LABELS`(:541) 只在该模块内 :669 用（导出多余）；`evidenceSliceTitle`(:559)/`evidenceByFile`(:621) 只被 `tests/frontend/evidence-view.test.mjs:4,29,51` 使用（产品零引用）。
- 判定方法：`grep -rn "<name>" frontend/ tests/` 逐名计数 + 测试来源甄别。
- 怎么做：产品死（`REVIEW_PATH`、`CHAPTER_STAGES`、两个 role 别名）直接删；测试维持的（4 个 mode 函数 + 2 个 evidence 函数）二选一——补产品用法或删函数+测试（见 F3）。**不要为了测试保留零产品行为的 API。**

**B9 api.js 11 个未调用方法**：
- 判定：按 `api.<group>.<method>(` 精确扫描（排除 api.js 自身），零命中。明细：`projects.createPackage`(:44)、`projects.patch`(:95)、`projects.lock`(:101)、`materials.mapSources`(:164)、`blocks.update`(:173)、`blocks.ai`(:210)、`blocks.updateRequirement`(:216)、`revisions.list`(:225)、`revisions.restore`(:227)、`workflow.getSplitAnswers`(:287)、`review.get`(:441)。
- 特别注意 `blocks.update`/`blocks.updateRequirement`/`updateFields` 三个方法都 PUT `/api/blocks/{id}`（`api.js:173/216/177`），只有 `updateFields` 在用；`api.blocks.update` 与 `updateRequirement` 是同一契约的重复封装。
- 后端对应路由仍在（`backend/routes/blocks.py:196` PUT、`:312` POST `/ai`；`revisions.py:28/68`；`materials.py:180/587`；`projects.py:164/177`；`packages.py:188`；`review.py:19`）——所以**不能只删前端**，否则「客户端覆盖 = 后端刻意保留的契约」这条信息就丢了。建议与后端负责人一起决策（§4）。

### 2.4 `context-panel.js` 运行时空转（B10）

- 它是什么：右栏「应标要求 + 匹配素材 + 原文溯源」渲染模块（70 行），导出 `initContextPanel()`。
- 为什么判定死：模块只通过 `document.querySelector("[data-panel-requirement]")`（:13）与 `"[data-panel-sources]"`（:14）找挂点。`grep -rn "data-panel-requirement\|data-panel-sources" frontend/` **全仓 4 处，全部在 context-panel.js 自身**（:13/:14/:18/:26）——没有任何 HTML 元素带这两个属性。`git log -S "data-panel-requirement" -- frontend/` 显示最后一次被引入是 `2026-05-31 31393a5`，之后页面重构删掉了挂点。`mode-editor.js:25` 仍 `initContextPanel()` → 每次编辑器模式加载都会执行，但两个 section 都是 `null`，函数直接 return，**零副作用**。
- 它引用了什么替代品：三模式右栏的「要求 / 依据」由 `mode-editor.js`/`mode-outline.js` 自己渲染（`renderContext`、evidence 渲染），以及 `workspace-model.mjs` 的 `renderEvidenceSection`。
- 具体做法：①删除 `mode-editor.js:8` 的 import 与 `:25` 的调用；②删除 `context-panel.js`；③若产品仍想要独立上下文面板，则补 `[data-panel-requirement]/[data-panel-sources]` DOM 挂点（当前没有）。
- 注意：这是「看起来死但其实被 import」的典型（§3），判定依据必须是 **DOM 挂点存在性**，而不是 import 存在性。

### 2.5 CSS 死规则与重复（C1–C10）

**方法**：Python 扫描每个 CSS 文件的规则块（brace 匹配），提取选择器里的 class/id；用「HTML（剥离内联 CSS，但保留内联 JS）+ 全部 JS/MJS + 测试」作为引用语料；所有 class/id 都无引用的规则块记为死块。另外用「字符串拼接/模板字符串前缀」检查动态 class 生成（`'score-'+x`、`` `level-${x}` ``），确认**不存在**对死块的动态构造（动态构造只命中 `level-${...}`、`ui-btn--*`、`sr-page-warn`、`ws-button` 等活类）——因此死块判定偏保守但不漏大块。

**结果**：

| 文件 | 总行数 | 规则块 | 完全死块 | 死块行数（估算） |
|---|---|---|---|---|
| `styles.css` | 2,727 | 389 | 177 | ≈1,394（51%） |
| `review.css` | 628 | 186 | 55 | ≈381（60%） |
| `response-outline.css` | 141 | 135 | 26 | ≈63（44%） |
| `workspace.css` | 80 | 55 | 14 | ≈29（36%） |
| `chapter-studio.css` | 39 | 186 | 5 | 5（12%） |
| `three-mode.html` 内联 | 3,342 | 795 | 85 | ≈648（19%） |
| `ui.css` | 174 | 24 | 1 | 2（≈1%，基本无死码） |

**典型死块**（均按当前工作区行号）：
- `styles.css`：`.page-shell`(:62-66)、`.gallery-page`(:66-70)、`.gallery-header`(:70-78)、`.search-shell`(:100-113)、`.view-toggle`(:113-124)、`.list-view`+`.list-row*`(:135-192)、`.brand-lockup/.brand-name/.brand-subline`(:186-203)、`.gallery-hero/.gallery-grid`(:322-339)、`.project-card/.add-card`(:353-415)；`.context-pane` 在 :1773/:1990/:2099/:2114/:2391/:2398/:2419 共 **7 次定义且全部无引用**。
- `review.css`：`.review-content`(:17-27)、`.thread-id-display`(:27-33)、`.review-banner/.banner-*`(:33-79)、`.review-recover`(:85-100)、`.review-actions/.actions-*`(:100-124)、`.review-stage/.stage-*`(:159-176)、`.review-grid`(:176-184)、`.card-head/.card-checkbox/.card-title/.card-scores`(:207-235)、`.score-pill*`(:235-260)、`.card-section*`(:260-273)、`.issue-list/.severity-tag`(:273-295)。review.css 实际只被 `review.html` 加载，且 `review.js` 只用 `quality-*`/`v-*`/`model-*`/`verdict-*` 等 71 个 class。
- `response-outline.css`：`.sr-btn/.sr-btn-primary`(:1-4)、`.sr-mount`(:32-36)、`.sr-two*/.sr2-*`(:48-63)、`.sr-checks/.sr-check`(:95-99)、`.sr-echo*`(:102-105)、`.sr-struct-*`(:107-112)、`.sr-req-table .sr-req-*`(:117-129)、`.spec-attention-hint`(:127-128)。
- `workspace.css`：`.ws-count`(:5-6)、`.ws-status`(:6-7)、`.ws-state-copy`(:7-8)、`.ws-status-actions`(:9-12)、`.ws-outline-tools`(:45-47)、`.ws-editor-review`(:47-51) —— 对应「共享壳状态条已按原型移除」（`workspace.js:96-101` 注释）。
- `three-mode.html`：`:101-109 .status-bar-tools`（源码注释自己写着「已挪走，保留选择器以防残留引用」——这种「以防万一」正是应清理项）、`:109-135 .materials-panel-header/.app-logo-sm/.app-title`、`:181-193 .mat-role-badge*`、`:932-946 .init-sidebar`、`:1337-1356 .btn-mark-done`、`:1483-1516 .ref-section*`、`:1690-1765 .block-detail-*`。

**同文件重复声明**（C6）：`styles.css` 35 个选择器被重复声明（`.editor-block`×3、`.editor-block h1`×3、`.tiptap-shell`×3、`.block-history`×3、`.history-item`×3 …共 43 条冗余声明）；`three-mode.html` 9 条（`.btn-add-mat`、`.extract-hint`、`.model-selector-wrap`、`.api-modal-backdrop`、`.stage-progress`、`.doc-outline-item` 等）；`review.css` 2 条；`response-outline.css`/`workspace.css` 各 1 条。

**跨文件重复定义**（C10）：40 个 class 在 >1 个 CSS 文件里定义。典型：`.sr-btn/.sr-btn-primary` 在 `response-outline.css` 与 `ui.css`（两处都无元素使用 → 彻底死）；`.ws-count/.ws-dialog/.ws-button/.ws-chapter-status/.ws-primary` 在 `ui.css` 与 `workspace.css`；`.workspace-shell/.editor-pane/.outline-pane/.project-header/.context-pane` 在 `styles.css` 与 `workspace.css`/`review.css`；`.v-*/.quality-*/.bad/.btn-primary/.btn-danger` 在 `review.css` 与 `ui.css`。`ui.css` 文件头注释自称「组件最小集：最后加载以统一历史类名」——它本应成为合并终点，但历史规则仍散落在页面文件里。

**具体做法**：
1. 先删「整块死规则」（按文件分批，每批一个 commit）。
2. 再合并同文件重复选择器（C6）：把后者覆盖前者的属性合并进一条，**不改视觉**。
3. 最后做跨文件收敛（C10）：确立 `ui.css` 为组件层、`styles.css` 只留 base/变量、页面级 CSS 只写本页布局。
4. 每批验证：`grep` 目标 class 归零 + 5 个活页（`/three-mode` 三模式、`/projects`、`/project-intake`、`/review`、`/diff-review`）截图对比。**没有自动化视觉回归**，这是 CSS 清理必须人工确认的原因（风险标 needs-check 的依据）。

### 2.6 重复实现与工具函数（D1–D6）

**D1 转义函数 16 份**：`escHtml` 7 份（含旧页 2 份）、`escapeHtml` 4 份（含旧页 1 份）、`esc` 5 份。唯一的 `export` 是 `workspace-model.mjs:158 escapeHtml`（`workspace.js:1` 已 `escapeHtml as esc` 再导出）。**注意各实现转义集不同**：多数只转 `&<>"`，`chapter-team.mjs:1`/`intake.js:5`/`chapter-studio.mjs:5`/`chapter-live.mjs:1`/`workspace-model.mjs:158?` 转 `&<>"'` 五字符；`mode-editor.js:2563`/`mode-outline.js:716` 的 `escapeHtml(s)` 只转 `&<>"` 且不处理 `null`（`(s || '')`）。收敛前必须统一语义（否则单引号场景会变），建议以 `workspace-model.mjs:158` 为唯一实现，其余 import 别名。

**D2 `escapeHtmlForRole` 逐字重复**：`project-init.html:4708-4712` 与 `mode-outline.js:7380-7384` 完全一致（含单引号转义）。旧页删除后自然只剩一份；测试注释需同步（F4）。

**D3 projects.html ↔ mode-outline.js 的模型弹窗（活代码最大重复）**：8 个同名函数两份实现（`loadModels`、`renderDefaultModels`、`bindModelOptions`、`openApiModal`、`closeApiModal`、`resetApiForm`、`escHtml`、`fmtSize`），约 120 行/边。两处已出现漂移（逐函数比对非逐字相同，例如 `projects.html` 版本更早）。它同时牵涉 `api` 的模型配置接口与 DOM（`#api-modal-backdrop`、`#provider-chips`、`#model-list-*`）——这些 DOM 在两页各有一份，因此抽取时建议做成 `assets/model-config.js`：接受「根节点 + api 适配器」参数，两边共用。
**怎么做**：新建 `assets/model-config.js`（纯浏览器 DOM 模块，不依赖页面全局），`projects.html` 与 `mode-outline.js` 各自 import；旧页随 A1/A2 消失。验证：两页的「添加模型 / 验证连接 / 保存 / 删除」全流程 + 模型菜单渲染。

**D4 `fmtSize`**：`projects.html:860-863` 与 `mode-outline.js:726-729` 重复，合并进共享工具（可跟 D1 同一模块）。`fmtTime` 是死函数（B6）。

**D5 编辑器左栏树双渲染（同宿主）**：`workbench.js:92-127 renderOutline()` 与 `mode-editor.js:2568-2622 renderExtractOutline()` 都往 `#blocks-list` 写树：
- 前者由 `mode-dispatch.js:77`（`mode==='editor' → window.__renderEditorTree?.()`）触发，`rowData:{blockId}`，`selectedClass:'active'`，点击靠 `hostEl()` 上的委托（`workbench.js:129`）；
- 后者在 SSE 流式拆分期间调用（`mode-editor.js:2788`），`rowData:{idx}`，并在 `:2615-2623` 自己绑 `.doc-outline-item` 点击。
两者共用 `chapter-tree.mjs` 的渲染器，但**行数据键不同、选中语义不同、点击处理两套**。这不是死代码，但是合并后最明显的重复逻辑；若流式期间 `workbench.js` 也响应 `refreshWorkspace` 重渲染，会互相覆盖（`workbench.js:91` 已用 `body[data-view]!=='editor' return` 做了一层保护，但同一模式下双方都满足条件）。建议：明确「编辑器左栏树唯一渲染入口 = `workbench.js`」，`mode-editor.js` 只更新数据并调用 `window.__renderEditorTree()`。风险标 needs-check（需实测流式过程中的左栏行为）。

**D6 window 全局通道**：`window.__renderEditorTree`、`__reloadOutlineTree`、`__extractBlockMap`、`__allBlocks`、`__selectedModel`、`__editorDirty`、`__discardEditorChanges`、`__onBlockSelected`、`window.__bootErrors`/`__dshAppReady`（三模式页看门狗 4023-4055）等，跨模块靠 `window` 和 `document.dispatchEvent('workspace:mode')` 通信。合并后模块边界已经清晰，这类通道可以逐步换成显式事件/共享 store；不属于本轮删除项，列入 §4 的中期整理。

### 2.7 God module 与巨量内联（E1–E7）

- **E1 `mode-outline.js` 7,725 行 / 430,920 B**：顶层函数 205 个；`openSplitReport()` 单函数 1,725 行（3,892-5,616，占文件 22%），且内部还有 B1/B2 的死代码。`panel-helpers.test.mjs` 文件头的真实事故说明（「重写 `openSplitReport()` 时整段替换，连带删除了 `bindScopeSelector()`」）就是 God function 的直接后果。建议中期拆分：`split-report.js`（拆分面板 + 确认项）、`materials.js`（素材/标包）、`outline.js`（目录树/结构编辑）、`matching.js`（匹配工作区）。**不建议在本轮清理里做**——先删死码（B1/B2 155 行）降低风险。
- **E2 `mode-editor.js` 2,848 行**：编辑器 + 流式生成 + 素材抽屉 + 评审入口，60 个函数；与 `workbench.js` 有 D5 双渲染。与 E1 同批规划。
- **E3 `three-mode.html` 4,068 行**：内联 `<style>` 占 3,342 行（10-3352），191 个 id，5 个 `<script>`（`workbench.js`、`mode-dispatch.js`、动态 import `mode-outline/mode-editor`、`workspace.js`、看门狗内联脚本 4023-4055）。建议把内联 CSS 抽成 `assets/three-mode.css`（顺带清 C5 的 648 行死规则）；HTML 只留结构。
- **E4 旧页内联 9,770 行**：见 A4/A5/A6/A7。
- **E5 `styles.css` 2,727 行**：824 个 class 选择器、245 个唯一类；包含「项目列表页 / 旧 workbench 布局 / 旧 project-init 布局」三代残骸（C1 1394 行死规则）。建议拆 `base.css`（变量/重置/排版）+ 组件（并入 `ui.css`）+ 页面样式。
- **E6 `.js`/`.mjs` 混用 + 无 `package.json`**：10 个 `.js` + 8 个 `.mjs`；仓库根与 `frontend/` 都没有 `package.json`。浏览器侧无影响（`type="module"`）；Node 侧目前靠 `.mjs` 扩展名 + Node 26 的 ESM 探测，`tests/frontend/api-stream.test.mjs:14` 直接 `import('../../frontend/assets/api.js')` 能通过属于 Node 26 行为，属于隐性依赖。建议：统一 `.mjs`，或补 `package.json`（`{"type":"module"}`）后统一 `.js`，并把该测试改成显式契约。
- **E7 `?v=` 版本号**：68 处（HTML 40 + 模块 28），每次发版要同步 18 个文件；而 `main.py:264-275` 已对 `/assets` 设置 `Cache-Control: no-cache, must-revalidate`，服务端已经兜底缓存问题。中期可以改构建期 hash 或直接去掉 `?v=`（F2 的测试也可删除）。这与 A11 相关：删旧页后维护点 68→53。

### 2.8 失效契约与测试契约（A10、F1–F6）

- **A10 旧路由表残留在死页**：`project-init.html:1468-1470` 仍构造 `/project-init?...`、`:4666/:4694` 仍 `location.href='/workbench?...'`；`workbench.html:2708-2710` 同样构造 `/project-init?...`/`/workbench?...`，`:2854/:2874` 的提示横幅也链到 `/project-init?...`。活代码已统一为 `/three-mode?view=...`（`mode-outline.js:482-483`、`mode-editor.js:228-230/374/393`）。这些是「生成已下线路由的链接」，但只存在于不可达页面里，所以随整页删除即可；**不要单独去改它们**（改了也是死代码）。
- **F1 路由无测试**：`grep -rn "workbench\|project-init\|three-mode\|project-intake\|diff-review" tests/ --include=*.py` = 0。`/workbench`、`/project-init` 的 307 是这次合并的关键契约，却零覆盖。建议新增 `tests/routes/test_frontend_routes.py`（4 行断言即可）：
  - `/workbench` → 307，Location `/project-intake?projectId=...`（带 id）或 `/projects`（无 id）；
  - `/project-init` 同理；`/three-mode`、`/review`、`/projects`、`/project-intake`、`/diff-review` → 200。
- **F2 测试既保死页又漏活文件**：`module-imports.test.mjs` 的两个文件数组（:111-117、:141-147）包含死页 `project-init.html`/`workbench.html`（这是删除旧页的**唯一阻塞**），同时漏掉活页 `project-intake.html` 和 4 个含 `?v=` 相对导入的模块（`intake.js:1`、`chapter-studio.mjs:1-3`、`chapter-tree.mjs:23`、`workspace-model.mjs:8`）。也就是说「版本号必须统一」这条守卫有 5 个盲区。
- **F3 测试维持死 API**：`workbench-modes.test.mjs` 的 8 个测试全部围绕产品零引用的 4 个函数 + `MODE_PANEL`。这形成「测试通过 = 功能正确」的假覆盖。建议二选一（见 §4）。
- **F4 陈旧测试注释**：`material-roles.test.mjs:72` 注释引用旧页的 `escapeHtmlForRole`；旧页删除后改为 `mode-outline.js:7380`。`panel-helpers.test.mjs:80` 读 `three-mode.html` 是正确依赖，保留。
- **F5 `/diff-review` 孤儿页**：路由存在（`main.py:309-313`）但全前端无链接（`grep -rn "diff-review" frontend/` 只命中自身 + `workspace.js:57` 注释）。它渲染「修订对比 / 接受 diff / 导出 Word」，功能上像是有用的页面，但没有入口。需要产品决定：补入口（在 `/review` 的「章节版本」区）或删除。
- **F6 文档与现实不符**：`docs/design/UI一致性审计.md:306` 写「旧页保留重定向…`?legacy=1` 可绕过」、`:730` 写「旧页文件仍在磁盘…真正下线需要删文件」。前者是失效契约（后端 307 已使 legacy 不可用），后者是待办。删页时同步更新。

---

## 3. 已核实「看起来像死代码但其实活着」的排除项

> 这一节专门记录**不能删**的东西，避免清理时误伤。

| 对象 | 为什么看起来死 | 实际引用（证据） |
|---|---|---|
| `frontend/assets/generate-session.js` | 唯一 `<script>` 直接引用在死页 `workbench.html:2505` | 活代码 import：`mode-editor.js:5`（`GenerateSession`），实例化 `mode-editor.js:554` → 三模式编辑器生成链路活 |
| `frontend/assets/context-panel.js` | 同上，只在 `workbench.html:2506` 出现 | `mode-editor.js:8` import、`:25` 调用 → 模块图上是活的；**但运行时是 no-op**（见 B10），判定为「活引用、死行为」，需单独处理，不能按死文件直接删 |
| `frontend/assets/workspace-model.mjs` | 直接 `<script>` 只在旧页 `workbench.html:2514`、`project-init.html:1121` | 4 个活模块 + 测试引用：`workspace.js:1`、`review.js:2`、`workbench.js:3`、`mode-editor.js:24`、`mode-outline.js:30-32`、`chapter-tree.mjs:23`；`tests/frontend/**` 大量 import |
| `frontend/assets/chapter-live.mjs`/`chapter-team.mjs`/`chapter-studio.mjs`/`spec-content.mjs` | 名字只在模块链深处出现，无页面 `<script>` | 链：`three-mode.html` → `mode-outline.js:5`(`chapter-studio`) → `chapter-studio.mjs:1-3`(`chapter-live`/`chapter-team`/`spec-content`)；`mode-outline.js:7` 也直接 import `spec-content` |
| `frontend/assets/chapter-tree.mjs` | 只在三个 JS 里 import | `workbench.js:9`、`mode-editor.js:7`、`mode-outline.js:177`（活）；`tests/frontend/chapter-tree.test.mjs:3` |
| `frontend/assets/material-roles.mjs` | 名字只在注释里出现于旧页 | `workspace.js:5-8`（再导出）、`workspace-model.mjs:8`、`tests/frontend/material-roles.test.mjs:7,84`、`backend/routes/materials.py:48` 注释对照 |
| `frontend/assets/workbench-modes.mjs` | 名字里的 workbench 像旧页 | `mode-dispatch.js:13` import（`DEFAULT_MODE`/`isValidMode`）→ `three-mode.html:4057` 加载，活 |
| `frontend/assets/workbench.js` | 名字像旧页 | `three-mode.html:4056` 直接 `<script type="module">` 加载；编辑器左栏树/顶栏由它渲染（`workbench.js:197` 暴露 `window.__renderEditorTree`） |
| `frontend/assets/api.js`、`intake.js`、`workspace.js`、`review.js`、`mode-dispatch.js`、`mode-outline.js`、`mode-editor.js` | 部分只被旧页 `<script>` 直接引用 | 活引用：`three-mode.html:4056-4066`、`review.html:99-100`、`diff-review.html:101,329`、`project-intake.html:35`、`projects.html:748` |
| 6 个 CSS：`styles.css`/`workspace.css`/`ui.css`/`review.css`/`response-outline.css`/`chapter-studio.css` | 无 | 分别被活页 `<link>` 加载：`three-mode.html:7-9,3353-3354`、`review.html:7-9,16`、`projects.html:7,616`、`project-intake.html:6`、`diff-review.html:7-8`、`three-mode.html:8` |
| `workspace.js:39-41 workspaceUrl()` | 注释里提到「旧的 /project-init、/workbench」，像旧契约 | 活：`workbench.js:169`、`workspace.js:236`、`review.js:17` 均调用，输出 `/three-mode` —— 是**已修正**的 URL 生成器，不是旧实现 |
| `mode-outline.js:1331 chapterBadgesHtml()` | 与 `chapter-tree.mjs:26` 同名，像重复 | 不是重复：`mode-outline.js:1337` 委托 `sharedBadgesHtml`（import 别名），只负责补 evidenceCount —— 保留 |
| `#ced-mode-switch` 的 `data-mode="edit|preview"`（`three-mode.html:3828-3829`） | 名字含 `data-mode`，像页面三模式导航 | 它是**编辑器内部**编辑/预览切换（`mode-dispatch.js:10-11` 专门注释「别和编辑器工具栏搞混」）；不要按三模式导航处理 |
| `mode-dispatch.js:29 setMode` | 导出但无外部 import | 内部活：`init()` 的 `popstate`（:106）调用，浏览器前进/后退切模式依赖它；只应去掉 `export`（B5），不能删函数 |
| `workspace.js:26 subscribe`、`renderWorkspaceChrome`、`request` 等 | 名字通用、部分只在模块内用 | `subscribe` 被 `workbench.js:1`、`review.js:1` import；`renderWorkspaceChrome` 被 `mode-dispatch.js:14`、旧页、活页 import；`request` 被 `review.js:1` 使用 |
| `tests/frontend/panel-helpers.test.mjs` 对 `mode-outline.js` 源码的读取 | 像「测试依赖实现细节」 | 它是有意的事故守卫（文件头记录真实事故），删除死函数（B1/B2）不会破坏它：其 critical 列表为 `openSplitReport`/`maybeAutoOpenSplitReport`/`markRevisionConfirmed`/`getConfirmedRevision`/`resolvePanelThreadId`/`refreshOutlineCoverage`/`toggleScoringRisk`/`loadExistingBlocks`，与 B1/B2 无交集 |
| `tests/frontend/outline-structure.test.mjs`/`evidence-view.test.mjs` 的大体量 | 像冗余测试 | 分别覆盖 `workspace-model.mjs` 的目录结构纯函数与依据投影；`evidence-view.test.mjs:4-5` 是 `evidenceSliceTitle/evidenceByFile` 的**唯一**使用方（见 B8），删这两个导出必须同步改测试 |

---

## 4. 需要产品决策的项

1. **两个旧整页的删除时机（A1/A2，10,542 行）**  
   现状：HTTP 不可达、`?legacy=1` 逃生口已被 307 破坏、文档明确写「真正下线需要删文件」。  
   选项：①立即删除（推荐：收益 10.5k 行 + 版本号维护点减 15）；②保留一个**真实可达**的 `/legacy/workbench` 回退路由（需要新写路由 + 接受继续双份维护）；③原样保留（不推荐，等于每次发版多改 15 处版本号）。  
   若选①，建议在 release 说明里写清「旧页已删除，回退请 checkout 本 commit 之前的 tag」。

2. **`/diff-review` 页面去留（F5，331 行）**  
   路由在（`main.py:309-313`）、功能在（diff 接受/导出 Word），但全前端零入口。  
   选项：①在 `/review` 的「章节版本」区补「与上次评审对比」链接（产品保留功能）；②删除路由 + 文件（若实际由外部脚本/人工 URL 使用，需确认）。

3. **`?v=` 版本号机制是否保留（E7）**  
   服务端已对 `/assets` 设 `no-cache`，`?v=` 的缓存作用已弱化；但它是真实事故（模块一半新一半旧）的防线。  
   选项：①保留并补全测试盲区（F2，低成本）；②改构建期 hash；③去掉并用服务端 header 兜底。

4. **`api.js` 11 个未调用方法（B9）与后端契约**  
   后端路由仍在，前端零调用。需要和「后端契约是否还有人用（脚本/其它客户端/未来功能）」一起决策；只删前端会让契约清单失真。

5. **`workbench-modes.mjs` 的 4 个测试-only 函数（B8/F3）**  
   选项：①删函数 + 删测试（承认它们从未上线）；②把 `parseMode/panelVisible` 接进 `mode-dispatch.js`（它现在自己内联了一段简化逻辑），让测试有产品意义；③保留并标注「预留 API」。

6. **四模式导航（C7/D6）**  
   `workspace.js:84-92` 按用户要求把导航清空隐藏，注释「逻辑待重新设计」。在重做之前：`mode-dispatch.js` 的点击委托、`#workspace-nav a` 的 CSS、`setMode` 的 push 分支都是无效逻辑。选项：①先删无效部分（导航重做时再加）；②保持现状直到导航重做（明确记录为已知死代码）。

7. **`mode-outline.js`（7,725 行）与 `mode-editor.js`（2,848 行）拆分是否现在做（E1/E2）**  
   拆分风险高（`openSplitReport()` 1,725 行、`panel-helpers.test.mjs` 记录过整段替换事故）。建议本轮只删死码，拆分单独立项。

8. **CSS 死规则清理的视觉验收方式（C1–C5）**  
   没有自动化视觉回归。需要产品/设计确认「截图对比通过」的标准（或接受仅靠人工 smoke）。

---

## 5. 建议清理顺序与每步验证命令

> 以下命令**仅供执行者核对，本轮审计未执行任何删除/修改**。按顺序做，每步独立可回滚。

### Step 0 — 基线（先记录）

```bash
cd /Users/jianghe/projects/ge-solution
git status --short
node --test tests/frontend/*.test.mjs            # 期望 160 passed
wc -l frontend/*.html frontend/assets/*          # 期望 total 34,284
grep -rc "?v=" frontend/*.html frontend/assets/* # 记录版本号维护点（68）
```

### Step 1 — 低风险死码（safe，预计 -450 行）

```bash
# 1. 活模块内死函数
grep -rn "readConfirmedPkg\|renderPackageCards\|renderPointsAlignment" frontend/ tests/   # 期望各 1（定义）
grep -rn "confItem\|statusCell\|rpRows\|notGenerated\|unitRows\|scopeRows" frontend/ tests/ # 期望各 1
grep -rn "currentView\|inPlace" frontend/assets/workspace.js                              # 删除后归零
grep -rn "setSelectedRow\|currentMode\|toggleRefCard" frontend/ tests/                    # 期望各 1
grep -rn "fmtTime" frontend/diff-review.html                                              # 期望 1

# 2. 未使用 import（B7 9 个符号）
# 3. backend/main.py:246 删 _index_path；frontend/.DS_Store 删除
node --test tests/frontend/*.test.mjs            # 仍 160 passed
grep -rn "_index_path" backend/                  # 归零
```

### Step 2 — 测试契约修正（删旧页的前置）

```bash
# F2：module-imports.test.mjs 删 'project-init.html'、'workbench.html'，补 project-intake.html + intake.js/chapter-studio.mjs/chapter-tree.mjs/workspace-model.mjs
# F4：material-roles.test.mjs:72 注释改指 mode-outline.js
node --test tests/frontend/*.test.mjs
# 故意把一个模块的 ?v= 改错，确认测试会红（验证盲区已补）
```

### Step 3 — 删除两个旧整页（需 §4 决策 1）

```bash
# 前置确认（只在确认后执行）
grep -rn "workbench.html\|project-init.html" backend/ tests/ frontend/   # 应只剩历史注释
curl -sI http://127.0.0.1:8000/workbench | head -3                        # 期望 307
git rm frontend/workbench.html frontend/project-init.html
node --test tests/frontend/*.test.mjs
grep -rc "?v=" frontend/*.html frontend/assets/*   # 期望从 68 → 约 53
wc -l frontend/*.html frontend/assets/*            # 期望从 34,284 → 约 23,742
```

### Step 4 — CSS 死规则（needs-check，分批）

```bash
# 每批只动一个文件；每批后做 5 页截图对比（/three-mode 三模式 + /projects + /project-intake + /review + /diff-review）
# 4a styles.css：先删 .page-shell/.gallery-*/.list-*/.search-*（约 -400 行）
grep -rn "page-shell\|gallery-page\|list-row\|search-shell" frontend/     # 删除目标类应只剩 CSS 定义（删后归零）
# 4b styles.css：再删 .context-pane / .editor-* / .block-*（约 -900 行）
grep -rn "context-pane" frontend/*.html frontend/assets/*.js frontend/assets/*.mjs
# 4c review.css（约 -381 行）
grep -rn "review-banner\|review-grid\|score-pill\|card-section" frontend/
# 4d response-outline.css + workspace.css + chapter-studio.css
# 4e three-mode.html 内联 CSS：先抽 assets/three-mode.css，再删 85 个死块（约 -648 行）
grep -rn "status-bar-tools\|block-detail-\|ref-section" frontend/
```

每批验证：目标类 `grep` 归零 + 5 页截图 + `node --test tests/frontend/*.test.mjs`。

### Step 5 — 收敛活代码重复（needs-check）

```bash
# D3：抽 assets/model-config.js，projects.html 与 mode-outline.js 共用
# D1：活代码统一 import workspace-model.mjs:158 escapeHtml（先核对转义集差异）
# D5：明确编辑器左栏树唯一渲染入口（workbench.js），mode-editor 只调 window.__renderEditorTree()
# 验证：模型弹窗全流程 + 三模式各屏 + 流式拆分过程中左栏不丢选中
```

### Step 6 — 中期（不在本轮）

- E1/E2 God module 拆分；E3 内联 CSS 抽取；E7 版本号机制替换；D6 window 全局通道收敛；
- 每拆一个模块，先跑 `node --test tests/frontend/*.test.mjs`，再手工回归受影响的模式。

---

## 附录：本次审计执行的检查方法

- 引用图：对每个 assets 文件执行 `grep -rn "<文件名>" frontend/ tests/ backend/`，排除自身文件行；覆盖 HTML `<script src>`、CSS `<link>`、静态 import、动态 `import()`。
- 死函数：Python 解析每个模块顶层 `function`/`const fn` 声明，统计「本文件注释/字符串外的引用数 + 其它所有文件（含 tests/backend）的引用数」，两者均为 0 才记死；再逐个人工 `grep -n` 复核，避免把 `window.foo()`、模板字符串误判。
- 重复度：`difflib.SequenceMatcher` 对非空行比对；函数体按 brace 匹配逐字比较；CSS 用规则块扫描 + 选择器集合交集。
- CSS 死块：规则块（brace 匹配）→ 选择器 class/id → 引用语料（HTML 剥离内联 CSS 保留内联 JS + JS/MJS + tests）→ 全无引用判死；另检查 `'prefix-' + x` 与 `` `prefix-${x}` `` 动态构造，排除动态类误判。
- 路由可达性：`grep -n "@app.get" backend/main.py`、`grep -c "app.mount"`、`ls frontend/index.html`。
- 历史：`git log -1 --format='%ad' --date=short -- <file>`、`git log -S <symbol>`、`git show --stat <commit>`。
- 测试：`node --test tests/frontend/*.test.mjs`（160 passed）；测试契约审查逐个读 `tests/frontend/*.test.mjs` 的 import 与断言对象。
