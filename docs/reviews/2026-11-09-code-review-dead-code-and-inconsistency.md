# ge-solution 代码审查报告：死代码、无用代码与逻辑不一致

> 审查日期：2026-11-09
> 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`
> 快照：HEAD `299073c`（审查开始时为 `a91b1e9` + 工作区未提交改动；审查过程中该批改动被提交为 `299073c`，代码内容未变）
> 工作区说明：审查期间有并发修改（新增 `block_purged_items` 表、占位符净化链路、测试反转），本报告已按最终工作区内容复核；未修改、未删除任何项目文件，未启动服务。
> 依据：四份只读审计报告
> - `docs/reviews/_audit2/frontend.md`（48 项）
> - `docs/reviews/_audit2/backend-a.md`（88 项）
> - `docs/reviews/_audit2/backend-b.md`（44 项）
> - `docs/reviews/_audit2/contract.md`（跨层一致性约 79 条，含 13 张空表）
>
> 本报告按用户要求重新组织为三类：**死代码**、**无用代码**、**与当前逻辑不一致**。

---

## 0. 结论摘要

### 0.1 一句话结论

**代码量翻倍后，死代码的重心从"没人用的文件"转移到了"没人用但仍在注册/维护的端点、函数和样式"：43 个端点零调用、753 块 CSS 死规则、12 个零调用客户端方法、10,542 行不可达旧页仍在跟版本号；同时出现了 2 个会让新镜像起不来/功能静默失效的配置错位，以及 4 处"前端在调一个已废弃或根本不存在的接口"。测试全绿（后端 1152、前端 161），说明这些问题全部落在测试覆盖的缝隙里。**

### 0.2 分类总览

| 类别 | 核心数量 | 规模 | 代表项 |
|---|---|---:|---|
| **死代码** | 2 个不可达整页 + 1 个无路由页 + 1 个孤儿页 + 43 个零调用端点 + 6 个死模块 + 30+ 组死符号 + 14 个前端死函数（另有 6 个死 helper）+ 753 块死 CSS + 7 个一次性脚本 + 约 1,100 行死代码测试 | 前端可删约 13,500 行；后端可删约 2,300 行 | `workbench.html` 5,580 行、`project-init.html` 4,962 行、`routes/upload.py` 292 行、`block_store` 重名函数 |
| **无用代码** | 21 个未使用 import + 12 个零调用 API 方法 + 1 个 no-op 模块 + 8 个仅测试导出 + 268 次同文件 CSS 重复定义 + 27 个跨文件重复选择器 + 4 份 provider 硬编码 | 约 500 行 + 2,446 行死 CSS 之外的重复样式 | `context-panel.js`、`api.outline`、`_drop_empty_sections` |
| **与当前逻辑不一致** | 2 个 P0 配置错位 + 3 个前端调死/不存在接口 + 1 个「读活写死」字段 + 1 个重名函数导致数据不落库 + 4 个硬编码开关 + 6 类新旧实现漂移 + 3 组测试固化旧契约 + README 4 处 + 设计文档 6 处 API 表 + 13 张 0 行表 | 影响启动、功能可用性与数据正确性 | `jieba` 缺依赖、`infra.llm` 漏转出、`MATCH_MODE` 假开关、双评审表 |

### 0.3 最高危 12 条（按影响排序）

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `requirements.txt` ↔ `backend/infra/retrieval/keyword.py:6-7` | 从未声明 `jieba`、`rank_bm25`，而 keyword.py 在模块顶层 import 它们，经 `routes/materials.py:35` 进入启动路径；Dockerfile 只装 requirements | **全新镜像/容器启动即 `ModuleNotFoundError`**；本地 `.venv` 恰好装过，掩盖了 3 周 |
| 2 | `backend/infra/llm/__init__.py` ↔ `routes/materials.py:741-749` | 未转出 `dispatch_package_merge_json`；import 在 `try/except` 内，异常被吞成 info 日志 | LLM 标包合并**永远静默降级**，`use_llm=1` 与 `0` 结果相同，约 200 行链路 + 6 个测试不可达 |
| 3 | `frontend/assets/mode-editor.js:2765` → `backend/routes/materials.py:674-686` | 编辑器「准备 / 重新拆分评分点」仍 POST 已 `[DEPRECATED]` 的 `/api/projects/{id}/outline`，该端点固定返回 410 | 提炼流**永远起不来**，用户看到「准备请求失败」 |
| 4 | `frontend/assets/generate-session.js:27` | 「批量编写」兜底 POST `/api/projects/{id}/generate-all`，该路由**不存在**（250 端点中零命中） | 必然 404，`GenerateSession` 在 `mode-editor.js:554-555` 真实实例化，点按钮无反馈 |
| 5 | `backend/services/block_store.py:516` 与 `:1112` | 同名函数 `update_block_source` 定义两次，后者覆盖前者；`nodes.py:639` 仍按旧签名调用 → `TypeError` 被 `except Exception` 吞掉 | 自动匹配结果**从不写入 `blocks.source`**；而 `GET /blocks/{id}/matched-sources` 仍被活跃前端读取 → 页面依据为空/过期 |
| 6 | `backend/routes/materials.py:486` | 唯一的上传大小上限（50MB）在死模块 `routes/upload.py:35`，活跃上传入口直接 `await file.read()` 无上限 | 任意大文件可打满内存/磁盘（接口无鉴权） |
| 7 | `backend/orchestrator/runner.py:1285` + `backend/utils/sse.py:43` | SSE 下发 `download_url=/api/workflow/{tid}/download` 与 `/api/download/{task_id}`，**两个路由都不存在**；唯一"消费方"是测试字符串断言 | 前端拿到 404 下载地址；前端实际 0 消费，属契约幻觉 |
| 8 | `backend/orchestrator/nodes.py:563` | `MATCH_MODE = "assign"` 是模块级字面量，无 env/配置开关；`nodes.py:655-700` 旧 rerank 整段**生产永不可达**，但 `main.py` 仍构造 `ShenKuoAgent` | 所谓"保留用于回滚"实际回滚不了；约 430 行实现 + 约 560 行测试是死负载 |
| 9 | `backend/services/citations.py:493` | 引用过滤写成 `... if False else " AND EXISTS (...)"`，硬编码只走 EXISTS；同逻辑另 3 处（`:379/:388/:418`）仍认 `citations.block_id` | 只写 `block_id` 的新引用在全量列表里**消失**（刚选的引用刷新后不见） |
| 10 | `backend/routes/config.py:166` + 4 份前端硬编码 | 权威 `GET /api/config/providers` 零调用；4 份前端 provider 表已分叉：`deepseek-v4-pro` vs `deepseek-chat`，claude/kimi/minimax 默认值也不同 | 用户看到的预填模型 ≠ 后端实际默认模型；4 个 provider 中 3 个不一致 |
| 11 | `backend/services/chapter_review.py:140/215/318` | 同一次评审同时写 `chapter_reviews` 与 `reviews` 两代表，字段集不同、读路径分叉、无事务一致性 | 跨表聚合必须写兼容分支；`delivery_verdict`/`practice_recycle` 只对其中一代有效 |
| 12 | `tests/frontend/module-imports.test.mjs:111-117/141-147` | `?v=` 白名单漏 5 个仍在维护版本号的文件，且保留 2 个死页；而版本错配正是本仓库发生过的"整页脚本静默哑掉"事故根因 | 版本守卫有盲区；死页仍在发版时被迫同步 15 处 `?v=` |

### 0.4 基线（实测）

| 指标 | 数值 |
|---|---:|
| git 跟踪文件 / 跟踪字节 | 886 / 78.1 MB |
| 前端代码 | 42,916 行（9 个 HTML 17,431 + 18 个 js/mjs 20,241 + 6 个 css 5,244） |
| 后端 Python | 179 文件 / 47,977 行 |
| 后端测试 | 125 个 py 文件 / 23,205 行；`pytest` 1152 passed / 1 deselected（本次实测） |
| 前端测试 | 18 个 mjs 文件；`node --test` 161 passed（本次实测） |
| 距 2026-10-19 审查 | +284 提交、+54,374 行（401 文件变更） |
| 页面路由 | 9 条（`/`、`/three-mode`、`/projects`、`/workbench`(307)、`/lp`、`/diff-review`、`/project-init`(307)、`/project-intake`、`/review`） |
| 后端端点 | 250 个（GET 113 / POST 108 / PUT 17 / DELETE 9 / PATCH 3），23 个路由模块 |

---

## 1. 审查范围与方法

### 1.1 范围

- 后端：`backend/routes/`（24 模块）、`services/`（79 模块）、`infra/`、`orchestrator/`、`agents/`、`domain/`、`utils/`、`scripts/`、`backend/skills/`、根 `scripts/`、`tests/**/*.py`。
- 前端：`frontend/*.html`（9 个）、`frontend/assets/**`（24 个 js/mjs/css + landing 图片）、`tests/frontend/*.test.mjs`。
- 跨层：端点 ↔ 前端调用矩阵、DB schema ↔ 代码、测试 ↔ 现状、文档 ↔ 代码、未跟踪依赖。

### 1.2 判定口径

| 类别 | 判定 |
|---|---|
| **死代码** | 无任何运行时/测试/文档可达引用；或 HTTP 层不可达；或生产分支永不可执行 |
| **无用代码** | 存在但无价值：no-op、空壳、未使用 import、仅测试维持、无人调用的客户端方法、重复且无人用的实现 |
| **与当前逻辑不一致** | 契约漂移（前端调已废弃/不存在端点）、依赖声明与实际 import 不符、硬编码开关导致不可达、新旧两代实现并存且已分叉、测试/文档固化旧行为、读写不对称、DB 状态与代码假设不符 |

### 1.3 证据与盲区

- 端点矩阵：解析 250 个 `@router.*`，展开 `APIRouter(prefix=...)`，与前端 `fetch`/`api.js` 调用归一化比对，再逐条人工复核模板字符串。
- 符号级：AST 统计生产/测试引用；对 `getattr`、字符串引用、装饰器注册、子进程调用做了人工排除（见 §6）。
- CSS：规则块扫描 + class/id 引用语料 + 动态 class 前缀检查。
- **盲区**：静态 grep 覆盖不到运行时拼接路径与外部脚本；CSS 清理无自动化视觉回归；后端 pytest 通过不代表真实链路可用。

---

## 2. 死代码

### 2.1 不可达 / 孤儿页面

| ID | 位置 | 规模 | 判定证据 | 严重度 | 建议 |
|---|---|---:|---|---|---|
| FE-D01 | `frontend/workbench.html` 全文件 | 5,580 行 / 245 KB | `/workbench` 在 `main.py:337-342` 是 307 → `/project-intake`；前端只挂 `/assets`（`main.py:292`），无任何 `FileResponse` 指向该文件 | 高 | 产品确认后整页删除 |
| FE-D02 | `frontend/project-init.html` 全文件 | 4,962 行 / 239 KB | `/project-init` 在 `main.py:360-365` 是 307；同上 | 高 | 随 FE-D01 删除 |
| FE-D03 | `frontend/bench.html` | 310 行 | `main.py` 无 `/bench` 路由；唯一服务方是**未跟踪**的 `scripts/bench_server.py:86`；页面 12 处调用 `/bench/*`，读 `marketing/*.mp4`，是「视频逐帧工作台」独立本地工具 | 高 | 移出 `frontend/`（如 `tools/bench/`）并把服务脚本入库，或标注 dev-only |
| FE-I02 | `frontend/diff-review.html` | 331 行 | 路由存在（`main.py:353-357` 返回 200），但全前端除自身自链接（`diff-review.html:17`）外零入口；`workspace.js:57` 仅注释 | 高 | 在 `/review` 补「修订链」入口，或删路由 + 文件 |

**两个死页不只是"不可达"，还是活模块的冻结快照，且已漂移：**

| 对照 | inline 顶层函数 | 全部存在于活模块 | 函数体逐字相同 | 逐行字面同现 | 活模块独有 |
|---|---:|---:|---:|---:|---:|
| `workbench.html:2504-5224` ↔ `mode-editor.js` | 55 | 55/55 | 42/55 = 76% | 96.0% | 5 个 |
| `project-init.html:1099-4959` ↔ `mode-outline.js` | 138 | 138/138 | 94/138 = 68% | 93.3% | **67 个** |

两者合计内联 JS 6,582 行、内联 CSS 2,932 行；死页内仍保留 `/workbench`、`/project-init` 的失效链接生成（10 处）与 `?legacy=1` 逃生口（已被后端 307 抢占，永不执行）。**每次发版仍需同步它们的 15 处 `?v=`，而它们不产生任何运行时效果。**

### 2.2 零调用端点（43 个）

判定：完整路径在 `frontend/**/*.{html,js,mjs}` 与 `tests/**/*.{py,mjs}` 中零命中（含方法级；仅统计"产品/测试都无调用"，不含可能的外部集成）。审计口径差异：跨层审计按路径级统计为 36 个，本表按方法级为 43 个。

| 路由模块 | 零调用端点 | 数量 | 证据要点 |
|---|---|---:|---|
| `routes/upload.py` | `POST /upload`、`GET /parse/stream/{id}`、`GET /upload/supported-formats` | 3 | 旧上传管线；实际上传走 `POST /projects/{id}/materials` |
| `routes/changelog.py` | `GET /changelog` | 1 | 运行时 `git log`；Docker 镜像无 `.git`，只会 503 |
| `routes/config.py` | `POST/GET/DELETE /config`、`/config/check`、`/config/providers`、`/configs/reorder`、`PUT /configs/{id}` | 7 | 前端全部走 `/api/configs*`（复数）；单数旧接口零调用 |
| `routes/materials.py` | `POST /projects/{id}/outline`（410）、`POST /projects/{id}/map-sources` | 2 | 410 端点仍被活跃前端调用（见 §4.1）；map-sources 写端已死（见 §4.3） |
| `routes/blocks.py` | `GET /blocks/{id}/material-search` | 1 | 服务函数仍被新证据链复用，仅端点死 |
| `routes/chapter_sse.py` | `POST /blocks/{id}/studio/rewrite-selection` | 1 | — |
| `routes/chapter_studio.py` | `/studio/outline/self-heal`、GET `/studio/outline`、`/section-results`、`/align-outline`、非流式 `POST /studio/section`、`GET /projects/{id}/todo` | 6 | 前端走 SSE 版与 `todo_v3`；非流式版仍会写库，与 SSE 版写库序列已分叉 |
| `routes/chapter_writing.py` | 裸 `GET /writing-plans/{id}`、`/confirm-chapter`、`/figures-plan`、`/confirm`、`GET /writing-jobs` 列表 | 5 | 前端只用 coverage/needs/requirements/needs-bind |
| `routes/citations.py` | `/link`、`/link/{block_id}`、`/search`、`/reject` | 4 | 换引用走 `/citations/fill` |
| `routes/material_library.py` | `/materials/{id}/recut`、`/materials/recut-all`、`/citations/swap`、项目级 `GET /materials/needs` | 4 | 前端只用章级 needs |
| `routes/todo_v3.py` | `GET /todo/chapter`、`/delivery-gate/auto`、`/delivery-gate/fix` | 3 | 与 `chapter_studio` 的 `/todo` 同功能重复 |
| `routes/review_batch.py` | `GET /reviews/batch`（最新任务）、`GET /studio/review/issues/summary` | 2 | 前端只 POST 创建 + GET `{jobId}` |
| `routes/diagram_assets.py` | `POST /studio/diagrams/{id}/regenerate` | 1 | 前端只用 `/svg` |
| `routes/projects.py` | `GET /projects/{id}/basis` | 1 | 前端已有等价 `/match-overview` |
| `routes/writing_settings.py` | `GET /projects/{id}/writing-settings` | 1 | 前端只 PUT 项目级、GET block 级 |
| `routes/workflow.py` | `POST /workflow/{tid}/analyze-points` | 1 | 前端用 `/redraft-outline` + `/confirm-outline` |

**连带死代码**：这些端点专属的服务函数随端点一起死（共 20 组、约 500 行），例如 `writing_plans.load_plan`(8)、`writing_plans.confirm_plan`(58)、`chunk_quality.recut_material`(49)、`material_search.search_block_materials`(56)、`delivery_gate.auto_fix`(28)、`citations.*`(4 个)、`body_align.align_to_outline`(24)、`section_outline.ensure_outline_elements`(19)、`outline_self_heal.heal_block_outline`(40)、`review_issues.project_summary`(14)、`writing_settings.project_settings`(4)、`config_store` 5 个旧方法。

### 2.3 死模块

| ID | 模块 | 规模 | 证据 | 严重度 |
|---|---|---:|---|---|
| BE-A01–A03 | `backend/routes/upload.py` | 292 行 / 3 端点 | 仅 `main.py:18/243` 注册；前端/测试 0 调用；最后提交 2026-06-17 | 高 |
| BE-A04 | `backend/routes/changelog.py` | 71 行 | 前端/测试/文档 0 引用；最后提交 2026-05-13 | 中 |
| BE-B/U08 | `backend/utils/validators.py` | 11 行 | `validate_uuid` 唯一调用点在死模块 upload.py | 低 |
| BE-B/D01 | `backend/domain/spec/repository.py` | 120 行 + 173 行测试 | `SpecRepository` 生产零引用，仅 `tests/domain/test_spec_repository.py` | 中 |
| BE-B/D02 | `backend/domain/proposal/repository.py` | 84 行 + 274 行测试 | 同上；注释称"Phase 7 切换"但从未接线 | 中 |
| BE-B/D03 | `backend/domain/material/repository.py` | 111 行 + 225 行测试 | 同上；三仓均反向 import `services.block_store`（分层倒挂） | 中 |

> `domain/review/repository.py` 是例外：类本身活（`routes/review.py`、`nodes.py:1050`、`runner.py`），只有 3 个方法死（见 §2.4）。

### 2.4 活文件内的死函数 / 变量 / 常量

**前端（13 个零引用函数 + 4 组死变量）**

| ID | 位置 | 规模 | 证据 |
|---|---|---:|---|
| FE-D12 | `mode-outline.js:2833` `readConfirmedPkg` | 7 行 | 全仓 1 处（定义） |
| FE-D13 | `mode-outline.js:2981` `renderPackageCards` | 30 行 | 全仓 1 处；独占依赖 `pkgMergeNote` 随之死 |
| FE-D14 | `mode-outline.js:3013` `renderPointsAlignment` | 13 行 | 全仓 1 处；`CAT_NAME` 随之死 |
| FE-D15 | `mode-outline.js` 6 个局部 helper（4056/4142/4147/4218/4229/4273） | 105 行 | 各 1 处；位于 1,725 行的 `openSplitReport()` 内 |
| FE-D16 | `mode-outline.js:200/2153/5856` 死变量 | 7 行 | `currentReader`/`pendingRenameId`/`lastRunErrorMessage` 各 1 处 |
| FE-D17 | `workspace.js:47` `currentView()`、`:83` `inPlace` | 6 行 | 各 1 处；导航整块移除后遗留 |
| FE-D18 | `diff-review.html:156` `fmtTime` | 9 行 | 全页零调用 |
| FE-D19 | `mode-dispatch.js:98-103` 点击委托 | 6 行 | `#workspace-nav` 被清空隐藏，`a[data-mode]` 永不存在 |
| FE-D（新增） | `chapter-studio.mjs` 9 个函数：`materialRequestsHtml`(902)、`refreshPlanRequirements`(1089)、`openDesignDiagramDialog`(2043)、`openDeploymentDiagramDialog`(2425)、`openWritingPlan`(2688)、`showProposalBar`(4472)、`restoreLastRevision`(4499)、`todoPaneHtml`(4622)、`gapGroupsHtml`(4873) | 约 250 行 | 每个全仓仅 1 处（定义） |

**后端（活文件内零引用符号，节选；完整见 backend-a/backend-b）**

| ID | 位置 | 规模 | 说明 |
|---|---|---:|---|
| BE-B/D04 | `utils/sse.py:24-62` 6 个函数 | 39 行 | `sse_token`/`sse_section_*`/`sse_all_done`/`sse_doc_summary`/`sse_section_skip` 全仓 0 引用 |
| BE-B/D05 | `orchestrator/graph.py:170-181` `_route_after_generate` | 12 行 | 实际用内联 `_generate_fanout` |
| BE-B/D06 | `orchestrator/state.py:189-228` `merge_state` | 40 行 | 仅测试使用，LangGraph 用 `_merge_dict` |
| BE-B/D07 | `agents/shen_kuo.py:52-170` `match()` | 119 行 | `MATCH_MODE` 恒 assign，调用点在 `return` 之后 |
| BE-B/D08 | `infra/retrieval/rerank.py:50-127` `llm_rerank` | 78 行 | 只被 D07 死路径调用；`Match` 类仍活 |
| BE-B/D09 | `dispatcher.py:110-184,428-459` doc_summary 链 | 107 行 | 生产 0 调用，`doc_summary` 恒空 |
| BE-B/D10 | `domain/review/repository.py` 3 个方法 | 27 行 | `clear_thread`/`list_findings_by_block`/`latest_for_scope` |
| BE-B/D11 | `outline_builder.py:34-36,45,329-348,814-823` | 34 行 | 2 常量 + 2 函数 |
| BE-B/D12 | `review_points.py:70-72,222-226,520-523` | 8 行 | 1 常量 + 1 方法 + 1 函数 |
| BE-B/D13 | `toc.py:11`、`scope_index.py:31`、`tech_requirements.py:89` | 3 行 | 3 个零引用正则常量 |
| BE-B/D14 | `infra/llm/usage.py:52` `get_sink` | 2 行 | 全仓 1 处 |
| BE-B/D15 | `domain/material/models.py:99-109` | 3 行 | 3 个零引用常量 |
| BE-B/D16 | `infra/llm/errors.py:143,278` | 6 行 | `is_retryable`/`unhealthy_snapshot` 被 `blocks_chain` 取代 |
| BE-B/D19 | `services/placeholder_purge.py:198-220` `_drop_empty_sections`（**最新提交**） | 23 行 | 定义即无人调用；`purge()` 未接它 |
| BE-B/D20 | `db.py:656` `_migrate_review_v3` | 8 行 | 零引用，逻辑已内联重复在 `init_db` |
| BE-A/B01–B17 | 20 组"仅被死端点引用"的服务函数 | 约 500 行 | 见 §2.2 连带死代码 |
| BE-A/B18 | `config_store.py` 5 个旧方法 | — | `get/save/clear/update/reorder` 只被死端点调用 |
| BE-A/B20 | `block_store.py:137` `list_materials_by_ids` | 18 行 | 全仓 1 处 |
| BE-A/C02–C17 | 16 组死常量/空壳/死方法 | 约 90 行 | `_CITE_IN_BODY`、`MODE_LABELS`、`LEVEL_ORDER`、`_parent_key`、`rollup_totals`、`uninstall`、`_edge` 等 |
| BE（前端相关） | `backend/main.py:272` `_index_path` | 1 行 | 定义后从未使用；`frontend/index.html` 不存在 |

### 2.5 死 CSS（753 块 / 2,446 行）

| 位置 | 死块 | 死行 | 说明 |
|---|---:|---:|---|
| `frontend/assets/styles.css` | 208 | 1,217 | 历史项目列表页、旧 workbench/project-init 布局残骸 |
| `frontend/assets/chapter-studio.css` | 354 | 368 | 压缩格式，多规则同行；`.cs-tab-badge`、`.cs-ins-*` 一串 |
| `frontend/assets/review.css` | 54 | 276 | `.review-banner`、`.review-grid`、`.score-pill`、`.card-section` 等 |
| `frontend/assets/response-outline.css` | 26 | 33 | `.sr-mount`（注意 `.sr-mount-cell` 是活的） |
| `frontend/assets/workspace.css` | 14 | 14 | `.ws-count`/`.ws-status` 等，共享壳状态条已移除 |
| `frontend/assets/ui.css` | 1 | 1 | `.ui-card--flat` |
| `three-mode.html:10-3352` 内联 | 83 | 467 | `.mat-tab`、`.btn-mark-done`、`.ai-action-btn` 等 |
| `projects.html:8-615` 内联 | 13 | 70 | `.modal-footer`（注意 `.api-modal-footer` 是活的） |

**必须先做的前置**：`three-mode.html` 内联 803 个选择器中 **713 个（89%）在任何 assets CSS 中都不存在**，页面实际样式主要活在内联 `<style>`；`mode-outline.js:158/175/4678` 另有三处运行时注入样式。**正确顺序是先把内联 CSS 抽成 `assets/three-mode.css`，再清死规则**，否则清理与视觉回归强耦合。

### 2.6 一次性脚本与开发工具

| 位置 | 规模 | 判定 |
|---|---:|---|
| `backend/scripts/normalize_extract_fields.py`、`recut_old_materials.py` | 291 行 | 文件头自写"一次性脚本"；无 import、无调度、无 CI 入口（2026-10-12 / 06-17） |
| `scripts/cleanup_pending_placeholders.py`、`normalize_diagram_blocks.py`、`recover_gaps_from_revisions.py`、`add_table_captions.py`、`build_response_templates.py` | 270 行 | 前 4 个均自述一次性；`scripts/README.md` 只登记了 `check.sh`/`ui-selfcheck.py`/`clear_data.py` |
| 未跟踪：`scripts/bench_server.py`、`bench_render.py`、`analyze_motion.py`、`make_qr.py` | — | 开发/营销工具；`bench_server.py` 是 `bench.html` 的唯一后端，未入库 |

### 2.7 为死代码"续命"的测试（约 1,100 行）

| 测试 | 规模 | 问题 |
|---|---:|---|
| `tests/domain/test_{spec,proposal,material}_repository.py` | 672 行 | 100% 围绕三套零生产引用的 repository |
| `tests/agents/test_shen_kuo.py` | 332 行 | 覆盖不可达的 `match()` |
| `tests/infra/test_retrieval.py:41-110`、`tests/orchestrator/test_nodes.py:225-257` | 约 130 行 | 保护 rerank 死路径；后者靠 monkeypatch 把 `MATCH_MODE` 切回 rerank |
| `tests/domain/test_letter_detector.py:31-36` | 6 行 | 唯一目的是断言旧 import 路径仍可用 |
| `tests/infra/test_llm_errors.py:60,254` | 2 断言 | 断言已被取代的 `is_retryable` |
| `tests/orchestrator/test_events.py:149-151` | 1 用例 | 把不存在的 `/api/download/{task_id}` 固化为契约 |

---

## 3. 无用代码

### 3.1 未使用 import（21 处确认）

| 文件 | 未使用符号 |
|---|---|
| `routes/chapter_studio.py:11` | `JSONResponse` |
| `routes/config.py:31` | `OPENAI_COMPATIBLE_PROVIDERS` |
| `routes/materials.py:16` | `StreamingResponse` |
| `routes/materials.py:19` | `create_block`、`delete_blocks_by_project`、`update_material_parsed`、`list_chunks_by_materials` |
| `routes/materials.py:35,36` | `assign_chunks_to_sections`、`format_sse_event` |
| `routes/todo_v3.py:4` | `re` |
| `routes/workflow.py:19` | `Any`、`Optional` |
| `services/chapter_writing.py:8` | `content_hash` |
| `services/deployment_diagram.py:13` | `Path` |
| `services/evidence_needs.py:8`、`services/review_batch.py:9` | `json` |
| `services/image_material.py:6` | `io` |
| `services/plan_ai.py:16` | `get_block` |
| `services/point_store.py:29` | `Optional` |
| `services/section_context.py:13`、`services/writing_workspace.py:6` | `re` |
| `infra/llm/dispatcher.py:13`、`infra/retrieval/rerank.py:6`、`agents/wang_anshi.py:12`、`agents/bao_zheng.py:13` | `json`（JSON 解析早已收敛到 `json_utils`） |
| `infra/parser/docx.py:10,19`、`infra/parser/scope_index.py:22` | `re`、`_SPECIAL_MARK_RE`、`Optional` |
| `orchestrator/runner.py:25,26`、`orchestrator/nodes.py:31` | `asynccontextmanager`、`Awaitable`、`Finding` |

> `infra/llm/dispatcher.py:1052` 的 `LETTER_KEYWORDS`/`is_letter_section` 是**有意的旧路径 re-export**，不属误删项，但应随 §4.5 的 re-export 一起清理。

### 3.2 零调用 API 客户端方法（12 个）

| `api.js` 位置 | 方法 | 对应后端 | 说明 |
|---|---|---|---|
| `:44` | `projects.createPackage` | `packages.py:188` | 前端改走 `intake.js` 裸 fetch，导致封装零调用 |
| `:95` | `projects.patch` | `projects.py:164` | 无 UI 入口 |
| `:101` | `projects.lock` | `projects.py:177` | 只读查库 `project_snapshots` = 0 行 |
| `:136-163` | `materials.outline` | `materials.py:674`（410） | 28 行 SSE 解析专为死端点而写 |
| `:164` | `materials.mapSources` | `materials.py:687` | 写端已死 |
| `:173` | `blocks.update` | `blocks.py:197` | 无调用 |
| `:210` | `blocks.ai` | `blocks.py:330` | 无调用 |
| `:216` | `blocks.updateRequirement` | `blocks.py` | 无调用 |
| `:225` | `revisions.list` | `revisions.py:28` | 无调用 |
| `:227` | `revisions.restore` | `revisions.py:68` | 无调用 |
| `:287` | `workflow.getSplitAnswers` | `materials.py:280` | 无调用 |
| `:441` | `review.get` | `review.py:19` | 只有测试调用 |

> 除 `materials.outline`（后端唯一 410，可单独立即删）外，其余 11 个需前后端一起决策：要么补 UI，要么同批删端点。

### 3.3 no-op / 空壳 / 仅测试维持

| 项 | 位置 | 规模 | 证据 |
|---|---|---:|---|
| `context-panel.js` 整体 | `frontend/assets/context-panel.js` + `mode-editor.js:8,25` | 70 行 | 只查 `[data-panel-requirement]`/`[data-panel-sources]`，两选择器全仓只在自身定义；`.editor-block` 在活页 0 次；`window.__materialsMap` 从未赋值；输出 6 个 class 零引用；顶层调用每次进编辑器空转 |
| `toggleRefCard` 空壳 | `workbench.html:5228` | 1 行 | `function toggleRefCard(id) {}`，注释"保留空壳避免旧代码报错" |
| `_drop_empty_sections` | `services/placeholder_purge.py:198`（最新提交） | 23 行 | 无调用，`purge()` 未接线 |
| `_purged` 变量 | `routes/chapter_studio.py:658,772`（最新提交） | 2 处 | 赋值后从未使用；且 `update_block_content` 会再净化一次 |
| 仅测试维持的导出（8 个） | `chapter-studio.mjs:105 exportScope`；`workbench-modes.mjs` 的 `MODE_LABEL/parseMode/modeUrl/resolveSelectedBlock/panelVisible`；`workspace-model.mjs` 的 `evidenceSliceTitle/evidenceByFile` | — | 产品零引用、模块内零自用、只有测试引用 |
| 零外部引用导出（7 个） | `setSelectedRow`、`currentMode`、`REVIEW_PATH`、`materialRoleLabels`、`materialRoleHints`、`EVIDENCE_SOURCE_LABELS`、`CHAPTER_STAGES` | — | 全仓各 1 处（定义） |
| 无意义 `export`（2 个） | `workspace.js:336 openWritingSpecs`、`:467 openMaterialsHub` | — | 函数活着，但无外部 import，`export` 多余 |
| `is_retryable`/`unhealthy_snapshot` | `infra/llm/errors.py:143,278` | 6 行 | 被 `blocks_chain` 取代，生产 0 引用 |

### 3.4 重复实现与冗余

| 项 | 位置 | 规模 | 状态 |
|---|---|---:|---|
| 模型/API 配置弹窗 | `projects.html:939-1058` ↔ `mode-outline.js:514-615` | 约 120 行 / 8 个同名函数 | 两处已漂移；活代码里最大重复 |
| provider 默认表 | 前端 4 份 + 后端权威 1 份 | — | **4 个 provider 中 3 个默认值不一致**（见 §4.4） |
| `request()` | `intake.js:8-12` ↔ `api.js:26-36` | 约 10 行 | 同一"非 2xx 抛 detail"语义两份；`save()` 绕过 `api.projects.*` |
| 同文件 CSS 重复选择器 | 6 个 CSS 文件 | 268 次多定义 | `chapter-studio.css` 94 个选择器重复（121 次）、`styles.css` 89 个（137 次） |
| 跨文件 CSS 重复选择器 | 6 个 CSS 文件 | 27 个选择器 | `.v-pill`、`.workspace-shell`、`.ws-dialog`、`.context-pane`、`.sr-btn` 等 |
| 项目待办两份实现 | `chapter_studio.py:1190` ↔ `todo_v3.py:99` | 52 / 68 行 | 前端只用 todo_v3 |
| "写本节"两份实现 | `chapter_studio.py:739`（非流式） ↔ `:523`（SSE） | 42 / 190 行 | 写库序列已分叉 |
| 上传白名单两份 | `upload.py:34 ALLOWED_EXTENSIONS` ↔ `materials.py:44 ALLOWED_SUFFIXES` | — | 死模块与活模块各维护一份 |
| 切片函数两份 | `materials.py:115-136` ↔ `scripts/recut_old_materials.py:32-51` | 21 行 | 逐字同逻辑 |
| 双评审表 | `chapter_review.py:140/215/318` | — | 同一批评审写 `chapter_reviews` + `reviews` 两代 |
| `update_block_source` 双定义 | `block_store.py:516` / `:1112` | 19 + 16 行 | 后者覆盖前者（见 §4.3） |

### 3.5 未跟踪工具混入产品目录

| 路径 | 性质 | 问题 |
|---|---|---|
| `frontend/bench.html`（tracked） + `scripts/bench_server.py`（untracked） | 视频逐帧工作台 | 产品 `frontend/` 里有一个依赖未入库脚本的页面；部署后必然 404 |
| `packages/`（dsh-video-bench）、`plugins/`（dsh-sticky-notes）、`marketing/`（1.6 GB 视频产物） | 本地工具/营销产物 | 与产品无引用关系，但在仓库工作区（未跟踪） |
| `docs/mockups/` 中的 mp4/png/html | 设计稿与视频 | 部分 tracked、部分 untracked，状态混杂 |

---

## 4. 与当前逻辑不一致

### 4.1 前后端契约错位

| # | 前端 | 后端 | 后果 |
|---|---|---|---|
| 1 | `mode-editor.js:2765`（`autoExtract=1` 与「准备」按钮两条入口，`:2270-2294`） | `materials.py:674-686` `[DEPRECATED]`，固定 `raise HTTPException(410)` | 提炼流永远起不来，抛「准备请求失败」 |
| 2 | `generate-session.js:27` POST `/projects/{id}/generate-all` | **不存在**（250 端点零命中） | 「批量编写」必然 404，无反馈；`mode-editor.js:554` 真实实例化 |
| 3 | `runner.py:1285` / `utils/sse.py:43` 下发 `download_url` | `/api/workflow/{tid}/download`、`/api/download/{task_id}` **均不存在** | 前端 0 消费；测试把 404 地址固化为契约 |
| 4 | 跨层矩阵：36 个路径级端点无任何前端/测试调用方 | — | 两代 API 面并存（详见 §2.2） |

### 4.2 P0：依赖与启动阻断

| 项 | 证据 | 影响 |
|---|---|---|
| `requirements.txt` 缺 `jieba`/`rank_bm25` | `grep -n "jieba\|rank" requirements*.txt` = 0；`keyword.py:6-7` 模块顶层 import；`Dockerfile:13` 只装 requirements；本地 `.venv` 恰有 jieba 0.42.1 / rank-bm25 0.2.2 | Docker / Railway / Render 三个部署面启动即 `ModuleNotFoundError` |
| `infra/llm/__init__.py` 漏转出 `dispatch_package_merge_json` | `routes/materials.py:743` 在 try 内 import；函数实际在 `dispatcher.py:1096`；`except Exception` → `logger.info` | `/package-analysis?use_llm=1` 与 `use_llm=0` 结果相同；约 200 行 + 6 个测试不可达 |

### 4.3 读写在死 / 数据不落库

| # | 位置 | 问题 |
|---|---|---|
| 1 | `block_store.py:516` / `:1112` | 同名 `update_block_source` 双定义，后者覆盖前者；`nodes.py:639` 按旧签名调用 → `TypeError` 被吞；自动匹配结果从不写入 `blocks.source` |
| 2 | `routes/blocks.py:63` ↔ `materials.py:687` | `matched-sources` 读端活跃（`mode-editor.js:2141`、`workbench.html:4517`），写端 `map-sources` 零调用 → 旧依据展示链路半瘫痪 |
| 3 | `materials.py:486` | 活跃上传入口 `await file.read()` 无大小上限；唯一 50MB 校验在死模块 `upload.py:35,126` |
| 4 | `services/citations.py:493` | `if False else` 硬编码走 EXISTS；同逻辑另 3 处仍认 `citations.block_id` → 只写 block_id 的新引用在列表里消失 |
| 5 | `services/placeholder_purge.py:198` | 最新提交新增的 `_drop_empty_sections` 未接线；产品承诺的"没有的内容就不要体现"当前未生效 |

### 4.4 硬编码开关与不可达分支

| # | 位置 | 开关 | 影响 |
|---|---|---|---|
| 1 | `nodes.py:563` | `MATCH_MODE = "assign"` 模块字面量，无 env/config | `:655-700` rerank 整段生产不可达；`ShenKuoAgent` 每次启动仍构造 |
| 2 | `review_precheck.py:120-127` | `resolve_tier(requested="auto")` | 分支 `if requested in ("fast","full")` 永不成立：唯一入口 `chapter_studio.py:1246` 默认 auto，前端从不传 tier → 人工指定档位是死参数 |
| 3 | `chapter_review.py:136` | `"tier": "skip"` | 前端只识别 `fast`（`chapter-studio.mjs:820`），`skip`/`full` 在 UI 上无法区分 |
| 4 | `config.py:166` | `GET /api/config/providers` | 权威 provider 表零调用；前端 4 份硬编码已分叉（见 §3.4） |

### 4.5 新旧两代/三代实现并存且已漂移

| 项 | 旧 | 新 | 漂移 |
|---|---|---|---|
| 前端整页 | `workbench.html` / `project-init.html`（10,542 行，不可达） | `three-mode.html` + `mode-editor.js` / `mode-outline.js` | 快照逐行 93-96% 同现，但活模块多 5/67 个函数；死页仍在改 `?v=` |
| config API | 单数 5 端点（0 调用） | 复数 `/api/configs*`（活） | 两套 `ConfigStore` 语义共存 |
| 待办 API | `chapter_studio.py:1190` `/todo`（死） | `todo_v3.py` `/todo/*`（活）；`projects.py:294 /todos`（活） | 三代并存，中间代整段死 |
| 评审存储 | `reviews`（44 行） | `chapter_reviews`（20 行） | 同次评审双写；字段集不同；读路径分叉 |
| "写本节" | 非流式 `chapter_studio.py:739`（死） | SSE 版 `:523`（活） | 两套写库序列已不一致 |
| 上传管线 | `routes/upload.py`（死，含 50MB 校验） | `POST /projects/{id}/materials`（活，无校验） | 旧能力丢失 |
| letter 判定 | `dispatcher.py` re-export（仅测试） | `domain/letter_detector.py` | 旧路径靠测试续命 |
| 匹配 | rerank（死） | assign（活） | 旧实现 + 测试约 990 行死负载 |

### 4.6 测试固化旧契约

| 位置 | 问题 |
|---|---|
| `tests/frontend/module-imports.test.mjs:111-117,141-147` | 白名单漏 5 个仍维护 `?v=` 的文件（`chapter-studio.mjs`、`chapter-tree.mjs`、`intake.js`、`project-intake.html`、`workspace-model.mjs`），且保留 2 个死页；当前 68 处 token 全为 `20261016-v503`，测试是绿的——是覆盖率漏洞 |
| `tests/orchestrator/test_nodes.py:225-257` | 靠 monkeypatch 进入生产不可达的 rerank 分支 |
| `tests/orchestrator/test_events.py:149-151` | 断言不存在的 `/api/download/{task_id}` |
| `tests/domain/test_*_repository.py`（672 行） | 保护三套零生产引用的 repository |
| `tests/domain/test_letter_detector.py:31-36` | 只为旧 re-export 的兼容性存在 |
| `tests/routes/*` 页面路由 | 307 重定向零测试（`grep "307\|redirect" tests/routes/*.py` → 0） |

### 4.7 文档与代码矛盾

| 位置 | 文档写的 | 实际 |
|---|---|---|
| `README.md:11` | `/api/workflow/*` 7 端点 + `/api/review/{tid}` | workflow.py 23 端点，routes 共 250 端点 / 23 模块 |
| `README.md:5,13` | 5 个 agent | `backend/agents/` 7 个 agent 类（新增 `ChapterChiefAgent`/`SpecialistAgent`，主链已启用） |
| `README.md:51` | 前端主路径 5 个页面 | 漏 `landing.html`；未说明 `workbench.html`/`project-init.html` 已不可达 |
| `docs/design/素材-需求供给与检索-方案v3.md:466-473` | `/materials/{id}/parse`、`/confirm`、`/blocks/{id}/needs`、`/evidence/{id}/bind` | 5 条路径全部不存在（实现已换口径） |
| `docs/design/方案A-标包工作空间-实施规划.md:313-327` | `/packages/merge`、`/runs/{}/state`、`/actions/{}`、`/points`、`/chapters`、`/reviews` | 多数不存在，聚合实为 `GET /packages/{id}/workspace` |
| `docs/design/待办-可操作交付清单-方案v3.md:390-393` | todo-id 级 `/todo/{}/upload`、`/ignore` | 实际是项目级 `/projects/{id}/todo/apply` |
| `docs/design/模型分工与编写过程-整体方案.md:31` | `POST /api/chapters/{}/write` | 实际 `chapter-writes` / `condense` |
| `docs/design/生图-DiagramDesign技能接入-方案.md:280` | `POST /api/studio/diagrams`（创建） | 实际 `POST /blocks/{id}/studio/diagram-assets` |
| `docs/reviews/2026-10-15-code-review-report.md:79` | `GET /api/parse/stream` | 实为 `/parse/stream/{job_id}`，且该端点零调用 |

### 4.8 数据库状态与代码假设不一致

| 项 | 实测 | 说明 |
|---|---|---|
| 13 张"有写路径、生产库 0 行"表 | `leads`、`writing_model_settings`、`evidence_need_actions`、`project_writing_facts`、`chapter_materials`、`citations` 等均为 0 行 | 功能已接线但从未跑通；`evidence_need_actions`/`project_writing_facts`/`writing_model_settings` 前端已有入口，点击即写空表（口径：单个开发库 01:29 快照，期间有活后端在写库） |
| `block_purged_items`（最新提交新增） | 审查开始时实库中不存在；01:29 运行中的后端重启/建表后出现，293 行 | `init_db` 只在启动时 `CREATE TABLE IF NOT EXISTS`；**任何未重启的实例**在 placeholder_purge 读写时会 `no such table`；部署时需确认迁移时机 |
| `PRAGMA user_version` | 0 | 无 schema 版本表 |
| `PRAGMA foreign_keys` | 0，且全仓无 `PRAGMA foreign_keys=ON` | DDL 里 `REFERENCES` 只是注释性约束 |
| `workflow_runs.package_id IS NULL` | 41/50，且这些行 `target/package_no` 全空 | 启动期回填对它们永远无效（`db.py` 每次启动仍跑） |
| `project_snapshots` | 0 行 | `projects.lock` 功能从未被真实调用 |

---

## 5. 高危项详述（怎么修）

### 5.1 P0：补依赖、补转出（两处各 1 行）

- `requirements.txt` 在"文档解析/检索"段补 `jieba==0.42.1`、`rank-bm25==0.2.2`；建议同时把 `keyword.py` 的顶层 import 改成函数内延迟 import 作为二道防线。验证：干净 venv `pip install -r requirements.txt` 后 `PYTHONPATH=backend python -c "import main"`。
- `infra/llm/__init__.py` 的 `from .dispatcher import (...)` 加入 `dispatch_package_merge_json` 并补进 `__all__`；验证 `python -c "from infra.llm import dispatch_package_merge_json"`。若产品决定不做 LLM 合并，则删 `use_llm` 分支 + `merge_with_llm`/`validate_merge_proposal` 及 6 个测试，**二选一，不要继续静默降级**。

### 5.2 删除 410 端点 + 死客户端（不需产品决策）

`materials.py:674-686` 的 `generate_outline` 函数体只有 410；`api.js:136-163` 是专为它写的 28 行 SSE 解析器且零调用；`mode-editor.js:2765` 是唯一真实调用点。三处同批删除，并检查 `three-mode.html` 的 `#extract-action-bar` 入口是否需要改造到新流程（`/workflow/start`）。

### 5.3 修复 `update_block_source` 双定义（数据正确性）

删除 `block_store.py:516-534` 旧定义；`nodes.py:639` 改为拿 `row_id` 后调用新签名 `(db, row_id, rows)`；补一条 AST 断言单测（"同模块不得有重名顶层函数"），防止复发。修复后 `blocks.source` 的自动匹配写入才会真正落库。

### 5.4 恢复上传大小上限

在 `materials.py upload_material` 开头加 `content-length` 预检（>50MB 返回 413），读取后再 `len(content)` 兜底；把 `MAX_FILE_SIZE` 作为唯一常量放在活跃模块/domain 常量。随后删除 `routes/upload.py` 时不会再次丢失该能力。

### 5.5 统一引用过滤（`citations.py`）

抽 `_chapter_clause()` 返回 `" AND (block_id=? OR EXISTS(...))"` 与两个参数，`:379/:388/:418/:493` 四处共用；删除 `if False else`。同时统一 `save_citation` 是否写 `citation_links`，避免"首个关联"两套同步机制。

### 5.6 两个死页：删除前先修测试契约

1. 改 `tests/frontend/module-imports.test.mjs` 两个数组：删 `workbench.html`/`project-init.html`，补 `project-intake.html` 与 4 个漏检模块；
2. 跑 `node --test` 确认 161 passed；
3. `git rm frontend/workbench.html frontend/project-init.html`；
4. `grep -rn "workbench.html\|project-init.html" frontend tests backend` 只应剩历史注释；
5. 打 tag 作回退；补 `/workbench`、`/project-init` 的 307 路由测试。

### 5.7 `bench.html`：移出产品目录

把 `frontend/bench.html` 与 `scripts/bench_server.py`、`bench_render.py` 一起迁到 `tools/bench/`（或独立仓库），并在 `frontend/` 不再保留；若产品确实要它，需要给 `main.py` 加 `/bench` 路由并纳入部署，否则它只是"部署后 404 的开发工具"。

### 5.8 `MATCH_MODE`：删或改成真开关

若不再需要回滚：删 `nodes.py:655-700`、`shen_kuo.match()`、`llm_rerank`、`GraphDeps.shen_kuo` 的 match 引用、`main.py` 的无用构造，并删对应约 560 行测试。若需要回滚：改为 `os.environ.get("MATCH_MODE","assign")`，在 README 写清回滚 SOP。当前"字面量常量 + monkeypatch 测试"是最坏形态。

### 5.9 单一 provider 来源

前端 4 份硬编码改为从 `GET /api/configs` 返回的 `model_examples`/默认值渲染，或抽 `assets/model-config.js` 单点；后端 `config_store.DEFAULT_MODELS` 作为唯一权威，并核对 `deepseek`/`claude`/`kimi`/`minimax` 的默认值。

### 5.10 评审表收敛（需产品/架构决策）

`chapter_reviews` 与 `reviews` 二选一：要么停止双写、保留一代并迁移读路径；要么明确分工（如 `reviews` 存逐条 finding、`chapter_reviews` 存章节汇总），并在同一事务里写。当前双写且字段集不同，跨表聚合只能写兼容分支。

---

## 6. 已核实"看起来死其实活"（不要删）

| 项 | 为什么容易误判 | 实际引用 |
|---|---|---|
| `assets/` 24 个 js/mjs/css 文件 | 目录杂乱 | 从 7 个路由页做传递闭包，31 个文件全部可达，**无整文件零引用** |
| `landing.html`（835 行） | `/lp` 不在导航 | `main.py:345-350` 200 路由；`:818` POST `/api/leads`；`leads.py:52` 存在且被测试覆盖 |
| `ai-revision.mjs`、`chapter-live.mjs`、`spec-content.mjs`、`generate-session.js` | 小或名字像残留 | 分别被 `chapter-studio.mjs` 导入并有真实调用点 |
| `review.js`（`grep "api\."` = 0） | 看起来不连 API | 从 `workspace.js` 取 `request`/`statusLabels`，是活页 |
| `body[data-view]` 机制 | 导航被移除，像整套模式切换死了 | CSS 中 `[data-view]` 40 条规则依赖；URL `?view=` 落地仍活；只有顶栏 `a[data-mode]` 点击那条死 |
| `three-mode.html` 的 6 个 CSS 文件引用 | 单引用、死规则多 | 引用方是唯一主页面；只能清死规则，不能删文件 |
| `services/material_search.py` | 端点和 `search_block_materials` 死 | `extract_terms`/`score_chunk` 被 4 个活服务使用 |
| `services/config_store.py` | 旧接口全死 | `get_all`/`get_configs_and_next_index`/`is_configured`/`verify_all` 被活路由使用 |
| `agents/shen_kuo.py` | `match()` 死 | `retrieve_for` 被 `nodes.py:1201` 使用 |
| `infra/retrieval/rerank.py` 的 `Match` | 与 `llm_rerank` 同文件 | `nodes.py:1483` 构造；assign 路径在用 |
| `domain/review/repository.py` | 3 个方法死 | 类本身被 `routes/review.py`、`nodes.py`、`runner.py` 使用 |
| `backend/skills/diagram_design/` | vendored 目录像静态资产 | 运行时读取 `references/*.md` 并 spawn `scripts/*.py`；只有 `export_png.py` 未接线 |
| `tests/conftest.py` 的 `_isolate_db` | 像测试基建冗余 | 生产库被测试污染事故后的修复，不要动 |
| `scripts/bench_server.py`（未跟踪） | 无产品引用 | 被未跟踪的 `packages/dsh-video-bench` 拉起；属"依赖未入库目录的活文件"，需与 packages 一起决策 |
| `Keyword` 的 `keyword_search`/`build_bm25_index` | 跨文件计数 0 | `agents/zhang_heng.py` 同文件内调用 |
| `dispatch_subpoints_json` | 不在 `__all__` | `agents/zhang_heng.py` 以属性式访问 |
| `_route_after_pause`/`_route_after_report` | 与死函数同风格 | 已注册进 `add_conditional_edges` |

---

## 7. 与 2026-10-19 审查的对比

| 状态 | 内容 |
|---|---|
| **仍未修复（0 条已修）** | 后端 B 复核的 24 条旧线索**全部仍存在**（upload 管线、changelog、domain 三仓、config 旧 API、rerank/doc_summary、启动期回填、letter re-export、缺依赖、缺转出）；前端旧审计 A1–A11 / B1–B12 / C1–C10 / D3 / E1–E7 / F1–F5 **全部仍在**，仅行号与规模变化；`.dockerignore` 仍未排除 `backend/data/`；`.claude/worktrees/` 两个损坏 gitlink 仍在 |
| **已修复** | P1-1（要求类文件不切片）、P1-6（clear_data 不全清）已修（2026-10-15 报告项）；`tests/routes/test_chapter_studio.py` 的占位符断言已随最新提交反转 |
| **新出现** | `bench.html` 路由错位；`chapter-studio.mjs` 9 个死函数；`placeholder_purge` 的 `_drop_empty_sections` 未接线；`block_purged_items` 新表在审计期间才由运行中的后端建出（旧库/未重启实例仍有 `no such table` 风险）；双评审表问题随评审 v3/v4 扩大；provider 默认值分叉；`generate-session` 的 `generate-all` 404 |
| **规模变化** | 死 CSS 与旧审计同源（旧审计按估算约 2,500 行；本轮按规则块重扫为 753 块 / 2,446 行，`chapter-studio.css` 因压缩格式死块数从 5 增至 354）；死端点从旧审计 21 个增至 43 个（方法级）；零调用 API 方法从 11 增至 12 |

---

## 8. 处置优先级建议

### P0：立即修（1 天内，能防止线上事故）

1. `requirements.txt` 补 `jieba`/`rank_bm25`（启动阻断）。
2. `infra/llm/__init__.py` 补转出（功能静默假死）。
3. 删 410 端点 + `api.outline` 客户端（三处成对）。
4. 修 `update_block_source` 双定义 + `nodes.py` 调用签名（数据不落库）。
5. 恢复上传 50MB 上限（安全/稳定性）。
6. 确认所有部署实例已重启：`block_purged_items` 只在 `init_db` 时创建（本机活后端在审计期间已建表并写入 293 行，未重启的实例仍会 `no such table`）。
7. `.dockerignore` 补 `backend/data/`（客户数据不进镜像）。

### P1：低风险死代码删除（1–2 天，可独立提交）

- 后端：`routes/upload.py`、`routes/changelog.py`、旧 config 5 端点、`utils/validators.py`、`utils/sse.py` 6 个死函数、21 处未使用 import、`_index_path`、`list_materials_by_ids`、`_parent_key`/`rollup_totals` 等 16 组死符号。
- 前端：`context-panel.js` + `mode-editor.js` 调用、13 个死函数、7 个零外部引用导出、`materials.outline`、`fmtTime`、死变量、`frontend/.DS_Store`。
- 测试：随死模块删 3 个 repository 测试（672 行）与 rerank/letter 相关用例；先删实现再删测试。
- CSS：**先抽 `three-mode.html` 内联 CSS 为 `assets/three-mode.css`**，再按文件分批删 753 块死规则（每批 5 页截图对比）。

### P2：需产品/架构决策（1–2 天 + 评审）

- 两个死整页是否删除（先修测试白名单，再删；打 tag 回退）。
- `diff-review.html` 补入口还是删路由。
- `bench.html`/`scripts/bench_*` 迁到 `tools/` 还是纳入产品。
- domain 三仓：继续 Phase 7 接线，还是删仓 + 672 行测试。
- rerank / doc_summary：删干净，还是改成真开关。
- 双评审表、provider 单一来源、`config` 单复数收敛。
- `_drop_empty_sections` 接线还是删除。

### P3：结构重构（单独立项，本轮不做）

- `mode-outline.js`（7,722 行）/ `mode-editor.js`（2,848 行）God module 拆分。
- 内联 CSS 抽取与 CSS 分层（`ui.css` 组件单源）。
- `?v=` 机制替换为构建期 hash；`window.__*` 通道收敛为显式事件/store。
- DB 迁移版本化、外键启用、13 张空表的功能验收。

---

## 9. 复现命令与基线

```bash
cd /Users/jianghe/projects/ge-solution

# 基线
git rev-parse --short HEAD                 # 299073c
git ls-files | wc -l                       # 886
node --test tests/frontend/*.test.mjs      # 161 pass
.venv/bin/python -m pytest -q              # 1152 passed, 1 deselected

# 死页不可达
grep -n "@app.get\|app.mount" backend/main.py
curl -sI http://127.0.0.1:8000/workbench | head -3        # 307

# P0 依赖
grep -n "jieba\|rank" requirements*.txt                    # 0
grep -n "dispatch_package_merge_json" backend/infra/llm/__init__.py   # 0

# 契约错位
grep -rn "api/projects/\${projectId}/outline\|/generate-all" frontend/assets
grep -rn "download_url" backend frontend
grep -rn "update_block_source" backend --include='*.py'

# 死函数/端点抽查
grep -rn "\breadConfirmedPkg\b\|\brenderPackageCards\b\|\brenderPointsAlignment\b" frontend tests
grep -rn "material-search\|analyze-points\|delivery-gate/auto\|recut-all" frontend tests

# 版本号契约
grep -rhoE '\?v=[A-Za-z0-9._-]+' frontend/ | sort -u
sed -n '108,150p' tests/frontend/module-imports.test.mjs

# DB 状态（只读）
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:backend/data/ge_solution.db?mode=ro', uri=True)
print('user_version', c.execute('pragma user_version').fetchone()[0])
print('foreign_keys', c.execute('pragma foreign_keys').fetchone()[0])
print('runs_null_pkg', c.execute("select count(*) from workflow_runs where package_id is null").fetchone()[0])
for t in ('leads','writing_model_settings','evidence_need_actions','project_writing_facts','citations'):
    print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
PY
```

---

## 附录：审计证据索引

| 报告 | 内容 |
|---|---|
| `docs/reviews/_audit2/frontend.md` | 48 项（D01–D29 / U01–U11 / I01–I08），含死 CSS 逐块清单、函数逐行比对、13 项排除项、新旧行号对照 |
| `docs/reviews/_audit2/backend-a.md` | 88 项：43 个死端点、37 个死服务符号、21 个未使用 import、8 项逻辑不一致（含 E01–E04 详述与修法） |
| `docs/reviews/_audit2/backend-b.md` | 44 项：infra/编排/agents/domain/scripts/tests；含缺依赖复现、死模块、一次性脚本、启动期回填、术语 re-export |
| `docs/reviews/_audit2/contract.md` | 跨层：250 端点↔前端矩阵（36 个无调用、2 个前端调不存在）、DB schema 校验、13 张 0 行表、10 处文档矛盾、4 个硬编码开关、未跟踪依赖 |
| `docs/reviews/2026-10-19-code-optimization-plan.md` | 上一轮清理方案（可作为删除顺序参考；本轮确认其 P0/P1 项几乎均未执行） |
| `docs/reviews/2026-10-15-code-review-report.md` | 更早的缺陷类审查（与本报告互补） |
