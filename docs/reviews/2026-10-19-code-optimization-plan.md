# ge-solution 死代码与冗余代码优化方案

> 日期：2026-10-19
> 分支：`feature/multi-agent-bid-system`（HEAD `4a6f627`，工作区另有 19 个前端未提交改动，均为 `?v=` 版本号与一次篇幅行回滚）
> 范围：全仓库的**老旧实现、死代码、无用代码、重复冗余**，逐个目录、逐项遍历后归并为可执行的处置清单
> 依据：三份只读审计报告
> - `docs/reviews/_audit/frontend.md`（52 项）
> - `docs/reviews/_audit/backend.md`（86 项）
> - `docs/reviews/_audit/aux.md`（23 项）
>
> 本文是**优化方案**，不是执行记录：本轮没有删除、修改任何项目文件，没有启动服务，没有跑后端 pytest。

---

## 0. 结论摘要

### 0.1 五条结论

1. **仓库里真正的"垃圾"比预想的少，真正的"重复"比预想的多。** 两个前端旧整页（`frontend/workbench.html`、`frontend/project-init.html`）合计 **10,542 行**在 HTTP 层已 100% 不可达，却仍是 `mode-editor.js` / `mode-outline.js` 的历史快照，并且每次发版都被迫同步 `?v=`。这是单项收益最大的一笔。
2. **后端有 18% 的 HTTP 端点（21/115）没有前端或产品调用方**，其中旧上传管线（`routes/upload.py`，292 行，3 个端点）与旧单配置 API（6 个端点，151 行）是整块可删的死模块；三套 `domain/*/repository.py`（315 行）只被测试养着，生产零引用。
3. **有两个"静默失效"必须在清理前先修**：`requirements.txt` 从未声明 `jieba`/`rank_bm25`（新镜像启动即 `ModuleNotFoundError`）；`infra/llm/__init__.py` 漏转出 `dispatch_package_merge_json`，导致 LLM 标包合并永远静默降级。**不先修这两处，后续任何清理的回归结果都会失真。**
4. **`.dockerignore` 是本次审计发现的最危险配置**：未排除 `backend/data/`，Docker build context 约 **630MB**，且 `COPY backend/` 会把 180.9MB 生产 DB、约 420MB 备份和 22MB 真实客户投标 PDF 打进镜像。改几行配置即可，收益和风险比最高。
5. **结构性问题（God module、单文件内联 3,342 行 CSS、`?v=` 机制、window 全局通道、schema 无版本、外键未启用）不在本轮清理范围**。本轮只做"删零引用、收敛重复、修风险"，结构重构单独立项，避免把可回滚的清理变成高风险大改。

### 0.2 量化基线（实测）

| 指标 | 数值 | 说明 |
|---|---:|---|
| git 跟踪文件 | 541 | `git ls-files \| wc -l` |
| 跟踪字节 | 35.88 MB | 其中 `docs/screenshots/` 占 27.86 MB（77.6%） |
| `.git` | 47 MB（pack 36.23 MiB） | 删 HEAD 文件不会缩小 clone，除非重写历史 |
| 工作区 | 1.2 GB | `.venv` 490MB + `backend/data` 599MB（均被 ignore） |
| 前端代码 | 34,284 行 | 7 个 HTML + 18 个 assets |
| 后端 Python | 109 文件 / 28,244 行 | 含 `backend/scripts/` 2 文件 |
| 后端测试 | 82 文件 / 18,905 行 | — |
| 前端测试基线 | **160 passed** | 2026-10-19 实测 `node --test tests/frontend/*.test.mjs` |
| 后端测试基线 | 795 passed / 1 deselected | 取自 2026-10-15 报告，本轮未重跑 |

### 0.3 可回收量（工作区口径，不含历史重写）

| 层级 | 保守可删 | 含需产品确认项 |
|---|---:|---:|
| 前端行数 | 约 2,600–3,000 行 | 约 13,100–13,500 行（含两个旧整页） |
| 后端行数（含测试） | 约 2,300 行 | 约 3,200 行 |
| 仓库体积 | 约 1.6 MB | 约 28.6 MB（其中 26.46 MB 是未引用截图） |
| Docker build context | 630 MB → **<15 MB**（只改配置） | 同左 |
| 本地 `backend/data` 垃圾 | 约 400 MB（未跟踪） | 同左 |

> 注意：git 历史里的截图 blob 仍在 pack 中，删除 HEAD 文件后 clone 体积不变；真要瘦身需 `git filter-repo` 重写历史（见 §7.3，默认不做）。

---

## 1. 判定标准与口径

### 1.1 分类定义

| 类别 | 定义 | 典型处置 |
|---|---|---|
| **DEAD** | 无任何运行时/测试/文档引用，或 HTTP 层不可达 | 直接删除（先补测试守卫） |
| **LEGACY** | 已被新实现取代，但因"回退/对照/过渡"仍留在仓库 | 删除或明确标注保留期 |
| **DUPLICATE** | 同一实现存在两份及以上（内联↔模块、路由↔服务、CSS 跨文件） | 收敛到单一来源 |
| **UNUSED-API** | 端点/导出/客户端方法存在但无产品调用方 | 前后端同批删除，或补入口 |
| **JUNK** | 空壳函数、无效分支、无效依赖、误提交产物、本地垃圾 | 直接删除或加 ignore |
| **BROKEN-CONFIG** | 配置/依赖/转出与实际引用不一致，导致功能静默降级 | 立即修复，不能当清理项 |

### 1.2 风险分级

| 风险 | 含义 | 处理要求 |
|---|---|---|
| `safe` | 引用检查为 0，且有测试/编译兜底 | 可在同一阶段批量做，按目录分组提交 |
| `needs-check` | 无静态引用，但可能存在动态拼接、外部脚本、浏览器视觉依赖 | 先补证据（grep/浏览器 smoke），再做 |
| `product-decision` | 涉及产品回退能力、历史证据、客户数据、部署平台 | 产品/负责人拍板后执行 |
| **P0** | 启动阻断、客户数据外泄、功能静默假死 | **先于一切清理修复** |

### 1.3 证据口径与盲区

- 引用检查统一用"文件级 + 符号级 grep"：HTML `<script>/<link>`、静态 `import`、动态 `import()`、Python `from X import`、测试引用、文档引用。
- 前端死函数另用 AST/括号匹配确认"仅定义行出现 1 次"；CSS 用规则块扫描 + class/id 引用语料 + 动态 class 前缀检查（排除 `'prefix-' + x` 误判）。
- 后端端点用"路径 + 模板字符串插值"宽松匹配，再逐个关键字 grep 复核。
- **盲区（已如实记录）**：静态 grep 覆盖不到运行时拼接的路径、外部脚本调用、非 git 消费者；后端 pytest 本轮未跑；CSS 清理没有自动化视觉回归，必须人工截图对比。

---

## 2. 总体处置原则

1. **先修风险，再删代码。** P0 项（依赖、转出、dockerignore、gitlink）必须在任何清理提交之前完成。
2. **一次提交只做一件事。** 删一个模块 / 删一组端点 / 改一个测试契约，各自独立 commit，提交信息写清"删除依据 = 引用检查为 0"，保证可单独 revert。
3. **先删实现，再删测试。** 反过来会先绿后红（测试引用会让死代码看起来"活着"）。
4. **不删历史，只删 HEAD。** 需要回退能力时用 git tag，不在工作区保留两份实现。
5. **产品决策项单独列，不夹带。** 旧页面、`skill/`、截图、原型、部署清单、旧匹配管线都列在 §9，拍板前不动。
6. **结构重构单独立项。** God module 拆分、CSS 分层、`?v=` 替换、window 通道收敛不在本轮。

---

## 3. 逐项处置清单

> ID 沿用审计报告编号并加前缀（`BE-` 后端、`FE-` 前端、`AUX-` 仓库资产），便于与三份审计报告逐条对照。
> "规模"是行数或文件体积；"阶段"对应 §5 路线图。

### 3.1 P0：先修复的风险项

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| BE-X01 | `requirements.txt:10-19` | DEAD-DEP | 缺 2 行声明 | `infra/retrieval/keyword.py:6-7` 顶层 `import jieba` / `from rank_bm25 import BM25Okapi`，经 `assign.py`、`locate.py`、`nodes.py` 进入启动路径；requirements 从未声明（`git log -S jieba -- requirements.txt` 为空）；本地 `.venv` 恰好装过，`Dockerfile:11` 只装 requirements | **高**：全新环境/镜像启动即 `ModuleNotFoundError` | 补 `jieba==0.42.1`、`rank-bm25==0.2.2`；用干净 venv 跑 `python -c "import main"` 验证 | P0 |
| BE-X02 | `backend/infra/llm/__init__.py:34-50` | BROKEN-CONFIG | 1 行 | `routes/materials.py:643` `from infra.llm import dispatch_package_merge_json`；函数实际在 `dispatcher.py:1096`，`__init__.py` 未转出；import 包在 `try/except` 里，异常被吞成 info 日志 | **高**：`/package-analysis?use_llm=1` 永远走规则降级，`merge_with_llm`/`validate_merge_proposal`（约 200 行 + 6 测试）产品不可达 | 在 `__init__.py` 导入并加入 `__all__`；补一条"导入可用性"单测 | P0 |
| AUX-F2 | `.dockerignore` | CONFIG | 387 B | 未排除 `backend/data/`、`docs/`、`skill/`、`ge-solution/`、`.claude/`、`.planning/` 等；`Dockerfile:31` 为 `COPY backend/ ./backend/`；本地 `backend/data` 599 MB（180.9 MB DB + 397.7 MB + 22.2 MB 备份 + 22 MB 客户 PDF） | **高**：build context ≈630 MB；客户标书与本地 DB 被打进镜像 | 追加排除规则（见 §4.1）；验证 context <15 MB | P0 |
| AUX-C1 | `.claude/worktrees/groovy-shimmying-elephant`、`security-hardening` | JUNK | 2 个 gitlink | `git ls-files -s .claude` = 2×`160000`；无 `.gitmodules`；指向 commit `ffeda42` 但 `git cat-file -t` 失败（对象不存在）；`git submodule status` fatal | 中：clone 留空目录，CI 跑 submodule 即失败 | `git rm --cached` 两条目；不删本地分支 `worktree-security-hardening`（未合并，见 §9） | P0 |
| AUX-F6 | `.gitignore` | CONFIG | 525 B | 已覆盖 `.venv/`、`backend/data/`、`.planning/` 等；**缺 `.claude/worktrees/`、`.pytest_cache/`** | 低：gitlink 事故根因 | 追加两行 | P0 |
| BE-J02 | `backend/data/aibidding.db`、`app.db`、`ge_solution.db.bak-20261013-1742`、`…-1751` | JUNK | 本地约 400 MB（未跟踪） | 两个 0 字节库全仓零引用（`db.py:21` 只用 `ge_solution.db`）；两个 `.bak` 是 2026-10-13 快照 | 中：会被 Docker `COPY backend/` 打包（与 AUX-F2 同源） | 备份到仓外后删除；`.dockerignore` 排除 `backend/data/` | P0 |
| AUX-J1/J2/D3 | 根/`frontend/`/`ge-solution/` 的 `.DS_Store`、`.pytest_cache/`、未跟踪的 `docs/screenshots/live-kind-row.png` | JUNK | 约 0.3 MB | 均未跟踪；`live-kind-row.png` 全仓零引用 | 低 | 本地删除；`.pytest_cache/` 加 ignore | P0 |

### 3.2 后端死模块与死端点

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| BE-D01 | `backend/routes/upload.py:1-292` | DEAD | 292 行 / 3 端点 | 仅 `main.py:18` import、`:227` 注册；`/api/upload`、`/parse/stream`、`supported-formats` 前端/测试 0 调用；最后提交 2026-06-17；`docs/design/方案A…md:550` 已写"下线旧上传管线"；另有 SSE 单 queue 抢断缺陷（`upload.py:178-191`） | safe | 删文件 + `main.py:18/227` 两行；连带 BE-D03、BE-C01 | P1 |
| BE-D02 | `backend/routes/changelog.py:1-71` | DEAD | 71 行 / 1 端点 | 前端/测试/文档 0 引用；最后提交 2026-05-13（最老路由）；运行时 `git log` 生成版本表，Docker 镜像无 `.git` → 只会 503 | safe | 删文件 + `main.py:20/229` | P1 |
| BE-D03 | `backend/utils/validators.py:1-11` | DEAD | 11 行 | `validate_uuid` 唯一调用点是 `upload.py:162` | safe | 随 BE-D01 删 | P1 |
| BE-D04/05/06 | `backend/domain/{spec,proposal,material}/repository.py` | DEAD | 315 行 + 672 行测试 | 三个类生产代码零引用（`grep -rn SpecRepository backend scripts` 排除 domain = 0）；唯一消费者是各自测试；三仓反向 import `services.block_store`（分层倒挂）；注释称"Phase 7 切换"但未发生 | needs-check（架构方向待决） | 方案 A：删三仓 + `__init__.py` 转出 + T01–T03 测试；方案 B：若继续 Phase 7，先接线再删 block_store 重复 SQL | P2 |
| BE-D07 | `backend/services/material_search.py:1-148` | DEAD | 148 行 + 81 行测试 | 唯一生产引用是死端点 U11（`blocks.py:90`）；消费者只剩 `tests/services/test_material_search.py` | needs-check | 随 BE-U11 一起删；若产品要做"按需检索"则补前端入口 | P2 |
| BE-U05–U10 | `backend/routes/config.py:99-236,268-280` | UNUSED-API | 151 行 / 6 端点 | 前端全部走 `/api/configs*`（20+ 处），`/api/config[^s]` 0 处；测试 0；注释自称"旧接口（向后兼容）"；`config_store` 6 个方法随之死（`get/save/clear/reorder/get_next_index/save_all`） | safe | 删 6 端点 + `ReorderRequest` + BE-C02；`_KEEP_KEY` 保留（新 PUT 用） | P1 |
| BE-U11 | `GET /api/blocks/{id}/material-search`（`blocks.py:90`） | UNUSED-API | 21 行 | 前端/测试 0 调用；docstring 称"两者都保留"但从未接入 | needs-check | 确认不做 → 删端点 + BE-D07 + T04 | P2 |
| BE-U12 | `POST /api/blocks/{id}/ai`（`blocks.py:312`） | UNUSED-API | 54 行 | 仅 `api.js:211` 有封装且该封装从未被调用；`_AI_PROMPTS`/`AIActionRequest` 只服务它 | needs-check | 确认无入口 → 删端点 + 封装 + 校验；要做则补 UI | P2 |
| BE-U13 | `GET /api/projects/{id}/split-answers`（`materials.py:180`） | UNUSED-API | 8 行 | 只有 `api.workflow.getSplitAnswers` 封装且从未调用；同路径 POST 在用 | safe | 删 GET + api 封装 | P1 |
| BE-U14 | `POST /api/projects/{id}/map-sources`（`materials.py:587`） | UNUSED-API | 35 行 | 只有 `api.materials.mapSources` 封装且从未调用；其内部还带 `retrieve_chunks`/`build_bm25_index` 导入 | safe | 删端点 + 封装 + 相关 import | P1 |
| BE-U15 | `POST /api/projects/{id}/lock`（`projects.py:177`） | UNUSED-API | 28 行 | 前端/测试 0 调用；只读查库 `project_snapshots` = **0 行**（功能从未被真实使用）；`api.projects.lock` 封装也未调用 | safe | 删端点 + 封装；`create_snapshot` 保留（导出链路用） | P1 |
| BE-U16 | `PATCH /api/projects/{id}`（`projects.py:164`） | UNUSED-API | 18 行 | `projects.patch` 封装 0 调用，无测试；可能是"改项目名"预留 | needs-check | 要改名功能 → 补 UI；否则删端点 + 封装 + `ProjectPatchRequest` | P2 |
| BE-U17 | `POST /api/workflow/{tid}/analyze-points`（`workflow.py:352`） | UNUSED-API | 20 行 | 前端/测试 0 调用；真实入口是 `workflow/start` 带 `points_only:true`；它是全仓唯一 `points_only=True` 调用 | safe | 删端点 + `runner.redraft_outline` 的 `points_only` 参数分支；`nodes/graph` 的 config 字段保留 | P1 |
| BE-U18 | `GET /api/review/{thread_id}`（`review.py:19`） | UNUSED-API | 34 行 | 仅 `api.review.get` 封装，从未调用；测试 `test_review.py:103` 是唯一调用方；评审页现从 workspace/SSE 取数 | needs-check | 无外部集成 → 删端点 + 封装；`ReviewRepository` 保留（`nodes.py:1047` 在用） | P2 |
| BE-U19 | `POST /api/blocks/{id}/studio/draft`（`chapter_studio.py:269`） | UNUSED-API | 54 行 | 前端已改用多 Agent `writing-jobs`；`grep studio/draft frontend` = 0；只有测试在调；`DraftBody` 只为它存在 | needs-check | 确认不回退 → 删端点 + `DraftBody` + 测试用例；`build_writing_prompt` 保留 | P2 |
| BE-U20 | `GET /api/readme`（`main.py:339-347`） | UNUSED-API | 9 行 | 前端/测试/文档 0 引用 | safe | 删路由函数 | P1 |
| BE-U21 | `POST /api/projects/{id}/outline`（`materials.py:574`，固定 410） | UNUSED-API | 11 行 | 被 LangGraph `/workflow/start` 取代；但活跃前端仍在 POST（`mode-editor.js:2765`、旧 `workbench.html:5148`），触发条件是 `role='requirement'`（当前库 0 行，属休眠契约漂移） | needs-check | 前后端同批删：端点 + `mode-editor.js` 的 `startWorkbenchExtraction` 与 `#extract-action-bar` 入口 | P2 |

### 3.3 后端活文件内的死代码

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| BE-C01 | `backend/utils/sse.py:24-62` | DEAD | 39 行 | `sse_token`/`sse_section_*`/`sse_all_done`/`sse_doc_summary`/`sse_section_skip` 全仓 0 引用；`sse_parse_*`/`sse_error` 只被 BE-D01 使用 | safe | 删 6 函数；BE-D01 删除后连 `sse_parse_*`/`sse_error` 一起删，仅留 `format_sse_event` | P1 |
| BE-C02 | `backend/services/config_store.py:115-121,158-176,258-278` | DEAD | 44 行 | `save_all` 仅测试用；`reorder` 仅死端点 U10 用；`get_next_index` 0 引用；`get/save/clear` 仅旧 `/config` 端点用 | safe | 删 6 个方法（先删 U05–U10）；`mark_verified` 保留 | P1 |
| BE-C06 | `backend/main.py:246` | DEAD | 1 行 | `_index_path` 定义后从未使用；且 `frontend/index.html` 不存在；`/` 实际返回 `_projects_path` | safe | 删该行（与 FE-B11 同一处） | P1 |
| BE-C07 | `backend/orchestrator/graph.py:170-181` | DEAD | 12 行 | `_route_after_generate` 仅定义 1 处；generate 后实际用内联 `_generate_fanout` | safe | 删函数 | P1 |
| BE-C08 | `backend/infra/llm/errors.py:143-145,273-280` | DEAD | 9 行 | `is_retryable`/`unhealthy_snapshot` 生产 0 引用；`reset_health` 只被 `tests/conftest.py` 用 | safe | 删前两个；`reset_health` 若要留，标注 test-only | P1 |
| BE-C09 | `backend/infra/llm/usage.py:52-53` | DEAD | 2 行 | `get_sink` 全仓 1 处（定义） | safe | 删 | P1 |
| BE-C10 | `backend/infra/parser/outline_builder.py:34,45,329-350,814-825` | DEAD | 36 行 | `_ENUM_RE`/`_TOPIC_LEVEL_HINTS`/`match_spec_nodes`/`render_outline_text` 各 1 处；后两者已被 `build_outline` 内部实现取代 | safe | 删 4 个符号；T08 局部删 | P1 |
| BE-C11 | `backend/infra/parser/review_points.py:70-72,222-226,520-524` | DEAD | 13 行 | `_SCOPE_LINE_RE`/`total_points`/`review_points_of` 生产 0 引用 | safe | 删 3 个符号；T08 局部删 | P1 |
| BE-C12/C18 | `toc.py:11`、`scope_index.py:31`、`tech_requirements.py:89`、`domain/material/models.py:99-109` | DEAD | 6 行 | 6 个常量全仓各 1 处（定义） | safe | 删 | P1 |
| BE-C13 | `backend/services/block_store.py:137-155` | DEAD | 19 行 | `list_materials_by_ids` 全仓 1 处；`main.py:86-90` 自己写了等价 SQL | safe | 删函数；可顺手让 `main.py` 复用 | P1 |
| BE-C14 | `backend/services/page_budget.py:114-123,146-165` | DEAD | 30 行 | `_parent_key`/`rollup_totals` 各 1 处；后者含永假分支 `any(c is chapter for c in [])` | safe | 删两函数（含 J05） | P1 |
| BE-C15 | `backend/services/usage_store.py:69-70` | DEAD | 2 行 | `uninstall` 全仓 1 处；`install` 在 `main.py:174` 用 | safe | 删 | P1 |
| BE-C16 | `backend/orchestrator/state.py:189-229` | DEAD | 41 行 | `merge_state` 生产 0 引用，仅 `tests/orchestrator/test_state.py` 8 用例；LangGraph 用 `_merge_dict` reducer | needs-check | 删函数 + `__all__` + 对应用例；或移到测试 helper | P2 |
| BE-C17 | `backend/domain/review/repository.py`（3 个方法） | DEAD | 约 40 行 | `clear_thread`/`list_findings_by_block`/`latest_for_scope` 生产 0 引用，仅测试用；类本身活（`routes/review.py`、`nodes.py:1047`） | safe | 删 3 个方法，保留类 | P1 |
| BE-J01 | 12 个文件 / 32 个未使用 import | JUNK | 约 32 行 | AST 扫描"仅 import 行出现 1 次"并人工确认（`materials.py`、`config.py`、`chapter_studio.py`、`workflow.py`、`docx.py`、`dispatcher.py`、`rerank.py`、`wang_anshi.py`、`bao_zheng.py`、`chapter_writing.py`、`scoring_map.py`、`runner.py`、`nodes.py`） | safe | 批量删；装 `pyflakes` 兜底 | P1 |
| BE-J03/J04/J05 | `materials.py:663-664`、`main.py:244-245`、`page_budget.py:153-154` | JUNK | 5 行 | `if note: pass` 空分支；`frontend_dir` 连续两次赋值；恒假表达式 | safe | 就地改成等价写法/删除 | P1 |

### 3.4 后端重复实现

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| BE-P01 | `domain/*/repository.py` 反向包装 `block_store` | DUPLICATE | 315 行 | 三仓方法体几乎全是转调 `services.block_store`，而 routes/services/orchestrator 又直接调 block_store → 同一 CRUD 两套门面 | needs-check | 与 BE-D04/05/06 同批：接线或删除 | P2 |
| BE-P02 | `routes/materials.py:115-136` ↔ `backend/scripts/recut_old_materials.py:32-51` | DUPLICATE | 21 行 | `_split_for_scoring_evaluation` 逐字同逻辑（4 行/片、步长 3），脚本注释自认"保持一致" | safe | 抽到 `infra/parser` 单一实现；脚本归档时一并删 | P1 |
| BE-P03 | `revisions.py:53`、`packages.py:167,194` | DUPLICATE | 9 行 | 3 处手写 `SELECT id FROM projects WHERE id=?`；`block_store.get_project` 已有 | safe | 统一调 `get_project`，顺带统一 404 文案 | P1 |
| BE-P04 | `services/package_scope.py:20-25` ↔ `block_store.py:427-432` | DUPLICATE | 6 行 | 最新 run 查询 SQL 逐字相同 | safe | `package_run_thread` 改调 `current_run_thread_id` | P1 |
| BE-P05 | `services/page_budget.py:286-293` ↔ `services/split_answers.py:48-60` | DUPLICATE | 8 行 | `split_answers` UPSERT 逐字重复 | safe | page_budget 改调 `set_answer` | P1 |
| BE-P06 | `routes/materials.py:707-715` ↔ `services/split_answers.py:31-46` | DUPLICATE | 9 行 | 同表 SELECT + JSON 解析重复 | safe | 改调 `get_answers` | P1 |
| BE-P07/P08 | `chapter_studio.py:327` ↔ `export.py:220-222`；`export.py:95-97` ↔ `:138-140` | DUPLICATE | 8 行 | 同一 `response_document_settings`/`project_snapshots` 查询重复 | safe | 抽小函数复用 | P1 |
| BE-P09/P10 | `domain/proposal/repository.py:76` ↔ `block_store.py:1082-1085`；`block_store.py:173,195` ↔ `scripts/recut_old_materials.py:95` | DUPLICATE | 5 行 | 同一 UPDATE/DELETE 两处写 | safe | 随死仓/脚本删除收敛 | P1 |
| BE-P11 | `routes/chapter_writing.py:91` ↔ `services/chapter_writing.py:33-37` | DUPLICATE | 4 行 | 同 block 进行中 job 查询重复 | safe | 路由改调 service | P1 |
| BE-P12 | `nodes.py:563` 的 `MATCH_MODE` 两套匹配 | DUPLICATE/LEGACY | ≈430 行 | `assign` 默认、`rerank` 仅回滚；两条管线并存 | product-decision | 见 BE-L04 | P5 |

### 3.5 后端遗留实现与停用链路

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| BE-L01 | 旧上传管线（同 BE-D01） | LEGACY | 342 行 | 被 `POST /api/projects/{id}/materials` 取代；旧 SSE 单 queue 抢断缺陷仍在 | safe | 整体删除 | P1 |
| BE-L02 | `routes/materials.py:574-584` | LEGACY | 11 行 | 固定 410；已无测试；仅陈旧前端调用（休眠） | needs-check | 与 BE-U21 同批前后端删除 | P2 |
| BE-L03 | 旧单配置 API（同 BE-U05–U10） | LEGACY | 151 行 | 2026-05 单 Key 设计，与现在多配置语义冲突 | safe | 删端点 + 旧方法 | P1 |
| BE-L04 | `nodes.py:563,637-700` + `agents/shen_kuo.py`(256) + `infra/retrieval/rerank.py`(127) | LEGACY | ≈430 行 + 约 560 行测试 | `MATCH_MODE="assign"` 硬编码；rerank 注释明说"保留用于回滚/对照"；`main.py:136` 每次 start 仍实例化 ShenKuoAgent | product-decision | ①删旧路径（含 GraphDeps、main.py 实例化、测试）；②保留则改成环境变量开关并写 README 回滚 SOP | P5 |
| BE-L05 | `dispatcher.py:110-188,428-459` + `zhang_heng.py:369-381` | LEGACY | ≈110 行 | 2026-09-17 起 `doc_summary` 停用，恒为 `""`；`dispatch_doc_summary` 仅测试 spy 引用 | product-decision | 恢复（换非推理模型/给足 max_tokens）或一次性删干净（涉及 10+ 文件），不要留半条链路 | P5 |
| BE-L06 | `backend/scripts/normalize_extract_fields.py`(147)、`recut_old_materials.py`(144) | LEGACY | 291 行 | 文件头自写"一次性脚本"；全仓无 import/无调度入口 | product-decision | 移到 `scripts/oneoff/` 或归档删除；确认线上数据是否还需重跑 | P5 |
| BE-L07 | `backend/db.py:388-398,401-435,466-490` | LEGACY | 71 行 | 三段一次性回填挂在每次 `init_db()`：名称清洗（只读查库脏值 0）、display_name 回填、package_id 回填（41/50 行 `package_id` 为 NULL 且 target/package_no 全空 → 永远修不好） | needs-check | ①可删；②③确认所有库已回填后删；长期引入 `schema_migrations` | P5 |
| BE-L08 | `domain` 过渡层（同 BE-D04/05/06） | LEGACY | 315 行 | 名义分层 `routes→services/domain→infra`，实际 domain 反向依赖 services；`block_store` 1,323 行 God module | needs-check | 与团队确认 Phase 7 去留，二选一 | P2 |
| BE-L09 | `dispatcher.py:1051-1054` re-export | LEGACY | 4 行 | 定义已迁 `domain/letter_detector.py`；调用方都走新路径；仅测试验证"旧路径仍可用" | safe | 删 re-export + T05 用例；`infra/llm/__init__.py` 改直连 domain | P1 |
| BE-L10 | `main.py:54`、`materials.py:49` 的 `requirement` 角色 | LEGACY | 2 行 | `main.py:51` 自注"早期版本遗留写法"；只读查库 `role='requirement'` = 0 行 | needs-check | 做一次 `UPDATE ... SET role='main_rfp'` 后删；或保留到下个大版本 | P5 |
| BE-L11 | `config_store.py:306-308` 旧 JSON 单 dict 兼容 | LEGACY | 3 行 | 兼容极老配置文件格式；删除会让未升级环境丢配置 | needs-check | 保留；确认各环境配置已是 list 后删 | P5 |

### 3.6 后端测试（随死代码一起处理）

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| BE-T01–T03 | `tests/domain/test_{spec,proposal,material}_repository.py` | DEAD-TEST | 672 行 | 全部围绕 BE-D04/05/06 死仓 | safe | 随死仓删除 | P2 |
| BE-T04 | `tests/services/test_material_search.py` | DEAD-TEST | 81 行 | 围绕 BE-D07 死模块 | safe | 随 D07/U11 删 | P2 |
| BE-T05/T08 | `test_letter_detector.py:32-36`、`test_outline_builder.py`、`test_structure.py`、`test_review_points.py:211` | DEAD-TEST | 约 35 行 | 保护 BE-L09/BE-C10/C11 的死符号 | safe | 局部删对应用例 | P1 |
| BE-T06/T07 | `test_chapter_studio.py:134,137`、`tests/routes/test_review.py` | DEAD-TEST | 约 154 行 | 围绕 U19/U18；T07 需拆分保留 repository 用例 | needs-check | 随端点删除，保留有价值的 repository 断言 | P2 |
| BE-T09/T10 | `test_llm_errors.py`、`test_config_store.py`、`test_state.py`、`test_llm_dispatcher.py`、`test_shen_kuo.py`、`test_zhang_heng.py`、`test_nodes.py:225-231` | DEAD-TEST/LEGACY-TEST | 约 280 行 | 保护 C08/C02/C16、L04 rerank、L05 doc_summary | product-decision | 随对应决策同批处理；不要先删实现留测试 | P5 |

### 3.7 前端旧页面与旧契约

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| FE-A1 | `frontend/workbench.html` 全文件 | DEAD | 5,580 行 / 245 KB | 路由表无任何 `FileResponse` 指向；`/workbench` 是 307→`/project-intake`（`main.py:301-307`）；只挂 `/assets`（`main.py:256`）；页内 `?legacy=1` 逃生口已失效 | product-decision | 整页删除；先做 FE-F2 测试契约修正 | P2 |
| FE-A2 | `frontend/project-init.html` 全文件 | DEAD | 4,962 行 / 239 KB | 同上；`/project-init` 307→`/project-intake`（`main.py:316-322`） | product-decision | 整页删除 | P2 |
| FE-A3 | `workbench.html:2061-2071`、`project-init.html:895-905` | LEGACY | 22 行 | 页内自跳 `/three-mode` 并留 `?legacy=1`，但页面不可达 → 永不执行，逃生口被后端 307 抢占 | safe | 随 A1/A2 删 | P2 |
| FE-A4/A5 | `workbench.html:2504-5224`、`project-init.html:1099-4959` | LEGACY+DUPLICATE | 6,582 行 | 内联 module 与 `mode-editor.js` 函数名 55/55 重合、非空行 94.4% 一致；与 `mode-outline.js` 138/138 重合、94 个函数体逐字相同；是机械抽取前的历史快照，且已落后 5–67 个新函数 | product-decision | 随整页删除；模块是唯一实现 | P2 |
| FE-A6 | `workbench.html:8-2055`、`project-init.html:8-891` | DUPLICATE | 2,932 行 | 内联 CSS 与 `three-mode.html` 选择器重合 427/429、327/327 | product-decision | 随整页删除 | P2 |
| FE-A7/A8/A9 | `workbench.html:5326-5577`、`:5226-5229`、`:2057` | DUPLICATE/DEAD/JUNK | 257 行 | 模型选择器 IIFE 已被 `mode-outline.js:514-615` 重写；`toggleRefCard` 空壳；`marked@12.0.0` CDN 仅旧页使用 | safe | 随整页删除；前端不再依赖外网 CDN | P2 |
| FE-A10 | 旧页内 `/project-init?...`、`/workbench?...` 链接 | INVALID-CONTRACT | 10 行 | 活代码已全部改 `/three-mode`；这些只存在于不可达页面 | product-decision | 随整页删除，**不要单独改**（改了也是死代码） | P2 |
| FE-A11 | 两个旧页的 15 处 `?v=` | JUNK（维护成本） | 15 处 | 最近 4 个功能提交都同时改 5 个文件，但旧页差异 100% 是版本号（0 行逻辑） | safe | 删页后版本号维护点 68→53 | P2 |

### 3.8 前端死函数、死导出与未用 API

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| FE-B1 | `mode-outline.js:2827-2833,2975-3004,3007-3019` | DEAD | 50 行 | `readConfirmedPkg`/`renderPackageCards`/`renderPointsAlignment` 全仓各 1 处（定义）；标包确认流程已迁 `/project-intake` | safe | 删 3 函数；三模式标包选择另有实现 | P1 |
| FE-B2 | `mode-outline.js:4049-4274` 内 6 个局部 helper | DEAD | 105 行 | `confItem`/`statusCell`/`rpRows`/`notGenerated`/`unitRows`/`scopeRows` 全仓各 1 处；位于 `openSplitReport()`（1,725 行）内 | safe | 删 6 个 helper；建议同时引入 eslint `no-unused-vars` | P1 |
| FE-B3 | `workspace.js:47-51,83` | DEAD | 6 行 | `currentView()`、`inPlace` 各 1 处；四模式导航已被用户要求整块移除（`workspace.js:84-92` 清空并隐藏 nav） | safe | 删两处；导航重做时再加 | P1 |
| FE-B4 | `chapter-tree.mjs:146-151` | UNUSED-API | 6 行 | `setSelectedRow` 全仓 1 处（定义）；同模块其他导出均在活模块使用 | safe | 删导出（或标注预留并补使用点） | P1 |
| FE-B5 | `mode-dispatch.js:48-50` | UNUSED-API | 3 行 | `currentMode` 全仓 1 处；`setMode` 无外部 import，但内部 popstate 在用 | safe | 删 `currentMode`；`setMode` 去掉 `export`，不删函数 | P1 |
| FE-B6 | `diff-review.html:156-164` | DEAD | 9 行 | `fmtTime` 全页零调用；同文件 `timeKey`/`groupByTime` 在用 | safe | 删函数 | P1 |
| FE-B7 | `mode-editor.js:23-24`、`workbench.js:3`、`mode-outline.js:24,30,32` | UNUSED-API | 9 个符号 | import 后全文再无该标识符：`evidenceSourceLineHtml`、`evidenceSnippet`、`chapterStage`、`CHAPTER_STAGE_LABELS`、`chapterBadges`、`constraintLevelLabel`、`groupEvidenceByItem`、`itemCode` | safe | 从 import 列表删除 | P1 |
| FE-B8 | `workbench-modes.mjs:19,26,29,46,58,75,89`、`workspace-model.mjs:6,541,559,621,849` | UNUSED-API | 约 35 行 | `parseMode/modeUrl/resolveSelectedBlock/panelVisible` 仅测试引用；`MODE_LABEL`/`MODE_PANEL`/`REVIEW_PATH`/`CHAPTER_STAGES` 全仓仅定义；两个 role 别名仅再导出 | needs-check | 真死（REVIEW_PATH、CHAPTER_STAGES、role 别名）直接删；测试维持的 API 二选一（见 FE-F3） | P2 |
| FE-B9 | `api.js:44,95,101,164,173,210,216,225,227,287,441` | UNUSED-API | 11 个方法 / 约 30 行 | 精确引用扫描 0 调用：`projects.createPackage/patch/lock`、`materials.mapSources`、`blocks.update/ai/updateRequirement`、`revisions.list/restore`、`workflow.getSplitAnswers`、`review.get`；对应后端路由仍存在 | needs-check | 与后端契约同批决策（FE-B9 与 BE-U13/14/15/16/18/21 联动），单独删前端会让契约清单失真 | P2 |
| FE-B10 | `context-panel.js:1-70` + `mode-editor.js:8,25` | DEAD（运行时 no-op） | 70 行 | 只查 `[data-panel-requirement]`/`[data-panel-sources]`，这两个属性全仓只在该文件命中；DOM 挂点最后出现于 2026-05-31；`initContextPanel()` 每次 editor 加载都执行并立即 return | needs-check | 删 `context-panel.js` + import/调用；若产品要恢复面板则补回 DOM 挂点（二者只能选一） | P2 |
| FE-B11 | `backend/main.py:246` | JUNK | 1 行 | 同 BE-C06（前端审计重复发现） | safe | 删 | P1 |
| FE-B12 | `frontend/.DS_Store` | JUNK | 6 KB | 未跟踪 | safe | 删（同 AUX-J1） | P0 |

### 3.9 前端重复实现

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| FE-D1 | 16 处 `escHtml`/`escapeHtml`/`esc` 定义 | DUPLICATE | 约 80 行 | `escHtml` 7 处、`escapeHtml` 4 处、`esc` 5 处；唯一 export 是 `workspace-model.mjs:158`；**各实现转义集不同**（部分不转单引号、部分不处理 null） | needs-check | 活代码统一 import `workspace-model.mjs` 的 `escapeHtml`（先核对转义语义）；旧页随 A1/A2 消失 | P3 |
| FE-D2 | `project-init.html:4708-4712` ↔ `mode-outline.js:7380-7384` | DUPLICATE | 5 行 | `escapeHtmlForRole` 逐字相同 | product-decision | 随旧页删除；保留 mode-outline 一份；同步 FE-F4 注释 | P2 |
| FE-D3 | `projects.html:939-1058` ↔ `mode-outline.js:514-615` | DUPLICATE | 约 120 行 | 模型选择器 + API 配置弹窗 8 个同名函数两份实现，且已出现漂移（逐函数 diff 非逐字相同） | needs-check | 抽 `assets/model-config.js`（根节点 + api 适配器参数），两处共用；这是活代码里最大重复 | P3 |
| FE-D4 | `projects.html:860-863` ↔ `mode-outline.js:726-729`；`diff-review.html:156-164` | DUPLICATE+DEAD | 8 行 | `fmtSize` 两份；`fmtTime` 死 | safe | 合并进共享工具模块；`fmtTime` 删 | P3 |
| FE-D5 | `workbench.js:92-127` ↔ `mode-editor.js:2568-2622` | DUPLICATE | 约 120 行 | 两模块都往同一宿主 `#blocks-list` 写章节树：行数据键不同（`blockId` vs `idx`）、选中语义不同、点击处理两套；流式期间可能互相覆盖 | needs-check | 明确"编辑器左栏树唯一渲染入口 = workbench.js"，mode-editor 只更新数据并调 `window.__renderEditorTree()` | P3 |
| FE-D6 | `window.__*` 全局通道（约 15 个） | DUPLICATE（架构债） | 约 15 个 | `__renderEditorTree`、`__reloadOutlineTree`、`__extractBlockMap`、`__allBlocks`、`__selectedModel`、`__editorDirty`、`__discardEditorChanges`、`__onBlockSelected` 等跨模块通信；`CustomEvent` 与 `document.addEventListener` 混用 | needs-check | 中期改显式事件/共享 store，先列清单再逐步替换；不属于本轮删除 | P6 |
| FE-C6 | `styles.css` 35 个选择器、`three-mode.html` 9 个、`review.css` 2 个等 | DUPLICATE | 55 条冗余声明 | 同一文件内同选择器重复声明（`.editor-block`×3、`.tiptap-shell`×3、`.block-history`×3…） | safe（合并不改视觉） | 合并为单条声明；每文件一次提交 | P3 |
| FE-C10 | 6 个 CSS 文件之间 | DUPLICATE | 40 个 class | 跨文件重复定义：`.sr-btn`、`.ws-*`、`.workspace-shell/.editor-pane/.outline-pane`、`.v-*/.quality-*` 等；`ui.css` 本应是合并终点 | needs-check | 确立单一来源：ui.css 组件层、styles.css 只留 base、页面级只写本页布局 | P3 |

### 3.10 前端 CSS 死规则

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| FE-C1 | `styles.css` 177 个整块 | DEAD | 约 1,394 行 | 规则级扫描（含 HTML 内联 JS 引用语料）；`.page-shell`/`.gallery-*`/`.list-*`/`.search-*`/`.project-card` 等历史项目列表页样式；`.context-pane` 7 次定义且无元素引用 | needs-check | 分批删：先删项目列表页整段，再删旧 workbench/project-init 布局段；每批 5 页截图对比 | P4 |
| FE-C2 | `review.css` 55 个整块 | DEAD | 约 381 行 | 例：`.review-banner/.banner-*`、`.review-grid`、`.score-pill*`、`.card-section*`、`.issue-list`；review.js 实际只用 71 个 class | needs-check | 删除后 review.css 628→约 250 行；截图对比 | P4 |
| FE-C3 | `response-outline.css` 26 个整块 | DEAD | 约 63 行 | `.sr-btn`、`.sr-two*`、`.sr-echo*`、`.sr-req-*` 等无引用 | needs-check | 删；顺带与 ui.css 去重（FE-C10） | P4 |
| FE-C4 | `workspace.css` 14 个整块 | DEAD | 约 29 行 | `.ws-count/.ws-status/.ws-state-copy/.ws-status-actions` 等；共享壳状态条已按原型移除 | needs-check | 删；保留 `#workspace-nav` 的处置见 C7 | P4 |
| FE-C5 | `three-mode.html:10-3352` 内联 CSS 85 个整块 | DEAD | 约 648 行 | 例：`.status-bar-tools`（注释自写"已挪走，保留选择器以防残留引用"）、`.materials-panel-header`、`.mat-role-badge*`、`.block-detail-*` | needs-check | 先抽成 `assets/three-mode.css` 再清死块；三模式逐屏截图 | P4 |
| FE-C7 | `workspace.css:2-5,29,45,56` + `styles.css:470-480` | DEAD（契约失效） | 约 10 行 | `workspace.js:84-92` 主动 `nav.innerHTML=''` + 隐藏；唯一输出 `a[data-mode]` 的 `renderWorkspaceChrome()` 已不产出链接；`mode-dispatch.js:98-103` 点击委托永不触发 | needs-check | ①保留容器、删 `a` 样式与点击委托；②等导航重做一起恢复（二选一，建议①） | P4 |
| FE-C9 | `chapter-studio.css:2,6` | DEAD | 5 行 | `.cs-insert/.cs-suggestion/.ct-steps` 无引用 | safe | 删 3 个类规则 | P4 |

### 3.11 前端测试与契约

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---|---|---|---|---|
| FE-F1 | `tests/` vs 路由 | TEST-CONTRACT（缺失） | 0 处 | `grep -rn "workbench\|project-init\|three-mode\|project-intake\|diff-review" tests/ --include=*.py` = 0：307 重定向零回归保护 | needs-check | 新增 `tests/routes/test_frontend_routes.py`：`/workbench`、`/project-init` 307 且 Location 正确；5 个页面 200 | P2 |
| FE-F2 | `tests/frontend/module-imports.test.mjs:111-117,141-147` | TEST-CONTRACT（错误/不完整） | 2 个数组 | ①数组仍含两个死页 → **删旧页的唯一测试阻塞**；②漏掉活页 `project-intake.html` 与 4 个含 `?v=` 相对导入的模块（`intake.js`、`chapter-studio.mjs`、`chapter-tree.mjs`、`workspace-model.mjs`），版本守卫有 5 个盲区 | safe（改测试） | 删旧页两项；补活页与 4 模块；故意改错一个版本号验证测试会红 | P2 |
| FE-F3 | `tests/frontend/workbench-modes.test.mjs` | TEST-CONTRACT（假覆盖） | 83 行 / 8 测试 | 8 个测试全部围绕产品零引用的 4 个函数 + `MODE_PANEL` | needs-check | 与 FE-B8 一起决策：删函数+测试，或把 `parseMode/panelVisible` 接进 `mode-dispatch.js` | P2 |
| FE-F4 | `tests/frontend/material-roles.test.mjs:72` | TEST-CONTRACT（陈旧注释） | 1 处 | 注释仍写"由 project-init.html 的 escapeHtmlForRole 转义"；旧页删除后失真 | safe | 注释改指 `mode-outline.js:7380` | P2 |
| FE-F5 | `frontend/diff-review.html` + `main.py:309-313` | LEGACY（孤儿页） | 331 行 | 路由存在，但全前端无任何链接指到 `/diff-review`（只命中文件自身与 `workspace.js:57` 注释）；功能（修订对比/接受 diff/导出）存在 | product-decision | ①在 `/review` 章节版本区补入口；②删路由 + 文件。不要长期留孤儿页 | P2 |
| FE-F6 | `docs/design/UI一致性审计.md:306,730` | INVALID-CONTRACT（文档失真） | 2 处 | 文档写"旧页保留重定向…`?legacy=1` 可绕过"，现实 307 已使页面不可达、legacy 无效；同文又写"真正下线需要删文件" | safe | 删页时同步更新状态；若保留回退期，应提供真实可达的 `/legacy/workbench` 而不是死文件 | P2 |

### 3.12 前端中期结构项（本轮不做，仅记录）

| ID | 位置 | 类别 | 规模 | 问题 | 处置 | 阶段 |
|---|---|---|---|---|---|---|
| FE-E1 | `frontend/assets/mode-outline.js` | JUNK（God module） | 7,725 行 / 430 KB；`openSplitReport()` 单函数 1,725 行 | 205 个顶层函数；事故记录：整段替换时误删 `bindScopeSelector` | 先删 FE-B1/B2 降低体量；中期拆 `split-report/`→`materials/`→`outline/`→`matching/` | P6 |
| FE-E2 | `frontend/assets/mode-editor.js` | JUNK（God module） | 2,848 行 / 60 函数 | 编辑器 + 流式生成 + 素材抽屉 + 评审入口混在一个模块 | 与 E1 同批拆分；先解 FE-D5 双渲染 | P6 |
| FE-E3/E4/E5 | `three-mode.html`（内联 CSS 3,342 行）、旧页内联 9,770 行、`styles.css` 2,727 行 | JUNK | — | 巨量内联/历史样式堆积（项目列表页/旧 workbench/旧 project-init 三代残骸） | E4 随旧页消失；E3 抽 `three-mode.css`；E5 拆 base/components/pages | P6 |
| FE-E6 | `.js` / `.mjs` 混用，无 `package.json` | JUNK（结构） | 10 `.js` + 8 `.mjs` | Node 侧靠 `.mjs` 扩展名与 Node 26 ESM 探测，`api-stream.test.mjs:14` 直接 import `.js` 属隐性依赖 | 统一 `.mjs` 或补 `package.json {"type":"module"}`；测试改显式契约 | P6 |
| FE-E7 | `?v=` 版本号机制 | JUNK（维护成本） | 68 处 / 18 文件 | 服务端已对 `/assets` 设 `no-cache`，版本号缓存作用弱化；但它是真实事故（模块一半新一半旧）的防线 | 保留并补全 FE-F2 盲区（低成本），或改构建期 hash；本轮不动 | P6 |

### 3.13 仓库遗留资产

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| AUX-G1/G2 | `ge-solution/B - 应标流程.html`(82 KB)、`sidebar.html`(22 KB) | LEGACY | 2,437 行 | 2026-09-15 `591a841` 有意归档（提交信息写"保留作设计决策追溯依据"）；全仓零代码引用；原型词汇是"应标/素材/大纲"，尚无"标包/评分点/工作台" | product-decision | 留档则移到 `docs/design/legacy/ge-solution-prototype/` 并写 README；否则随目录删除 | P5 |
| AUX-G3/G4 | `ge-solution/design-canvas.jsx`(49 KB)、`tweaks-panel.jsx`(25 KB) | JUNK | 1,534 行 | 通用 Anthropic artifact 工具壳（host bridge / EDITMODE 协议）；产品词 0 次；原型 HTML 不引用 | safe | 删除 | P1 |
| AUX-G5 | `ge-solution/uploads/pasted-*.png` ×5 | JUNK | 520 KB | 原型 HTML 不引用；文件名是 Claude 粘贴截图（2026-05-19~20）；内容只是早期大纲编辑界面 | safe | 删除 | P1 |
| AUX-K1 | `skill/`（18 目录 / 39 文件） | DEAD | 236 KB / 17 SKILL.md + 13 py | backend/frontend/tests/scripts/根 md 引用全为 0；SKILL.md 自指不存在的 `.claude/skill/...`；与 backend 能力平行（parser/retrieval/docx/agents）；提交信息自述"后端不引用本目录"；2026-09-16 还修过脚本 → 近期有人用 | product-decision | 二选一：①迁 `.claude/skills/<name>/` 并修正路径、去重；②移出产品仓库（独立 repo/wiki） | P5 |
| AUX-K2 | `skill/*/references/应标语言规范.md` ×5 | DUPLICATE | 5 份同 md5 | 五份完全相同，且与 `_shared_references/` 版本内容分叉 | safe（若保留 K1） | 收敛为单一共享副本 | P5 |
| AUX-D1 | `docs/screenshots/` 中 164 个未引用 png | JUNK | 26.46 MB | 172 张中仅 8 张被 3 份设计文档引用；代码引用 0；115 个提交逐个加图 | product-decision | 保留被引用 8 张；其余迁 wiki/对象存储或删除；后续定"只提交被引用截图"规则 | P5 |
| AUX-D2 | `docs/screenshots/` 5 对完全重复 | DUPLICATE | 10 文件 / 2.0 MB | md5 相同：`strip-simplified`=`strip-expanded`、`live-budget-confirmed`=`live-budget-height` 等；**`ia-no-extract-term.png` 是被引用的那份**，只能删其副本 `panel-head-clean.png` | safe | 只删每对未引用副本 | P5 |
| AUX-D3 | `docs/screenshots/live-kind-row.png` | JUNK | 183 KB | 未跟踪、零引用、未随任何提交入库 | safe | 删除（P0 已列） | P0 |
| AUX-M1 | `docs/design/mockups/prod-plan-budget.html` | DUPLICATE | 1.06 MB / 15,214 行 | 自身 0 引用；与 `prod-prototype.html` 去空白后仅 679 行差异（95%+ 相同） | product-decision | 删除或并入 `prod-prototype` 的变体说明 | P5 |
| AUX-M2 | `docs/design/mockups/` 其余 13 个 html | LEGACY | 310 KB | 逐个引用均为 0；是 10-13~10-17 的迭代过程稿（`five-stage-panel` v1/v2、三段式、stage-rail 三变体） | product-decision | 移入 `mockups/archive/` 或删除；保留 `prod-prototype.html`（唯一被引用） | P5 |
| AUX-N1 | `docs/design/*.md` 24 份 | LEGACY | 308 KB / 5,423 行 | 5 份被代码/文档反向引用；其余 19 份外部引用 0，其中"规划 v2/v3、三方案、诊断"已被实现取代 | product-decision | 保留被引用 5 份 + 实施记录；迭代规划稿移 `archive/` | P5 |
| AUX-N2 | `docs/reviews/2026-10-15-code-review-{plan,report}.md` | LEGACY | 13.7 KB | 外部引用 0；报告结论"发布门禁不通过（P1×6）"是 10-15 快照，P1-1/P1-6 已修复 | product-decision | 保留作审计痕迹，文首补"部分已关闭，勿据此判断当前门禁" | P5 |

### 3.14 配置与文档

| ID | 位置 | 类别 | 规模 | 判定依据 | 风险 | 处置 | 阶段 |
|---|---|---|---:|---|---|---|---|
| AUX-F1 | `railway.toml` + `render.yaml` | DEAD/DUPLICATE | 596 B | 均 2026-05-07 初始提交后再未改动；全仓 0 引用；`railway.toml:6` 的 startCommand 与 `Dockerfile:46 CMD` 相同，`/health` 三处重复声明；无 CI、无部署文档 | needs-check | 确认实际部署平台后只留一套（推荐只留 Dockerfile）；若确用 Railway/Render 删另一个 | P5 |
| AUX-F3 | `clear_data.sh` | DUPLICATE（表层） | 152 B | 只是 `exec python3 scripts/clear_data.py "$@"` 的薄包装，不是重复实现；全仓对它的引用 0 | safe | 保留（用户入口）或删除；**不要删 Python 实现** | P5 |
| AUX-F5 | `README.md` | LEGACY（部分过时） | 1,632 B | 架构段仍是 5 月 v1.0：写 5 agents（实际 8 个 agent 实现）、7 端点（实际 14 个路由模块）；启动说明有效 | needs-check | 更新架构/端点/agent 数；补"跑测试先 `pip install -r requirements-dev.txt`" | P5 |
| BE-X03 | `requirements.txt:26` `aiofiles==24.1.0` | DEAD-DEP | 1 行 | 全仓 0 引用（已改用同步 `open()`/`Path.write_bytes`） | safe | 删该行 | P1 |
| BE-X05 | `infra/llm/errors.py` test-only 公开 API | JUNK-API | 9 行 | `reset_health` 仅 conftest 用，`is_retryable`/`unhealthy_snapshot` 无调用；`__all__` 仍列为公开 API | safe | 删或标注 test-only | P1 |

### 3.15 安全与合规

| ID | 问题 | 证据 | 风险 | 处置 | 阶段 |
|---|---|---|---|---|---|
| AUX-SEC1 | 真实客户/招标信息在跟踪文件与本地数据中 | `docs/design/mockups/*.html` 出现"南方电网"850 次、"南网"94 次、"广东电网"7 次；`docs/tender/` 是真实标包 2 的评分点/技术要求拆解（被测试引用）；`backend/data/uploads/` 22 MB 真实投标 PDF（未跟踪但会被 Docker 打包）；remote 为 `github.com/shelby982/tech-solution-generator`，**可见性未核实** | 中高（合规/客户数据外泄） | ①确认 repo 是否私有；②决定 mockups/tender/截图是否需要脱敏或移出仓库（tender 被 `tests/infra/test_review_points.py:173` 引用，脱敏需同步改测试期望值）；③AUX-F2 修复后客户 PDF 不再进镜像 | P0（决策）+ P5（落地） |

---

## 4. 重点项详述

### 4.1 `.dockerignore`：改 10 行，消除 630MB build context 与客户数据泄露

现状：`.dockerignore`（387 B）排除了 `.venv/`、`tests/`、`.git/`、`.api_config.json`，但**没有**排除 `backend/data/`。Docker 只认 `.dockerignore`，不读 `.gitignore`；`Dockerfile:31` 是 `COPY backend/ ./backend/`。本地 `backend/data` 599 MB：`ge_solution.db` 180.9 MB + `.bak-20261013-1742` 397.7 MB + `.bak-...-1751` 22.2 MB + `uploads/` 22 MB 真实投标 PDF。

建议追加（按现有分组风格）：

```dockerignore
# 运行期本地数据（含客户上传与 SQLite，绝不入镜像）
backend/data/
*.db
*.db.bak*
backend/.tmp_uploads/

# 文档 / 归档 / 本地工具目录（镜像不需要）
docs/
skill/
ge-solution/
.planning/
.superpowers/
.claude/
.gstack/
.pytest_cache/
```

验证：`tar --exclude-from` 粗估或 `docker build` 观察 "transferring context" 应从约 630 MB 降到 <15 MB；镜像内 `ls /app/backend/data` 应不存在（`RUN mkdir -p /data` 保留）。

### 4.2 补 `jieba`/`rank_bm25`：不修则新镜像启动即崩

`infra/retrieval/keyword.py:6-7` 在模块顶层 `import jieba` / `from rank_bm25 import BM25Okapi`，调用链 `main.py → routes → orchestrator/graph → nodes → infra.retrieval.assign → keyword`。`requirements.txt` 从未声明这两个包，Dockerfile 只装 requirements。本地能跑是因为 `.venv` 里早先手工装过（jieba 0.42.1 / rank_bm25 0.2.2）。

建议：在"文档解析/检索"段补 `jieba==0.42.1`、`rank-bm25==0.2.2`，并跑一次干净 venv 的 `python -c "import main"`。**这一步必须先于所有清理工作**，否则"删完跑不起来"会分不清是清理引入还是原本就缺依赖。

### 4.3 修复 `infra.llm` 漏转出：恢复被静默吞掉的 LLM 标包合并

`routes/materials.py:643` 写 `from infra.llm import dispatch_package_merge_json`，但 `infra/llm/__init__.py` 的导入与 `__all__` 里都没有这个名字（函数定义在 `dispatcher.py:1096`）。该 import 位于 `try/except` 内，异常被吞成 `logger.info("模型整理不可用")`，于是 `/package-analysis?use_llm=1` 永远走规则降级，`merge_with_llm`/`validate_merge_proposal` 与 6 个测试对应的能力在产品中不可达。

建议在 `__init__.py` 的 `from .dispatcher import (...)` 中加入 `dispatch_package_merge_json`，并补进 `__all__`；验证 `PYTHONPATH=backend python -c "from infra.llm import dispatch_package_merge_json"`。若产品决定不做模型合并，则改走"删 `use_llm` 分支 + 相关服务与测试"的方案，**二选一，不要继续静默降级**。

### 4.4 删除两个前端旧整页（10,542 行，需产品确认）

**为什么不可达**：`main.py:301-307` 的 `/workbench` 与 `:316-322` 的 `/project-init` 都是 `RedirectResponse(307)`；前端目录只 `app.mount("/assets", ...)`（`main.py:256`），HTML 本身没有静态访问路径。页内自跳 `/three-mode` 的脚本永不执行，`?legacy=1` 逃生口也被后端 307 抢占。

**为什么是重复**：`workbench.html:2504-5224` 与 `mode-editor.js` 函数名 55/55 重合、非空行 94.4% 逐行一致；`project-init.html:1099-4959` 与 `mode-outline.js` 函数名 138/138 重合、94 个函数体逐字相同；两页内联 CSS 与 `three-mode.html` 选择器重合 427/429、327/327。它们是 2026-10-13 机械抽取前的历史快照，之后又落后了 5/67 个新函数。

**维护成本是真实的**：最近 4 个功能提交都同时改 5 个文件，但旧页差异 100% 是 `?v=`（0 行逻辑）；删页后版本号维护点从 68 降到约 53。

**执行顺序**：
1. 先改 `tests/frontend/module-imports.test.mjs` 两个数组（删两个死页、补 `project-intake.html` 与 4 个漏检模块），跑 `node --test` 确认仍 160 passed。
2. `git rm frontend/workbench.html frontend/project-init.html`。
3. `grep -rn "workbench.html\|project-init.html" frontend/ tests/ backend/` 只应剩历史注释。
4. 更新 `docs/design/UI一致性审计.md:730` 的"旧页文件仍在磁盘"条目。
5. `curl -I /workbench`、`curl -I /project-init` 验证 307；建议顺手补 FE-F1 路由测试。

**回退策略**：删除前打 tag（如 `pre-legacy-pages-removal`）；需要回退时 checkout 该 tag，而不是在工作区保留第二份实现。

### 4.5 后端旧上传管线与旧配置 API：整块删除

- `routes/upload.py`（292 行）：`POST /api/upload`、`GET /api/parse/stream/{id}`、`GET /api/upload/supported-formats` 在 2026-06 之后被 `POST /api/projects/{id}/materials` 取代；旧管线产出的 `sections` 没有消费方，也不写 `material_chunks`。它还带一个真实缺陷：`parse_stream` 每次连接覆盖 `job.event_queue`（`upload.py:178-191`），多客户端/断线重连会串事件。删除时同步删 `main.py:18/227`、`utils/validators.py`、`utils/sse.py` 的 upload-only 函数。
- `routes/config.py` 旧单配置 6 端点（151 行）：2026-05 的"一个 API Key"设计，与现在多配置 CRUD 的产品语义冲突；前端全部走 `/api/configs*`。删除时同步删 `config_store` 的 `get/save/clear/reorder/get_next_index/save_all`。
- `routes/changelog.py`（71 行）：无引用；且运行时依赖 `.git`，Docker 镜像里只会 503。

### 4.6 `domain` 三套 repository：先决策，不要"两套并存"

`domain/{spec,proposal,material}/repository.py` 合计 315 行，生产零引用，唯一消费者是 672 行测试；三个仓反向 import `services.block_store`（分层倒挂），注释写着"Phase 7 services 删除时切换"，但 Phase 7 未发生，`block_store` 继续膨胀到 1,323 行。

两条路必须选一条：
- **继续 Phase 7**：排期把 `block_store` 的 CRUD 接到三个仓上，然后删 block_store 里的重复 SQL；
- **放弃 Phase 7**：删三仓 + 三份测试 + 三个 `__init__.py` 转出，domain 只留 models 与纯函数，`block_store` 短期维持现状但禁止再膨胀。

继续"两套并存"是当前最坏状态：改一处要同步两处，测试保护的是没人用的实现。

### 4.7 旧匹配 rerank 与 doc_summary：要么删干净，要么开关化

- **rerank**：`nodes.py:563` 硬编码 `MATCH_MODE = "assign"`，`rerank`（`shen_kuo.py` 256 行 + `rerank.py` 127 行 + nodes 分支）只能改代码启用，注释称"保留用于回滚/对照"，`main.py:136` 每次 start 仍实例化 `ShenKuoAgent`。约 430 行实现 + 约 560 行测试。
- **doc_summary**：2026-09-17 起因推理模型 max_tokens 被思考耗尽而停用，`doc_summary` 恒为 `""`，但 `dispatch_doc_summary`/`_generate_doc_summary` 与 state→prompts 全链路仍保留（约 110 行，涉及 10+ 文件）。

建议：不再需要就一次性删干净（包括测试与 state 字段），不要留"半条链路"；需要保留就改成环境变量开关，并在 README 写清回滚 SOP，避免下一个人把它当死代码删掉。

### 4.8 前端 CSS：先删整块死规则，再合并重复声明

扫描结果：`styles.css` 177 个整块死规则（约 1,394 行，51%）、`review.css` 55 块（381 行，60%）、`response-outline.css` 26 块（63 行）、`workspace.css` 14 块（29 行）、`three-mode.html` 内联 85 块（648 行）。另有 55 条同文件重复声明与 40 个跨文件重复 class。

风险在于**没有自动化视觉回归**，所以：
1. 每批只动一个文件/一段；
2. 每批后对 5 个活页做截图对比：`/three-mode`（三模式各一屏）、`/projects`、`/project-intake`、`/review`、`/diff-review`；
3. 先删"整块死规则"，再合并"同选择器重复声明"，最后做跨文件收敛；
4. `three-mode.html` 的内联 CSS 先抽成 `assets/three-mode.css`（便于 diff 与回滚），再清死块。

### 4.9 前端重复：先做 MAX 的两处

1. **模型选择器/API 配置弹窗**（`projects.html:939-1058` ↔ `mode-outline.js:514-615`，约 120 行/边）：两处已漂移，抽 `assets/model-config.js`，接受"根节点 + api 适配器"参数，两页共用。
2. **转义函数**（16 份，80 行）：活代码统一 import `workspace-model.mjs:158` 的 `escapeHtml`；**先核对转义集差异**（有的不转单引号、有的不处理 null），统一语义后再替换，否则输入含 `'` 的属性会变。

### 4.10 仓库资产：截图是最大体积项，skill 是最大"定位未定"项

- 截图：172 张 / 27.86 MB，占跟踪字节 77.6%；仅 8 张被 3 份文档引用。建议保留 8 张 + 只删 5 对重复里未引用的副本，其余迁 wiki/对象存储或删除；**不要为数字好看删被引用的 8 张**。
- `skill/`：236 KB / 39 文件 / 17 个 skill，零运行时引用，且 SKILL.md 指向不存在的 `.claude/skill/`。它不是垃圾（2026-09-15 有意入库、9-16 还修过脚本），但当前形态既不可加载也不可维护 → 必须产品决策：迁 `.claude/skills/` 或移出仓库。
- 原型：15 个 mockup 只有 `prod-prototype.html` 有引用链；`prod-plan-budget.html` 与它 95%+ 相同。建议保留一份真原型，其余归档。
- `ge-solution/` 同理：两份 HTML 有追溯价值，两个 jsx 工具壳与 5 张粘贴图与产品无关，可删。

---

## 5. 执行路线图

| 阶段 | 内容 | 对应项 | 工作量 | 依赖 | 出口标准 |
|---|---|---|---|---|---|
| **P0 风险修复** | 补依赖、修转出、改 `.dockerignore`、清 gitlink、清本地垃圾、安全决策启动 | BE-X01/X02、AUX-F2/C1/F6、BE-J02、AUX-J1/J2/D3、AUX-SEC1（决策） | 0.5–1 天 | 无 | 干净 venv `import main` 通过；`from infra.llm import dispatch_package_merge_json` 通过；build context <15 MB；`git submodule status` 不再 fatal |
| **P1 零引用删除（后端为主）** | 删旧上传管线、changelog、旧配置 API、死端点、活文件死符号、未用 import、重复 SQL 收敛、`aiofiles` | BE-D01/02/03、U05–U10、U13/14/15/17/20、C01/C02/C06–C15/C17、L01/L03/L09、P02–P11、J01/J03/J04/J05、X03/X05、T05/T08 | 1.5–2 天 | P0 | `compileall` 通过；pytest 全绿（测试数按删除量下降并记录）；每个删除项对应 grep 归零 |
| **P2 旧页面/旧契约（需产品确认）** | 删两个旧整页；修测试契约；处理 410 端点、`requirement` 角色、孤儿 `/diff-review`、context-panel、测试-only API、domain 三仓决策 | FE-A1–A11、F1–F6、B8/B9/B10、BE-D04/05/06/D07、U11/12/16/18/19/21、L02/L08、T01–T04/T06/T07 | 1–1.5 天 | P1 + 产品拍板 | `node --test` 全绿（160 起，删测试后记录新基线）；`/workbench`、`/project-init` 307 有测试；前端行数 34,284 → 约 23,700 |
| **P3 重复收敛（后端 SQL + 前端工具）** | 后端逐字重复 SQL 收敛；前端模型弹窗抽取、转义函数统一、双树渲染归一、CSS 同文件重复声明合并 | BE-P02–P11、FE-D1–D5、FE-C6/C10 | 2–3 天 | P1/P2 | pytest + `node --test` 全绿；两页模型弹窗全流程手工回归；三模式截图无差异 |
| **P4 CSS 死规则清理（需视觉验收）** | styles/review/response-outline/workspace/three-mode 内联 CSS 分批删；导航死逻辑 | FE-C1–C5、C7、C9 | 2–3 天 | P2（旧页删除后） | 每批 5 页截图对比通过；目标文件行数下降（styles 2,727→约 1,300，review 628→约 250） |
| **P5 资产与产品决策项** | skill/、截图、mockups、docs/design、部署清单、README、ge-solution 留档、rerank/doc_summary/一次性脚本/启动回填 | AUX-K1/K2、AUX-D1/D2、AUX-M1/M2、AUX-N1/N2、AUX-F1/F3/F5、AUX-G1/G2、BE-L04–L07/L10/L11、T09/T10、AUX-SEC1 | 1–2 天 | 产品决策 | 跟踪字节 35.88 MB → 约 9 MB；跟踪文件 541 → 约 360；README 与现状一致 |
| **P6 中期结构重构（单独立项，本轮不做）** | God module 拆分、CSS 分层、`?v=` 机制、window 全局通道、schema 迁移版本、外键、`_runs` 回收、组合索引 | FE-E1–E7、D6；BE-L08 的另一半、2026-10-15 报告 P1-4/P2-4/P2-7/P2-8 | 各自立项 | P3/P4 | 不在本方案验收范围 |

**依赖关系**：P0 → P1 → P2（旧页测试契约先于删页）→ P3（重复收敛依赖旧实现已删，避免白改）→ P4（CSS 清理依赖旧页与结构稳定）；P5 的资产决策可与 P2–P4 并行，但**落地放在最后**，避免边删边改文档。

---

## 6. 验证与验收

### 6.1 每个阶段都要跑的通用门禁

```bash
cd /Users/jianghe/projects/ge-solution

# 1. 后端编译 + 测试
python3 -m compileall -q backend scripts tests
python3 -m pytest -q

# 2. 前端测试
node --test tests/frontend/*.test.mjs

# 3. 引用归零（按当阶段删除项替换 pattern）
grep -rn "<deleted-symbol>" backend frontend tests scripts

# 4. 工作区卫生
git diff --check
git status --short
```

### 6.2 分阶段专项验证

| 阶段 | 验证命令 / 动作 | 期望 |
|---|---|---|
| P0 | 干净 venv `pip install -r requirements.txt` 后 `PYTHONPATH=backend python -c "import main"` | 无 `ModuleNotFoundError` |
| P0 | `PYTHONPATH=backend python -c "from infra.llm import dispatch_package_merge_json"` | 通过 |
| P0 | Docker build context 模拟或 `docker build` 观察 transferring context | 约 630 MB → <15 MB |
| P0 | `git ls-files -s .claude`、`git submodule status` | 空 / 不再 fatal |
| P1 | 每删一个后端模块：`compileall` + 对应 `tests/` 子目录 | 全绿；`grep` 归零 |
| P1 | 删除旧上传/旧配置后：`grep -rn "upload_router\|config_store.get()\|sse_parse" backend` | 0 |
| P2 | 改完测试契约后先跑：`node --test tests/frontend/*.test.mjs` | 160 passed（未删测试前） |
| P2 | 删旧页后：`grep -rn "workbench.html\|project-init.html" frontend tests backend` | 只剩历史注释 |
| P2 | `curl -sI localhost:8000/workbench`、`/project-init` | 307 + 正确 Location |
| P2 | 新增 `pytest tests/routes/test_frontend_routes.py -q` | 通过 |
| P3 | 模型弹窗：两页各走一遍"添加模型 → 验证连接 → 保存 → 删除" | 行为一致 |
| P3 | 三模式：编辑器左栏树在流式拆分中不丢选中、不被覆盖 | 无回归 |
| P4 | 5 个活页逐屏截图对比（`/three-mode` 三模式、`/projects`、`/project-intake`、`/review`、`/diff-review`） | 视觉无差异 |
| P5 | `git ls-files \| wc -l`、跟踪字节统计、`du -sh docs/screenshots` | 541 → 约 360；35.88 MB → 约 9 MB |
| P5 | `grep -rn "railway\|render.yaml" . --exclude-dir=.git` | 0（若决定删除） |

### 6.3 验收标准

1. **P0 全部关闭**：无启动阻断依赖、无静默假死转出、build context <15 MB、无损坏 gitlink。
2. **零引用删除可证明**：每个删除项在提交信息里附"引用检查 = 0"的命令与结果。
3. **测试不倒退**：删测试导致的用例数下降必须显式记录（from → to），不允许"删测试让 CI 变绿"。
4. **旧入口契约有测试**：`/workbench`、`/project-init` 的 307 目标有 pytest 覆盖。
5. **回滚可控**：每个阶段一个 tag；产品决策项删除前打 tag；不重写 git 历史。
6. **明确不做**：P6 结构重构不混入本轮清理提交。

---

## 7. 风险与回滚

### 7.1 主要风险

| 风险 | 触发条件 | 缓解 |
|---|---|---|
| 误删"看起来死但活着"的代码 | 动态拼接路径、外部脚本、动态 import | 每个删除前做 §1.3 的引用检查；三份审计的 §3"排除项"清单逐条对照；新增项进 §8 |
| CSS 删错导致视觉回归 | 无自动化视觉回归 | 每批只动一个文件 + 5 页截图对比；先删整块死规则再合并重复声明 |
| 删旧页后产品需要回退 | 产品临时要求对比旧版 | 删除前打 tag；不保留双份实现 |
| 测试数下降被误读为质量下降 | 随死代码删测试 | 提交信息记录 from → to，并说明"删除的是死代码测试" |
| 依赖/转出修复引入行为变化 | X01/X02 修复后原本降级的路径恢复 | 分别独立提交；X02 修复后跑 `/package-analysis?use_llm=1` 冒烟 |
| 客户数据/公开仓库合规 | repo 可见性未核实 | AUX-SEC1 先决策；`.dockerignore` 先修；必要时脱敏 `docs/tender` 并同步测试期望 |

### 7.2 回滚策略

- 每个阶段开始前打 tag：`pre-cleanup-p0`、`pre-cleanup-p1`……；单个提交保持单一主题，出问题 `git revert <commit>` 即可。
- 删除文件用 `git rm`（保留历史），不用 `rm` 后提交。
- 数据清理（`backend/data/*.bak`）先复制到仓外目录再删；数据库不做自动删除。
- 若旧页删除后必须恢复：`git checkout pre-cleanup-p2 -- frontend/workbench.html frontend/project-init.html`，并同步测试数组。

### 7.3 关于 git 历史重写

删 HEAD 截图只回收工作区约 26.46 MB，`.git` 的 36.23 MiB pack 不变。若目标包含"缩小 clone 体积"，需要 `git filter-repo --path docs/screenshots --invert-paths` 等历史重写：**会改写全部 commit hash、影响所有协作者与 open PR（本分支领先 origin 249 个提交）**。默认建议：**本轮不做**；先通过 `.gitignore`/入库流程阻止继续增长，等分支合并后单独评估。

---

## 8. 明确保留 / 不要动（防误删清单）

以下项"看起来像死代码/垃圾"，但审计已核实有真实引用或价值：

| 项 | 为什么不能删 |
|---|---|
| `frontend/assets/generate-session.js` | 只被旧页 `<script>` 直接引用，但 `mode-editor.js:5` import 并实例化 → 三模式编辑器生成链路活 |
| `frontend/assets/context-panel.js` | 模块图上是活的（`mode-editor.js:8,25`），但运行时 no-op → 处理方式见 FE-B10，**不能当死文件直接删** |
| `frontend/assets/workspace-model.mjs` | 4 个活模块 + 大量测试引用 |
| `frontend/assets/workbench.js` / `workbench-modes.mjs` | 名字像旧页，实际被 `three-mode.html:4056-4057` 加载，活 |
| `workspace.js:39-41 workspaceUrl()` | 注释提到旧 `/workbench`，实际输出 `/three-mode`，是已修正的生成器 |
| `#ced-mode-switch` 的 `data-mode="edit\|preview"` | 编辑器内部编辑/预览切换，不是页面三模式导航 |
| `mode-dispatch.js` 的 `setMode` | 去掉 `export` 即可，函数本身被 `init()` 的 popstate 使用 |
| `tests/frontend/panel-helpers.test.mjs` | 有意的事故守卫（整段替换误删函数），与 FE-B1/B2 无交集 |
| `docs/superpowers/`（15 份 plan/spec） | 被 `frontend/project-init.html:2159`、`mode-outline.js:1310`、`dispatcher.py:691`、`domain/material/models.py:89`、`domain/spec/response_items.py:4` 直接引用 |
| `docs/tender/`（3 份 md） | `tests/infra/test_review_points.py:173` 的测试期望值来自它 |
| `docs/design/mockups/prod-prototype.html` | 唯一有引用链的"真原型"（`原型到代码-落点对照.md:3`、`test_page_budget.py:3`） |
| `scripts/clear_data.py` | `clear_data.sh` 只是它的薄包装；Python 实现是唯一实现 |
| `scripts/build_response_templates.py` | 被 `docs/design/Word交付格式与导出链路.md:33` 引用，是模板重建入口 |
| `requirements-dev.txt` | 是 `-r requirements.txt` + pytest 的增量分层，无版本冲突；Docker 不装它 |
| `start.sh` | README 与 superpowers plan 引用的本地开发入口；Docker 不用它 |
| `.api_config.json`（未跟踪） | 含本地 Key，已被 `.gitignore`/`.dockerignore` 双重排除，**不要入库** |
| `backend/infra/llm/_mock.py` | `LLM_MODE=mock` 测试/演示模式，`clients.py` 6 处短路 |
| `backend/infra/parser/_ocr.py` | 扫描件 OCR 兜底路径，`docx.py`/`pdf.py` 在用 |
| `backend/services/scope_backfill.py`、`run_recovery.py`、`point_sync.py`、`material_integrity.py`、`package_scope.py`、`response_drafts.py` | 名字像迁移脚本，实际都在 routes/main 有真实调用 |
| `backend/domain/review/repository.py` | 类本身活（`routes/review.py`、`nodes.py:1047`），只删 3 个死方法 |
| `backend/agents/subpoints.py`、`chapter_team.py` | 生产活（`zhang_heng.py:676`、`services/chapter_writing.py:9`） |
| `backend/routes/revisions.py`、`export.py` 等 | 端点有产品调用，个别方法死不影响路由本身 |
| `backend/scripts/*.py` 的 `sys.path.insert` | "从任意 cwd 直接跑"的必要自举，不是遗留 |
| 本地 `.planning/` | 未跟踪、含决策历史，建议保留而非删除 |
| 本地分支 `worktree-security-hardening` | 未并入 main/feature，可能含未回收的安全改造；删 gitlink 时**不要**删这个分支 |

---

## 9. 待决策清单（需要产品/负责人拍板）

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | 两个前端旧整页是否删除 | ①立即删；②新写真实可达的 `/legacy/workbench` 回退路由；③原样保留 | ①。307 已让旧页不可达，保留只增加每次发版的版本号维护 |
| D2 | `/diff-review` 孤儿页去留 | ①在 `/review` 补入口；②删路由+文件 | 先补入口（功能像是有用的），若确认无人用再删 |
| D3 | domain 三仓（Phase 7） | ①继续接线；②删仓+测试 | 二选一尽快定；在定之前**禁止两套并存**继续膨胀 |
| D4 | rerank 旧匹配管线 | ①删；②环境变量开关 + README SOP | 若无回滚需求→删；若保留→开关化，不要硬编码常量 |
| D5 | doc_summary | ①恢复（换非推理模型/给足 max_tokens）；②删干净 | 二选一；不要留恒空字段与半条链路 |
| D6 | `skill/` 定位 | ①迁 `.claude/skills/`；②移出产品仓库 | 先确认是否还有 agent 工作流依赖；9-16 刚修过脚本，建议先问 owner |
| D7 | 截图留存策略 | ①保留 8 张被引用 + 规则；②全部移 wiki/对象存储；③Git LFS | ①（成本最低）；26.46 MB 未引用截图不再增量入库 |
| D8 | mockups 是否长期留档 | ①保留 prod-prototype + 其余 archive；②全部保留；③全删 | ①；`prod-plan-budget` 与主原型 95% 重复，没必要留两份 |
| D9 | 部署平台 | ①只留 Dockerfile；②留 Railway；③留 Render | 先确认实际平台；仓库里三套启动声明并存是漂移风险 |
| D10 | `api.js` 11 个未调用方法与后端契约 | ①前后端同批删；②保留并标"外部集成" | 与 D11 联动；删之前确认无外部调用方 |
| D11 | 哪些死端点可能被外部脚本/集成使用 | U11/U12/U16/U18/U19/U21 | 逐个确认；无外部依赖才删 |
| D12 | `requirement` 角色兼容期 | ①做 UPDATE 迁移后删；②保留到下个大版本 | 当前库 0 行 → 迁移后删 |
| D13 | git 历史是否重写缩容 | ①不做；②分支合并后做 filter-repo | 本轮不做；先阻止未来增长 |
| D14 | repo 可见性与客户数据脱敏 | ①确认私有；②脱敏 mockups/tender/截图 | 优先确认；`docs/tender` 被测试引用，脱敏要同步改期望值 |

---

## 附录 A：关键基线命令（可复现）

```bash
cd /Users/jianghe/projects/ge-solution

# 体量
git ls-files | wc -l                                  # 541
git count-objects -vH                                 # size-pack 36.23 MiB, commits 447
du -sh .git backend/data docs/screenshots              # 47M / 599M / 28M

# 代码量
wc -l frontend/*.html frontend/assets/* | tail -1      # 34,284
find backend -name '*.py' | xargs wc -l | tail -1      # 28,244
find tests -name '*.py' | xargs wc -l | tail -1        # 18,905

# 测试基线
node --test tests/frontend/*.test.mjs                  # 160 passed（2026-10-19 实测）
# 后端 pytest：795 passed / 1 deselected（2026-10-15 记录，本轮未重跑）

# 死页不可达证据
grep -n "@app.get\|app.mount" backend/main.py          # /workbench、/project-init 均 RedirectResponse
curl -sI http://127.0.0.1:8000/workbench | head -3     # 期望 307

# 依赖声明缺口
grep -n "jieba\|rank.bm25" requirements.txt              # 当前 0 命中
grep -n "dispatch_package_merge_json" backend/infra/llm/__init__.py  # 当前 0 命中

# 引用归零模板
grep -rn "<符号或文件名>" backend frontend tests scripts

# 只读数据核对（不得写库）
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:backend/data/ge_solution.db?mode=ro', uri=True)
print(c.execute("select role,count(*) from materials group by role").fetchall())   # main_rfp 4 / spec 4
print(c.execute("select count(*) from workflow_runs where package_id is null").fetchone())  # 41/50
print(c.execute("select count(*) from project_snapshots").fetchone())              # 0
print(c.execute("select count(*) from materials where role='requirement'").fetchone())  # 0
PY
```

## 附录 B：审计证据索引

| 报告 | 内容 |
|---|---|
| `docs/reviews/_audit/frontend.md` | 52 项前端发现；旧页/死函数/重复/CSS/测试契约逐项证据；16 条排除项 |
| `docs/reviews/_audit/backend.md` | 86 项后端发现；死模块/死端点/死符号/重复 SQL/遗留链路/测试；与 2026-10-15 报告逐条对照 |
| `docs/reviews/_audit/aux.md` | 23 项仓库资产发现；`.dockerignore`/截图/skill/mockups/部署配置/本地垃圾；16 条排除项 |
| `docs/reviews/2026-10-15-code-review-report.md` | 上一轮全面审查（缺陷类），本方案与其互补：它管"是否坏"，本方案管"是否多余" |
