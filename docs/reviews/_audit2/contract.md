# 跨层一致性审计

- 仓库：`/Users/jianghe/projects/ge-solution`　分支 `feature/multi-agent-bid-system`　HEAD `a91b1e9`
- 审计时间：2026-11-09　类型：只读（未修改任何项目文件，未启动服务，未写数据库）
- 工作区注意：`git status` 实际有 **4 个**未提交后端文件，不止任务说明的 3 个——
  `backend/db.py`（+11 行，新增 `block_purged_items` 表）、`backend/routes/chapter_studio.py`、
  `backend/services/block_store.py`、`backend/services/placeholder_purge.py`。本报告一律以工作区为准。
- 并发变更提示：审计进行中（01:25）**另有他方**改动了 `tests/routes/test_chapter_studio.py`
  （`git diff` 显示把 `assert '待补充' in …` 反转为 `not in`，与 `placeholder_purge` 占位符清理配套）。
  该文件不在审计员改动范围内，审计员全程只读、只新增本报告；此变更使工作区「未提交文件」由 4 个变为 5 个。
- 端点基线：`backend/routes/*.py` 全部 `@router.*` 共 **250** 个（GET 113 / POST 108 / PUT 17 / DELETE 9 / PATCH 3），
  23 个路由模块全部在 `main.py:243-265` 以 `prefix="/api"` 挂载，另有 `citations.py`（自带
  `prefix="/projects/{project_id}/citations"`）与 `review.py`（自带 `prefix="/review"`）两层前缀。
- 前端调用点基线：`frontend/**/*.{html,js,mjs}` 内 `/api/` 字面量 349 处，归一化后 **193** 条不同路径。

---

## 0 摘要

| 类别 | 数量 | 说明 |
|---|---|---|
| 1a 端点无任何前端/测试调用方 | **36** / 250 | 含整块 legacy 面：`/api/config`(5)、`upload.py`(3) |
| 1b 前端调用后端不存在 / 已废弃 | **2** | 1 条路径完全不存在，1 条已 410 Gone 仍在被调用 |
| 1b HTTP 方法不符 | **0** | 19 个初筛候选逐一读源核实，全部为解析器误报（见 §1.c） |
| 2 DB schema ↔ 代码错位 | **0** | 表级/列级 INSERT 校验均 0 错；但 **13** 张表「有写路径、生产库 0 行」 |
| 3 测试 ↔ 现状错位 | **3** | 2 处固化了已失效契约，1 处保护死分支 |
| 4 文档 ↔ 代码矛盾 | **10** | README 4 条 + 设计/评审文档 API 表 6 处 |
| 5 硬编码开关 / 不可达分支 | **4** | `MATCH_MODE`、review tier、`/config/providers`、tier `skip` |
| 6 新旧实现漂移 | **6** | 死页面 vs 抽取模块、config 单复数、三代 todo、双评审表等 |
| 7 追踪文件 ↔ 未跟踪依赖 | **5** | `frontend/bench.html` 依赖未跟踪 `scripts/bench_server.py` |

### 最高危 8 条

| # | 位置 | 一句话理由 |
|---|---|---|
| 1 | `frontend/assets/mode-editor.js:2765` → `backend/routes/materials.py:674-686` | 三模式编辑器「准备 / 重新拆分评分点」按钮（`#btn-start-extract`，`mode-editor.js:2292`）POST 的 `/api/projects/{id}/outline` **已标 `[DEPRECATED]` 并 `raise HTTPException(410)`**；`resp.ok` 为假即抛「准备请求失败」/`generate-all` 分支，提炼流永远起不来 |
| 2 | `frontend/assets/generate-session.js:27` → 无对应路由 | 「批量编写」兜底路径 POST `/api/projects/{id}/generate-all`，**250 个端点中不存在该路径**（`grep -rn "generate-all" backend` 零命中），必然 404；该类在 `mode-editor.js:554-555` 被真实实例化 |
| 3 | `frontend/project-init.html:4666` + `:1470` + `:4694` → `backend/main.py:337-343` | 页面跳 `/workbench?projectId=&threadId=&backend=generating`，而 `/workbench` 现为 **307 重定向**到 `/project-intake?projectId=`，**丢弃 `threadId` 与 `backend=generating`**；确认大纲后用户被打回准备页 |
| 4 | `backend/routes/config.py:166` + `frontend/assets/mode-outline.js:595-600` | `GET /api/config/providers`（后端权威 `DEFAULT_MODELS`）**零调用方**，4 个前端各自硬编码 provider 表，且值已分叉：`deepseek-v4-pro`（mode-outline/workbench/project-init）vs `deepseek-chat`（projects.html + 后端 `config_store.py:82`）；claude 默认 `claude-3-5-sonnet-20241022`（后端）vs `claude-sonnet-4-6`（全部前端） |
| 5 | `backend/orchestrator/nodes.py:563` / `:648` / `:655` | `MATCH_MODE = "assign"` 为模块级字面量，**无任何 env/配置开关**；`:655` 起的旧 rerank 路径（40 行，含 `agent.match` 全部调用）在生产**永不可达**，仅靠 `tests/orchestrator/test_nodes.py:231` monkeypatch 维持 |
| 6 | `backend/orchestrator/runner.py:1285` + `backend/utils/sse.py:43` | `done` 事件向客户端下发 `download_url=/api/workflow/{tid}/download` 与 `/api/download/{task_id}`，**两个路由都不存在**（`grep "@router.*download\|@app.*download" backend` 零命中）；唯一「消费方」是 `tests/orchestrator/test_events.py:151` 的字符串断言 |
| 7 | `backend/services/chapter_review.py:140` / `:215` / `:318` | 同一次评审**同时写 `chapter_reviews` 与 `reviews` 两代表**（实测 `chapter_reviews` 20 行 / `reviews` 44 行），读路径分散在 `domain/review/repository.py:33` 与 `chapter_review.py:267`，双写无事务一致性保障 |
| 8 | `backend/routes/upload.py:100/150/285` + `backend/routes/config.py:103-166` | **8 个端点零调用方**（整个 upload 路由 3 个 + 单数 config 5 个）；上传实际走 `POST /api/projects/{id}/materials`（`materials.py:450` multipart），配置实际走 `/api/configs`（复数）——两代面并存，tracked 代码里留死路由 |

---

## 1 端点 ↔ 前端调用矩阵

### 方法说明

端点全集由脚本从 `@router.<method>(` 提取（含跨行写法），前缀按 `main.py:243-265` 的 `prefix="/api"` 与两处 router 自带 prefix 拼接。
前端路径经三步归一化后与端点比对：
① 剥 `${...}`（**花括号配平**，避免 `?` 在 ternary 内被误当 query 分隔符）；
② 剥 `?query` / `#hash`；
③ 末段 `{}` 且前一字符非 `/` 视作 `${x?'?a=b':''}` 追加物丢弃，再对末段动态（`/studio/${role?'diagram':'table'}`）做同深度父路径匹配。

方法判定：从 URL 字面量结尾起，用括号配平扫描到 `fetch(...)` 闭合括号，只在该 span 内找 `method:`；
`send('POST',…)` / `post(…)` / `workflowAction(…)` / `href=` / `window.open` 分别按包装器语义处理。

### 1.a 端点无任何前端/测试调用方（36 条）

验证方法：对每个端点取其最具辨识度的两段路径，`grep -rl` 全 `frontend/` 与 `tests/`；
命中 0 即判定无调用方（下表全部为命中 0，仅动态模板串场景另行读源排除）。

| 方法 | 端点 | 定义位置 |
|---|---|---|
| `GET` | `/api/blocks/{block_id}/material-search` | `blocks.py:90` |
| `GET` | `/api/changelog` | `changelog.py:65` |
| `POST` | `/api/blocks/{block_id}/studio/rewrite-selection` | `chapter_sse.py:267` |
| `POST` | `/api/blocks/{block_id}/studio/outline/self-heal` | `chapter_studio.py:370` |
| `GET` | `/api/blocks/{block_id}/section-results` | `chapter_studio.py:516` |
| `POST` | `/api/blocks/{block_id}/studio/align-outline` | `chapter_studio.py:713` |
| `POST` | `/api/blocks/{block_id}/studio/section` | `chapter_studio.py:739` |
| `GET` | `/api/projects/{project_id}/todo` | `chapter_studio.py:1190` |
| `GET` | `/api/projects/{project_id}/writing-plans/{plan_id}` | `chapter_writing.py:77` |
| `POST` | `/api/projects/{project_id}/writing-plans/{plan_id}/confirm-chapter` | `chapter_writing.py:168` |
| `PUT` | `/api/projects/{project_id}/writing-plans/{plan_id}/figures-plan` | `chapter_writing.py:179` |
| `POST` | `/api/projects/{project_id}/writing-plans/{plan_id}/confirm` | `chapter_writing.py:220` |
| `POST` | `/api/projects/{project_id}/citations/{citation_id}/link` | `citations.py:130` |
| `DELETE` | `/api/projects/{project_id}/citations/{citation_id}/link/{block_id}` | `citations.py:140` |
| `GET` | `/api/projects/{project_id}/citations/search` | `citations.py:194` |
| `POST` | `/api/projects/{project_id}/citations/{citation_id}/reject` | `citations.py:225` |
| `POST` | `/api/config` | `config.py:103` |
| `GET` | `/api/config` | `config.py:130` |
| `GET` | `/api/config/check` | `config.py:139` |
| `DELETE` | `/api/config` | `config.py:158` |
| `GET` | `/api/config/providers` | `config.py:166` |
| `POST` | `/api/studio/diagrams/{asset_id}/regenerate` | `diagram_assets.py:221` |
| `POST` | `/api/projects/{project_id}/materials/{material_id}/recut` | `material_library.py:554` |
| `POST` | `/api/projects/{project_id}/materials/recut-all` | `material_library.py:568` |
| `POST` | `/api/blocks/{block_id}/citations/swap` | `material_library.py:701` |
| `GET` | `/api/projects/{project_id}/materials/needs` | `material_library.py:930` |
| `PATCH` | `/api/projects/{project_id}/packages/{package_id}` | `packages.py:70` |
| `GET` | `/api/projects/{project_id}/basis` | `projects.py:240` |
| `GET` | `/api/projects/{project_id}/studio/review/issues/summary` | `review_batch.py:53` |
| `GET` | `/api/projects/{project_id}/todo/chapter` | `todo_v3.py:54` |
| `POST` | `/api/blocks/{block_id}/delivery-gate/auto` | `todo_v3.py:167` |
| `POST` | `/api/blocks/{block_id}/delivery-gate/fix` | `todo_v3.py:203` |
| `POST` | `/api/upload` | `upload.py:100` |
| `GET` | `/api/parse/stream/{job_id}` | `upload.py:150` |
| `GET` | `/api/upload/supported-formats` | `upload.py:285` |
| `POST` | `/api/workflow/{thread_id}/analyze-points` | `workflow.py:361` |

**可能的外部集成 / 有意保留（非缺陷）**：

| 端点 | 判断依据 |
|---|---|
| `GET /api/changelog` | 语义为对外版本记录；仓内无任何页面渲染。若 `docs` 或外部站点消费，需显式声明。 |
| `GET /api/projects/{id}/basis` | 返回应答依据汇总，前端已有等价 `/match-overview`（`api.js:125`）。属被取代而非外部集成。 |

**已排除的误报（3 条，读源确认确有调用方）**：

- `POST /api/blocks/{id}/studio/coverage-review` — `tests/test_coverage_review.py:72` 调用。
- `GET /api/blocks/{id}/materials/evidence` — `tests/test_material_library_api.py:57` 调用。
- `DELETE /api/blocks/{id}/studio/materials/{material_id}` — `chapter-studio.mjs:5922` 用
  `${material.dataset.material==='unlink'?'/'+m.id:''}` 动态拼出，字面量归一化后与 PUT 同形，故初筛误判。
- 另注：`POST /api/blocks/{id}/studio/section`（`chapter_studio.py:739`）**保留在死清单**——
  前端只调 `GET .../studio/section/stream`（`chapter-studio.mjs:3772`），是不同端点。
  `GET /api/projects/{id}/materials/needs` 同理：前端只调 block 级 `materials/needs`（`chapter-studio.mjs:764`）。

### 1.b 前端调用但后端不存在 / 方法不符 / 参数契约不符（2 条真问题）

| # | 前端位置 | 调用 | 后端实况 | 后果 |
|---|---|---|---|---|
| 1 | `frontend/assets/generate-session.js:27` | `POST /api/projects/${this.projectId}/generate-all` | **不存在**。`grep -rn "generate-all\|generate_all" backend` → 0 命中；250 端点清单无此项 | `resp.ok===false` → `_running=false` 直接 return，用户点「批量编写」无任何反馈。注入点真实：`mode-editor.js:554-555` `session = new GenerateSession(projectId); session.start()` |
| 2 | `frontend/assets/mode-editor.js:2765`（及 `workbench.html:5148`） | `POST /api/projects/${projectId}/outline?model=…` | 存在但**已废弃**：`materials.py:674-686` 文档串 `[DEPRECATED]`，函数体直接 `raise HTTPException(410, "该接口已废弃，请改用 /api/workflow/start")` | 410 无 body → 抛「准备请求失败」；`autoExtract=1`（`mode-editor.js:2270-2273`）与「准备」按钮（`:2289-2294`）两条入口均命中 |

证据（原文）：

```
backend/routes/materials.py:674  @router.post("/projects/{project_id}/outline")
backend/routes/materials.py:675  async def generate_outline(project_id: int, model: str | None = None):
backend/routes/materials.py:676      """[DEPRECATED] 老 demo 路径，已被 LangGraph workflow 取代。
...
backend/routes/materials.py:681      raise HTTPException(
backend/routes/materials.py:682          status_code=410,
```

附带死代码：`frontend/assets/api.js:136-163` 的 `materials.outline()` 是专为该 410 端点写的 50 行 SSE 解析器，
`grep -rn "materials.outline" frontend` → 0 调用方。

### 1.c HTTP 方法核对结论：0 处不符

初筛得到 19 个「方法不符」候选，逐条读源后**全部为解析器误报**，典型三类：

| 误报原因 | 实例 | 实际方法 |
|---|---|---|
| 包装器 `send(method, body)`（`chapter-studio.mjs:12`） | `chapter-studio.mjs:2941` `json(url, send('PUT',{…}))` | PUT ✓ 对 `blocks.py:197` |
| 包装器 `post(url, body)`（`chapter-studio.mjs:1811`） | `chapter-studio.mjs:1846` `post(/citations/fetch,…)` | POST ✓ 对 `citations.py:203` |
| URL 与 `fetch` 分行、方法在下一行 | `mode-editor.js:2765-2766`，`workbench.html:5148-5149` | POST ✓ 对 `materials.py:674` |
| ternary 双方法 | `chapter-studio.mjs:5922`：unlink→`{method:'DELETE'}`，否则 `send('PUT')` | DELETE 对 `chapter_studio.py:227`；PUT 对 `:207` ✓ |

结论：**方法层面跨层一致**，无缺陷可报。

---

## 2 DB schema ↔ 代码错位

### 2.1 校验结果：0 处缺表 / 缺列

- 代码侧表引用（`FROM|JOIN|INTO|UPDATE <t>`）全集 vs schema 全集：**0 个未知表**。
  初筛 6 个「未知表」全是 SQL 关键字/别名误报：`set`（`ON CONFLICT DO UPDATE SET`）、
  `of`（`UPDATE OR …`）、`json_each`、`sqlite_master`、`title`、`source`。
- `INSERT INTO t (cols)` 列清单 vs 实库 `PRAGMA table_info`：**0 处不一致**（含 `INSERT OR REPLACE`）。
- 初筛 5 处「列不存在」经查全部由**循环式 ALTER** 补齐，`db.py` 确实建了这些列：

| 表 | 初筛缺失列 | 实际补齐位置 |
|---|---|---|
| `reviews` | `criterion_text/satisfied/covered_points/missing_points` | `db.py:945-947` 循环 `ALTER TABLE reviews ADD COLUMN` |
| `chapter_reviews` | `content_version/evidence_version` | `db.py:681-684` 循环 ALTER |
| `llm_usage` | `block_id/section/role` | `db.py:759-761` 循环 ALTER |

- schema 定义但零代码引用的表：**0**（47 张 CREATE TABLE + 1 张内联 `project_writing_facts`（`db.py:675`）全部被引用）。

### 2.2 实库表清单与行数（只读 `?mode=ro`）

```
sqlite3 connect('file:backend/data/ge_solution.db?mode=ro', uri=True)
→ 49 张业务表
```
实库 49 = schema 48 + LangGraph saver 自建 **2** 张（`checkpoints` 164 行、`writes` 360 行）− 待迁移 1 张。
`checkpoints`/`writes` 不在 `db.py` 是**有意为之且有防护**，非缺陷：
`services/run_recovery.py:44-51` 先查 `sqlite_master` 再跳过；`routes/projects.py:96-101` 对每条
DELETE 逐句 `try/except` 并 `logger.warning`，删不到只记 `-1`。该设计一致，**不计入错位**。

行数一览（仅列判断所需）：`blocks` 1734、`spec_requirements` 3126、`material_chunks` 1446、
`llm_usage` 1310、`writes` 360、`review_issue_states` 305、`block_revisions` 178、`checkpoints` 164、
`projects` 5、`workflow_runs` 50、`reviews` 44、`chapter_reviews` 20、`citation_chunks` 66。

### 2.3 「有写路径、生产库 0 行」的 13 张表（功能未被执行）

| 表 | 行数 | 写入点佐证 |
|---|---|---|
| `chapter_materials` | 0 | 触发器维护 + `chapter_studio` 绑定路径 |
| `chapter_material_chunks` | 0 | `chapter_material_chunks.chunk_kind/requirement_id` ALTER 存在（`db.py:752-756`）仍有列无数据 |
| `citations` | 0 | 建表 `db.py:59`，还有 `segments/selected` ALTER（`db.py:795-798`） |
| `citation_links` | 0 | 建表 + 代码写，无行 |
| `citation_searches` | 0 | 建表 + 代码写，无行 |
| `diagram_settings` | 0 | `diagram_assets.py:34/43` GET/PUT 均有 |
| `evidence_need_actions` | 0 | 对应 `materials/needs/action`（`material_library.py:210`，前端有调用）却 0 行 |
| `leads` | 0 | `landing.html:818` POST `/api/leads` 有调用；`leads.py:52` 有写 |
| `material_images` | 0 | `materials/{id}/images`（`material_library.py:722`）有读 |
| `need_snippets` | 0 | 建表 + 代码写 |
| `project_snapshots` | 0 | 建表 + `reset-analysis` 显式 DELETE（`projects.py:88`） |
| `project_writing_facts` | 0 | `db.py:675` 建表；`chapter_studio.py:1500/1507` GET/PUT facts |
| `writing_model_settings` | 0 | 建表 `db.py:97` + `writing_settings.py` 4 端点 |

判读：这些属**功能已接线但从未在生产路径跑通**，不是 schema 错误。其中 `evidence_need_actions`、
`project_writing_facts`、`writing_model_settings` 风险最高——前端已有点击入口（见 §5），点击即写空表，
说明对应业务分支实际未被验证。注意口径：这是**单个开发库**样本，不等于线上从未使用。

### 2.4 待迁移表（预期内）

`block_purged_items`（`db.py:305-313`，工作区新增）**尚未出现在实库** 49 张表中，
因 `init_db` 只在启动时 `CREATE TABLE IF NOT EXISTS`。进程未重启时
`placeholder_purge.py` 对该表的读写会 `no such table`——若该服务以热重载方式运行，需确认已重启。

---

## 3 测试 ↔ 现状错位

### 3.1 保护已废弃实现（死分支）的用例

| 位置 | 问题 | 证据 |
|---|---|---|
| `tests/orchestrator/test_nodes.py:226-231` | docstring 明写「旧路径（`MATCH_MODE="rerank"`）」，靠 `monkeypatch.setattr(nodes, "MATCH_MODE", "rerank")` 才可达。生产代码 `nodes.py:563` 是字面量 `MATCH_MODE = "assign"`，无 env 开关 → 该用例保护的是**生产永不可执行**的 40 行分支 | `sed -n '226,231p' tests/orchestrator/test_nodes.py` |

### 3.2 断言已失效契约的用例

| 位置 | 问题 | 证据 |
|---|---|---|
| `tests/orchestrator/test_events.py:149-151` | `events.done("/api/download/tid-1")` 并断言 `data == {"download_url": "/api/download/tid-1"}`。`/api/download/{task_id}` **无对应路由**，测试把一个 404 地址固化为契约 | `grep -rn "@router.*download" backend` → 0 命中 |
| `tests/orchestrator/test_events.py:149` 同源 | `backend/utils/sse.py:43` 的 `sse_all_done` 也拼 `/api/download/{task_id}`，同样无人服务；且 `sse_all_done` 本身在 backend 内**无调用点** | 见 §0 #6、§6.6 |

### 3.3 逐条排除的误报（读源确认不是缺陷）

- `tests/routes/test_chapter_workspace_jobs.py:64` POST
  `/api/projects/{pid}/studio/chapter-writes/retry-chart/retry-chart`：看似重复段，实为
  `job_id="retry-chart"`（`:56` 的 INSERT 就用该 id）+ 动作 `/retry-chart`，路由合法，
  断言 `status_code == 200` 正确。
- `tests/routes/test_workflow.py`、`test_packages.py`、`test_blocks.py` 等大量 `/api/...` 字面量
  含具体 id（`tid-1`/`missing`/`busy`/`{block['id']}`），属 f-string 字典下标，
  非路径不匹配（归一化后全部命中）。
- 测试对 36 条死端点的覆盖：`test_coverage_review.py` 等**只覆盖其中 2 条**，其余 34 条前后端皆无测试，
  §1.a 的规模不是「测试兜住了所以前端没调」。

---

## 4 文档 / README 矛盾点

### 4.1 README.md

| 位置 | 文档写的 | 代码实况 | 证据 |
|---|---|---|---|
| `README.md:11` | 「`/api/workflow/*` **7 端点** + `/api/review/{tid}`」 | `workflow.py` 实为 **23** 个端点；且 routes 层共 **23 个路由模块 / 250 端点**，文档只描述了 2 个模块 | `grep -c "@router\." backend/routes/workflow.py` → `23` |
| `README.md:5` / `README.md:13` | 「**5 个**领域 agent」「5 个 agent：张衡/沈括/诸葛亮/王安石/包拯」 | `backend/agents/` 现有 **7 个** agent 类：上述 5 个 + `chapter_team.py:114 ChapterChiefAgent` + `chapter_team.py:134 SpecialistAgent`（分支名 `multi-agent-bid-system` 的新章节团队，主链已启用：`services/chapter_writing.py:101/115`） | `grep -rn "^class .*Agent" backend/agents/*.py` |
| `README.md:51` | 前端主路径 `{projects,project-intake,three-mode,review,diff-review}.html` | 遗漏 `landing.html`（`main.py:345` `/lp` 真实服务，835 行）；另 `project-init.html`(4962 行) 与 `workbench.html`(5580 行) 已**不再被服务**（见 §6.1），未在文档说明 | `backend/main.py:345-350` |
| `README.md:52` | 旧 `/project-init`、`/workbench` 跳转项目准备页 | **此条正确**（`main.py:337-343`、`:360-365` 均为 307 → `/project-intake`）。列出以免误改 | `sed -n '337,343p;360,365p' backend/main.py` |

补充：`docs/reviews/2026-10-19-code-optimization-plan.md:295` 已把「更新 README 架构/端点/agent 数」列为
`AUX-F5`（P5，`needs-check`），21 天过去**未执行**，计数持续失真。

### 4.2 `docs/design` / `docs/reviews` 的 API 表与实现直接矛盾

六处设计文档列出的端点路径在代码中**不存在**（实现换了口径但文档未回写）：

| 文档位置 | 文档端点 | 实际实现 |
|---|---|---|
| `docs/design/素材-需求供给与检索-方案v3.md:466` | `POST /api/materials/{id}/parse` | 无（解析走 `POST /api/upload`→`/parse/stream` 链，或 `materials.py:585` `/reparse`） |
| 同上 `:468` | `POST /api/materials/{id}/confirm` | 无（`PATCH /api/materials/{id}`，`materials.py:606`） |
| 同上 `:469` | `GET /api/blocks/{id}/needs` | `GET /api/blocks/{block_id}/materials/needs`（`material_library.py:900`） |
| 同上 `:470-471` | `…/needs/{}/plan`、`…/needs/{}/search` | 无（统一 `POST …/materials/needs/action`，`material_library.py:210`） |
| 同上 `:473` | `POST /api/evidence/{id}/bind` | `POST /api/blocks/{block_id}/materials/bind`（`material_library.py:301`） |
| `docs/design/方案A-标包工作空间-实施规划.md:313-327` | `/packages/merge`、`/packages/manual`、`/packages/{}/runs/{}/state`、`/runs/{}/actions/{}`、`/points`、`/chapters`、`/reviews`（7 条） | 仅 `/packages/{package_id}/merge`（`packages.py:39`）与 `/runs`（`:273`）近似；`runs/{}/state`、`actions/{}`、`points/chapters/reviews` 子资源**全无**，工作区聚合实为 `GET /packages/{id}/workspace`（`:240`） |
| `docs/design/待办-可操作交付清单-方案v3.md:390-393` | `/api/todo/{}/apply`、`/upload`、`/ignore`、`/api/todo/recheck`（todo-id 级） | 实为**项目级**：`/api/projects/{id}/todo/apply`（`todo_v3.py:72`）、`/todo/recheck`（`:90`）；`/todo/{}/upload`、`/todo/{}/ignore` **不存在** |
| `docs/design/模型分工与编写过程-整体方案.md:31` | `POST /api/chapters/{}/write` | 无（`/api/projects/{id}/studio/chapter-writes`，`chapter_sse.py:103`；或 `/chapters/{block_id}/condense`，`chapter_writing.py:146`） |
| `docs/design/生图-DiagramDesign技能接入-方案.md:280` | `POST /api/studio/diagrams`（创建） | 无独立创建端点；创建走 `POST /api/blocks/{id}/studio/diagram-assets`（`diagram_assets.py:161`），`/api/studio/diagrams/{asset_id}/*` 仅子资源 |
| `docs/reviews/2026-10-15-code-review-report.md:79` | `GET /api/parse/stream` | 实为 `/api/parse/stream/{job_id}`（`upload.py:150`），且该端点本身**零调用方**（§1.a） |

排除项：`/api/paas/v4`（`docs/superpowers/plans/2026-09-20-*.md:170`）是智谱外部 API，非本站路径。

---

## 5 硬编码开关与不可达分支

| # | 位置 | 开关/常量 | 影响 | 证据 |
|---|---|---|---|---|
| 1 | `backend/orchestrator/nodes.py:563` | `MATCH_MODE = "assign"` | 模块级字面量，**无 env、无配置读入**。`:648` `if MATCH_MODE == "assign": return …` 之后 `:655-694` 整段（`agent.match` 调用 + 回调签名探测 `inspect.signature`）**生产不可达**，只能改源码切换 | `grep -rn "MATCH_MODE" backend frontend tests` → 仅 `nodes.py:563/648/655` + `test_nodes.py:226/231` |
| 2 | `backend/services/review_precheck.py:120-127` | `resolve_tier(requested="auto")` | 分支 `if requested in ("fast","full"): return requested` **永不成立**：唯一入口 `chapter_studio.py:1246` 默认 `tier="auto"`，而前端**从不传 tier**（`grep -rn "tier" frontend/assets/chapter-studio.mjs` 仅 `:820` 一处读 `review.tier` 做展示）。所以 fast/full 只能由 `has_critical` 自动推导，人工指定档位是死参数 | 见左 |
| 3 | `backend/services/chapter_review.py:136` | `"tier": "skip"` | 产生第四种 tier 值 `skip`（空章/极短章，`should_skip_llm`），但前端只识别 `'fast'`（`chapter-studio.mjs:820`）→ `skip` 与 `full` 在 UI 上无法区分，展示分支覆盖不全 | 见左 |
| 4 | `backend/routes/config.py:166` | `GET /api/config/providers` | 该端点存在的**唯一目的**是给前端下发权威 provider 表，但零调用方；4 份前端各自硬编码并已分叉（见 §6.5）。等于权威源被架空 | `grep -rno "/api/config[^s]" frontend` → 0 |

不构成缺陷（已核查，避免误报）：`REVIEW_SCORE_THRESHOLD`/`MAX_ITERATIONS`（`nodes.py:63-64`）、
`REVIEW_BATCH_CONCURRENCY`（`review_batch.py:94/161`）、`LLM_MODE=mock`（`clients.py:154`）
均为真实可覆盖的环境开关，非写死。

---

## 6 新旧实现漂移

### 6.1 两个 5k 行死页面 vs 已抽取的 assets 模块（最大体量漂移）

| 页面 | 行数 | 状态 | 证据 |
|---|---|---|---|
| `frontend/workbench.html` | 5580 | **不再被服务**。`main.py:337-343` `/workbench` 是 307 重定向到 `/project-intake`\|`/projects`，全文无 `FileResponse(workbench.html)` | `grep -rn "workbench.html" backend --include=*.py` → 0 命中 |
| `frontend/project-init.html` | 4962 | **不再被服务**。`main.py:360-365` `/project-init` 同为 307 重定向；`_project_intake_path` 指向的是 `project-intake.html`（`main.py:274`），无 `_project_init_path` | 同上 |

而两者的内联 JS **已被机械抽取**成现役模块，抽取注释留在文件头：

```
frontend/assets/mode-editor.js:1   /* 由 workbench.html 的内联 <script type="module"> 机械抽取而来（2026-10-13 三模式合并）。
frontend/assets/mode-outline.js:1  /* 由 project-init.html 的内联 <script type="module"> 机械抽取而来（2026-10-13 三模式合并）。
```

现役页面是 `three-mode.html`，它在 `:4066-4070` 动态 import `mode-dispatch.js` + `mode-outline.js` + `mode-editor.js`。
**后果**：死页面里保留着抽取前的旧实现副本（如 `workbench.html:5052 startWorkbenchExtraction` 与
`mode-editor.js:2669` 同名两份、`workbench.html:5148` 与 `mode-editor.js:2765` 同 URL 两份）。
两份都在调 410 端点，但只有 `mode-editor.js` 那份**活着**——维护者极易改错文件。
此外 `project-init.html` 仍带 §0 #3 的 `/workbench` 跳转，属「页面已下线但内部逻辑仍假设自己在线」的错位。

### 6.2 config API 单数 vs 复数两代并存

| 代 | 端点 | 调用方 |
|---|---|---|
| 旧（单数，5 个） | `POST/GET/DELETE /api/config`、`/config/check`、`/config/providers`（`config.py:103-166`） | **0**（前后端、测试皆无） |
| 新（复数，6 个） | `GET/POST /api/configs`、`/configs/reorder`、`PUT/DELETE /api/configs/{id}`、`/configs/{id}/check`（`config.py:243-316`） | `project-init.html:1502/1633/1636/1642`、`mode-outline.js:656/662`、`projects.html:1115/1125/1158`、`workbench.html:5529/5535/5556` |

单数版是多配置模型（`/configs` 有 `reorder`、`{id}`）之前的遗留，`ConfigStore` 两套语义共存。

### 6.3 三代 todo API，两代活两代死

| 代 | 端点 | 状态 |
|---|---|---|
| 1 | `GET /api/projects/{id}/todos`（`projects.py:294`） | **活**：`workspace.js:306/532` |
| 2 | `GET /api/projects/{id}/todo`（`chapter_studio.py:1190`） | **死**（§1.a） |
| 3 | `/todo/overview`、`/todo/state`、`/todo/apply`、`/todo/recheck`（`todo_v3.py:60-99`） | **活**：`chapter-studio.mjs:3101/3978/4077/4172/5799` |
| 3' | `GET /todo/chapter`（`todo_v3.py:54`） | **死** |

### 6.4 双评审存储（同一次评审写两张表）

- `chapter_review.py:140`、`:215` → `INSERT INTO chapter_reviews`
- `chapter_review.py:318` → `INSERT INTO reviews`（同函数末尾，同一批评审结果）
- 读侧也分叉：`domain/review/repository.py:33`（`reviews`）与 `chapter_review.py:267`（`chapter_reviews` 口径）
- 实库：`chapter_reviews` 20 行、`reviews` 44 行——两表都在用，非迁移残留，属**长期双写**
- 字段集不同（`chapter_reviews` 有 `tech_score/comp_score/tier/content_version`，
  `reviews` 有 `agent/thread_id`），同一概念两套 schema，跨表聚合必然要写兼容分支
  （实例：`services/delivery_verdict.py:87` 与 `services/practice_recycle.py:24` 都从 `review.get("tier")` 取，
  只对其中一代有效）

### 6.5 provider / 默认模型表 4 份副本，值已分叉

同一个 `PROVIDERS` 映射在前端复制 4 次，`deepseek` 与 `claude` 的默认值不一致：

| 副本位置 | deepseek 默认 | 来源 |
|---|---|---|
| `frontend/assets/mode-outline.js:597` | `deepseek-v4-pro` | 硬编码 |
| `frontend/workbench.html:5471` | `deepseek-v4-pro` | 硬编码 |
| `frontend/project-init.html:1577` | `deepseek-v4-pro` | 硬编码 |
| `frontend/projects.html:1025` | **`deepseek-chat`** | 硬编码 |
| **后端权威** `backend/services/config_store.py:82` | **`deepseek-chat`** | `DEFAULT_MODELS` |
| 后端 `config.py:231`（作为 `model_examples` 下发） | `deepseek-chat` / `deepseek-reasoner` | 同上 |

`claude` 侧同样分叉：后端 `config_store.py:78` 为 `claude-3-5-sonnet-20241022`，
而 4 份前端副本与 `config.py:185` 的 examples 全部是 `claude-sonnet-4-6`。
即：用户看到的预填模型 ≠ 后端实际会用的默认模型；且 `projects.html` 与其它三页互不一致。

同一批次还可见 `kimi` 分歧：后端 `config_store.py:80` = `moonshot-v1-32k`，前端 4 份全是 `moonshot-v1-8k`；
`minimax` 后端 `MiniMax-M2.7` vs 前端 `abab6.5s-chat`。**4 个 provider 中 3 个默认值不一致。**

### 6.6 同一 URL 在生产被三个不同模块拼装

| 拼装点 | URL | 语义 |
|---|---|---|
| `orchestrator/runner.py:1285` | `/api/workflow/{tid}/download` | SSE `done.download_url` |
| `utils/sse.py:43` | `/api/download/{task_id}` | `all_done.download_url` |
| `orchestrator/events.py:183-185` | 把上述字符串原样塞进 `done` 帧 | 无校验 |

三个都不存在的下载地址，无任何前端消费者（`grep -rn "download_url" frontend` → 0），
仅 `tests/orchestrator/test_events.py:151` 断言字符串相等。

---

## 7 追踪文件 ↔ 未跟踪依赖

`git ls-files` 实测（`tracked_files` 为 0 即未跟踪，未被 `.gitignore` 覆盖，属「脏工作区」而非忽略）：

| 路径 | 追踪 | 性质判定 | 依据 |
|---|---|---|---|
| `frontend/bench.html` | **tracked（1）** | 追踪文件，但**依赖未跟踪产物**：它调 `/bench/films`、`/bench/meta/`、`/bench/project/`、`/bench/frames/`、`/bench/upload/`、`/bench/asset/`、`/bench/render/`（`:110-301`） | `grep -n "/bench" frontend/bench.html` |
| `scripts/bench_server.py` | **0（未跟踪）** | **唯一**能服务 `/bench/*` 的实现。`main.py` 无任何 `/bench` 路由，`bench.html` 也不被 `main.py` 的任何 `FileResponse` 引用 | `git ls-files --error-unmatch scripts/bench_server.py` → `did not match any file(s) known to git` |
| `scripts/bench_render.py` | **0** | 本地渲染工具，`bench_server.py` 的配套 | 同上 |
| `packages/`（`dsh-video-bench`） | **0** | 本地工具，**非产品依赖**：`backend`/`frontend` 无任何引用（`packages.py` 的命中是 `/api/.../packages/` 路由名，同名不同物） | `grep -rn "marketing/\|plugins/\|packages/" backend frontend` 仅路由名 |
| `plugins/`（`dsh-sticky-notes`） | **0** | 同上，Agent 工具链插件 | 同上 |
| `marketing/`（`launch-30s.mp4` 等） | **0** | 同上，宣传视频产物，对应 `docs/design/宣传片-投放包.md` | 同上 |
| `docs/mockups/` | **1 tracked / 其余未跟踪** | 设计稿，非运行时依赖 | `docs/mockups/*.html/png/mp4` 均 `??`（见 `git status`） |

结论：
- **产品依赖**（不进库就坏）：无。上述 4 类均不被 `backend/` 运行时引用。
- **需要追踪**：`scripts/bench_server.py` + `scripts/bench_render.py`——因为 `frontend/bench.html` 是
  已追踪文件且对外暴露 `/bench/*` 七个调用；两者不同步提交，等于提交了一个**每个请求都 404 的页面**。
- **形态问题**：`bench.html` 无 `main.py` 路由（不在 `/`、`/three-mode`、`/projects`、`/workbench`、
  `/lp`、`/diff-review`、`/project-init`、`/project-intake`、`/review` 之列），只能靠本地起
  `bench_server.py` 直出，属**本地工具页混入产品静态目录**。

---

## 8 只读验证命令

以下命令均为只读（`grep` / `git ls-files` / `sqlite ?mode=ro`），可复现本报告全部结论：

```bash
# ── 端点全集（250）与方法分布
grep -c "@router\." backend/routes/*.py | sort -t: -k2 -rn
grep -n "include_router\|prefix=" backend/main.py

# ── 1a 死端点抽查（示例，替换 <seg> 为端点末两段）
for s in material-search analyze-points delivery-gate/auto recut-all \
         figures-plan confirm-chapter citations/swap self-heal \
         rewrite-selection align-outline section-results issues/summary \
         todo/chapter supported-formats; do
  printf "%-24s frontend=%s tests=%s\n" "$s" \
    "$(grep -rl "$s" frontend 2>/dev/null | tr '\n' ' ')" \
    "$(grep -rl "$s" tests 2>/dev/null | tr '\n' ' ')"
done
grep -rn "@router.*download\|@app.*download" backend          # → 0 命中（§0#6）
grep -rn "generate-all\|generate_all" backend frontend        # → 仅前端 generate-session.js:27
grep -rn "materials.outline" frontend                         # → 0（api.js:136 死helper）

# ── 1b 410 端点
sed -n '674,686p' backend/routes/materials.py                 # [DEPRECATED] + 410
git log --oneline -3 -- backend/routes/materials.py           # 确认废弃提交

# ── 2 schema ↔ 代码
grep -n "CREATE TABLE\|ALTER TABLE\|ADD COLUMN" backend/db.py
grep -n "CREATE TABLE IF NOT EXISTS [a-z_]*(.*)" backend/db.py   # 单行建表 project_writing_facts
# 实库（只读）表清单 + 行数
python3 -c "
import sqlite3
c=sqlite3.connect('file:backend/data/ge_solution.db?mode=ro',uri=True)
for (t,) in c.execute(\"select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name\"):
    print(t, c.execute(f'select count(*) from \"{t}\"').fetchone()[0])
"
# 零行但代码有写路径
python3 -c "
import sqlite3,re,pathlib,glob
c=sqlite3.connect('file:backend/data/ge_solution.db?mode=ro',uri=True)
cnt={t:c.execute(f'select count(*) from \"{t}\"').fetchone()[0] for (t,) in c.execute(\"select name from sqlite_master where type='table'\")}
w=set()
for f in glob.glob('backend/**/*.py',recursive=True):
    s=open(f,errors='replace').read()
    w|={m.group(1).lower() for m in re.finditer(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)',s,re.I)}
print([t for t in sorted(cnt) if cnt[t]==0 and t in w])
"

# ── 3 测试错位
sed -n '226,231p' tests/orchestrator/test_nodes.py            # 死分支 rerank
sed -n '145,152p' tests/orchestrator/test_events.py           # 死 download_url 契约
sed -n '55,66p'  tests/routes/test_chapter_workspace_jobs.py   # 已排除的误报

# ── 4 文档矛盾
grep -n "workflow/\*.*7\|7 端点" README.md docs/reviews/*.md
grep -c "@router\." backend/routes/workflow.py                # 23（README 写 7）
grep -rn "^class .*Agent" backend/agents/*.py                 # 7 个（README 写 5）
sed -n '337,343p;360,365p' backend/main.py                    # 两个 307（README:52 正确）
sed -n '51,52p' README.md

# ── 5 硬编码开关
grep -rn "MATCH_MODE" backend frontend tests
sed -n '560,566p;646,660p' backend/orchestrator/nodes.py
sed -n '120,128p' backend/services/review_precheck.py
grep -n "tier" backend/routes/chapter_studio.py | head
grep -rn "tier" frontend/assets/chapter-studio.mjs             # 仅展示，从不传参

# ── 6 漂移
grep -rn "workbench.html" backend --include=*.py              # → 0：页面未服务
sed -n '1,2p' frontend/assets/mode-editor.js frontend/assets/mode-outline.js
grep -rno "/api/config[^s]" frontend                          # → 0：单数版全死
grep -rn "INSERT INTO reviews\|INSERT INTO chapter_reviews" backend --include=*.py
grep -n "deepseek-\|claude-sonnet\|gpt-4o" \
  frontend/assets/mode-outline.js frontend/projects.html frontend/workbench.html \
  frontend/project-init.html backend/services/config_store.py
sed -n '77,84p' backend/services/config_store.py

# ── 7 未跟踪依赖
git ls-files --error-unmatch scripts/bench_server.py          # → did not match
for p in scripts/bench_server.py scripts/bench_render.py packages plugins marketing \
         docs/mockups frontend/bench.html; do
  printf "%-28s tracked=%s\n" "$p" "$(git ls-files "$p" | wc -l | tr -d ' ')"
done
grep -n "/bench" frontend/bench.html
grep -rn "marketing/\|plugins/\|packages/" backend frontend   # 仅 /api/.../packages 路由名
```

---

## 附：本次审计**未**发现问题的项（避免后续重复排查）

| 检查项 | 结论 | 依据 |
|---|---|---|
| HTTP 方法跨层一致 | 一致，0 处不符 | §1.c，19 候选逐一读源排除 |
| 缺表 / 缺列 | 0 | §2.1 |
| schema 零引用表 | 0 | §2.1 |
| `checkpoints`/`writes` 不在 `db.py` | 有意设计 + 双处防护 | `run_recovery.py:44-51`、`projects.py:96-101` |
| `/project-init`、`/workbench` 重定向 | 与 README:52 一致 | `main.py:337`、`:360` |
| 环境开关（阈值/并发/LLM_MODE） | 真实可覆盖，非写死 | §5 末段 |
| `?mode=ro` 读库 | 未写库，未改任何文件 | 全程只读 |
