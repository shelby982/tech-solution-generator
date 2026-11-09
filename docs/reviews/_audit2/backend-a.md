# 后端 A 审计（routes + services）

- 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`，审计基线 HEAD `a91b1e9`（2026-11-09；审计期间前移到 `299073c`，文件内容未变，见下条）
- 审计范围：`backend/routes/*.py`（24 个模块，**250** 个端点）、`backend/services/*.py`（**79** 个模块，17,597 行）
- 不在范围：`backend/infra|orchestrator|agents|domain|tests`（仅在需要证明「调用方已死/已漂移」时引用其行号）
- 工作区状态：审计开始时 `backend/db.py`、`backend/routes/chapter_studio.py`、`backend/services/block_store.py`、`backend/services/placeholder_purge.py` 有未提交修改（任务说明写 3 个，实际 **4** 个：`db.py` 也改了，新增 `block_purged_items` 表）。本报告**全部按「包含这 4 个改动的工作区内容」**核实，行号即该内容下的行号。
- **审计期间 HEAD 变动提示**：审计进行中这些改动被提交为 `299073c`（`fix(正文红线): 占位符全链路净化…`），HEAD 由 `a91b1e9` 前移。再次核对 `git diff` 为空、`_drop_empty_sections` 仍只出现 1 次（`placeholder_purge.py:198` 定义处），说明**文件内容与我审计时完全一致**，本报告行号对 `299073c` 与审计时工作区都成立。
- 方法：先把 250 个端点（含 `APIRouter(prefix=...)`）展开成完整路径，用「参数位宽松通配」正则扫 `frontend/**/*.{html,js,mjs}` 与 `tests/**/*.{py,mjs}`；再对同路径多方法、模板字符串拼接、`api.js` 封装层等易误判场景**逐条人工复核**。服务侧用 AST 取定义、用 token 倒排 + 死端点行号区间反查「只被死代码引用」的符号。

---

## 0 摘要

### 0.1 三类数量

| 类别 | 数量 | 说明 |
|---|---|---|
| 死代码 | **63 项** | 43 个无任何产品/测试引用的端点 + 20 组服务级符号（共 26 个函数/方法与常量组） |
| 无用代码 | **17 项** | 1 组 21 个未使用 import、14 个无用常量/空壳函数、1 处未使用变量＋重复净化、1 组 2 个死方法 |
| 与当前逻辑不一致 | **8 项** | 读写不对称、校验丢失、同逻辑多份实现已漂移、硬编码开关、常量口径不符 |
| 合计 | **88 项** | 表格行数；部分「不一致」项与「死代码」项指向同一处代码的不同侧面 |

### 0.2 严重度分布

- **高 8**：A01、A02、A03、A13、E01、E02、E03、E04
- **中 56**：A04–A10、A12、A16–A33、A35–A37、A39、A40、A42、A43、B01–B19、E05–E08
- **低 24**：A11、A14、A15、A34、A38、A41、B20、C01–C17
- 合计 88 行，与 0.1 的三类合计一致；每行严重度见表格第 7 列，可用第 5 节命令复核：`awk -F'|' '/^\| [ABCE][0-9]+ /{gsub(/ /,"",$8); print $8}' docs/reviews/_audit2/backend-a.md | sort | uniq -c`

### 0.3 最高危 5 条

1. **E01 `services/block_store.py:516` 与 `:1112` 同名函数重复定义**，后者覆盖前者（签名不同）；`orchestrator/nodes.py:639` 仍按 4 参数旧签名调用 → 运行时 `TypeError` 被宽 `except` 吞掉，匹配阶段的依据**从不落库**。而 `GET /blocks/{id}/matched-sources`（前端仍在用）读的正是这一列。
2. **E02 `blocks.source` 写入路径已死、读取路径仍活**：唯一写入者 `POST /projects/{id}/map-sources`（`materials.py:687`）只有 `api.js:164` 封装且封装本身无人调用；读取者 `GET /blocks/{id}/matched-sources`（`blocks.py:63`）被 `workbench.html:4517`、`mode-editor.js:2141` 活跃调用。
3. **E03 上传体积上限丢失**：唯一的大小校验在死模块 `routes/upload.py:35,126`（50 MB），活跃上传入口 `routes/materials.py:450` 在 `:486` 直接 `await file.read()` 无上限 → 大文件可打爆内存。
4. **E04 引用过滤 SQL 四份实现、两种语义**：`services/citations.py:493` 用 `if False else` 硬编码留一条死分支，且与 `:379`、`:388`、`:418` 的「OR `citations.block_id`」语义不一致 —— 只挂在 `citations.block_id` 上的历史引用在新 `list_citations` 里查不到。
5. **A01–A03 `routes/upload.py` 整模块（292 行／3 端点）已是死管线**，但 `main.py:243` 仍注册；其依赖 `utils/sse.py` 的 `sse_parse_*` 4 个函数随之全死，且与 `materials.py` 的上传链路（`ALLOWED_SUFFIXES`／`ALLOWED_EXTENSIONS` 两份常量）长期并存。

---

## 1 逐项清单

> 「规模」= `wc -l` 的模块行数，或 AST 取的函数行跨度（`起-止`）。所有行号均为当前工作区行号。
> 证据中的 grep 均为在仓库根执行的实际命令与实测结果。

### 1.1 死端点（43）

匹配口径：完整路径在 `frontend/**/*.{html,js,mjs}` 与 `tests/**/*.{py,mjs}` 中**零命中**；对同路径多方法、`'...' + id` 拼接、`api.js` 封装层三种误判来源逐条人工复核（见第 3 节）。

| ID | 文件:行 | 类别 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|---|
| A01 | `backend/routes/upload.py:100` | 死代码 | 死端点（旧管线） | 292 行模块 | `grep -rn "api/upload" frontend tests` → 0；`grep -rn "/upload" frontend` 仅 `bench.html:250` 的 `/bench/upload/`（不同服务） | 高 | 整模块 + `main.py:243` 注册一并删除 |
| A02 | `backend/routes/upload.py:150` | 死代码 | 死端点（SSE） | 同上 | `grep -rn "parse/stream" frontend tests` → 0 | 高 | 同上；连带 `utils/sse.sse_parse_*` |
| A03 | `backend/routes/upload.py:285` | 死代码 | 死端点 | 同上 | `grep -rn "supported-formats" frontend tests` → 0 | 高 | 同上 |
| A04 | `backend/routes/changelog.py:65` | 死代码 | 死模块（1 端点） | 71 行模块 | `grep -rn "changelog" frontend tests --include=*.{py,js,mjs,html}` → 0（前端「版本记录」走 revisions API，非它） | 中 | 删模块 + `main.py:245` |
| A05 | `backend/routes/config.py:103` | 死代码 | 旧单配置 API | 24 行（104-127） | `grep -rn "/api/config'" frontend tests` → 0 | 中 | 删；`config_store.get/save` 随之死 |
| A06 | `backend/routes/config.py:130` | 死代码 | 旧单配置 API | 6 行 | 同上，`grep -rn "/api/config\`"` → 0 | 中 | 删 |
| A07 | `backend/routes/config.py:139` | 死代码 | 旧单配置 API | 16 行 | `grep -rn "/api/config/check" frontend tests` → 0 | 中 | 删 |
| A08 | `backend/routes/config.py:158` | 死代码 | 旧单配置 API | 5 行 | `grep -rn "/api/config"` 命中全为 `/api/configs`；旧路径 0 | 中 | 删 |
| A09 | `backend/routes/config.py:166` | 死代码 | 旧单配置 API | 70 行（167-236） | `grep -rn "/api/config/providers" frontend tests` → 0 | 中 | 删（提供商清单前端硬编码在 `loadModelOptions`） |
| A10 | `backend/routes/config.py:268` | 死代码 | 旧接口 | 12 行 | `grep -rn "/api/configs/reorder" frontend tests` → 0 | 中 | 删；`config_store.reorder` 随之死 |
| A11 | `backend/routes/config.py:283` | 死代码 | 死方法（同路径仅 DELETE 在用） | 20 行 | 前端仅 `DELETE /api/configs/{id}`（`workbench.html:5535` 等 5 处）；`grep -rn "method: 'PUT'" 附近无 configs` | 低 | 删 PUT，或补前端编辑入口 |
| A12 | `backend/routes/materials.py:674` | 死代码 | 旧 410 占位端点 | 11 行（674-684） | 端点恒 `raise HTTPException(410)`；唯一引用是 `api.js:136` 的 `outline:` 封装，而 `grep -rn "api.outline" frontend` → 0、`grep -rn "\.outline(" frontend` → 0 | 中 | 删端点 + 删 `api.js:136` 起的 `outline` 封装 |
| A13 | `backend/routes/materials.py:687` | 死代码 | 旧管线写入端 | 38 行（688-725） | `grep -rn "map-sources" frontend` → 1（`api.js:165` 封装定义）；`grep -rn "mapSources(" frontend` → 0 | 高 | 删端点；见 E02 |
| A14 | `backend/routes/blocks.py:90` | 死代码 | 旧检索端点 | 17 行 | `grep -rn "material-search" frontend tests` → 0（`tests/services/test_material_search.py` 只 import 服务函数，不调端点） | 低 | 删端点，保留服务函数 |
| A15 | `backend/routes/chapter_sse.py:267` | 死代码 | 死端点 | 16 行（268-283） | `grep -rn "rewrite-selection" frontend tests` → 0 | 低 | 删；`chapter_sse.stream_chapter_write` 仅被它引用 |
| A16 | `backend/routes/chapter_studio.py:370` | 死代码 | 死端点 | 9 行 | `grep -rn "self-heal" frontend tests` → 0 | 中 | 删；`heal_block_outline` 随之死 |
| A17 | `backend/routes/chapter_studio.py:382` | 死代码 | 死方法（GET，POST 在用） | 15 行（383-397） | 前端只 `POST /studio/outline`（`chapter-studio.mjs:1536,1591`） | 中 | 删 GET |
| A18 | `backend/routes/chapter_studio.py:516` | 死代码 | 死端点 | 4 行 | `grep -rn "section-results" frontend` → 0 | 中 | 删；`section_results.list_results` 随之死 |
| A19 | `backend/routes/chapter_studio.py:713` | 死代码 | 死端点 | 23 行 | `grep -rn "align-outline" frontend tests` → 0 | 中 | 删；`body_align.align_to_outline` 随之死 |
| A20 | `backend/routes/chapter_studio.py:739` | 死代码 | 死端点（与 SSE 版并存） | 42 行（740-781） | 前端只走 `GET /studio/section/stream`（`chapter-studio.mjs:3772` EventSource） | 中 | 删非流式版 |
| A21 | `backend/routes/chapter_studio.py:1190` | 死代码 | 死端点（重复实现） | 52 行（1191-1242） | `grep -rn "api/projects/\${projectId}/todo\`" frontend` → 0（前端只用 `/todo/overview`、`/todo/state`、`/todo/apply`、`/todo/recheck`，`chapter-studio.mjs:3101,3978,4077,5799`） | 中 | 删；项目待办只留 `todo_v3.project_overview` |
| A22 | `backend/routes/chapter_writing.py:77` | 死代码 | 死端点 | 7 行 | 前端只调 `writing-plans/{id}/coverage`、`/needs`、`/requirements`、`/needs/bind`（`chapter-studio.mjs:1093,1094,1264,1642`）；无裸 GET | 中 | 删 |
| A23 | `backend/routes/chapter_writing.py:168` | 死代码 | 死端点 | 8 行 | `grep -rn "confirm-chapter" frontend tests` → 0 | 中 | 删；`confirm_chapter_plan` 随之死 |
| A24 | `backend/routes/chapter_writing.py:179` | 死代码 | 死端点 | 10 行 | `grep -rn "figures-plan" frontend tests` → 0 | 中 | 删；`update_figures_plan` 随之死 |
| A25 | `backend/routes/chapter_writing.py:220` | 死代码 | 死端点 | 8 行 | 无 `writing-plans/{id}/confirm` 调用 | 中 | 删；`confirm_plan` 随之死 |
| A26 | `backend/routes/chapter_writing.py:273` | 死代码 | 死方法（GET 列表） | 8 行（274-281） | 前端只 `POST /studio/writing-jobs`（`chapter-studio.mjs:4746`）与 `GET .../{jobId}`（4753） | 中 | 删 GET 列表 |
| A27 | `backend/routes/citations.py:130` | 死代码 | 死端点 | 7 行 | `grep -rn "citations/.*/link" frontend tests` → 0（前端仅用 `citations/{id}/image`、`/refresh`、`/select`、`/suggest`、`/history`、`/portals`、`/discover`、`/fetch`） | 中 | 删；`link_citation` 随之死 |
| A28 | `backend/routes/citations.py:140` | 死代码 | 死端点 | 3 行 | 同上 | 中 | 删；`unlink_citation` 随之死 |
| A29 | `backend/routes/citations.py:194` | 死代码 | 死端点 | 6 行 | `grep -rn "citations/search" frontend tests` → 0 | 中 | 删；`search_official` 随之死 |
| A30 | `backend/routes/citations.py:225` | 死代码 | 死端点 | 5 行 | `grep -rn "reject"` 在 citations 上下文 0 | 中 | 删；`reject_citation` 随之死 |
| A31 | `backend/routes/material_library.py:554` | 死代码 | 死端点（重切子系统） | 11 行 | `grep -rn "materials/recut" frontend tests` → 0 | 中 | 删；`chunk_quality.recut_material`（49 行）随之死 |
| A32 | `backend/routes/material_library.py:568` | 死代码 | 死端点（重切子系统） | 12 行 | `grep -rn "recut-all" frontend tests` → 0 | 中 | 删 |
| A33 | `backend/routes/material_library.py:701` | 死代码 | 死端点 | 18 行 | `grep -rn "citations/swap" frontend tests` → 0 | 中 | 删（换引用走 `citations/fill`） |
| A34 | `backend/routes/material_library.py:930` | 死代码 | 死端点（项目级） | 29 行（931-959） | 前端只用章级 `/blocks/{id}/materials/needs`（`chapter-studio.mjs:764`） | 低 | 删 |
| A35 | `backend/routes/todo_v3.py:54` | 死代码 | 死端点（与 A21 同功能） | 3 行 | `grep -rn "todo/chapter" frontend tests` → 0 | 中 | 删 |
| A36 | `backend/routes/todo_v3.py:167` | 死代码 | 死端点 | 18 行 | `grep -rn "delivery-gate/auto" frontend tests` → 0 | 中 | 删；`delivery_gate.auto_fix` 随之死 |
| A37 | `backend/routes/todo_v3.py:203` | 死代码 | 死端点 | 18 行 | 同上 `/fix` → 0 | 中 | 删 |
| A38 | `backend/routes/review_batch.py:29` | 死代码 | 死方法（GET 最新任务） | 4 行 | 前端只 `POST /reviews/batch`（`review.js:114`）与 `GET .../{jobId}`（124） | 低 | 删 |
| A39 | `backend/routes/review_batch.py:53` | 死代码 | 死端点 | 6 行 | `grep -rn "issues/summary" frontend tests` → 0 | 中 | 删；`review_issues.project_summary` 随之死 |
| A40 | `backend/routes/diagram_assets.py:221` | 死代码 | 死端点 | 13 行（222-234） | `grep -rn "diagrams/.*regenerate" frontend tests` → 0（前端只用 `/svg`，`chapter-studio.mjs:2346` 等 3 处） | 中 | 删 |
| A41 | `backend/routes/projects.py:240` | 死代码 | 死端点 | 9 行 | `grep -rn "basis" frontend/assets/api.js` → 0；全前端 0 | 低 | 删（`quality_check.basis_coverage` 仍被 `writing_plans.py:1096` 使用，勿删） |
| A42 | `backend/routes/writing_settings.py:28` | 死代码 | 死方法（GET 项目级） | 5 行 | 前端只 `PUT /projects/{id}/writing-settings`（`chapter-studio.mjs:3271`）；读路径走 `GET /blocks/{id}/writing-settings`（3823） | 中 | 删；`project_settings`、`ROLE_DEFAULTS` 随之死 |
| A43 | `backend/routes/workflow.py:361` | 死代码 | 死端点 | 19 行（362-380） | `grep -rn "analyze-points" frontend tests` → 0（前端用 `/redraft-outline` + `/confirm-outline`） | 中 | 删；确认无脚本引用后再删 |

### 1.2 死／无用服务符号与常量（37）

「仅被死端点引用」= 用 AST 取死端点函数体的行号区间，反查服务符号的全部调用点，全部落在区间内。

| ID | 文件:行 | 类别 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|---|
| B01 | `backend/services/writing_plans.py:1364` | 死代码 | 仅被 A22 引用 | 8 行 | `grep -rn "load_plan" backend tests` → 仅 `chapter_writing.py:82`（A22 体内） | 中 | 删 |
| B02 | `backend/services/writing_plans.py:1374` | 死代码 | 仅被 A25 引用 | 58 行 | 仅 `chapter_writing.py:226`（A25 体内） | 中 | 删 |
| B03 | `backend/services/writing_plans.py:586` | 死代码 | 仅被 A23 引用 | 27 行 | 仅 `chapter_writing.py:174`（A23 体内） | 中 | 删 |
| B04 | `backend/services/writing_plans.py:637` | 死代码 | 仅被 A24 引用 | 26 行 | 仅 `chapter_writing.py:185`（A24 体内） | 中 | 删 |
| B05 | `backend/services/chunk_quality.py:305` | 死代码 | 仅被 A31/A32 引用 | 49 行（305-353） | 仅 `material_library.py:565,579` | 中 | 删；连同 `recut` 路由 |
| B06 | `backend/services/material_search.py:93` | 死代码 | 仅被 A14 引用 | 56 行（93-148） | 仅 `blocks.py:107`；但 `extract_terms`/`score_chunk` 仍被 4 个活服务使用（见第 3 节） | 中 | 删该函数，保留模块其余部分 |
| B07 | `backend/services/section_results.py:32` | 死代码 | 仅被 A18 引用 | 15 行 | 仅 `chapter_studio.py:520`；模块其余符号被 `chapter_sse.py` 使用 | 中 | 删该函数 |
| B08 | `backend/services/outline_self_heal.py:7` | 死代码 | 仅被 A16 引用 | 40 行（7-46） | 仅 `chapter_studio.py:376`；`heal_thread_outlines`（:49）仍被 `workflow.py:178` 使用 | 中 | 删 `heal_block_outline` |
| B09 | `backend/services/delivery_gate.py:189` | 死代码 | 仅被 A36/A37 引用 | 28 行（189-216） | `grep -rn "auto_fix"` → 仅 `todo_v3.py:178,214` | 中 | 删；产品已无「一键修复」入口 |
| B10 | `backend/services/citations.py:514` | 死代码 | 仅被 A27 引用 | 13 行 | 仅 `citations.py:135` | 中 | 删 |
| B11 | `backend/services/citations.py:529` | 死代码 | 仅被 A28 引用 | 8 行 | 仅 `citations.py:143` | 中 | 删 |
| B12 | `backend/services/citations.py:539` | 死代码 | 仅被 A30 引用 | 6 行 | 仅 `citations.py:228` | 中 | 删 |
| B13 | `backend/services/citations.py:253` | 死代码 | 仅被 A29 引用 | 25 行 | 仅 `citations.py:198`；`search_configured` 仍被 `material_library.py:200,245` 使用 | 中 | 删 |
| B14 | `backend/services/body_align.py:65` | 死代码 | 仅被 A19 引用 | 24 行 | 仅 `chapter_studio.py:731` | 中 | 删 |
| B15 | `backend/services/section_outline.py:327` | 死代码 | 仅被 A17 引用 | 19 行（327-345） | `grep -rn "ensure_outline_elements"` → 仅 `chapter_studio.py:396`（A17 体内） | 中 | 删 |
| B16 | `backend/services/review_issues.py:155` | 死代码 | 仅被 A39 引用 | 14 行（155-168） | 仅 `review_batch.py:59`；`sync_issues`/`list_issues` 仍活 | 中 | 删 `project_summary` |
| B17 | `backend/services/writing_settings.py:40` | 死代码 | 仅被 A42 引用 | 4 行 | 仅 `writing_settings.py:31`；`block_settings`/`resolve_model`/`save_settings` 仍活 | 中 | 删 |
| B18 | `backend/services/config_store.py:258,263,273,147,158` | 死代码 | 旧单配置方法 | 5 个方法 | `get`（A05/A06/A07）、`save`（A05）、`clear`（A08）、`update`（A11）、`reorder`（A10）—— 全部调用点都在死端点内 | 中 | 5 个方法删除；`verify_all`（main.py:192）与 `get_configs_and_next_index` 必须保留 |
| B19 | `backend/services/placeholder_purge.py:198` | 死代码 | 工作区新增但无调用 | 23 行（198-220） | `grep -rn "_drop_empty_sections" backend tests` → 仅定义处 1 命中（本次未提交改动新增，无人调用） | 中 | 要么接线到 `purge()`，要么删除（docstring 承诺的「没有的内容就不要体现」当前未生效） |
| B20 | `backend/services/block_store.py:137` | 死代码 | 无任何调用 | 18 行（137-154） | `grep -rn "list_materials_by_ids" backend tests` → 仅定义处 | 低 | 删 |
| C01 | 21 处 | 无用代码 | 未使用 import | 21 行 | 见下表 | 低 | 批量删 |
| C02 | `backend/routes/material_library.py:345` | 无用代码 | 占位常量 | 1 行 | `_CITE_IN_BODY = None`，全仓仅此 1 命中 | 低 | 删 |
| C03 | `backend/services/material_context.py:13` | 无用代码 | 死常量 | 1 行 | `MODE_LABELS` 全仓仅定义处 1 命中 | 低 | 删 |
| C04 | `backend/services/material_context.py:68` | 无用代码 | 死常量 | 3 行（68-70） | `NO_MATERIAL_HINT` 同上 | 低 | 删 |
| C05 | `backend/services/delivery_verdict.py:9` | 无用代码 | 死常量 | 1 行 | `LEVEL_ORDER` 仅定义处 | 低 | 删 |
| C06 | `backend/services/deployment_diagram.py:23` | 无用代码 | 死常量 | 1 行 | `ZONE_H` 仅定义处 | 低 | 删 |
| C07 | `backend/services/writing_plans.py:109` | 无用代码 | 死常量 | 1 行 | `REQUIREMENT_PREFIX` 仅定义处 | 低 | 删 |
| C08 | `backend/services/todo_tasks.py:22` | 无用代码 | 死常量 | 1 行 | `_ACTION_LABEL` 仅定义处 | 低 | 删 |
| C09 | `backend/services/page_budget.py:114,146` | 无用代码 | 死函数 | 28 行（114-122、146-164） | `_parent_key`、`rollup_totals` 仅定义处（后者只被前者之外无人调用） | 低 | 删 |
| C10 | `backend/services/production_diagrams.py:142` | 无用代码 | 死私有函数 | 12 行（142-153） | `_edge` 仅定义处 | 低 | 删 |
| C11 | `backend/services/delivery_gate.py:219` | 无用代码 | 死私有函数 | 3 行（219-221） | `_is_legal_block_start` 仅定义处 | 低 | 删 |
| C12 | `backend/services/section_context.py:219` | 无用代码 | 死函数 | 4 行 | `coverage_for_section` 仅定义处 | 低 | 删 |
| C13 | `backend/services/writing_workspace.py:67` | 无用代码 | 死函数 | 14 行（67-80） | `execution_outline` 仅定义处 | 低 | 删 |
| C14 | `backend/services/writing_specs.py:130` | 无用代码 | 死函数 | 4 行 | `_row_dict` 仅定义处 | 低 | 删 |
| C15 | `backend/services/usage_store.py:81` | 无用代码 | 死函数 | 2 行 | `uninstall` 仅定义处 | 低 | 删 |
| C16 | `backend/routes/chapter_studio.py:658,772` | 无用代码 | 未使用变量＋重复净化 | 2 处 | `text, _purged = purge(text)` 后 `_purged` 从未使用；且随后 `update_block_content`（`block_store.py:461`）会再净化一次 | 低 | 改成 `text, _ = purge(text)`，或删掉这层（由 `update_block_content` 统一净化） |
| C17 | `backend/services/config_store.py:115,169` | 无用代码 | 死方法（测试外无引用） | 2 个方法 | `save_all` 仅 `tests/services/test_config_store.py` 使用；`get_next_index` 全仓仅定义处 | 低 | 删 `get_next_index`；`save_all` 若无测试需要一并删 |

**C01 的 21 处未使用 import**（AST 判定后逐条 `grep -c` 复核，均只命中 import 行本身）：

```
routes/chapter_studio.py:11  JSONResponse
routes/config.py:31          OPENAI_COMPATIBLE_PROVIDERS
routes/materials.py:16       StreamingResponse
routes/materials.py:19       create_block, delete_blocks_by_project, update_material_parsed, list_chunks_by_materials
routes/materials.py:35       assign_chunks_to_sections
routes/materials.py:36       format_sse_event
routes/todo_v3.py:4          re
routes/workflow.py:19        Any, Optional
services/chapter_writing.py:8   content_hash
services/deployment_diagram.py:13 Path
services/evidence_needs.py:8    json
services/image_material.py:6    io
services/plan_ai.py:16          get_block
services/point_store.py:29      Optional
services/review_batch.py:9      json
services/section_context.py:13  re
services/writing_workspace.py:6 re
```

其中 `routes/materials.py` 的 6 个残留 import（`StreamingResponse`、`create_block`、`format_sse_event`、`assign_chunks_to_sections` 等）正是已删除的「outline SSE 生成」管线的化石，与 A12 同源。

### 1.3 与当前逻辑不一致（8）

| ID | 文件:行 | 类别 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|---|
| E01 | `backend/services/block_store.py:516` / `:1112` | 不一致 | 同名重复定义互相覆盖 | 19 行 + 16 行 | `grep -n "async def update_block_source" services/block_store.py` → 516、1112 两处，签名分别为 `(db, project_id, block_id, sources)` 与 `(db, block_id, rows)`；后者生效。`orchestrator/nodes.py:639` 以 `update_block_source(db, project_id=…, block_id=…, sources=…)` 调用 → `TypeError`，被 `nodes.py:640` 的 `except Exception` 吞成 warning「source 落库失败」 | 高 | 删 :516 旧版；`nodes.py` 改调 `(db, row_id, rows)` 语义 |
| E02 | `backend/routes/blocks.py:63` ↔ `backend/routes/materials.py:687` | 不一致 | 读端活着、写端已死 | 读 27 行 / 写 38 行 | 读：`grep -rn "matched-sources" frontend` → `workbench.html:4517`、`mode-editor.js:2141`；写：`grep -rn "mapSources(" frontend` → 0 | 高 | 二选一：删读端（旧 `blocks.source` 依据展示）并在前端切到 `/blocks/{id}/materials/evidence`；或恢复写端 |
| E03 | `backend/routes/materials.py:486` | 不一致 | 校验随旧管线丢失 | 1 行 | 唯一大小校验：`grep -rn "MAX_FILE_SIZE" backend --include=*.py` → 3 命中全在 `routes/upload.py`（35/126/290，死模块）；活跃入口 `materials.py:486` 直接 `await file.read()` | 高 | 把 `MAX_FILE_SIZE` 校验迁到 `upload_material`（先 `content-length` 预判，再 `len(content)` 兜底） |
| E04 | `backend/services/citations.py:493` | 不一致 | 同逻辑 4 份实现已漂移 | 6 行（488-494） | 该处写成 `("... block_id = ? OR id IN (SELECT ...) " if False else " AND EXISTS (... l.block_id = ?)")`，硬编码走 EXISTS 分支；同一「本章引用」过滤在 `:379`、`:388`、`:418` 仍是「OR `citations.block_id`」 | 高 | 抽一个 `_chapter_filter()` 供 4 处共用，语义统一为「block_id 列 OR citation_links」 |
| E05 | `frontend/assets/api.js:136` ↔ `backend/routes/materials.py:674` | 不一致 | 死客户端封装 + 410 端点成对残留 | 25 行 JS（`api.js:136-160`）+ 11 行 PY | `grep -rn "api.outline" frontend` → 0；端点仍注册，任何人恢复调用只会拿到 410 | 中 | 同 A12，成对删除 |
| E06 | `backend/routes/chapter_studio.py:1190` ↔ `backend/routes/todo_v3.py:99` | 不一致 | 项目级待办两份实现 | 52 行 / 68 行 | 前端只调 `/todo/overview`（`chapter-studio.mjs:3101`）；`/todo`（A21）零调用但仍在维护同一份 `writing_plans.chapter_plan` 聚合逻辑 | 中 | 删 A21，保留 `project_overview` |
| E07 | `backend/routes/chapter_studio.py:739` ↔ `:523` | 不一致 | 同一「写本节」两份实现 | 42 行 / 190 行 | 前端只走 SSE 版（`chapter-studio.mjs:3772`）；非流式版仍会写库、写 revision，两套写库序列（净化→替换→revision→checkpoint）已不一致（A20 版在 `:772` 调 `purge`，SSE 版在 `:658` 也调，但 `_purged` 都丢弃） | 中 | 删非流式版，或把写库序列抽成共享函数 |
| E08 | `backend/routes/config.py:31` | 不一致 | 常量口径不符 | 1 行 | `_ALLOWED_PROVIDERS`（config.py:43）＝ 6 个；`OPENAI_COMPATIBLE_PROVIDERS`（config_store.py:87）＝ 5 个；`JUDGMENT_PROVIDERS`（:92）另含 `typesafe`。config.py 导入了 `OPENAI_COMPATIBLE_PROVIDERS` 却从未使用（C01），说明「哪些 provider 合法」已无单一来源 | 中 | 统一到 `config_store` 一处导出（含 `typesafe`），`POST /config` 删除后只需保留 `_validate` 一处 |

---

## 2 高危逐项详述

### E01 同一个 `update_block_source` 定义两次，第一个永远不可达（数据不落库）

- **是什么**：`services/block_store.py` 在 `:516` 和 `:1112` 各定义一次 `async def update_block_source`。Python 后定义覆盖前定义，运行时生效的是 `(db, block_id: int, rows: list[dict])`。
- **为何是问题**：`:516` 的旧版是 `(db, project_id, block_id: str, sources)`，`orchestrator/nodes.py:639` 仍在按旧签名以关键字参数调用（`project_id=`、`sources=`）。参数名不匹配 → `TypeError: update_block_source() got an unexpected keyword argument 'project_id'`，而调用点 `nodes.py:640-643` 是 `except Exception as e: logger.warning(...)`，异常被静默吞掉。
- **当前影响**：每轮「沈括匹配」结束后，`MATCH_MODE` 走默认分支时章节依据**不会写入 `blocks.source`**，只留在 checkpoint；页面刷新后 `GET /api/blocks/{id}/matched-sources`（`blocks.py:63`，读 `block.get("source")`）返回空/旧值。日志里只会看到一行 `match_node：source 落库失败`。`orchestrator/runner.py:953` 的人工挂接路径用的是新签名，不受影响 —— 于是同一列出现「自动匹配写不进、人工挂接写得进」的半瘫痪状态。
- **怎么改**：删除 `:516-534` 旧定义（先确认没有脚本/测试按旧签名调用：`grep -rn "update_block_source" backend tests scripts` 当前只有 nodes.py、runner.py 两处）；把 `nodes.py:639` 改为取 `row_id` 后 `await update_block_source(db, row_id, rows)`；再补一条断言式单测（同模块不得有重名顶层函数，可用 `ast` 实现，正好覆盖本次审计发现的 1 处）。

### E02 旧依据列「写端已死、读端仍活」

- **是什么**：`blocks.source` 这一 JSON 列是旧「批量匹配」管线的产物。写它的活跃入口只剩 `POST /api/projects/{id}/map-sources`（`materials.py:687`，内部 `:715 UPDATE blocks SET source`），而它在前端只剩一个**从未被调用的封装** `api.mapSources`（`api.js:164`）；新的依据体系是 `chapter_material_chunks` + `evidence_need_actions` + `/blocks/{id}/materials/evidence*`。
- **为何是问题**：读端 `GET /api/blocks/{block_id}/matched-sources`（`blocks.py:63`）被两个活跃页面调用（`workbench.html:4517`、`mode-editor.js:2141`）。写端死了以后，这个接口要么返回历史残留（老项目还能看到过期依据），要么返回空（新项目必然空），UI 上会出现「本章有依据/无依据」与实际 `evidence-board` 不一致。
- **当前影响**：用户在「工作台 → 找素材」看到的匹配列表与新「依据板」不同步；叠加 E01 后，连自动匹配也写不进这一列，问题被放大成「整条旧展示链路失效但没人报错」。
- **怎么改**：确认 `matched-sources` 的消费字段后，把 `workbench.html:4517`、`mode-editor.js:2141` 切到 `/blocks/{id}/materials/evidence-board`（该接口活跃、数据源正确），随后删 `map-sources` 端点与 `api.mapSources`；若短期不能改前端，至少在 `map_sources` 里改为写 `chapter_material_chunks`，让两条链路同源。

### E03 上传大小上限只存在于死模块

- **是什么**：`routes/upload.py:35` 定义 `MAX_FILE_SIZE = 50MB`，`:126` 校验并返回 413。这个模块的 3 个端点全部零引用（A01–A03）。活跃上传入口 `routes/materials.py:450 upload_material` 在 `:486` 直接 `content = await file.read()`，全后端再无任何大小判断（`grep -rn "MAX_FILE_SIZE|max_size|content_length" backend` 仅命中死模块与一个无关字段）。
- **为何是问题**：`await file.read()` 会把整个请求体读进内存；没有上限意味着一个几百 MB 的 PDF 就能把 worker 内存打满（uvicorn 多 worker 时更糟）。这是「旧代码删一半」造成的安全/稳定性回归：文档里仍写着 50MB（`upload.py:290` 的死端点曾把该值喂给 UI）。
- **当前影响**：任何已登录用户（`/api/projects/{id}/materials` 无鉴权，见 `main.py`）都可触发；同时 `materials.py` 会把整份内容 `write_bytes` 落盘，磁盘也无配额。
- **怎么改**：在 `upload_material` 开头加 `content-length` 预检（>50MB 直接 413），读取后再 `len(content)` 兜底；把 `MAX_FILE_SIZE` 挪到 `materials.py`（或 `domain/` 常量模块）作为唯一来源；删掉 `upload.py` 后这条校验才不会再次丢失。

### E04 引用列表的「本章过滤」有 4 份实现、2 种语义

- **是什么**：`services/citations.py` 里同一句「本章的引用」过滤出现 4 次：

  - `:379`、`:388`（`suggest_query`）：`project_id=? AND (block_id=? OR EXISTS(SELECT 1 FROM citation_links l WHERE l.citation_id=citations.id AND l.block_id=?))`
  - `:418`（`save_citation` 去重）：同上 OR 形式
  - `:493`（`list_citations`）：写成 `(...) if False else " AND EXISTS (...)"`，硬编码只走 EXISTS 分支

- **为何是问题**：`db.py:59` 的 `citations` 表至今保留 `block_id` 列（`:202` 注释明说「citations.block_id 保留为『首个关联』」）。`list_citations` 不再看这一列，于是**只写过 `citations.block_id`、没有 `citation_links` 行的引用在全量列表里消失**；而 `suggest_query`/`save_citation` 还认它。`if False` 是明显的「先关掉旧分支、后忘了收尾」，两套语义并存且无人知道哪个是对的。
- **当前影响**：前端 `chapter-studio.mjs:1039` 用 `GET /citations?status=accepted` 拉本章引用，之后 `:1829` 又用 `POST /citations` 新建（只写 `block_id`）——新写入的引用**当次可能不出现在列表里**，表现为「刚选的引用刷新后不见了」。同时 `args.append(block_id)` 只补 1 个占位符，若有人把 `if False` 翻成 `if True`，参数个数立刻不匹配 → 500。
- **怎么改**：抽 `_chapter_clause(alias="citations")` 返回 `(" AND (block_id=? OR EXISTS(...))", [bid, bid])`，4 处共用；顺手统一 `save_citation` 是否同时写 `citation_links`，让「首个关联」不再靠人工同步。

### A01–A03 `routes/upload.py` 整模块死管线（含连带死代码）

- **是什么**：292 行、3 个端点（`POST /upload`、`GET /parse/stream/{id}`、`GET /upload/supported-formats`）、`ParseJobStore`/`ParseJobStatus`/`ParseJob` 三个类型，`main.py:243` 仍在注册。全部零引用（`grep -rn "api/upload" frontend tests` → 0）。
- **为何是问题**：不是「无害的旧文件」——它吞掉了几样仍然有价值的东西并导致漂移：(1) 50MB 校验（E03）；(2) 扩展名白名单另一份 `ALLOWED_EXTENSIONS`（`upload.py:34`）与活跃的 `materials.py:44 ALLOWED_SUFFIXES` 重复维护；(3) 它独占的 `utils/sse.py` 四个 `sse_parse_*` 函数（`grep -rn "sse_parse_" backend` 仅 `utils/sse.py` 与 `upload.py`）变成无人验证的死代码；(4) `POST /upload` 曾在文档/示例里作为入口，新人按它接会得到「上传成功但库里没有材料」。
- **当前影响**：注册着但不被调用 → 每次启动多加载一个 router、多一份 50MB 内存态任务表；更重要的是它给了「上传已有实现」的错觉，掩盖了 `materials.py` 缺校验的事实。
- **怎么改**：先迁走大小校验（E03），再删 `routes/upload.py`、`main.py:243` 与 `infra/parser` 无关（`infra.parser.parse_document` 仍被 `agents/zhang_heng.py:302` 使用，**不要**删）；同时删 `utils/sse.py` 的 4 个 `sse_parse_*`（其余 `format_sse_event`/`sse_error` 仍被活跃路由使用，注意区分）。

---

## 3 已核实「看起来像死代码但其实活着」的排除项

这些项在初次宽松扫描里会被判成死代码，人工复核后**确认活着**，列此备查（避免下一轮审计重复误报）：

| 对象 | 初次误判原因 | 实际引用位置 |
|---|---|---|
| `DELETE /api/blocks/{block_id}/studio/materials/{material_id}`（`chapter_studio.py:227`） | 路径里 `material_id` 由 `'/'+m.id` 拼接，正则匹配不到 | `frontend/assets/chapter-studio.mjs:5922`：`` `/api/blocks/${block.id}/studio/materials${material.dataset.material==='unlink'?'/'+m.id:''}` `` |
| `services/material_search.py` 模块 | 端点 A14 已死 | `extract_terms`/`score_chunk` 被 `services/evidence_needs.py:39`、`services/coverage_review.py:28`、`services/section_context.py:141,227`、`services/material_rerank.py:75` 使用；仅 `search_block_materials`（B06）死 |
| `services/config_store.py` 模块 | 旧接口 A05–A11 全死 | `get_all`（`routes/config.py:246,275`、`services/diagram_design.py:351`）、`get_configs_and_next_index`（`blocks.py:258,347`、`chapter_studio.py:625`、`chapter_writing.py:236`、`materials.py:745`）、`is_configured`（`blocks.py:293,336`）、`verify_all`（`main.py:192`） |
| `services/section_results.py` 模块 | `list_results` 被死端点 A18 引用 | `cited_sources`/`save_result` 被 `services/chapter_sse.py` 使用 |
| `services/outline_self_heal.py` 模块 | `heal_block_outline` 被死端点 A16 引用 | `heal_thread_outlines` 被 `routes/workflow.py:178` 使用 |
| `services/chapter_studio.py` 的 `extract_material`/`material_key_points` | 只在 `routes/chapter_studio.py` 出现 | 在 `routes/chapter_studio.py:84,89`（`source_for` 辅助函数，被活跃的 `get_studio`/`draft_chapter` 调用） |
| `services/diagrams.py` 的 `TYPE_LABELS` | 只在 `routes/chapter_studio.py` 出现 | `:265,273`，位于活跃的 `POST /blocks/{id}/studio/diagram`（`chapter_studio.py:256`） |
| `GET /api/studio/diagrams/{id}/svg` | 与 `/regenerate`、`/image`、`/preview` 同前缀，易整组误判 | `frontend/assets/chapter-studio.mjs:2346,2486,2619`（`下载 SVG`）；仅 `regenerate`/`image`/`preview`/`DELETE` 是测试专用或死 |
| `tests` 专用但产品无调用的端点（9 个） | — | `DELETE /blocks/{id}/studio/materials/{id}` 以外的：`POST /studio/coverage-review`（`tests/test_coverage_review.py:72`）、`POST /studio/draft`（`tests/routes/test_chapter_studio.py:136`）、`POST /studio/writing-jobs/{id}/cancel|retry`（`tests/routes/test_chapter_writing.py:99,163`）、`DELETE /studio/diagrams/{id}`、`GET /studio/diagrams/{id}/image|preview`、`GET /blocks/{id}/materials/evidence`。**未计入死代码**，但属「只有测试在保护、产品已无入口」，建议在 CI 里标注或补产品入口 |
| 2026-10-19 之后新增的 10 个 routes 模块 | 疑为「新代码没人用」 | 全部有活跃调用，仅个别端点死：`chapter_sse`（`create_chapter_write` 等 9 个端点在 `chapter-studio.mjs`）、`citations`（4 个活）、`diagram_assets`（`/svg` 活）、`leads`（`grep -rn "api/leads" frontend tests` → 8 处）、`material_library`（21 个活）、`plan_materials`（`chapter-studio.mjs:6127` 等 5 处）、`review_batch`（3 个活）、`todo_v3`（7 个活）、`writing_settings`（3 个活）、`writing_specs`（`grep` → 6 处） |
| 新增 services（`review_*`/`diagram_*`/`todo_*`/`writing_*`/`material_*`/`section_*`/`chapter_*`/`quality_check`/`delivery_*`） | 疑为「新服务无人调」 | 79 个服务模块**无一**是完全无引用：用导入图扫描（`from services.X import` + `services import X`）确认每个模块至少被一个模块/路由/测试导入；`diagram_render`/`diagram_svg`/`semantic_index` 只被其它 service 导入（`diagram_design`、`production_diagrams`、`deployment_diagram`、`material_match`），但链路上游是活跃端点，属正常分层 |

---

## 4 不确定／需要人工确认项

1. **A43 `POST /workflow/{id}/analyze-points`**：前端零调用，`docs/` 与 `scripts/` 也未命中（`grep -rn "analyze-points" docs scripts` → 0），但它与 `redraft-outline` 共享 `points_only=True` 语义；若产品文档里的「先拆评分点」步骤仍需单独入口，应保留并补前端按钮，而不是删。
2. **A40 `POST /studio/diagrams/{asset_id}/regenerate`**：前端零调用，但 `services/diagram_assets.py:122 read_html` 的历史版本机制是否只为「重新生成」服务，需要 diagram 归属审计员确认；若历史版本另有用途，删端点时可保留 `read_html`。
3. **A34 `GET /projects/{id}/materials/needs` 与 A21/A35 的关系**：章级 `materials/needs` 活着、项目级死了；项目级「素材缺口」是否已由 `/todo/overview` 覆盖，需要产品口径确认（当前代码里两处聚合维度不同：需求项 vs 交付任务）。
4. **B18 `config_store.save_all`**：目前只有 `tests/services/test_config_store.py` 用；删除前需确认没有发布脚本/迁移脚本通过它写配置文件（`grep -rn "save_all" scripts` → 0，但 `scripts/` 未全量审计）。
5. **E01 的修复面**：`nodes.py` 在 `orchestrator/`（本轮不入范围）。本报告只判定「服务层重名导致旧签名不可达」这一事实与调用点行号，改法涉及 orchestrator，交由后端 B 审计员/实现方确认。
6. **前端 `frontend/workbench.html` 与 `frontend/assets/mode-editor.js` 的大量同构代码**（例如两处都有 `matched-sources` 调用）：`workbench.html` 可能是 `mode-editor.js` 的内联旧副本。若如此，E02 的迁移面比本报告估计的小一半；这属于前端审计范围，建议交叉确认。
7. **`backend/routes/leads.py` 的 POST /leads 有 8 处前端命中**，但都集中在 `frontend/landing.html` 的营销表单；若营销页已下线/换域名，该端点会转为「准死代码」。需要产品确认 landing 是否仍在服务。

---

## 5 只读验证命令（可直接执行，不含任何删除动作）

```bash
cd /Users/jianghe/projects/ge-solution

# 0) 基线核对：端点总数
grep -rhcE "^\s*@(router|app)\.(get|post|put|delete|patch)\(" backend/routes/*.py | awk '{s+=$1} END{print "endpoints:", s}'   # 期望 250

# 1) A01–A03 旧上传管线
grep -rn "api/upload" frontend tests | wc -l            # 0
grep -rn "parse/stream" frontend tests | wc -l          # 0
grep -rn "supported-formats" frontend tests | wc -l     # 0
grep -rn "sse_parse_" backend --include=*.py            # 仅 utils/sse.py + routes/upload.py

# 2) A04 changelog
grep -rn "changelog" frontend tests --include=*.py --include=*.js --include=*.mjs --include=*.html | wc -l   # 0

# 3) A05–A11 旧单配置 API
grep -rn "/api/config'" frontend tests | wc -l          # 0
grep -rn "/api/config/check\|/api/config/providers\|/api/configs/reorder" frontend tests | wc -l   # 0
grep -rn "configs/\${" frontend | grep -c "method: 'PUT'"   # 0（只有 DELETE）

# 4) A12/E05 410 outline + 死客户端封装
grep -rn "api.outline" frontend | wc -l                 # 0
grep -rn "\.outline(" frontend | wc -l                  # 0
sed -n '674,684p' backend/routes/materials.py           # 恒 410

# 5) E01/E02 依据列读写不对称
grep -n "async def update_block_source" backend/services/block_store.py        # 516, 1112
grep -rn "update_block_source" backend --include=*.py                          # nodes.py:639 旧签名 / runner.py:953 新签名
grep -rn "matched-sources" frontend | wc -l             # 2（workbench.html:4517 / mode-editor.js:2141）
grep -rn "mapSources(" frontend | wc -l                 # 0（仅 api.js:164 定义）
grep -rn "map-sources" frontend | wc -l                 # 1（同上定义行）

# 6) E03 上传大小上限
grep -rn "MAX_FILE_SIZE" backend --include=*.py         # 全部在 routes/upload.py:35,126,290
grep -rn "content = await file.read()" backend/routes/materials.py   # :486 无上限

# 7) E04 引用过滤四份实现
grep -n "EXISTS (SELECT 1 FROM citation_links" backend/services/citations.py   # 379, 388, 418, 493
grep -n "if False" backend/services/citations.py backend/routes/chapter_studio.py   # citations.py:493 / chapter_studio.py:585

# 8) 死服务符号抽查（每行应只命中定义处）
for s in load_plan confirm_plan confirm_chapter_plan update_figures_plan recut_material \
         search_block_materials list_results heal_block_outline auto_fix link_citation \
         unlink_citation reject_citation search_official align_to_outline ensure_outline_elements \
         project_summary project_settings _drop_empty_sections list_materials_by_ids \
         _CITE_IN_BODY MODE_LABELS NO_MATERIAL_HINT LEVEL_ORDER ZONE_H REQUIREMENT_PREFIX \
         _ACTION_LABEL rollup_totals execution_outline coverage_for_section uninstall; do
  printf "%-26s %s\n" "$s" "$(grep -rn "\b$s\b" backend --include=*.py | wc -l | tr -d ' ')"
done

# 9) 未使用 import 复核（示例）
grep -c "JSONResponse" backend/routes/chapter_studio.py            # 1（仅 import 行）
grep -c "OPENAI_COMPATIBLE_PROVIDERS" backend/routes/config.py     # 1（仅 import 行）
grep -c "StreamingResponse" backend/routes/materials.py            # 1（仅 import 行）

# 10) 未使用变量 / 重复净化
grep -n "_purged" backend/routes/chapter_studio.py                 # 658,660,772,774 —— 赋值后从未读取

# 11) 测试专用端点（产品无入口，建议标注而非删除）
grep -rn "coverage-review\|studio/draft\|writing-jobs/.*cancel\|diagrams/.*image" tests --include=*.py | head

# 12) 死表确认（工作区新增表是否真的被写）
grep -rn "block_purged_items" backend --include=*.py    # db.py 建表 + block_store 写 + chapter_studio 读（活）
```
