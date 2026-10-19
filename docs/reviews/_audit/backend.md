# 后端审计

> 审计员：be-auditor（task-2）
> 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`（HEAD `4a6f627`，2026-10-17）
> 方式：**只读**。`grep` / `git log -1 --format=%ad` / `wc -l` / AST 静态引用图 / `sqlite3` 只读连接（`?mode=ro`）。未启动服务、未跑 pytest、未 checkout/stash/reset/rm 任何项目文件、未写数据库。
> 时点：2026-10-19。文中日期均为 **2026 年**（仓库历史 2026-05-07 ~ 2026-10-17）。
> 覆盖：`backend/**/*.py` 109 个文件 28,244 行（含 `backend/scripts/` 2 个共 291 行）、仓库根 `scripts/*.py` 2 个 181 行、`tests/**/*.py` 82 个文件 18,905 行。前端只用于核实 API 是否有产品调用方，不审计前端实现（见同目录 `frontend.md`）。
> 唯一写入：本文件。

---

## 0. 结论摘要

### 0.1 数量总览

| 类别 | 条目数 | 说明 |
|---|---:|---|
| **DEAD**（整文件死模块） | 7 | 837 行生产代码，另有 753 行测试只为它们存在 |
| **DEAD**（活文件内的死函数/常量/方法） | 15 | 约 294 行 |
| **LEGACY**（被取代的旧管线/旧 API/兼容分支/一次性迁移） | 11 | 其中约 902 行需要产品/数据决策后再删 |
| **DUPLICATE**（重复 SQL / 重复逻辑） | 12 | 主要是路由直连 SQL 绕过 `block_store` / `domain` 造成的 |
| **UNUSED-API**（115 个路由端点的死角） | 21 | 约占全部端点的 18%；其中 5 个只被测试调用 |
| **JUNK**（未使用 import / 无效分支 / 数据残留） | 5 | 含 `backend/data/` 下 2 个 0 字节库与 2 个 400MB 级备份 |
| **配置/依赖** | 5 | **2 项高风险**：缺声明依赖、缺 re-export 导致功能假死 |
| **测试保护死代码** | 10 | 约 750 行测试可随死代码一起删，另约 250 行需局部删改 |
| **合计** | **86** | — |

### 0.2 可安全删除行数估算

| 层级 | 行数 | 组成 |
|---|---:|---|
| 直接删（零引用、零产品依赖，safe） | **≈ 1,560 行** | 死模块 837 + 死函数 294 + 死端点 432（与死模块有重叠的已按模块计，端点部分只计重复路由内的新行） |
| 随死代码可删的测试 | **≈ 750 行** | `test_spec_repository`(173)、`test_proposal_repository`(274)、`test_material_repository`(225)、`test_material_search`(81) |
| 需产品决策后再删 | **≈ 900 行** | 旧匹配 rerank 管线 ~430、doc_summary 停用链路 ~110、一次性脚本 291、启动期一次性回填 71 |
| JUNK / 配置 | 少量代码 + 磁盘 ≈ 400MB | 未使用 import、0 字节 DB、`ge_solution.db.bak-*` |
| **合计** | **≈ 3,200 行（约占 backend+tests 的 6.8%）** | 全部为静态行数估算，不含前端 |

### 0.3 风险最高的 6 项

1. **全新环境/容器启动即失败**：`requirements.txt` 未声明 `jieba`、`rank_bm25`，而它们由 `infra/retrieval/keyword.py:6-7` 在**模块顶层**导入，被 `assign.py`/`locate.py`/`agents/shen_kuo.py` 传递链一路带到 `main.py` 启动路径。本地 `.venv` 恰好装过（jieba 0.42.1 / rank_bm25 0.2.2），`Dockerfile` 只 `pip install -r requirements.txt` → 新镜像 `import` 阶段即 `ModuleNotFoundError`。（X01）
2. **LLM 标包合并功能假死**：`routes/materials.py:643` 写 `from infra.llm import dispatch_package_merge_json`，但 `infra/llm/__init__.py` 没有转出这个名字（函数实际在 `dispatcher.py:1096`）。该 import 在 `try/except` 内，异常被吞成 `logger.info("模型整理不可用")` → `/package-analysis?use_llm=1` 永远走规则降级，`merge_with_llm`/`validate_merge_proposal` 整条链路（~200 行 + 6 个测试）在产品中不可达。（X02）
3. **deprecated 端点仍被活跃前端调用**：`POST /api/projects/{id}/outline` 在 `routes/materials.py:574-584` 固定返回 410，但 `frontend/assets/mode-editor.js:2765`（三模式页 editor 模式）与 `frontend/workbench.html:5148` 仍会 POST 它。触发条件是存在 `requirement` 角色材料；只读查库显示当前库 `materials.role` 仅 `main_rfp`(4)/`spec`(4)，所以目前"休眠"，但老项目一旦有 `requirement` 文件，按钮必 410。（L02/U21）
4. **旧匹配管线（LLM 重排）常驻但默认不可达**：`orchestrator/nodes.py:563 MATCH_MODE = "assign"` 是硬编码常量，`rerank` 分支（nodes.py:655-700）+ `agents/shen_kuo.py`(256) + `infra/retrieval/rerank.py`(127) 只能靠改代码启用；`main.py:136` 每次 start 仍会 `ShenKuoAgent()` 实例化。报告注释明说"保留用于回滚/对照"→ 需产品决策（L04）。
5. **domain 三套 repository 是"被测试养着"的死层**：`SpecRepository`/`ProposalRepository`/`MaterialRepository` 在生产代码（routes/services/agents/orchestrator/scripts）**零引用**，唯一消费者是各自的测试。它们还反向 import `services.block_store`（架构倒挂），注释写着"Phase 7 services 删除时切换"——但 Phase 7 未发生，block_store 继续膨胀到 1,323 行。删除可回收 315 行实现 + 672 行测试。（D04-D06/L08）
6. **启动期混入一次性数据回填**：`db.py:401-435`（packages.display_name 从引文回填，2026-10-16 一次性修复）与 `db.py:466-490`（旧 `project_packages` → 新 `packages.package_id` 迁移）每次启动都跑。只读查库：`packages.display_name` 已无脏值（`LIKE '%|%'` = 0），而 `workflow_runs.package_id IS NULL` 仍有 41/50 行且其 `target/package_no` 也为空，回填 SQL 对它们永远无效 → 属于"永远跑、永远修不好"的遗留迁移。（L07）

### 0.4 方法说明

- **死模块**：对每个 `.py` 文件生成"被 import 引用"集合（含 `from X import leaf`、`from .leaf import`、`from . import leaf` 三类），再人工核对 `main.py` 注册表与字符串式引用（如 `import_module`、`python -m scripts.xxx`）。
- **死代码**：AST 收集全部顶层 def/class/赋值，统计"其他文件引用数 = 0 且本文件除定义行/`__all__` 外引用数 = 0"；对装饰器注册（`@router.*`、`@app.*`、`@field_validator`）、`__aenter__` 等隐式调用做了排除。
- **端点**：解析 115 个 `@router.*` 端点，分别在 `frontend/**/*.{js,mjs,html}` 与 `tests/**/*.py` 中按"路径 + 模板字符串插值"的宽松正则匹配，再逐个用关键字 grep 人工复核（自动表对模板字符串有漏报，例如 `/packages/{id}/merge` 实际被 `intake.js:113` 调用）。
- **数据佐证**：以 `sqlite3.connect('file:backend/data/ge_solution.db?mode=ro', uri=True)` 只读查询表行数/脏值，未执行任何写操作。

---

## 1. 逐项清单

> 列说明：**规模** = 行数（`wc -l` 或删除区间行数）；**证据** = 引用检查命令与结果（均在本仓库实跑）；**风险**：`safe` 可直接删 / `needs-check` 需确认外部调用 / `product-decision` 需产品拍板。

### 1.1 死模块（整文件零引用）

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| D01 | `backend/routes/upload.py:1-292` | DEAD | 292 行 | `grep -rn "routes.upload" backend` = 1（仅 `main.py:18` import）；`grep -rn "upload_router" backend` = 2（`main.py:18` 别名 + `227` 注册）；`grep -rn "/api/upload\|/parse/stream" frontend tests` = 0；`grep -rn "validate_uuid" backend tests` = 3（全部在 upload.py + utils/validators.py）；最后提交 2026-06-17；`docs/design/方案A-标包工作空间-实施规划.md:550` 明写"下线无人调用的旧 `/api/upload` 管线" | safe | 删除整个文件，同时删 `main.py:18` import 与 `main.py:227` `include_router`；`utils/validators.py`（D03）随之删除 | `grep -rn "upload_router\|routes.upload" backend` = 0；`python -m pytest -q` |
| D02 | `backend/routes/changelog.py:1-71` | DEAD | 71 行 | `grep -rn "changelog" frontend` = 0；`grep -rn "changelog" tests` = 0（排除 `CHANGELOG.md`）；仅 `main.py:20` import + `229` 注册；最后提交 2026-05-13（全仓最老路由） | safe | 删文件 + `main.py:20/229` 两行；`README` 若提"更新记录"一并改 | 同上；`curl -s localhost:8000/api/changelog -o /dev/null -w '%{http_code}'`（改后 404） |
| D03 | `backend/utils/validators.py:1-11` | DEAD | 11 行 | `validate_uuid` 唯一调用点是 `routes/upload.py:162`；`grep -rn "validators" backend` 只有 upload 与自身 | safe | 随 D01 删除 | `grep -rn "validate_uuid\|utils.validators" backend tests` = 0 |
| D04 | `backend/domain/spec/repository.py:1-120` | DEAD | 120 行 | `grep -rn "SpecRepository" backend scripts` 排除 `backend/domain` = 0；`grep -rn "from domain.spec.repository" backend tests scripts` = 0；唯一消费者 `tests/domain/test_spec_repository.py`；`domain/spec/__init__.py:12` 只是转出；最后提交 2026-09-17 | needs-check（架构方向待决） | 删文件 + `domain/spec/__init__.py` 中 `SpecRepository` 导入与 `__all__` 条目；测试 T01 同删 | `grep -rn "SpecRepository" backend tests` = 0 |
| D05 | `backend/domain/proposal/repository.py:1-84` | DEAD | 84 行 | `grep -rn "ProposalRepository" backend scripts` 排除 domain = 0；唯一消费者 `tests/domain/test_proposal_repository.py`；文件头注释"Phase 7 services 删除时会切换"但从未接线 | needs-check | 同 D04，删 `domain/proposal/__init__.py:6` 转出；测试 T02 同删 | `grep -rn "ProposalRepository" backend tests` = 0 |
| D06 | `backend/domain/material/repository.py:1-111` | DEAD | 111 行 | `grep -rn "MaterialRepository" backend scripts` 排除 domain = 0；唯一消费者 `tests/domain/test_material_repository.py`；`domain/material/__init__.py:36` 转出；最后提交 2026-06-16 | needs-check | 同 D04，删 `domain/material/__init__.py` 转出；测试 T03 同删 | `grep -rn "MaterialRepository" backend tests` = 0 |
| D07 | `backend/services/material_search.py:1-148` | DEAD | 148 行 | 唯一生产引用 `routes/blocks.py:99`（属于死端点 U11）；`grep -rn "material_search" backend` 排除自身 = 1；消费者只剩 `tests/services/test_material_search.py`；最后提交 2026-10-14 | needs-check（端点去留先定） | 删 U11 后本文件成孤儿 → 一并删；T04 同删 | `grep -rn "material_search" backend tests` 仅余 0 |

### 1.2 死代码（活文件内零引用函数/常量/方法）

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| C01 | `backend/utils/sse.py:24-62`（`sse_token`/`sse_section_start`/`sse_section_done`/`sse_all_done`/`sse_doc_summary`/`sse_section_skip`） | DEAD | 39 行 | 6 个函数全仓引用 = 0（`grep -rn "sse_token\|sse_section_start\|..." backend tests` 只剩 `def` 行）；其余 `sse_parse_*`/`sse_error` 只被 D01 使用 | safe（随 D01） | 删 6 函数；若 D01 删除，则 `sse_parse_*`/`sse_error` 也一起删，仅留 `format_sse_event` | `grep -rn "sse_parse\|sse_error" backend` = 0（D01 删除后） |
| C02 | `backend/services/config_store.py:115-121,158-176,258-278`（`save_all`/`reorder`/`get_next_index`/`get`/`save`/`clear`） | DEAD | 44 行 | `save_all` 只被 `tests/services/test_config_store.py` 调用；`reorder` 只被死端点 U10 调用；`get_next_index` 全仓 0；`get/save/clear` 只被旧 `/config` 端点（U05-U08）调用；`grep -rn "config_store.get()\|config_store.save\|config_store.clear"` = 3+1+1 全部在 `routes/config.py` 旧接口区 | safe（须先删 U05-U10） | 删 6 个方法；`mark_verified` 保留（新端点 U-check 在用） | `grep -rn "get_next_index\|save_all\|config_store.reorder" backend tests` = 0 |
| C06 | `backend/main.py:246`（`_index_path`） | DEAD | 1 行 | `grep -rn "_index_path" backend` = 1（仅定义）；`serve_frontend` 用的是 `_projects_path` | safe | 删该行 | `grep -rn "_index_path" backend` = 0 |
| C07 | `backend/orchestrator/graph.py:170-181`（`_route_after_generate`） | DEAD | 12 行 | `grep -rn "_route_after_generate" backend tests` = 1（仅定义）；generate 后的条件路由实际用内联 `_generate_fanout`（`graph.py:362-377`） | safe | 删函数 | `grep -rn "_route_after_generate" backend tests` = 0 |
| C08 | `backend/infra/llm/errors.py:143-145,273-275,278-280`（`is_retryable`/`reset_health`/`unhealthy_snapshot`） | DEAD | 9 行 | 生产引用 0；`reset_health` 只被 `tests/conftest.py:22,24` 与 `tests/infra/test_llm_errors.py` 使用；`is_retryable`/`unhealthy_snapshot` 连测试都几乎没有（`unhealthy_snapshot` 全仓 1 处 = 定义） | safe（测试支持项可留一份 reset） | 删 `is_retryable`/`unhealthy_snapshot`；`reset_health` 如保留需在 docstring 标注"仅测试" | `grep -rn "is_retryable\|unhealthy_snapshot" backend tests` = 0 |
| C09 | `backend/infra/llm/usage.py:52-53`（`get_sink`） | DEAD | 2 行 | 全仓 1 处（定义）；模块内其他函数用全局 `_sink` | safe | 删函数 | `grep -rn "get_sink" backend tests` = 0 |
| C10 | `backend/infra/parser/outline_builder.py:34,45,329-350,814-825`（`_ENUM_RE`/`_TOPIC_LEVEL_HINTS`/`match_spec_nodes`/`render_outline_text`） | DEAD | 36 行 | 4 个符号全仓各 1 处（定义）；`render_outline_text` 只被 `tests/infra/test_outline_builder.py` 调用；`match_spec_nodes` 连测试都没有 | safe | 删 4 个符号；测试 T08 局部删 | `grep -rn "match_spec_nodes\|render_outline_text\|_ENUM_RE\|_TOPIC_LEVEL_HINTS" backend` = 0 |
| C11 | `backend/infra/parser/review_points.py:70-72,222-226,520-524`（`_SCOPE_LINE_RE`/`ReviewPointTable.total_points`/`review_points_of`） | DEAD | 13 行 | 三者生产引用 0；`total_points` 只被 `tests/infra/test_review_points.py:211` 用；`review_points_of` 全仓 1 处（定义） | safe | 删 3 个符号 | `grep -rn "review_points_of\|total_points\|_SCOPE_LINE_RE" backend tests`（测试行同步删） |
| C12 | `backend/infra/parser/toc.py:11`、`scope_index.py:31`、`tech_requirements.py:89`（`_CIRCLED_DIGITS`/`_NUM_PREFIX_RE`/`_ARTIFACT_RE`） | DEAD | 3 行 | 三个常量全仓各 1 处（定义） | safe | 删除 | `grep -rn "_CIRCLED_DIGITS\|_NUM_PREFIX_RE\|_ARTIFACT_RE" backend` = 0 |
| C13 | `backend/services/block_store.py:137-155`（`list_materials_by_ids`） | DEAD | 19 行 | 全仓 1 处（定义）；`main.py` 的 spec_loader 自己写了等价 SQL（`main.py:86-90`） | safe | 删函数；顺手把 main.py 那段改为调用 `block_store`（可选） | `grep -rn "list_materials_by_ids" backend tests` = 0 |
| C14 | `backend/services/page_budget.py:114-123,146-165`（`_parent_key`/`rollup_totals`） | DEAD | 30 行 | 两函数全仓各 1 处（定义）；`rollup_totals` 内还有一段永假分支 `any(c is chapter for c in [])`（`page_budget.py:153-154`） | safe | 删两函数 | `grep -rn "rollup_totals\|_parent_key" backend tests` = 0 |
| C15 | `backend/services/usage_store.py:69-70`（`uninstall`） | DEAD | 2 行 | 全仓 1 处（定义）；`install` 在 `main.py:174` 使用，`uninstall` 无人用 | safe | 删函数 | `grep -rn "usage_store.uninstall" backend tests` = 0 |
| C16 | `backend/orchestrator/state.py:189-229`（`merge_state`） | DEAD | 41 行 | `grep -rn "merge_state" backend` 排除 state.py = 0；唯一调用方 `tests/orchestrator/test_state.py`（8 个用例）。docstring 声称"routes 层在 update_state 前合并外部输入"，实际无任何 route 使用；LangGraph 侧用 `_merge_dict` reducer | needs-check（可能是给测试留的契约） | 删函数 + `__all__` 条目 + 测试中相关用例；若想保留"测试工具"，移到 `tests/` | `grep -rn "merge_state" backend` = 0 |
| C17 | `backend/domain/review/repository.py:54-63,75-?,208-?`（`clear_thread`/`list_findings_by_block`/`latest_for_scope`） | DEAD | ≈40 行 | 三个方法生产引用 0；`clear_thread`/`list_findings_by_block` 只被 `tests/domain/test_review_repository.py` 使用，`latest_for_scope` 只被 `tests/test_run_stage_mapping.py:100` 使用；类本身活着（`routes/review.py` + `nodes.py:1047`） | safe | 删 3 个方法（类保留） | `grep -rn "clear_thread\|list_findings_by_block\|latest_for_scope" backend` = 0 |
| C18 | `backend/domain/material/models.py:99-109`（`VALID_KINDS`/`VALID_SOURCE_KINDS`/`VALID_BY`） | DEAD | 3 行 | 三个常量全仓各 1 处（定义），且不在 `domain/material/__init__.py` 的 `__all__` | safe | 删除 | `grep -rn "VALID_KINDS\|VALID_SOURCE_KINDS\|VALID_BY" backend tests` = 0 |

### 1.3 老旧实现（被新实现取代 / 兼容分支 / 一次性迁移）

| ID | 文件:行 | 类别 | 规模 | 证据（被谁取代 / 为何遗弃） | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| L01 | `backend/routes/upload.py` + `utils/validators.py` + `utils/sse.py:24-103` | LEGACY | 342 行 | 上传已被 `POST /api/projects/{id}/materials`（`routes/materials.py:350`，2026-06 起）取代；旧管线还带"单 queue 被后续连接覆盖"的 SSE 抢断缺陷（`upload.py:178-191` 覆盖 `job.event_queue`，2026-10-15 报告 P2-3 已记录，当前仍在）；前端/测试引用 0 | safe | 整体删除（与 D01/D03/C01 同一动作） | 见 D01 |
| L02 | `backend/routes/materials.py:574-584`（`POST /projects/{id}/outline`，固定 410） | LEGACY | 11 行 | 被 LangGraph `POST /api/workflow/start` 取代；但**前端仍有调用方**：`frontend/assets/mode-editor.js:2765`、`frontend/workbench.html:5148`（触发条件 `role==='requirement'`，当前库 0 行，属休眠契约漂移，对应 2026-10-15 报告 P2-6） | needs-check | 前后端一起删：删端点 + 删 `mode-editor.js:2669-2800` 的 `startWorkbenchExtraction` 及其 `#extract-action-bar` 触发器；或保留 410 但在前端移除入口 | `grep -rn "projects/.*}/outline" frontend` = 0；`grep -rn "DEPRECATED" backend` 不再含该端点 |
| L03 | `backend/routes/config.py:99-236,268-280`（旧单配置 API 6 个端点） | LEGACY | 151 行 | 2026-05 的单配置接口，已被 §1.4 新接口 `/api/configs*` 取代；`grep -rn "/api/config[^s]" frontend` = 0；测试 0；代码注释自称"旧接口（向后兼容）" | safe | 删 6 个端点 + `config_store` 旧方法（C02）；`_KEEP_KEY` 保留（新 PUT 仍用） | `grep -rn "config_store.get()\|config_store.save\|config_store.clear" backend` = 0 |
| L04 | `backend/orchestrator/nodes.py:563,637-700` + `backend/agents/shen_kuo.py:1-256` + `backend/infra/retrieval/rerank.py:1-127` | LEGACY | ≈430 行 | `MATCH_MODE = "assign"`（nodes.py:563）硬编码，`assign` 用规则+BM25 反向分配（零模型）；`rerank` 是"逐章 BM25 top-k + LLM 重排"的旧匹配，注释明说"保留用于回滚/对照，默认不用"（nodes.py:559-562）。`tests/orchestrator/test_nodes.py:225-231` 用 monkeypatch 专门保护旧路径；`tests/agents/test_shen_kuo.py` 331 行、`tests/infra/test_retrieval.py` 也覆盖它 | product-decision | 二选一：①删旧路径（含 shen_kuo agent、rerank、`GraphDeps.shen_kuo`、main.py:138 实例化、对应测试）；②保留则把开关改成环境变量并在 README 写清回滚步骤，避免"死代码式保留" | 删除后 `grep -rn "MATCH_MODE\|llm_rerank\|ShenKuoAgent" backend` 只剩 assign；`python -m pytest -q` |
| L05 | `backend/infra/llm/dispatcher.py:110-188`（`_generate_doc_summary`）+ `428-459`（`dispatch_doc_summary`）+ `agents/zhang_heng.py:369-381`（恒空赋值） | LEGACY | ≈110 行 | 2026-09-17 起"摘要生成暂时停用"（zhang_heng.py:369-379 注明推理模型 max_tokens=2000 必空），`doc_summary` 恒为 `""`；`dispatch_doc_summary` 仅被 `tests/agents/test_zhang_heng.py:88` 的 spy 引用 | product-decision | 要么按注释"恢复调用并给足 max_tokens"，要么删 `dispatch_doc_summary`/`_generate_doc_summary` 及 `doc_summary` 参数链路（dispatcher/prompts/nodes/state 多处，需一次收敛） | `grep -rn "dispatch_doc_summary" backend` = 0（若删）；恢复则补真模型测试 |
| L06 | `backend/scripts/normalize_extract_fields.py:1-147`、`backend/scripts/recut_old_materials.py:1-144` | LEGACY | 291 行 | 两文件开头均写"**一次性脚本**"；normalize 对应 2026-09-21 prompt 口径修正（历史数据一次性归位），recut 对应切片算法上线前的老 material 重切；全仓无 import/无调度入口（`grep -rn "normalize_extract_fields\|recut_old_materials"` 仅 docs 提及） | product-decision（历史数据是否还需要重跑） | 建议移到 `scripts/oneoff/` 或 `docs/` 归档并加 README 说明；若确认线上数据都已处理则删除。recut 内 `_split_for_scoring_evaluation`（`:32-51`）还与 `routes/materials.py:115-136` 逐字重复（P02） | `git log --oneline -- backend/scripts/`；确认无 cron/文档入口 |
| L07 | `backend/db.py:388-398,401-435,466-490`（启动期一次性回填/清洗） | LEGACY | 71 行 | 三段都是"一次性数据修复"却挂在 `init_db()` 每次启动执行：①名称清洗（2026-10-17，"packages.display_name 取第一个 `\|` 前"）②从 evidence 回填 display_name（2026-10-16）③旧 `project_packages`→`packages.package_id` 回填。只读查库：脏名 `packages LIKE '%\|%'` = **0**（①已无事可做）；`workflow_runs.package_id IS NULL` = **41/50**，且这 41 行 `target/package_no` 全空（③永远修不好）。另 `init_db` 整体 237 行、无版本表（2026-10-15 报告 P2-7 仍成立） | needs-check | ①可安全删（数据已清）；②确认所有库已回填后删；③要么删、要么补一条"老 run 的 package_id 从 checkpoint 反推"的真实迁移；长期建议引入 `schema_migrations` 表（报告 P2-7） | 备份后对比启动前后 `SELECT COUNT(*) ... LIKE '%\|%'`；`python -c "from db import init_db"` 不报错 |
| L08 | `backend/domain/spec/repository.py`、`domain/proposal/repository.py`、`domain/material/repository.py` | LEGACY | 见 D04-D06 | 名义分层 `routes→services/domain→infra`，实际 domain 三仓反向 import `services.block_store`（`spec/repository.py:5-12`、`proposal/repository.py:8-14`、`material/repository.py:8-18`），且无生产调用——是"Phase 7"过渡层但 Phase 7 未落地（注释仍在）。block_store 已 1,323 行同时承担 CRUD/迁移/修复（2026-10-15 报告"架构评价"同样指出） | needs-check | 与团队确认 Phase 7 是否还做：做→接线并删 block_store 中重复 SQL；不做→删三仓（+672 行测试），domain 只留 models/纯函数 | `grep -rn "SpecRepository\|ProposalRepository\|MaterialRepository" backend scripts` = 0（除 domain 自身） |
| L09 | `backend/infra/llm/dispatcher.py:1051-1054`（`is_letter_section`/`LETTER_KEYWORDS` re-export） | LEGACY | 4 行 | 真实定义已迁至 `domain/letter_detector.py`（Phase 2.5），生产调用方（`agents/zhuge_liang.py:22`、`infra/llm/__init__.py:19`）都已走新路径；该 re-export 只剩 `tests/domain/test_letter_detector.py:32-36` 在验证"旧路径仍可用" | safe | 删 re-export + 删该测试用例；`infra/llm/__init__.py` 改为直接从 `domain.letter_detector` 导入 | `grep -rn "from infra.llm.dispatcher import" backend tests` 不含 is_letter_section |
| L10 | `backend/main.py:54`（`REQUIREMENT_ROLES` 含 `"requirement"`）、`routes/materials.py:49`（`ALLOWED_ROLES` 含 `"requirement"`） | LEGACY | 2 行 | `main.py:51` 自注"`requirement` 是早期版本遗留的『主招标』写法"；只读查库 `materials.role` 只有 `main_rfp`(4)/`spec`(4)，`requirement` 0 行；但 `main.py:91-99` 的 spec_loader 仍把它排进要求文件 | needs-check（老项目可能还有） | 保留一个发布周期观察后删除，或做一次 `UPDATE materials SET role='main_rfp' WHERE role='requirement'` 数据迁移再删 | `SELECT COUNT(*) FROM materials WHERE role='requirement'` = 0（迁移后） |
| L11 | `backend/services/config_store.py:306-308`（旧 JSON 单 dict 格式兼容） | LEGACY | 3 行 | `_load_from_file` 对"旧格式为单个 dict"做 `raw=[raw]` 兼容；当前配置文件格式已是 list（`_persist` 写 list），属数据格式兼容，删除会让极老配置文件加载失败 | needs-check | 保留（成本极低）；若要删，先确认所有部署环境 `.api_config.json` 已是 list | 检查各环境配置文件首字符是 `[` |

### 1.4 UNUSED-API（无前端/无测试调用的端点，共 21 个 / 115）

> 复核方法：端点路径 → 前端宽松正则 + 关键字 grep + 测试 grep；`api.js` 中"封装存在但从未被调用"的方法也算无调用。

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| U01 | `POST /api/upload` `routes/upload.py:100` | UNUSED-API | （含 D01） | 全仓前端/测试 0 调用（见 D01 证据） | safe | 随 D01 删 | `grep -rn "api/upload" frontend tests` = 0 |
| U02 | `GET /api/parse/stream/{job_id}` `routes/upload.py:150` | UNUSED-API | （含 D01） | 同上；`parse_job_id` 只由 U01 返回，U01 死则本端点无输入 | safe | 随 D01 删 | 同上 |
| U03 | `GET /api/upload/supported-formats` `routes/upload.py:285` | UNUSED-API | （含 D01） | `grep -rn "supported-formats" frontend tests backend` = 1（定义）；前端从 `ALLOWED_ROLES` 自带格式提示 | safe | 随 D01 删 | 同上 |
| U04 | `GET /api/changelog` `routes/changelog.py:65` | UNUSED-API | （含 D02） | `grep -rn "changelog" frontend tests` = 0 | safe | 随 D02 删 | `curl -s -o /dev/null -w '%{http_code}' localhost:8000/api/changelog` → 404 |
| U05 | `POST /api/config` `routes/config.py:103` | UNUSED-API | 25 行 | 前端全用 `/api/configs`（`grep -rn "/api/configs" frontend` 命中 20+ 处，`"/api/config[^s]"` = 0） | safe | 随 L03 删 | `grep -rn "fetch('/api/config'" frontend` = 0 |
| U06 | `GET /api/config` `routes/config.py:130` | UNUSED-API | 7 行 | 同上 | safe | 同上 | 同上 |
| U07 | `GET /api/config/check` `routes/config.py:139` | UNUSED-API | 17 行 | 前端检查用 `GET /api/configs/{id}/check`（`project-init.html:1636` 等） | safe | 同上 | `grep -rn "config/check" frontend tests` = 0 |
| U08 | `DELETE /api/config` `routes/config.py:158` | UNUSED-API | 6 行 | 前端删除用 `DELETE /api/configs/{id}` | safe | 同上 | `grep -rn "method: *'DELETE'" frontend` 无 `/api/config` |
| U09 | `GET /api/config/providers` `routes/config.py:166` | UNUSED-API | 71 行 | `grep -rn "config/providers" frontend tests` = 0；前端 provider 列表硬编码在各页面 | safe | 删端点；若前端要动态 provider 再另行接 | `grep -rn "providers" frontend` 确认 |
| U10 | `POST /api/configs/reorder` `routes/config.py:268` | UNUSED-API | 13 行 | `grep -rn "configs/reorder" frontend tests` = 0；`config_store.reorder` 唯一调用方就是它 | safe | 删端点 + `ReorderRequest` + `config_store.reorder` | 同上 |
| U11 | `GET /api/blocks/{block_id}/material-search` `routes/blocks.py:90` | UNUSED-API | 21 行 | `grep -rn "material-search" frontend tests` = 0；docstring 声称"两者都要保留"但前端从未接入；`services/material_search.py`（D07）只服务它 | needs-check（产品可能计划做"按需检索"） | 确认不做→删端点+D07+测试；要做→补前端入口 | 删后 `grep -rn "material-search" .` = 0 |
| U12 | `POST /api/blocks/{block_id}/ai` `routes/blocks.py:312` | UNUSED-API | 54 行 | 仅 `frontend/assets/api.js:211` 有封装且该封装从未被调用（`grep -rn "blocks.ai(" frontend` = 0）；`_AI_PROMPTS` + `AIActionRequest` 只服务它；功能与 `mode-editor.js` 的"AI 润色/扩写"是否有替代需产品确认 | needs-check | 确认无入口→删端点 + `_AI_PROMPTS` + `AIActionRequest` + `validate_action`，并删 api.js 封装 | `grep -rn "/ai\b" frontend/assets/mode-editor.js` 无该路径 |
| U13 | `GET /api/projects/{id}/split-answers` `routes/materials.py:180` | UNUSED-API | 8 行 | 只有 `api.workflow.getSplitAnswers` 封装，且该封装从未被调用（`grep -rn "getSplitAnswers(" frontend` = 0）；同路径的 POST 在用 | safe | 删 GET 端点 + api.js 封装 | `grep -rn "split-answers" frontend` 只剩 POST |
| U14 | `POST /api/projects/{id}/map-sources` `routes/materials.py:587` | UNUSED-API | 35 行 | 只有 `api.materials.mapSources` 封装且从未被调用；其内部 `retrieve_chunks`/`build_bm25_index`（`routes/materials.py:35`）也随之死 | safe | 删端点 + 两处 import + api.js 封装 | `grep -rn "mapSources(" frontend` = 0 |
| U15 | `POST /api/projects/{id}/lock` `routes/projects.py:177` | UNUSED-API | 28 行 | `grep -rn "/lock" frontend tests` = 0；`project_snapshots` 表只读查库 = **0 行**（该功能从未被真实调用）；api.js `projects.lock` 封装也未被调用 | safe | 删端点 + api.js 封装；`create_snapshot` 仍被 `export.py:171` 使用，保留 | `grep -rn "projects.lock(" frontend` = 0 |
| U16 | `PATCH /api/projects/{id}` `routes/projects.py:164` | UNUSED-API | 18 行 | `grep -rn "projects.patch(" frontend` = 0；前端 3 处 PATCH 分别是 materials/materials/packages；无测试 | needs-check（可能是"改项目名"预留） | 若产品确实需要改项目名，补 UI；否则删端点 + api.js 封装 + `ProjectPatchRequest` | `grep -rn "method: *['\"]PATCH" frontend` 不含 `/api/projects/\${pid}\`` |
| U17 | `POST /api/workflow/{thread_id}/analyze-points` `routes/workflow.py:352` | UNUSED-API | 20 行 | `grep -rn "analyze-points" frontend tests` = 0；"只拆评分点"的真实入口是 `workflow/start`/`packages/runs` 带 `points_only:true`（`mode-outline.js:6665`、`5005`）；它传给 `runner.redraft_outline(..., points_only=True)`，是全仓唯一 `points_only=True` 的调用 → runner 里该参数分支（`runner.py:722-723,799,811`）随之失效 | safe（先确认无外部脚本） | 删端点 + `runner.redraft_outline` 的 `points_only` 参数与 `_redraft` 中写入 config 的那一项；`graph`/`nodes` 的 config.points_only 保留（start 路径在用） | `grep -rn "points_only=True" backend` 只剩 agent 内部 |
| U18 | `GET /api/review/{thread_id}` `routes/review.py:19` | UNUSED-API | 34 行 | 仅 `api.review.get` 封装，从未被调用（`grep -rn "api.review\|review.get(" frontend` = 0）；`tests/routes/test_review.py:103` 是唯一调用方；`ReviewRepository` 本身在 `nodes.py:1047` 仍用于落库 | needs-check | 若评审页改从 workspace/SSE 拿数据（现状如此），删端点 + api.js 封装 + 路由文件；保留 Test 中的 repository 部分 | `grep -rn "api/review/" frontend` = 0 |
| U19 | `POST /api/blocks/{block_id}/studio/draft` `routes/chapter_studio.py:269` | UNUSED-API | 54 行 | 前端已改用多 Agent `writing-jobs`（`chapter-studio.mjs` 只调 `content-preview` / `writing-jobs`）；`grep -rn "studio/draft" frontend` = 0；只有 `tests/routes/test_chapter_studio.py:134,137` 在调；`DraftBody` 只为它存在 | needs-check（旧单发草稿 vs 新多 Agent 编制，确认不再回退） | 删端点 + `DraftBody` + 测试用例；`build_writing_prompt` 被 writing-jobs 复用，保留 | `grep -rn "studio/draft" .` = 0 |
| U20 | `GET /api/readme` `backend/main.py:339-347` | UNUSED-API | 9 行 | `grep -rn "api/readme" frontend tests docs` = 0；页面未展示 README | safe | 删路由函数 | `curl -s -o /dev/null -w '%{http_code}' localhost:8000/api/readme` → 404 |
| U21 | `POST /api/projects/{id}/outline` `routes/materials.py:574` | UNUSED-API（DEPRECATED） | 11 行 | 固定 410；调用方只余陈旧前端（L02）；无测试 | needs-check | 与 L02 同批前后端删除 | `grep -rn "projects/.*outline" frontend` = 0 |

### 1.5 DUPLICATE（重复 SQL / 重复逻辑）

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| P01 | `backend/domain/*/repository.py` 反向包装 `services/block_store.py` | DUPLICATE | 315 行 | 三仓方法体几乎全是 `from services.block_store import ...` 转调；`block_store` 同时被 routes/services/orchestrator 直接调用 → 同一 CRUD 两套门面（见 D04-D06） | needs-check | 与 L08 同批处理 | `grep -rn "from services.block_store" backend/domain` = 3 |
| P02 | `_split_for_scoring_evaluation`：`routes/materials.py:115-136` vs `backend/scripts/recut_old_materials.py:32-51` | DUPLICATE | 21 行 | 两份逐字同逻辑（4 行/片、步长 3）；recut 注释自认"与 routes/materials.py 中同名函数保持一致" | safe（随 L06 归档） | 抽到 `infra/parser` 或 `services` 单一实现，脚本引用之；或脚本归档时一并删 | `grep -rn "_split_for_scoring_evaluation" backend` = 1 处 |
| P03 | 项目存在性检查 `SELECT id FROM projects WHERE id = ?`：`routes/revisions.py:53`、`routes/packages.py:167`、`routes/packages.py:194` | DUPLICATE | 9 行 | 3 处手写同一检查；`block_store.get_project` 已有现成实现 | safe | 统一调用 `get_project`（顺带统一 404 文案） | `grep -rn "SELECT id FROM projects WHERE id" backend` = 0 |
| P04 | 标包最新 run 查询：`services/package_scope.py:20-25` vs `services/block_store.py:427-432` | DUPLICATE | 6 行 | `SELECT thread_id FROM workflow_runs WHERE project_id=? AND package_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1` 两处逐字相同；`package_scope.package_run_thread` 另包一层 `require_package` | safe | 让 `package_run_thread` 调 `block_store.current_run_thread_id(db, pid, package_id)` | 两处 SQL 只剩一处 |
| P05 | `split_answers` UPSERT：`services/page_budget.py:286-293` vs `services/split_answers.py:48-60` | DUPLICATE | 8 行 | `INSERT INTO split_answers (...) ON CONFLICT(project_id, thread_id, key) DO UPDATE` 逐字重复 | safe | page_budget 改调 `split_answers.set_answer` | 同上 |
| P06 | `split_answers` 读取：`routes/materials.py:707-715` vs `services/split_answers.py:31-46` | DUPLICATE | 9 行 | 同样 `SELECT key, value FROM split_answers WHERE project_id=? AND thread_id=?` + JSON 解析 | safe | 调 `split_answers.get_answers` | 同上 |
| P07 | `response_document_settings` 读取：`routes/chapter_studio.py:327` vs `routes/export.py:220-222` | DUPLICATE | 2 行 | 同一 `SELECT template ...` 两处 | safe | 抽小函数（如 `chapter_studio` 的 `document_template`） | `grep -rn "FROM response_document_settings" backend` = 1 |
| P08 | 最新快照查询：`routes/export.py:95-97` vs `routes/export.py:138-140` | DUPLICATE | 6 行 | 同文件内 `SELECT * FROM project_snapshots WHERE project_id=? AND package_id=? ORDER BY id DESC LIMIT 1` 两遍 | safe | 抽 `_latest_snapshot(db, pid, package_id)` | 同上 |
| P09 | `UPDATE blocks SET source = ? WHERE id = ?`：`domain/proposal/repository.py:76` vs `services/block_store.py:1082-1085` | DUPLICATE | 3 行 | 死仓 P01 内直接写 SQL，绕过 `block_store.update_block_source` | safe（随 D05） | 随 D05 删除 | — |
| P10 | `DELETE FROM material_chunks WHERE material_id = ?`：`services/block_store.py:173,195` vs `scripts/recut_old_materials.py:95` | DUPLICATE | 2 行 | 删除材料时清 chunks 的两处 SQL 与一次性脚本重复 | safe（随 L06） | 脚本改调 `block_store.delete_material` 或归档 | `grep -rn "DELETE FROM material_chunks" backend scripts` 收敛 |
| P11 | 同 block 进行中 job 查询：`routes/chapter_writing.py:91` vs `services/chapter_writing.py:33-37` | DUPLICATE | 4 行 | `SELECT id FROM chapter_writing_jobs WHERE block_id=? AND status IN ('queued','running')` 两处 | safe | 路由改调 service 内函数 | 同上 |
| P12 | 两套素材匹配实现（rerank vs assign） | DUPLICATE/LEGACY | ≈430 行 | `nodes.py:563` 的 `MATCH_MODE` 同时保留两条管线；`assign` 已默认、`rerank` 仅回滚 | product-decision | 见 L04 | 见 L04 |

### 1.6 JUNK（无效 import / 冗余分支 / 数据残留）

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| J01 | 未使用 import（12 个文件，32 个名字） | JUNK | ≈32 行 | AST 扫描"仅 import 行出现 1 次"并经人工确认：`routes/materials.py:16,19,35,36,687`（`StreamingResponse`/`create_block`/`delete_blocks_by_project`/`update_material_parsed`/`list_chunks_by_materials`/`assign_chunks_to_sections`/`format_sse_event`/`collect_parsed_points`）；`routes/config.py:31` `OPENAI_COMPATIBLE_PROVIDERS`；`routes/chapter_studio.py:11` `JSONResponse`；`routes/workflow.py:19` `Any`,`Optional`；`infra/parser/docx.py:10,17`（`re`,`_SPECIAL_MARK_RE`）；`infra/llm/dispatcher.py:13` `json`；`infra/retrieval/rerank.py:6` `json`；`agents/wang_anshi.py:12`、`agents/bao_zheng.py:13` `json`；`services/chapter_writing.py:8` `content_hash`；`services/scoring_map.py:54` `normalize_scope_name`；`orchestrator/runner.py:26` `Awaitable`；`orchestrator/nodes.py:31` `Finding`；`scripts/recut_old_materials.py:15` `os` | safe | 批量删（注意 `routes/materials.py` 的 `retrieve_chunks/build_bm25_index` 只在 U14 内用，删 U14 后一并清） | `python -m pyflakes backend`（需先装 pyflakes）或 `python -m compileall backend` + 人工 grep |
| J02 | `backend/data/aibidding.db`、`backend/data/app.db`（均 0 字节，2026-05/06）+ `ge_solution.db.bak-20261013-1742`(379MB)、`…-1751`(21MB) | JUNK | 磁盘 ≈400MB | `grep -rn "aibidding.db\|app.db" backend scripts docs` = 0；`git ls-files backend/data` 为空（未跟踪）；`db.py:21` 只用 `ge_solution.db` | safe | 删 2 个 0 字节库与 2 个 `.bak`（备份先移出仓库）；并在 `.dockerignore` 排除 `backend/data/`（与 `aux.md` §F2 同一条） | `ls backend/data/`；`du -sh backend/data` |
| J03 | `backend/routes/materials.py:663-664`（`if note: pass`） | JUNK | 2 行 | 空分支：`if note: pass / elif not chunk_n: ...`，等价于 `if not note and not chunk_n` | safe | 改成 `if not note and not chunk_n:` | 单测 `tests/...` 不受影响 |
| J04 | `backend/main.py:244-245`（`frontend_dir` 连续两次赋值） | JUNK | 1 行 | 244 `= os.path.join(...)`，245 `= os.path.normpath(frontend_dir)`；后者可并入前者 | safe | 合成一行 | — |
| J05 | `backend/services/page_budget.py:153-154`（`any(c is chapter for c in [])`） | JUNK | 2 行 | 恒假表达式 + 空 body（`pass`），位于死函数 `rollup_totals` 内 | safe（随 C14） | 随 C14 删除 | — |

### 1.7 无效配置 / 依赖

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| X01 | `requirements.txt:10-19`（缺 `jieba`、`rank_bm25`） | DEAD/CONFIG | 0 行声明 | `infra/retrieval/keyword.py:6-7` 顶层 `import jieba` / `from rank_bm25 import BM25Okapi`；被 `assign.py:36`、`locate.py:14` 引用，`assign`/`locate` 又被 `orchestrator/nodes.py:34`、`agents/zhang_heng.py:125` 引用 → 启动路径必import；`grep -n "jieba\|rank" requirements*.txt` = 0；`git log -S "jieba" -- requirements.txt` = 空（从未声明）；本地 `.venv` 有 jieba-0.42.1/rank_bm25-0.2.2；`Dockerfile:11` 只装 requirements.txt | **高（启动阻断）** | 在"文档解析/检索"段补 `jieba==0.42.1`、`rank-bm25==0.2.2`（或 `rank_bm25`，PyPI 名 `rank-bm25`）；`numpy` 可显式补一行（当前靠 rapidocr 传递依赖） | `python -m venv /tmp/v && /tmp/v/bin/pip install -r requirements.txt && PYTHONPATH=backend /tmp/v/bin/python -c "from infra.retrieval import keyword_search"`；或 `docker build .` |
| X02 | `backend/infra/llm/__init__.py:34-50`（`__all__`/导入缺 `dispatch_package_merge_json`） | BROKEN-CONFIG | 1 行 | `routes/materials.py:643` `from infra.llm import dispatch_package_merge_json`；该名字在 `infra/llm/__init__.py` 中 0 次（函数实际在 `dispatcher.py:1096`）；import 包在 try/except 里 → 永远静默降级；连带 `package_analysis.merge_with_llm/validate_merge_proposal`（`package_analysis.py:389-511`）与 `routes/materials.py` 的 `use_llm` 分支不可达 | **高（功能假死）** | 二选一：①在 `__init__.py` 导入并加入 `__all__`（推荐，恢复功能）；②删 `use_llm` 分支 + `merge_with_llm/validate_merge_proposal` + 6 个相关测试（若产品决定不做模型合并） | `PYTHONPATH=backend python -c "from infra.llm import dispatch_package_merge_json"` 现应报 ImportError；修复后通过 |
| X03 | `requirements.txt:26`（`aiofiles==24.1.0`） | DEAD-DEP | 1 行 | `grep -rn "aiofiles" backend tests scripts` = 0（全仓只有 requirements.txt 自身） | safe | 删该行 | `pip install -r requirements.txt` 后 `python -c "import aiofiles"` 非必需；确认无传递依赖需要显式 pin |
| X04 | `backend/data/*.db.bak-*`（见 J02） | JUNK-CONFIG | ≈400MB | 同上；未跟踪但会被 `Dockerfile:31 COPY backend/` 打进镜像（`aux.md` F2 已列） | safe | 移出仓库/删除 + `.dockerignore` 排除 | `docker build` 前 `du -sh`；`.dockerignore` grep |
| X05 | `backend/infra/llm/errors.py` 公开 API 中 test-only 项（见 C08） | JUNK-API | 9 行 | `reset_health` 只被 conftest 调用（测试卫生）、`is_retryable`/`unhealthy_snapshot` 无调用 | safe | 见 C08 | — |

### 1.8 测试仍在保护的死代码

| ID | 文件:行 | 类别 | 规模 | 证据 | 风险 | 建议动作 | 验证方式 |
|---|---|---|---:|---|---|---|---|
| T01 | `tests/domain/test_spec_repository.py:1-173` | DEAD-TEST | 173 行 | 全部围绕 `SpecRepository`（D04）；生产 0 引用 | safe | 随 D04 删 | `grep -rn "SpecRepository" tests` = 0 |
| T02 | `tests/domain/test_proposal_repository.py:1-274` | DEAD-TEST | 274 行 | 全部围绕 `ProposalRepository`（D05） | safe | 随 D05 删 | `grep -rn "ProposalRepository" tests` = 0 |
| T03 | `tests/domain/test_material_repository.py:1-225` | DEAD-TEST | 225 行 | 全部围绕 `MaterialRepository`（D06） | safe | 随 D06 删 | `grep -rn "MaterialRepository" tests` = 0 |
| T04 | `tests/services/test_material_search.py:1-81` | DEAD-TEST | 81 行 | 全部围绕 `services/material_search.py`（D07），后者只服务死端点 U11 | safe | 随 D07/U11 删 | `grep -rn "material_search" tests` = 0 |
| T05 | `tests/domain/test_letter_detector.py:32-36` | LEGACY-TEST | 5 行 | 专门断言"`from infra.llm.dispatcher import is_letter_section` 仍可用"——保护 L09 的 re-export | safe | 随 L09 删该用例 | `grep -rn "infra.llm.dispatcher import" tests` = 0 |
| T06 | `tests/routes/test_chapter_studio.py:134,137`（约 2 个用例） | DEAD-TEST | ≈15 行 | 唯一调用死端点 U19 `studio/draft` 的地方 | needs-check | 随 U19 删用例 | `grep -rn "studio/draft" tests` = 0 |
| T07 | `tests/routes/test_review.py:1-139` | DEAD-TEST（部分） | 139 行 | 围绕死端点 U18；其中 `ReviewRepository`/`WorkflowRunRepository` 集成断言仍有价值 → 需拆分保留 | needs-check | 保留 repository 用例，删 HTTP 端点用例 | `grep -rn "api/review/" tests` = 0 |
| T08 | `tests/infra/test_outline_builder.py`（`render_outline_text` 用例）+ `tests/domain/test_structure.py`（`subtree_range` 用例）+ `tests/infra/test_review_points.py:211`（`total_points` 用例） | DEAD-TEST（局部） | ≈30 行 | 分别保护 C10/C11 的死符号 | safe | 删对应用例 | 对应 grep = 0 |
| T09 | `tests/infra/test_llm_errors.py`（`is_retryable`/`unhealthy_snapshot` 段）+ `tests/services/test_config_store.py`（`save_all` 用例）+ `tests/orchestrator/test_state.py`（`merge_state` 用例） | DEAD-TEST（局部） | ≈80 行 | 保护 C08/C02/C16 | needs-check | 随对应符号一起删 | 对应 grep = 0 |
| T10 | `tests/infra/test_llm_dispatcher.py:86-126,227-248`（rerank 路径 + doc_summary spy）、`tests/agents/test_shen_kuo.py`(332)、`tests/agents/test_zhang_heng.py:73-90` | LEGACY-TEST | ≈200 行 | 保护 L04（rerank 回滚路径）与 L05（已停用的 doc_summary）；`test_nodes.py:225-231` 用 monkeypatch 强制 `MATCH_MODE="rerank"` | product-decision | 随 L04/L05 决策同批处理 | `grep -rn "MATCH_MODE\|llm_rerank\|dispatch_doc_summary" tests` = 0（若决定删） |

---

## 2. 逐项详述

> 本节只展开"为什么"，清单里的证据命令不重复。行号均对应工作区真实文件（只读核对）。

### 2.1 死模块

**D01 `routes/upload.py`（292 行）**
这是最早的"上传→异步解析→SSE 推目录"两步式管线：`POST /api/upload` 收文件只返回 `parse_job_id`，`GET /api/parse/stream/{id}` 再起线程跑 `infra/parser.parse_document`、经 `asyncio.Queue` 把 `parse_start/progress/section/done` 推给前端。2026-06 之后材料统一走 `POST /api/projects/{id}/materials`（落盘 + `_parse_and_chunk_in_background` 写 `material_chunks`），目录由 LangGraph `zhang_heng_outline_draft` 派生，旧管线彻底失配：它产出的 `sections` 没有任何消费方，也没有 `material_chunks`。`docs/design/方案A-标包工作空间-实施规划.md:550` 已把"下线旧 /api/upload 管线"写进规划。补充缺陷：`parse_stream` 每次新连接都会把 `job.event_queue` 覆盖（upload.py:181-182），生产者 `asyncio.run_coroutine_threadsafe(job.event_queue.put(...))` 拿到的是"最后连上来的那个 queue"，多客户端/断线重连时事件会串到错误连接（2026-10-15 报告 P2-3 已指出，当前代码未改）。删除即可，无外部脚本/文档依赖。

**D02 `routes/changelog.py`（71 行）**
`GET /api/changelog` 在运行时 `subprocess.run(["git","log",...])` 现场生成版本列表，版本号算法是"最旧 commit = v1.0 依次递增"。仓库内无任何前端/测试/文档引用；最后提交 2026-05-13，是 13 个 router 里最老、改动最少的一个。它还把部署形态绑死在"运行目录必须有 .git"上（Docker 镜像里没有 `.git`，该端点只会 503）。删除。

**D03 `utils/validators.py`（11 行）**
只有 `validate_uuid`，唯一调用点是 D01 的 `parse_stream`（校验 job_id 必须是 UUID4）。随 D01 死。

**D04-D06 `domain/{spec,proposal,material}/repository.py`（120/84/111 行）**
名义是领域聚合的持久化层，实际三个类都**反向依赖 `services.block_store`**（文件里的 import 就是证据），方法体基本是"转调 block_store + 数据类转换"。生产代码（`routes/`、`services/`、`agents/`、`orchestrator/`、`scripts/`）对三个类**零引用**，唯一消费者是各自的测试。`domain/review/repository.py` 是例外（`ReviewRepository`/`WorkflowRunRepository` 真的在用），说明这套分层并没有全线废弃，而是"只接了 review 一个仓"。三个仓在 `__init__.py` 里被转出，造成"看起来是公共 API"的假象。删除前需与团队确认 Phase 7（`proposal/repository.py:23`、`material/repository.py:27` 注释）是否还要继续：继续做就应尽快接线，否则删（连带 672 行测试）。

**D07 `services/material_search.py`（148 行）**
"按需检索素材"实现（jieba 分词 + BM25 + 标题/要点加权），只为 `GET /api/blocks/{id}/material-search` 服务。该端点 docstring 写"两者都要保留（matched-sources 与本接口）"，但前端从未接入，唯一消费者是 `tests/services/test_material_search.py`。属于"做了一半的功能"：要么补前端入口，要么删。

### 2.2 死代码

- **C01 SSE 辅助函数**：`sse_token/section_start/section_done/all_done/doc_summary/section_skip` 是更早的"逐节生成正文"（`/api/generate` 时代）遗留的事件封装，当时用它们推 token 流；现在正文走 LangGraph 的 `orchestrator/events.py`（独立 SSE 格式），这 6 个函数再没有任何调用。`format_sse_event` 仍是 materials/blocks/events 的公共实现，保留。
- **C02 `config_store` 旧方法**：`get/save/clear` 是"单配置"时代的 API，`reorder/get_next_index/save_all` 是"多配置"时代写完却没有接 UI 的 API。删除顺序必须是"先删旧路由 U05-U10，再删方法"，否则会断 import。
- **C06 `_index_path`**：`main.py` 定义后从未使用，`/` 实际返回 `_projects_path`（`main.py:281`）。
- **C07 `_route_after_generate`**：generate 后的条件路由最终采用内联闭包 `_generate_fanout`（支持 fan-out 列表），旧单目标路由函数被留在文件里，注释也还写着"generate 节点后的条件路由"。
- **C08 errors 三函数**：`is_retryable` 被 `note_failure` 的分类逻辑取代（`note_failure` 用 `blocks_chain`）；`unhealthy_snapshot` 是诊断用但无人调用；`reset_health` 只服务测试。`errors.py` 的 `__all__` 仍把它们列为公开 API，建议一并修正。
- **C10 outline_builder 四符号**：`render_outline_text` 是"给用户看/给模型做润色输入"的展示函数，但目录文本化最终由前端渲染，模型润色走 `dispatch_outline_draft_json` 的结构化 payload；`match_spec_nodes` 是把规范书节点匹配到评分点的实验函数，最终被 `build_outline` 内部的 `_resolve_target`/`spec_topic_sections` 取代；两个正则/常量随之无引用。
- **C11 review_points 三符号**：`review_points_of` 自称"薄封装，便于下游与测试共用"，实际只有测试用；`total_points` 是展示用汇总，界面改由 `scoring_map` 算；`_SCOPE_LINE_RE` 被 `_SCOPE_IN_LINE_RE`/`_SCOPE_NAME_RE` 取代。
- **C13-C15**：`list_materials_by_ids`（spec_loader 自己写了等价 SQL）、`rollup_totals`/`_parent_key`（篇幅分配最终用 `rollup_pages`/`allocation_targets`）、`get_sink`/`uninstall`（用法记账只装不卸）。
- **C16 `merge_state`**：docstring 自称"供 routes / 测试使用"，实际只有测试。LangGraph 用 `Annotated[..., _merge_dict]` reducer，节点返回 patch 由框架合并，不需要这个 helper。删除时注意 `orchestrator/state.py:241` 的 `__all__`。
- **C17 review 仓三方法**：`clear_thread`（重跑前清评审）、`list_findings_by_block`（按 block 查）、`latest_for_scope`（按标包取最新）都没有生产调用；当前评审落库/读取走 `nodes.py:1047` 的 `save*`/`list_findings_by_thread`。

### 2.3 老旧实现

- **L02 为什么不能直接删 410 端点**：`mode-editor.js` 的 `startWorkbenchExtraction()` 会 POST 该路径，并期待一个"SSE 逐节写 blocks"的旧协议；收到 410 后代码只判 `!resp.ok` 就 throw，用户看到"准备请求失败"。这个入口的可见条件是 `materials` 里存在 `role==='requirement'` 的文件——新上传已改用 `main_rfp`，所以当前库（4 份 main_rfp + 4 份 spec）不会显示。正确清理是前后端同批：删端点 + 删 `mode-editor.js` 的这段 + `three-mode.html` 的 `#extract-action-bar`/`#btn-start-extract`（或改造成新流程入口）。
- **L03 旧单配置 API**：`POST/GET/DELETE /config`、`/config/check`、`/config/providers` 是 2026-05 的"一个 API Key"设计；新界面（projects/project-init/workbench/mode-outline/review.js/chapter-team.mjs）全部调 `/api/configs*` 多配置 CRUD。旧接口的 `POST /config` 语义是"清空全部、只留一条"，与现在"轮询多条配置"的产品语义冲突，保留只会被误用。
- **L04 旧匹配**：`assign`（规则 + BM25 反向分配，`infra/retrieval/assign.py` 490 行）是当前默认；`rerank`（逐章 BM25 top-k 再让 LLM 打分）调用次数 O(章节数)、只留评语不留原文，已不是产品要的"依据带原文/出处"。节点里两条路径并存，且 `main.py` 每次都构造 `ShenKuoAgent`（其 `match` 只在 rerank 分支用）。若决定删，`infra/retrieval/rerank.py` 会连带死（它只被 `retrieval/__init__` 转出给 shen_kuo 用）。
- **L05 doc_summary**：代码注释把原因（推理模型 max_tokens 被思考耗尽）说得很清楚，但"停用"的方式是留一整条 `dispatch_doc_summary` + `_generate_doc_summary`（含 prompt 组装、fallback 循环）在库里，且 `doc_summary` 字段从 state 到 prompts 全链路保留。要么恢复，要么一次性删干净（涉及 `state.py:35`、`nodes.py:241,307`、`graph.py:129`、`prompts.py:354,387`、`dispatcher.py:191,289,350,387,535`、`blocks.py:236,246`、`zhang_heng.py:42,392,405,472`）。建议产品决策后再动，否则半删会留下空字段。
- **L07 启动期回填**：这类代码的问题是"每次启动都在跑一次性逻辑"，且失败只 warning 不影响启动。① 名称清洗已无事可做（只读查库 0 行脏值）；② display_name 回填依赖 `services.package_analysis.name_from_quotes`，启动路径反向依赖服务层（又是分层倒挂）；③ package_id 回填对 41 条 `target/package_no` 为空的老 run 永远无效，这些 run 的 `package_id` 会一直 NULL，前端按标包过滤时看不到它们。真正要做的可能是一次"老 run 归属推断"数据工程，而不是启动 SQL。
- **L08 domain 过渡层**：见 D04-D06 与 P01。`block_store.py` 1323 行同时承担 projects/materials/chunks/blocks/revisions/snapshots 六类 CRUD + `carry_over_by_stable_key` 迁移 + `apply_outline_structure` 修复，是当前最主要的"God module"；domain 仓本来是为了拆它，但没接线。两条路必须选一条，继续"两套并存"只会让改动成本翻倍。
- **L09 letter re-export**：`domain/letter_detector.py` 才是定义，`dispatcher.py:1054` 的 re-export 注释自己写了"Phase 7 一并清理"。验证性测试（`test_letter_detector.py:32-36`）是唯一原因它还"活着"——这是典型的"测试保护死代码"。
- **L10 `requirement` 角色**：`main.py:51` 注释已承认是早期"主招标"写法。当前库 0 行，但 `ALLOWED_ROLES`/`REQUIREMENT_ROLES` 还允许新上传写入（前端角色选择里没有它，所以只可能来自 API 调用）。建议下一版本做一次 UPDATE 迁移后从常量删除。
- **L11 config 旧格式**：`_load_from_file` 兼容 `dict` 老格式，成本 3 行，风险是删了会让未升级的机器启动丢配置。建议保留到确认所有环境都迁移过（本地配置文件内容检查即可）。

### 2.4 重复冗余

重复的根因是"路由直接写 SQL"：`routes/chapter_studio.py` 35 处 `execute`、`routes/packages.py` 27 处、`routes/materials.py` 13 处，绕过了 `block_store` 这层（`block_store` 自己也有 67 处）。上面 P03-P11 是"逐字相同"的实证；更值得治理的是**同类查询的两套写法**，例如：
- 取项目：`get_project`（block_store）vs 3 处手写 `SELECT id FROM projects WHERE id=?`；
- 取最新 run：`current_run_thread_id` vs `package_scope.package_run_thread` vs `response_drafts/projects` 里的手写查询；
- 存 answers：`split_answers.set_answer` vs `page_budget` 里手写 UPSERT。

建议做法不是"把 SQL 全塞进 block_store"（它已经太大），而是按用例拆小服务（见 2026-10-15 报告"架构评价"），本轮清理先把逐字重复收敛掉。

### 2.5 无效配置/依赖

- **X01 是本次审计最高风险项**：`requirements.txt` 是全新部署（`start.sh:89`、`Dockerfile:11`）的唯一依赖来源，而 `infra/retrieval/keyword.py` 在模块顶层 `import jieba/rank_bm25`。启动链：`main.py` → `routes.*` → `orchestrator.graph/nodes` → `infra.retrieval.assign` → `keyword`。也就是说**镜像能 build 成功、容器启动时 import 就崩**（`ModuleNotFoundError: No module named 'jieba'`）。本地能跑只是因为 `.venv` 里早先手工装过。修复只需两行，但必须先于任何清理动作做（否则"删代码后测试跑不起来"会误判）。
- **X02 是"功能假死"**：`try/except` 把 ImportError 吞成 info 日志，用户永远看不到"模型合并不可用"，只会觉得结果和规则版一样。验证方式：直接 `python -c "from infra.llm import dispatch_package_merge_json"` 会 ImportError；`/package-analysis?use_llm=1` 的行为与 `use_llm=0` 完全相同。
- **X03 `aiofiles`**：requirements 里唯一零引用依赖。它常见于"早期用 aiofiles 读写文件"的历史，现已改用同步 `open()`/`Path.write_bytes`（`routes/materials.py:417`、`main.py:119`）。删除安全。
- **X05 test-only 公开 API**：`reset_health` 等不删也无害，但 `__all__` 列的公开 API 与真实使用不符，建议在 docstring 标"test-only"或移入测试 helper。

### 2.6 测试保护死代码

结论：**有 4 个测试文件整体在为死模块打工（753 行），另有 6 个文件里的局部用例在保护死符号**。这不是测试写错了，而是"先写测试、后来接线改了方向"的沉淀：`test_spec_repository.py` 等都在测 `domain` 仓的 round-trip，但生产从未改用它们。清理顺序应是"先删实现，再删测试"（反过来会先绿后红）。注意 `test_routes/test_review.py`、`test_llm_errors.py` 里既有死代码用例也有活代码用例，必须拆着删。

---

## 3. 已核实「看起来像死代码但其实活着」的排除项

| 模块/符号 | 为什么看起来像死的 | 实际引用（证据） | 结论 |
|---|---|---|---|
| `backend/scripts/normalize_extract_fields.py` 里的 `domain.spec.field_normalize.normalize_row_fields` | 脚本是一次性的，容易被当成整个依赖都死 | `agents/zhang_heng.py:18` 生产导入并在 `draft_outline` 里用；`tests/domain/test_field_normalize.py`(233 行) 覆盖 | 生产活 |
| `agents/subpoints.py` | 名字生僻，且只在 zhang_heng 的函数内 import | `agents/zhang_heng.py:676` `from agents.subpoints import extract_points`；`infra/llm/dispatcher.py:461` `dispatch_subpoints_json` 配套；`tests/agents/test_subpoints.py` | 生产活 |
| `agents/chapter_team.py`（含 `Plan/TextPiece/...` 一批"零外部引用"类） | 类名只在文件内部用，像私有实现 | `services/chapter_writing.py:9` 导入 `ChapterChiefAgent/SpecialistAgent/assemble/review_chapter/ROLES/STREAM_SINK`；`routes/chapter_writing.py:7` 导入 `ROLES`；`main.py:190` `recover_jobs` | 生产活（多 Agent 编写主线） |
| `infra/llm/_mock.py` | 模块 import 引用只有 `clients.py` 的 `from . import _mock`，外部符号引用 0 | `clients.py:152-154` 的 `_is_mock_mode()` 在 6 处短路；`LLM_MODE=mock` 是设计好的测试/演示模式，`tests/infra/test_llm_usage.py:218` 使用 | 活（测试/演示模式） |
| `infra/parser/_ocr.py` | 名字以下划线开头、被 pdf/docx 函数内 import | `infra/parser/docx.py:44`、`pdf.py` 多处 `_ocr_image`；OCR 是扫描件兜底路径 | 生产活 |
| `infra/retrieval/rerank.py` | 只被 `retrieval/__init__.py` 转出，看起来无引用 | `agents/shen_kuo.py:18` `from infra.retrieval import ... llm_rerank`；但该路径受 `MATCH_MODE` 控制默认不可达 | **活但属于回滚路径**（L04） |
| `infra/docx/diagrams.py` | 只有 30 行，像示例 | `infra/docx/document_model.py:8` 的 `_diagram` 使用 | 生产活 |
| `infra/docx/samples.py` / `template_store.py` | 只被 chapter_studio 引用 | `routes/chapter_studio.py:310-313` 样张预览；`template_store` 被导出链路使用 | 生产活 |
| `utils/sse.py` 剩余部分（`format_sse_event`/`sse_parse_*`/`sse_error`） | 与死函数同文件 | `format_sse_event` 被 materials/blocks/events 使用；`sse_parse_*` 只被 D01 使用 | 部分活（删 D01 后按 C01 处理） |
| `services/scope_backfill.py` | 名字像"一次性回填脚本" | `routes/materials.py:240` 在读 split-report 时自愈调用；`tests/services/test_scope_backfill.py` | 生产活（设计为读时自愈） |
| `services/run_recovery.py` | 名字像迁移脚本 | `main.py:161` 启动对账调用 | 生产活 |
| `services/point_sync.py` / `point_store.py` / `material_integrity.py` / `package_scope.py` / `response_drafts.py` | 都是"工具型"命名 | 分别在 `routes/materials.py:686,240,762,142`、`routes/revisions.py:59`、`routes/projects.py:217`、`agents/zhang_heng.py:704`、`routes/export.py:91` 有真实调用 | 全部生产活 |
| `domain/letter_detector.py` | 从 dispatcher 迁出后只剩 re-export 痕迹 | `agents/zhuge_liang.py:22`、`infra/llm/__init__.py:19` 直接从 domain 导入 | 生产活（仅 re-export 是死的 C09→L09） |
| `domain/review/repository.py` | 与三套死仓同目录 | `routes/review.py:23`、`routes/projects.py:20`、`routes/materials.py:166`、`orchestrator/runner.py:31`、`nodes.py:1047` 真用 | 生产活（仅 3 个方法死 C17） |
| `infra/parser/review_points.py` / `outline_builder.py` / `tech_requirements.py` / `scope_index.py` / `segment.py` | 大文件里大量私有函数"零外部引用" | 分别是 `build_outline`/`extract_review_points`/`parse_target_tokens`/`extract_category` 的实现体，被 zhang_heng/scoring_map/split_report 调用；私有函数是内部调用 | 生产活（仅零星常量/函数死） |
| `orchestrator/events.py` 22 个事件构造器 | 每个都可能"没被调用" | 22 个全部在 nodes/runner 有调用（脚本核验 0 遗漏） | 全活 |
| `backend/scripts/*.py` 的 `sys.path.insert` 自举 | 像旧式脚本 | 是"从任意 cwd 直接跑"的必要自举，非遗留 | 保留 |

---

## 4. 需要产品 / 数据决策的项

| 决策项 | 关联 ID | 需要谁决定 / 现状证据 | 建议 |
|---|---|---|---|
| 旧匹配 rerank 管线是否彻底下线 | L04 / P12 / T10 / `main.py:138` | 产品：是否还需要"LLM 重排评语"作为回滚或对照；代码注释明确"保留用于回滚/对照" | 若不保留→删 ~430 行 + 约 560 行测试；若保留→改成环境变量开关并在 README 记录回滚 SOP（避免"看不懂为什么有这段死代码"） |
| `doc_summary` 恢复还是删除 | L05 / T10 | 产品：项目概述对目录/正文是否还有价值；当前恒空 | 恢复需换非推理模型/给足 max_tokens 并加真模型测试；否则一次性删干净（涉及 10+ 文件），不要保留半条链路 |
| 一次性数据脚本是否还需重跑 | L06 | 数据：线上是否还有"旧口径提炼"或"单 chunk 评分表"的项目 | 若数据已清理→归档删除；若还有老项目→保留但加 `--dry-run`/README，并抽掉重复的切片函数（P02） |
| 启动期一次性回填的去留 | L07 / X04 | 数据：`workflow_runs.package_id IS NULL` 41/50；`packages.display_name` 已干净 | ①直接删；②改用"读时补"（像 scope_backfill）或真实迁移脚本一次性跑完；③老 run 归属需要数据工程决策 |
| domain 三仓是否继续 Phase 7 | D04-D06 / L08 / P01 | 架构：团队是否还打算把存储层从 `services` 迁到 `domain` | 继续→排期接线；放弃→删仓+删测试，block_store 短期维持但禁止再膨胀 |
| `material-search`（按需检索）是否上产品 | D07 / U11 / T04 | 产品：docstring 说"两者都要保留"，但前端从未接入 | 上→补 UI 与测试；不上→删 148 行 + 81 行测试 |
| `studio/draft`（单发草稿）是否保留 | U19 / T06 | 产品：已有多 Agent `writing-jobs`；单发草稿是更快/更便宜的备选吗 | 若不再用→删端点+测试；若保留→补前端入口，否则就是死 API |
| `/api/review/{thread}` 是否是外部集成接口 | U18 / T07 | 产品/集成：`api.js` 有封装但无调用；评审页现在直接读 workspace | 无外部集成→删；有→保留并标注 |
| `requirement` 角色兼容期 | L10 | 数据：当前 0 行；老客户项目是否可能还有 | 做一次 UPDATE 迁移后删常量；或保留到下一个大版本 |
| 旧 JSON 配置格式 | L11 | 部署：各环境 `.api_config.json` 是否已是 list | 确认后删 3 行 |

---

## 5. 建议清理顺序与每步验证命令

> 以下命令**均未执行**，供实施者按序使用；每步应独立提交、独立回归。

**前置（必须最先做，否则后续回归会受干扰）**
```bash
# P0-1 补声明依赖（否则全新环境无法启动，任何清理都无法验证）
#   编辑 requirements.txt：新增 jieba==0.42.1 与 rank-bm25==0.2.2
#   验证（干净环境）：
cd /Users/jianghe/projects/ge-solution
python3 -m venv /tmp/ge-audit-venv
/tmp/ge-audit-venv/bin/pip install -q -r requirements.txt
PYTHONPATH=backend /tmp/ge-audit-venv/bin/python -c "import main; print('import ok')"

# P0-2 修复 infra.llm 漏转出（或决定删除 use_llm 分支）
#   编辑 backend/infra/llm/__init__.py 导入 dispatch_package_merge_json 并加入 __all__
PYTHONPATH=backend python3 -c "from infra.llm import dispatch_package_merge_json; print('ok')"
```

**第 1 步：删旧上传管线（D01/D03/C01/L01/U01-U03）**
```bash
git rm backend/routes/upload.py backend/utils/validators.py
# 编辑 backend/main.py：删 18 行 import、227 行 include_router
# 编辑 backend/utils/sse.py：删 sse_token/section_*/all_done/doc_summary/section_skip + parse_* + error（仅 upload 使用）
cd backend && python3 -m compileall -q . && cd ..
grep -rn "upload_router\|routes.upload\|validate_uuid\|sse_parse" backend tests scripts   # 期望 0
python3 -m pytest -q
```

**第 2 步：删 changelog + 死端点（D02/U04/U11/U12/U13/U14/U15/U17/U18/U19/U20/U21）**
```bash
git rm backend/routes/changelog.py
# 逐项按 §1.4 修改对应文件；每个端点删完立刻:
cd backend && python3 -m compileall -q . && cd ..
python3 -m pytest -q tests/routes -q
grep -rn "material-search\|/api/blocks/.*ai\|analyze-points\|api/review\|studio/draft\|map-sources\|projects/.*/lock" backend frontend tests
```

**第 3 步：删 domain 死仓（D04-D06/P01/C17/C18/L08/T01-T03）**
```bash
git rm backend/domain/spec/repository.py backend/domain/proposal/repository.py backend/domain/material/repository.py
git rm tests/domain/test_spec_repository.py tests/domain/test_proposal_repository.py tests/domain/test_material_repository.py
# 编辑三个 __init__.py 移除转出；删 ReviewRepository 的 3 个死方法
grep -rn "SpecRepository\|ProposalRepository\|MaterialRepository" backend tests   # 期望 0
python3 -m pytest -q tests/domain
```

**第 4 步：删旧配置 API（L03/U05-U10/C02）**
```bash
# 编辑 routes/config.py：删 99-236 与 268-280；删 ReorderRequest
# 编辑 services/config_store.py：删 get/save/clear/reorder/get_next_index/save_all
grep -rn "config_store.get()\|config_store.save\|config_store.clear\|get_next_index\|save_all\|ReorderRequest" backend tests
python3 -m pytest -q tests/services tests/routes
```

**第 5 步：删活文件内零引用符号（C06/C07/C08/C09/C10/C11/C12/C13/C14/C15/C16 + J01/J03/J04/J05）**
```bash
# 逐文件按 §1.2 删除；最后统一检查
python3 -m compileall -q backend
grep -rn "get_sink\|list_materials_by_ids\|rollup_totals\|match_spec_nodes\|render_outline_text\|merge_state\|_route_after_generate" backend
python3 -m pytest -q
# 建议同时装 pyflakes 做未使用 import 审计：
python3 -m pip install -q pyflakes && python3 -m pyflakes backend
```

**第 6 步：旧匹配 rerank 与 doc_summary（L04/L05/T10，需产品决策后执行）**
```bash
# 若决定删除：删 agents/shen_kuo.py、infra/retrieval/rerank.py 的 llm_rerank/Match（Match 若被
#   domain/material/models 取代需迁移引用）、nodes.py rerank 分支、GraphDeps.shen_kuo、main.py:138
grep -rn "ShenKuoAgent\|llm_rerank\|MATCH_MODE" backend tests   # 期望收敛到 0/1
python3 -m pytest -q tests/agents tests/orchestrator
```

**第 7 步：脚本归档 + 数据清理（L06/L07/J02/X03/X04）**
```bash
# 备份后清理数据目录（不经 git，因未跟踪）：
mkdir -p /tmp/ge-audit-backup && mv backend/data/*.db.bak-* /tmp/ge-audit-backup/
rm backend/data/aibidding.db backend/data/app.db
# 删 requirements.txt:26 aiofiles；删 db.py:388-398 名称清洗块（确认后）
git status --short
python3 -m pytest -q
```

**最终门禁**
```bash
cd /Users/jianghe/projects/ge-solution
python3 -m compileall -q backend scripts tests
python3 -m pytest -q
grep -rn "TODO\|FIXME\|DEPRECATED\|deprecated\|向后兼容\|旧接口\|Phase 7\|一次性脚本" backend | wc -l   # 目标：显著低于当前 87
git diff --check
```

---

## 6. 附：与 `docs/reviews/2026-10-15-code-review-report.md` 的对照（本轮实测）

| 历史项 | 当前状态（本轮核实） | 证据 |
|---|---|---|
| P1-1 主招标/规范书不生成正文切片 | **已修复**：所有材料都进 `_parse_and_chunk_in_background`（`routes/materials.py:431-440` 创建任务；注释明确"不能再把这两类文件直接标成 done"） | 读代码 + `materials.py:422-446`（`_parse_and_chunk_in_background` 对所有角色切片，scoring/evaluation 走细粒度、其余走章节切片） |
| P1-2 project_id/thread_id 未校验归属 | **部分仍在**：`routes/materials.py:147-177` 仍按 payload 的 `threadId` 写 `workflow_runs` scope，未校验归属 | `set_project_scope` 逻辑 |
| P1-3 前端非 2xx 当成功 | 部分修复（`api.js` 已有 `parseJsonResponse`/`workflowAction`），但仍有 `fetch(...).then(r=>r.json())` 直调（如 `mode-editor.js` 多处） | 前端项，交 `frontend.md` |
| P1-4 外键未启用 | **仍在**：`db.py` 无 `PRAGMA foreign_keys=ON` | `grep -n "foreign_keys" backend/db.py` = 0 |
| P1-5 测试 checkpointer 写真实库 | 未在本轮范围复核（测试基础设施，交 Lead/测试负责人） | — |
| P1-6 clear_data.py 不是全清 | **已修复**：现覆盖 18 张表 + checkpointer 表 + uploads（`scripts/clear_data.py:21-42`） | 读代码 |
| P2-2 旧页面约一万行重复 | 后端侧：`/project-init`、`/workbench` 已 307 跳新页（`main.py:301-306,316-321`），但 `frontend/workbench.html`、`project-init.html` 文件仍在且含完整实现 | 交 `frontend.md` |
| P2-3 旧上传 API 无调用 + SSE 抢断 | **仍在，且升级为整模块死代码**（D01/L01） | 本报告 §1.1 |
| P2-4 WorkflowRunner `_runs` 不回收 | **仍在**：`_runs` 只有赋值/读取，无 `pop`/`del`；`has_run()` 对历史线程永远 True | `grep -n "_runs" backend/orchestrator/runner.py`（无 pop） |
| P2-6 新旧路由契约漂移 | **仍在**：`/api/projects/{id}/outline` 已 410 但 `mode-editor.js` 仍在调（L02） | 本报告 L02 |
| P2-7 迁移缺版本与事务 | **仍在**：`init_db()` 237 行、无 schema_migrations | `db.py:318-555` |
| P2-8 高频查询缺组合索引 | **部分修复**：`_DDL` 增加了 `idx_*_run`/`idx_blocks_project` 等；报告点名的 `material_chunks(material_id,chunk_index)`、`material_packages(project_id,material_id)`、`workflow_runs(project_id,target,package_no,created_at)`、`spec_requirements(project_id,run_thread_id,order_idx)` **仍无组合索引**；`review_points` 只有 `(run_thread_id)` 单列 | `grep -n "CREATE INDEX" backend/db.py` |
| 报告"架构评价"：domain 反向依赖 services、block_store God module | **仍在**（L08/P01） | 本报告 §1.3 |
| 报告"已知不立项"：零 block 时空集判为收敛 | 未变化 | — |

> 说明：P1-1/P1-6 两项历史 P1 已确认修复，值得在复审结论里更新；其余结构性问题（外键、迁移版本、_runs 回收、契约漂移）当前仍成立，与本审计发现的死代码清理可以合并成一条"后端结构整改"主线。

---

*报告完。本文件是本次审计唯一写入；所有判定均可用文中命令复现，行号对应当前工作区真实文件。*
