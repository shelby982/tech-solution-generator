# 后端 B 审计（infra/编排/agents/domain/scripts/tests）

- 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`。**审计基线**：`a91b1e9`（2026-11-09 01:15）+ 工作区 4 个后端改动；**审计期间**这些改动被作者提交为 `299073c`（2026-11-09 01:31，7 个文件），故当前 HEAD = `299073c`。本报告结论在两种状态下均已复核（见 0.5）。
- 审计范围：`backend/infra/`、`backend/orchestrator/`、`backend/agents/`、`backend/domain/`、`backend/utils/`、`backend/scripts/`、`backend/skills/diagram_design/`、根 `scripts/`、`tests/**/*.py`（124 个测试文件）。**不含** `backend/routes/`、`backend/services/`（除「被引用/被调用」这一事实性交叉引用）。
- 只读审计：本次未修改、未删除、未 checkout/stash/reset 任何项目文件；未启动服务；数据库仅以 `?mode=ro` 打开。唯一写入是本文件。

> **前提修正（重要）**：任务简报称「工作区有 3 个未提交后端修改」，实际 `git status` 为 **4 个**——`backend/db.py` 也在工作区中改动（+11 行，新增 `block_purged_items` 表与索引，属于同一次「占位符净化留痕」改动）。审计进行到约 01:31 时，作者把该改动连同 `agents/prompts.py`、两个测试文件一起提交为 `299073c`；我在提交前后各复核了一次引用计数，本报告引用的结构性问题（`_drop_empty_sections` 零调用、SSE 两处双跑、`block_purged_items` 写入）在 `299073c` 上**同样成立**。
>
> 提交 `299073c` 的 message 本身还印证了本报告 D 类的一个历史事实：`placeholder_purge.purge` 在本次接线前「全项目 0 处调用」——即「写好了没人调」的死代码模式在本仓库已至少复发一次。

---

## 0 摘要

### 0.1 数量总览

| 类别 | 条目数 | 规模（生产代码行） |
|---|---|---|
| **死代码**（零引用模块/函数/常量/类、不可达分支、一次性脚本、测试养死代码） | 26 | ≈ 980 行 + 一次性脚本 561 行 + 死代码测试 ≈ 1,115 行 |
| **无用代码**（未使用 import、无消费方 re-export、重复执行、仅为旧路由续命） | 8 | ≈ 45 行 + 13 处多余 import |
| **与当前逻辑不一致**（硬编码开关、被取代但保留、依赖与 import 不符、启动期一次性迁移、测试断言旧契约） | 10 | ≈ 900 行（旧上传管线 333 + 不可达 rerank ≈180 + doc_summary 链路 ≈110 + 启动期迁移 ≈110） |
| **合计** | **44** | — |

严重度分布：**高 2 / 中 12 / 低 30**。

### 0.2 三条结论（按风险排序）

1. **容器起不来，且这一项 3 周前已被点名、至今未修。** `requirements.txt` 仍缺 `jieba`、`rank_bm25`，而 `backend/infra/retrieval/keyword.py:6-7` 在**模块顶层** import 它们；该模块经 `routes/materials.py:35` → `main.py:27` 进入启动路径，`Dockerfile:11-13` 只装 requirements.txt。我用 import 钩子做了可控复现：屏蔽 `jieba`/`rank_bm25` 后 `import routes.materials` 直接 `ModuleNotFoundError`（§2.1）。Docker（`Dockerfile:50`）、Railway（`railway.toml:6`）、Render（`render.yaml:4`）全部走这个镜像，等于三个部署面同时启动失败。
2. **标包「模型合并」整条链路仍是假死。** `routes/materials.py:743` 写 `from infra.llm import dispatch_package_merge_json`，但 `backend/infra/llm/__init__.py` 仍未转出该名字；异常被 `except Exception` 吞成一条 `logger.info`，`?use_llm=1` 与 `use_llm=0` 行为完全相同（§2.2）。函数自 2026-10-15 引入，三周无人发现。
3. **匹配阶段的「回滚开关」是假的。** `nodes.py:563` 把 `MATCH_MODE` 硬编码为 `"assign"`，`nodes.py:648` 的 `if MATCH_MODE == "assign": return ...` 使 655-700 行的 rerank 路径**在生产中永不可达**；但 `ShenKuoAgent` 仍在 `main.py:148` 被构造，`MATCH_MODE="rerank"` 只能靠测试 monkeypatch 或改代码重新发版才能触发——所谓「保留用于回滚」实际回滚不了（§2.3）。

### 0.3 与 2026-10-19 旧审计的对照

旧审计 `docs/reviews/_audit/backend.md` 中落在本次范围内的 **24 条线索**（D01/D03/D04/D05/D06、C01/C07/C08/C09/C10/C11/C12/C16/C17/C18、L01/L04/L05/L06/L07/L08/L09、X01/X02），逐条复核后 **0 条已修复**——问题本身全部持续成立，只有行号发生 ±数行漂移（例：`_route_after_generate` 仍在 `170-181`；letter re-export 从 `1051-1054` 变为 `1052-1054`；`outline_builder` 死符号从 `34,45,329-350,814-825` 变为 `34-36,45,329-348,814-823`）。这不是「行号过期所以问题过期」，而是清理工作从未落地。旧审计的两条最高风险（X01 缺依赖、X02 缺 re-export）本次仍是最高风险，且我补充了可执行的复现证据。

### 0.4 并发变更说明（审计窗口内）

审计期间工作区发生了两件与本次审计无关但影响取证的事：

1. **01:31 提交 `299073c`**：把审计中的 4 个未提交改动 + `agents/prompts.py` + 两个测试文件一次性提交。行号因此有小幅漂移（例：`block_store.py` 的净化块仍从 464 起，`placeholder_purge.py` 的 `_drop_empty_sections` 仍在 198）。
2. **01:29 本机库被活服务改写**：`127.0.0.1:8015` 上有后端在跑（`lsof` 可见 LISTEN），重启后 `init_db` 应用了新的 `block_purged_items` DDL 并写入 293 行留痕。取证细节与「哪些数字会漂移、哪些不会」见 §0.5 最后一条。

### 0.5 方法说明

- 符号级：用 AST 索引 314 个 .py（`backend/` + `tests/` + `scripts/`），对 1,017 个模块级/类级/方法级符号统计「生产文件引用数 / 同文件引用数 / 测试引用数」，再人工排除 `getattr`、字符串引用、装饰器注册等动态用法（排除项见 §3）。
- 模块级：对 73 个范围内模块统计「被 import 次数」，生产侧为 0 的仅 5 个，逐个给出解释（§3「排除项」表末四行）。
- 依赖级：枚举 `backend/` 全部三方顶层 import，与 `requirements.txt` / `requirements-dev.txt` / `.venv` 实际版本三方对照。
- 运行期事实：`?mode=ro` 只读查库取 `PRAGMA user_version`、`PRAGMA foreign_keys`、脏数据行数；`pytest --collect-only` 验证测试基线（未执行用例，避免写入）。
- **并发写入提醒**：审计期间本机 `127.0.0.1:8015` 有一个后端在跑（`lsof` 可见 LISTEN），并在我取证过程中写入了 `backend/data/ge_solution.db`（01:20 → 01:29）。因此 §2.5 的库内数字是**带时间戳的快照**，其中「`block_purged_items` 是否存在」在审计窗口内由 0 变 1；与代码结构无关的结论（脏名 0、41/50 老 run 无 package_id、user_version 0、foreign_keys 0）在两次取样间保持一致。

---

## 1 逐项清单

> 证据口径：`引用数` 均为「排除定义行自身后」的命中数；`prod` = 生产代码（`backend/` 不含 tests），`test` = 测试。日期为 `git log -1 --date=short -- <file>`。

| ID | 文件:行 | 类别 | 子类 | 规模 | 证据 | 严重度 | 建议动作 |
|---|---|---|---|---|---|---|---|
| D01 | `backend/domain/spec/repository.py:1-120` | 死代码 | 零生产引用类（4/4 方法只被测试调用） | 120 行 | `grep -rn "SpecRepository" backend/` 除自身与 `domain/spec/__init__.py:13,18`（纯转出）外命中 0；4 个方法 `prod=0 test=1~3`；仅 `tests/domain/test_spec_repository.py`(173 行) 消费；最后提交 2026-09-17 `f404a4c` | 中 | 删文件 + 删 `__init__.py` 转出与 `__all__` 条目 + 删对应测试；生产等价实现已在 `services/block_store.py` |
| D02 | `backend/domain/proposal/repository.py:1-84` | 死代码 | 零生产引用类 | 84 行 | `grep -rn "ProposalRepository" backend/` 仅 `domain/proposal/__init__.py:4,6`；`save_block_output`/`mark_block_failed` `prod=0 same=0`；仅 `tests/domain/test_proposal_repository.py`(274 行)；最后提交 2026-09-17 `f404a4c` | 中 | 同 D01；注意 `list_blocks`/`get_block` 的 prod 计数是**同名自由函数**（`block_store`）造成的假阳性，类本身无实例化 |
| D03 | `backend/domain/material/repository.py:1-111` | 死代码 | 零生产引用类 | 111 行 | `grep -rn "MaterialRepository" backend/` 仅 `domain/material/__init__.py:18,24`；`mark_parsing/mark_parsed/mark_failed` `prod=0 same=0`；仅 `tests/domain/test_material_repository.py`(225 行)；最后提交 2026-06-16 `e8db79d` | 中 | 同 D01 |
| D04 | `backend/utils/sse.py:24-62` | 死代码 | 6 个零引用函数 | 39 行 | `sse_token`/`sse_section_start`/`sse_section_done`/`sse_all_done`/`sse_doc_summary`/`sse_section_skip` 全仓引用各 0（只剩 `def` 行）；文件最后提交 2026-05-09 `6c00084` | 低 | 删 6 函数；`sse_parse_*`/`sse_error` 随 X05 一起删，最终只留 `format_sse_event`（`prod=47`） |
| D05 | `backend/orchestrator/graph.py:170-181` | 死代码 | 零引用函数（不可达路由） | 12 行 | `grep -rn "_route_after_generate" backend tests` = 1（仅定义）；真实路由是 `graph.py:362-377` 内联 `_generate_fanout` 与 `:383 _route_after_pause` | 低 | 删函数 |
| D06 | `backend/orchestrator/state.py:189-228` | 死代码 | 仅测试使用的公开函数 | 40 行 | `merge_state` `prod=0`，`test=22` 全部在 `tests/orchestrator/test_state.py`；仍在 `state.py:241 __all__` 中；LangGraph 侧实际用 `_merge_dict` reducer | 低 | 删函数 + `__all__` 条目 + `test_state.py` 相关 8 个用例；若团队想留「测试工具」应移到 `tests/` |
| D07 | `backend/agents/shen_kuo.py:52-170` | 死代码 | 不可达分支 `match()` | 119 行 | `MATCH_MODE="assign"` 恒真返回（见 X03），`agent.match(...)` 唯二调用点 `nodes.py:673,678` 均在 `return` 之后；`retrieve_for`（172-256）**活着**（`nodes.py:1201`） | 中 | 删 `match()` + 构造函数里 `rerank_top_n`；保留 `retrieve_for` |
| D08 | `backend/infra/retrieval/rerank.py:50-127` | 死代码 | 旧 LLM 重排实现 | 78 行 | `llm_rerank` `prod=8` 但全部来自 `shen_kuo.py`（D07 死）与 `infra/retrieval/__init__.py:21,27` 转出；`nodes.py` 侧已无调用；`Match` 数据类（25-43）**活着**（`nodes.py:1483 _dict_to_match`） | 中 | 删 `llm_rerank` + `__init__` 转出；`Match` 保留 |
| D09 | `backend/infra/llm/dispatcher.py:110-184,428-459` | 死代码 | 被停用的摘要生成链 | 107 行 | `dispatch_doc_summary` 生产调用 0（`grep` 仅 `dispatcher.py:428` 定义 + `__init__.py:21,42` 转出 + 注释）；`_generate_doc_summary` 唯一调用方 `dispatch_doc_summary:446`；`zhang_heng.py:380` 直接 `doc_summary = ""`；仅 `tests/agents/test_zhang_heng.py:88` spy | 中 | 二选一：①按 `zhang_heng.py:378` 注释接回并给足 `max_tokens`；②删两个函数 + `__init__` 转出 + `doc_summary` 形参链路（`dispatcher`/`prompts`/`state` 多处，需一次收敛） |
| D10 | `backend/domain/review/repository.py:54-59,75-85,208-217` | 死代码 | 活类中的 3 个死方法 | 27 行 | `clear_thread`/`list_findings_by_block`/`latest_for_scope` `prod=0 same=0`；分别只被 `tests/domain/test_review_repository.py`(1/1) 与 `tests/test_run_stage_mapping.py`(6) 使用；类本身活着（`routes/review.py:26`、`nodes.py:1050`） | 低 | 删 3 方法；`_row_to_finding`（88-112）保留（被 `add_finding`/`list_findings_by_thread` 内部调用） |
| D11 | `backend/infra/parser/outline_builder.py:34-36,45,329-348,814-823` | 死代码 | 2 常量 + 2 函数 | 34 行 | `_ENUM_RE`/`_TOPIC_LEVEL_HINTS`/`match_spec_nodes` 全仓各 1 处（定义）；`render_outline_text` 仅 `tests/infra/test_outline_builder.py`(3)；最后提交 2026-10-16 `3a1407e` | 低 | 删 4 符号；测试局部删改用例 |
| D12 | `backend/infra/parser/review_points.py:70-72,222-226,520-523` | 死代码 | 1 常量 + 1 方法 + 1 函数 | 8 行 | `_SCOPE_LINE_RE`/`review_points_of` 全仓各 1 处（定义，后者仍在 `__all__:537`）；`total_points` 仅 `tests/infra/test_review_points.py:211` | 低 | 删 3 符号 + `__all__` 条目 |
| D13 | `backend/infra/parser/toc.py:11`、`scope_index.py:31`、`tech_requirements.py:89` | 死代码 | 3 个零引用正则常量 | 3 行 | `_CIRCLED_DIGITS`/`_NUM_PREFIX_RE`/`_ARTIFACT_RE` 全仓各 1 处（定义） | 低 | 删除 |
| D14 | `backend/infra/llm/usage.py:52-53` | 死代码 | 零引用函数 | 2 行 | `get_sink` 全仓 1 处（定义）；模块其他函数直接用全局 `_sink` | 低 | 删函数 |
| D15 | `backend/domain/material/models.py:99-109` | 死代码 | 3 个零引用常量 | 3 行 | `VALID_KINDS`/`VALID_SOURCE_KINDS`/`VALID_BY` 全仓各 1 处；且不在 `domain/material/__init__.py` 的 `__all__` | 低 | 删除 |
| D16 | `backend/infra/llm/errors.py:143-145,278-280` | 死代码 | 被取代的判定 + 仅诊断用快照 | 6 行 | `is_retryable` 生产 0（13 处生产代码全部改用 `blocks_chain`，见 X07）；`unhealthy_snapshot` 全仓仅 `def` + `__all__:301`，连测试都没有 | 低 | 删 `unhealthy_snapshot`；`is_retryable` 见 X07 |
| D17 | `backend/skills/diagram_design/scripts/export_png.py:1-93` | 死代码 | vendored skill 中未被调用的脚本 | 93 行 | `grep -rn "export_png" backend/` 除自身外命中 1，且是注释（`services/diagram_render.py:3`「与上游 skill 的 export_png.py 同一条路」）；运行时只用 `export_svg.py`（`services/diagram_svg.py:13`）与 `self_check.py`（`services/diagram_design.py:326`） | 低 | 保留需在上游 skill 升级时同步；建议在 `VERSION`/`NOTICE` 旁注明「export_png 未接线，仅供上游对齐」，不要当成产品资产 |
| D18 | `backend/skills/diagram_design/scripts/export_svg.py:660-697,1058-1061,1114-1134` | 死代码 | vendored 脚本内零引用函数 | 63 行 | `_css_blocks`/`diagram_css_from_html`/`find_defs_ids` 在该文件内及全仓均 0 引用（AST 计数 `ext=0 same=0`） | 低 | vendored 代码，**不建议手改**；升级上游版本时自然消失或复活 |
| D19 | `backend/services/placeholder_purge.py:198-220`（HEAD `299073c`） | 死代码 | 新增即死的函数 | 23 行 | `grep -rn "_drop_empty_sections"` 全仓仅 `:198` 定义；`purge()` 未调用它；无测试引用；同批改动新增了它的 docstring 却未接线 | 低 | 要么在 `purge()` 返回前接上，要么删掉——现在两条路都没走 |
| D20 | `backend/db.py:656-663` | 死代码 | 零引用迁移函数 | 8 行 | `_migrate_review_v3` 全仓 1 处（定义）；其逻辑已内联重复在 `init_db:670-674`（同样的 `ALTER TABLE chapter_reviews ADD COLUMN tier` + `except: pass`） | 低 | 删函数；保留内联那条即可 |
| D21 | `tests/domain/test_spec_repository.py:1-173`、`test_proposal_repository.py:1-274`、`test_material_repository.py:1-225` | 死代码 | 只为死模块存在的测试 | 672 行 | 三个文件 100% 用例都只 import 并断言 D01-D03 的三个类；生产无调用方 → 这些测试保护的是不可达代码 | 中 | 随 D01-D03 一起删 |
| D22 | `tests/agents/test_shen_kuo.py:1-332`、`tests/infra/test_retrieval.py:41-110`、`tests/orchestrator/test_nodes.py:225-257` | 死代码 | 保护不可达 rerank 路径的测试 | ≈ 430 行 | `test_shen_kuo.py` 覆盖 `ShenKuoAgent.match`（D07）；`test_retrieval.py:54-110` 三个用例只测 `llm_rerank`（D08）；`test_nodes.py:225` 用例 docstring 自述「显式把模式切回去，保证回滚路径仍然可用」，靠 `monkeypatch.setattr(nodes, "MATCH_MODE", "rerank")` 才跑得到 | 中 | 与 D07/D08 同批删；若决定保留 rerank，则必须把开关改成环境变量（见 X03），否则这些测试是唯一入口 = 死代码的假活 |
| D23 | `tests/domain/test_letter_detector.py:31-36` | 死代码 | 断言旧 import 路径的测试 | 6 行 | `test_legacy_path_still_works` 只做 `from infra.llm.dispatcher import is_letter_section` 并断言与 `domain.letter_detector` 同一对象——它存在的唯一目的就是保护 X08 的 re-export | 低 | 随 X08 一起删 |
| D24 | `tests/infra/test_llm_errors.py:60,254` | 死代码 | 断言已死函数的测试 | 2 断言 | 两处 `assert errors.is_retryable(...)`，该函数生产 0 引用（D16/X07） | 低 | 改断言 `blocks_chain` 或删用例 |
| D25 | `backend/scripts/normalize_extract_fields.py:1-147`、`recut_old_materials.py:1-144` | 死代码 | 一次性数据修复脚本（无入口） | 291 行 | 两文件首行均写「一次性脚本」；模块级 import 计数 0；`grep` 在 `*.sh`/`*.toml`/`*.yml`/`*.json`/CI 配置中命中 0（无 cron、无调度、无 CI 步骤）；最后提交 2026-10-12 `d7438bc` / 2026-06-17 `2324bbb` | 低 | 移到 `scripts/oneoff/` 并配 README，或确认历史数据已处理完直接删 |
| D26 | `scripts/cleanup_pending_placeholders.py:1-50`、`normalize_diagram_blocks.py:1-72`、`recover_gaps_from_revisions.py:1-42`、`add_table_captions.py:1-55`、`build_response_templates.py:1-51` | 死代码 | 一次性脚本（无入口） | 270 行 | 均在自身 docstring 声明「一次性」；对 `*.sh`/`*.toml`/`*.yml`/CI 的引用 = 0；`scripts/README.md` 只登记 `check.sh`/`ui-selfcheck.py`/`clear_data.py` 三个入口，这 5 个未登记 | 低 | 同 D25；`scripts/README.md` 增删登记 |
| U01 | `backend/infra/llm/dispatcher.py:13`、`backend/infra/retrieval/rerank.py:6`、`backend/agents/wang_anshi.py:12`、`backend/agents/bao_zheng.py:13` | 无用代码 | 未使用 import（4 处 `import json`） | 4 行 | 四文件内 `json` 的全部命中都是 `import json` 行本身与 `json_utils` 名字；JSON 解析早已收敛到 `infra/llm/json_utils.py`（其文件头自述「原本散落在 dispatcher.py / rerank.py / wang_anshi.py / bao_zheng.py 四处」）——重构漏删 | 低 | 删 4 行 import |
| U02 | `backend/infra/parser/docx.py:10,19`、`backend/infra/parser/scope_index.py:22` | 无用代码 | 未使用 import | 3 行 | `docx.py` 全文 `re.` 命中 0；`_SPECIAL_MARK_RE` 只出现在 `:19` 的 import 列表里（`toc.py:138` 定义、`toc.py:146,274` 自用）；`scope_index.py` 的 `Optional` 只在 `:22` 出现 | 低 | 删 `import re`、从 import 列表去掉 `_SPECIAL_MARK_RE`、`Optional`；顺带消除「跨模块 import 私有名」的耦合 |
| U03 | `backend/orchestrator/runner.py:25,26`、`backend/orchestrator/nodes.py:31` | 无用代码 | 未使用 import | 3 行 | `runner.py` 的 `asynccontextmanager`/`Awaitable` 除 import 行外命中 0（`:76` 只是 docstring 提到）；`nodes.py` 的 `Finding` 除 `:31` 外命中 0（`GlobalReport` 在用） | 低 | 删 3 个名字 |
| U04 | `backend/infra/retrieval/locate.py:14`、`backend/infra/retrieval/assign.py:36`、`backend/scripts/recut_old_materials.py:15` | 无用代码 | 未使用 import | 3 行 | `locate.py` 只调 `build_bm25_index_raw`（`:60`），`build_bm25_index` 无引用；`assign.py` 的 `assign_chunks_to_sections` 只出现在注释/docstring（`:4,:11`）；`recut_old_materials.py` 的 `import os` 全文 `os.` 命中 0 | 低 | 删 3 个 import。注意：删 `assign.py:36` **不会**解除 jieba 启动依赖（同文件 `:133` 仍有函数内 import，且 `keyword.py` 被别处直接 import） |
| U05 | `backend/infra/llm/__init__.py:18-19,23,29,37,49` | 无用代码 | 未使用 re-export（含一处漏登记） | 5 处名字 | 包级 `from infra.llm import X` 的生产消费方只有：`LLMConfig`/`OPENAI_COMPATIBLE_PROVIDERS`（rerank、wang_anshi、bao_zheng）、`verify_api_key`（routes/config、services/config_store）、`dispatch_stream_generate`（routes/blocks）、`generate_letter_content`（zhuge_liang）、以及属性式访问的 `dispatch_outline_json`/`dispatch_block_write`/`dispatch_outline_draft_json`/`dispatch_scope_select_json`/`dispatch_subpoints_json`/`generate_section_outline`/`length_band`。**`LENGTH_BANDS`/`LETTER_KEYWORDS`/`is_letter_section` 生产 0 消费**（仅测试）；`dispatch_subpoints_json` 被 import 却未进 `__all__`（`:34-50`） | 低 | 删 3 个无用转出；`dispatch_subpoints_json` 补进 `__all__`；`LETTER_KEYWORDS`/`is_letter_section` 改由 `domain.letter_detector` 直取（与 X08 联动） |
| U06 | `backend/infra/retrieval/__init__.py:21,27` | 无用代码 | 为死函数保留的 re-export | 2 行 | `llm_rerank` 的唯一用途是 D08 的死路径；`__init__.py:5` docstring 仍写「llm_rerank：沈括 agent 用的 LLM 重排」 | 低 | 随 D08 删 |
| U07 | `backend/routes/chapter_studio.py:656-661,770-775`（HEAD `299073c`） | 无用代码 | 有意冗余（同一正文净化两次） | 2 段 ×7 行 | 两处先 `text, _purged = purge(text)`，随后都调用 `update_block_content(...)`（`:669`、`:778`），而后者在 `services/block_store.py:462-467` **又净化一次**；`_purged` 被丢弃，留痕实际由 `update_block_content` 写入。**注意**：提交 `299073c` 的 message 明写「SSE 写节两处（`replace_section` 前净化，返回给前端的正文也是干净的）」，即作者是有意为之（为了 `replace_section` 与返回体先干净）。因此这不是 bug，但同一次保存仍会跑两遍正则全量替换 | 低 | 保留可读性前提下二选一：①在 store 层加 `already_purged=True` 跳过二次净化；②删路由层前置 `purge`，改为对 `after` 净化后再算 `words`。当前双跑在长正文上是可观测的 CPU 浪费（`purge` 内含多轮正则 + 分句重排） |
| U08 | `backend/utils/validators.py:1-11` | 无用代码 | 仅为旧路由存在的模块 | 11 行 | `validate_uuid` 唯一调用点 `routes/upload.py:162`；`grep -rn "utils.validators" backend tests` 只命中 upload.py；删 X05 后本模块立刻成孤儿 | 低 | 随 X05 删 |
| X01 | `requirements.txt:1-41`、`backend/infra/retrieval/keyword.py:6-7`、`Dockerfile:11-13` | 逻辑不一致 | 依赖声明与实际 import 不符 → 启动阻断 | 0 行声明 / 阻断 3 个部署面 | `grep -n "jieba\|rank" requirements*.txt` = 0（exit 1）；`keyword.py:6-7` 模块顶层 `import jieba` / `from rank_bm25 import BM25Okapi`；启动链 `main.py:27 → routes/materials.py:35 → infra/retrieval/__init__.py:20 → keyword.py`；`Dockerfile:13` 只 `pip install -r requirements.txt`；本地 `.venv` 恰有 jieba 0.42.1 / rank-bm25 0.2.2 掩盖了问题；复现见 §2.1 | **高** | 在 requirements.txt 补 `jieba==0.42.1`、`rank-bm25==0.2.2`（PyPI 名带连字符）；同时建议把 `keyword.py` 的顶层 import 改为函数内延迟 import 作为二道防线 |
| X02 | `backend/infra/llm/__init__.py:17-50`、`backend/routes/materials.py:741-749` | 逻辑不一致 | 缺失 re-export 被 `except Exception` 吞掉 → 功能假死 | 1 行缺失 / 链路约 200 行不可达 | `dispatch_package_merge_json` 定义在 `dispatcher.py:1096`，在 `infra/llm/__init__.py` 中命中 0；`routes/materials.py:743` 在 `try` 内 import，`:747-748` `except Exception` → `logger.info` → `call_model` 恒为 `None`；`use_llm=1` 与 `0` 结果相同；自 2026-10-15 `03e16b7` 引入后未修 | **高** | 在 `__init__.py` 导入并加入 `__all__`（推荐，恢复功能）；若产品决定不做模型合并，则删 `use_llm` 分支 + `services/package_analysis.merge_with_llm/validate_merge_proposal` 及 6 个测试 |
| X03 | `backend/orchestrator/nodes.py:563,648,655-700` | 逻辑不一致 | 硬编码开关使分支永不可达 | ≈ 60 行不可达 + 开关失效 | `MATCH_MODE = "assign"` 是模块常量（非环境变量/配置），`:648` 恒真 `return`；`:655` 起整段 rerank 实现永不执行；`main.py:45,148` 仍构造 `ShenKuoAgent`；`tests/orchestrator/test_nodes.py:231` 靠 monkeypatch 才进入 | 中 | 二选一：①删 `:655-700` + D07/D08 + `GraphDeps.shen_kuo.match`，`ShenKuoAgent` 只留 `retrieve_for`；②真要做回滚，把 `MATCH_MODE` 读环境变量并在 README 写清回滚步骤——现在是「假开关」 |
| X04 | `backend/agents/zhang_heng.py:366-381`、`dispatcher.py:110-184,428-459`、`agents/prompts.py:354,387-388` | 逻辑不一致 | 被停用功能仍保留完整链路 + 测试断言「不调用」 | ≈ 110 行 | `doc_summary` 在 `:380` 硬编码空串；注释明写「暂时停用（2026-09-17）」并给出恢复条件；下游 `dispatcher.dispatch_stream_generate`、`prompts` 的 `doc_summary` 分支仍在，但永远拿到空值；`tests/agents/test_zhang_heng.py:71-95` 专门断言「不调用」 | 中 | 产品决策：恢复（按注释给足 `max_tokens`）或彻底删链路；「停用但保留」已 7 周，属典型的双份维护成本 |
| X05 | `backend/routes/upload.py:100,150,285`、`backend/main.py:18,243` | 逻辑不一致 | 旧上传管线仍注册但零消费方 | 292 行（路由）+ 41 行（依赖工具） | 新管线是 `POST /api/projects/{id}/materials`（`routes/materials.py:450`），前端统一走 `frontend/assets/api.js:106-115`；`grep -rn "api/upload\|parse/stream\|supported-formats" frontend/ tests/` = **0**；旧路由仍 `app.include_router(upload_router, prefix="/api")`（`main.py:243`），即对外仍是可用 HTTP 端点；最后提交 2026-06-17 | 中 | 删 `routes/upload.py` + `main.py:18,243` + `utils/validators.py`(U08) + `sse.py` 的 `sse_parse_*`/`sse_error`(D04 联动)。删除不破坏任何测试（已验证 0 引用）。旧实现还有内存泄漏风险：`ParseJob.file_data` 常驻、`parse_job_store` 无上限、`:194 asyncio.get_event_loop()` 在 3.12+ 已弃用 |
| X06 | `backend/db.py:824-835,837-871,908-932`、`backend/db.py:1007-1016` | 逻辑不一致 | 启动期一次性回填/清洗 + 无 schema 版本 + 外键未启用 | ≈ 110 行 | 三段「一次性数据修复」挂在 `init_db()` 每次启动执行：①名称清洗 `:827-834`；②`packages.display_name` 从 evidence 回填 `:842-871`（全表扫描 + 逐行 UPDATE）；③`project_packages`→`packages`/`package_id` 回填 `:909-932`。只读查库：脏名 `packages.display_name LIKE '%\|%'` = **0**（①已无事可做）；`workflow_runs.package_id IS NULL` = **41/50**（③永远修不好，这些行 `target/package_no` 全空）；`PRAGMA user_version` = **0**（无版本表）；`PRAGMA foreign_keys` = **0** 且全仓无 `PRAGMA foreign_keys=ON`（DDL 里所有 `REFERENCES` 只是注释性约束） | 中 | ①可安全删；②确认各环境已回填后删；③要么删、要么补一条从 checkpoint 反推 `package_id` 的真实迁移；长期引入 `schema_migrations` 表；是否开启外键需单独立项（现有 41 条孤儿 run 会立刻违约） |
| X07 | `backend/infra/llm/errors.py:143-145` vs `:148-155` | 逻辑不一致 | 旧判定被新判定取代但仍导出+测试 | 3 行 | `is_retryable(kind) = kind != KIND_REQUEST_ERROR`；`blocks_chain(kind)` 是实际语义（13 处生产调用：`dispatcher.py:413,455,508,593,652,982,1040,1089,1141`、`rerank.py:121`、`wang_anshi.py:166`、`bao_zheng.py:168`）；`is_retryable` 生产 0 调用，仅 2 条测试断言 | 低 | 删函数 + `__all__:295` + 2 条测试断言 |
| X08 | `backend/infra/llm/dispatcher.py:1052-1054` | 逻辑不一致 | 旧 import 路径 re-export | 4 行 | 真实定义在 `domain/letter_detector.py`；注释自称「保留 re-export 以兼容仍按旧路径…的调用点」，但该调用点全仓只剩 `tests/domain/test_letter_detector.py:32-36`（D23）；`infra/llm/__init__.py:19,29` 正是靠这条 re-export 才能转出 | 低 | 删 re-export，`__init__.py` 改从 `domain.letter_detector` 导入，删 D23 用例 |
| X09 | `requirements.txt:17-20` | 逻辑不一致 | 直接 import 的三方包靠传递依赖 | 1 行缺失 | `backend/infra/parser/_ocr.py` 顶层 `import numpy`，requirements 未声明，仅靠 `rapidocr-onnxruntime` 传递；同理 `pydantic`（20 个文件）靠 fastapi 传递 | 低 | 显式补 `numpy`（pydantic 可留传递，但建议加注释说明） |
| X10 | `backend/scripts/recut_old_materials.py:32-51` vs `backend/routes/materials.py`（同名函数） | 逻辑不一致 | 重复实现（同名两处） | 20 行 | `_split_for_scoring_evaluation` 在脚本内定义，`routes/materials.py` 内另有一份同名实现（`prod` 计数命中该文件但非 import），属切片规则两份口径 | 低 | 抽到 `domain/material` 或 `infra` 单一实现；若按 D25 删脚本则本条自动消解 |

---

## 2 高危逐项详述

### 2.1 X01：requirements 缺 `jieba`/`rank_bm25` → 镜像启动即崩（高）

**事实链（全部现场核实）**

1. `backend/infra/retrieval/keyword.py:6-7` 在**模块顶层**导入：`import jieba` / `from rank_bm25 import BM25Okapi`。
2. 该模块进入启动路径（import 期执行，非懒加载）：
   - `backend/main.py:27` `from routes.materials import router as materials_router`
   - `backend/routes/materials.py:35` `from infra.retrieval import keyword_search as retrieve_chunks, build_bm25_index, assign_chunks_to_sections`
   - `backend/infra/retrieval/__init__.py:20` `from .keyword import keyword_search, build_bm25_index, assign_chunks_to_sections`
   - 另有一条独立链：`main.py:45 from agents.shen_kuo import ShenKuoAgent` → `shen_kuo.py:18 from infra.retrieval import Match, keyword_search, llm_rerank`。
3. `requirements.txt` 全文无 `jieba`、无 `rank`（`grep -n "jieba\|rank" requirements*.txt` 退出码 1）；`requirements-dev.txt` 也没有。
4. `Dockerfile:11-13` 只 `pip install -r requirements.txt`；`:50` 的 CMD 是 `uvicorn main:app`；`railway.toml:6` 与 `render.yaml:4` 都指向同一个 Dockerfile。
5. 本地 `.venv` 里恰好有 `jieba==0.42.1` / `rank-bm25==0.2.2`（`.venv/bin/pip freeze` 可证），所以**本地永远复现不出来**——这是这条问题能活 3 周的直接原因。

**可控复现（不写库、不启动服务）**：给 `builtins.__import__` 装钩子，对 `jieba`/`rank_bm25` 抛 `ModuleNotFoundError`，再 `import routes.materials`：

```
IMPORT FAILED: No module named 'jieba' (simulated: absent from requirements.txt)
```

**影响**：Docker / Railway / Render 三个部署面全部在 import 阶段崩溃；`HEALTHCHECK`（`Dockerfile:47-48`）永远失败 → 容器反复 restart。**这是本次审计唯一的「部署即失败」级问题，应优先于任何清理动作修复**（否则清理后「测试跑不起来」会被误判为清理引入）。

**修复**：`requirements.txt` 文档解析段补两行，并建议把 `keyword.py` 的顶层 import 移入 `_tokenize`/`build_bm25_index` 内（二道防线）。

### 2.2 X02：`dispatch_package_merge_json` 未转出 → 标包模型合并静默降级（高）

- 函数实体：`backend/infra/llm/dispatcher.py:1096 async def dispatch_package_merge_json(...)`。
- 包出口：`backend/infra/llm/__init__.py:17-32` 的 import 列表与 `:34-50` 的 `__all__` **都不含**该名字（全文命中 0）。
- 调用方：`backend/routes/materials.py:741-749`：

```python
if use_llm:
    try:
        from infra.llm import dispatch_package_merge_json   # ImportError
        ...
    except Exception as e:      # 没配置模型就照旧走规则，不报错
        logger.info(f"标包分析：模型整理不可用（{e}），按规则结果返回")
```

- 后果：`call_model` 恒 `None` → `run_analysis(..., use_llm=True, call_model=None)` 走规则分支。用户点「让模型整理标包」与不点，结果**逐字节相同**，只有一条 INFO 日志。
- 旁证：该 import 与函数由同一个提交 `03e16b7`（2026-10-15）引入，说明是「加函数时忘了加出口」，不是有意下线。
- 连带不可达：`services/package_analysis.py` 的 `merge_with_llm` / `validate_merge_proposal`（约 200 行）与 6 个相关测试在产品中永远不会被走到。

**修复**：`__init__.py` 导入 + `__all__` 补一行即可恢复；若产品决定不做，则删 `use_llm` 分支与整条合并链路，别留「假开关」。

### 2.3 X03 + D07 + D08：匹配阶段「回滚开关」是假的（中）

- `backend/orchestrator/nodes.py:563` `MATCH_MODE = "assign"`（模块常量）。
- `:648` `if MATCH_MODE == "assign": return await _match_by_assignment(...)` —— 无条件返回。
- `:655-700` 的 rerank 实现（含 `inspect.signature(agent.match)` 兼容探测、`_bridge` 回调、兜底补全）**永不执行**。
- `backend/agents/shen_kuo.py:52-170` `ShenKuoAgent.match()`（119 行）与 `backend/infra/retrieval/rerank.py:50-127` `llm_rerank()`（78 行）的唯一生产入口就在这段死区。
- 但 `main.py:45,148` 仍 `ShenKuoAgent()` 注入 `GraphDeps`，`graph.py:258` 仍把它传给 `shen_kuo_match_node`——**对象活着、方法死了**，静态扫描容易误判。
- 唯一能让 rerank 跑起来的方式是改代码重新发版，或 `tests/orchestrator/test_nodes.py:231` 的 monkeypatch；注释里的「保留用于回滚/对照，默认不用」在工程上不成立（回滚需要改代码 + 发版，与直接 revert 一个提交等价）。

**建议**：既然 `assign` 已是 P0 主线（零模型调用、带原文出处），把开关做成 `os.environ.get("MATCH_MODE","assign")` 并写进 README 的回滚段；否则删干净（含 D07/D08/D22），别用「硬编码常量 + 测试 monkeypatch」伪装成可配置。

### 2.4 X05：旧上传管线仍是对外可用端点，但零消费方（中）

- 仍注册：`main.py:18` import、`main.py:243` `app.include_router(upload_router, prefix="/api")`。
- 三个端点：`POST /api/upload`(`upload.py:100`)、`GET /api/parse/stream/{job_id}`(`:150`)、`GET /api/upload/supported-formats`(`:285`)。
- 消费方：**0**。前端统一走 `POST /api/projects/{id}/materials`（`frontend/assets/api.js:106` 的 `api.materials.upload`）；`grep -rn "api/upload\|parse/stream\|supported-formats" frontend/ tests/` 命中 0；测试目录也没有任何用例 import `routes.upload`。
- 它还是三样东西的唯一消费者：`utils/validators.py`(U08)、`utils/sse.py` 的 `sse_parse_start/progress/section/done` 与 `sse_error`。所以「删旧路由」是一次四文件联动清理，收益 292 + 11 行 + 死 sse 函数。
- 附带缺陷（读代码可见，非本次重点，均在 `routes/upload.py`）：① `job.event_queue = queue`（`:180-183`）在**每次** SSE 连接时覆盖同一个 job 的队列，parse 线程只往最新队列 emit（`:234`）→ 多标签页/断线重连会互相抢断，先连的那个收不到事件（旧审计 P2-3，仍在）；② `asyncio.get_event_loop()`（`:180`）在 Python 3.12+ 已弃用，当前 3.13 环境行为不确定；③ 内存有界但偏大：`MAX_JOBS=50` × 单文件 ≤50MB（`:52,64-88`），最坏 ≈2.5GB 常驻，且 `file_data` 只在 evict 时释放。

### 2.5 X06：启动期一次性回填仍在每次启动执行，且 schema 无版本（中）

只读查库（`?mode=ro`）证据：

| 检查 | 结果 | 含义 |
|---|---|---|
| `SELECT COUNT(*) FROM packages WHERE display_name LIKE '%\|%'` | 0 | 名称清洗（`db.py:827-834`）在本库已无事可做，但每次启动仍执行 2 条全表 UPDATE |
| `SELECT COUNT(*) FROM workflow_runs WHERE package_id IS NULL` | 41 / 50 | `package_id` 回填（`:917-924`）落在 41 条**无 target/package_no** 的老 run 上，永远修不好 |
| `PRAGMA user_version` | 0 | 无 schema 版本，`init_db()` 靠 40+ 处 `PRAGMA table_info` 探测 + `try/except: pass` 幂等 |
| `PRAGMA foreign_keys` | 0 | DDL 里大量 `REFERENCES` 未被启用，无实际约束力 |

`init_db()` 现为 1016 行文件中的 341 行，把「建表 / 补列 / 一次性数据修复 / 索引补建」混在一个每次启动都跑的函数里。`:842-871` 那段还会 `import services.package_analysis`（触发器式反向依赖），启动期把服务层拉起来。

**建议**（按性价比排序）：① 删 `:827-834`（本库已 0 命中）；② `:842-871` 改为「仅在检测到空名行时执行」；③ 给 `workflow_runs.package_id` 补一条从 checkpoint 反推的真实迁移，或直接删这段（41 条老 run 无归属是数据事实，不该在启动时反复尝试）；④ 引入 `schema_migrations` 表（旧审计 P2-7 的同一建议，7 周未动）。

---

## 3 已核实「看起来死其实活」的排除项（附引用）

这一节是为了**防止把活的删掉**。下列项在自动扫描里会被标为「生产引用 0」，人工核实后确认活着，故不计入问题清单。

| 符号/文件 | 为什么扫描会误判 | 实际存活的引用 |
|---|---|---|
| `backend/agents/shen_kuo.py`（整文件） | `match()` 死、模块级 `prod` 计数低 | `retrieve_for` 被 `orchestrator/nodes.py:1201` 调用（collect_gaps 节点） |
| `infra/retrieval/rerank.py` 的 `Match` 数据类 | 与 `llm_rerank` 同文件 | `orchestrator/nodes.py:1483 _dict_to_match` 构造；assign 路径实际在用 |
| `infra/retrieval/keyword.py` 的 `keyword_search` / `build_bm25_index` | 其调用方 `zhang_heng._ground_sections` 跨文件计数为 0 | `agents/zhang_heng.py:458,519` 同文件调用 `_ground_sections`，其内部 `:132-139` 调用两者 |
| `agents/zhang_heng.py:102-147 _ground_sections` | `prod=0` | 同文件 `:458`、`:519` 两处调用 |
| `infra/llm/__init__.py` 的 `dispatch_subpoints_json` | 不在 `__all__` | `agents/zhang_heng.py:681` 以 `infra.llm.dispatch_subpoints_json(...)` 属性式访问 |
| `infra/llm/dispatcher.py:683 build_write_requirement_block` | 包外 `prod=0` | 同文件 `:795 dispatch_block_write` 内部调用 |
| `infra/llm/dispatcher.py:822 dispatch_outline_json` | `grep` 生产侧几乎全是注释 | `agents/zhang_heng.py:1051 import infra.llm` → `:1124` `infra.llm.dispatch_outline_json(...)`（函数作用域，`grep` 只看模块层会漏） |
| `domain/letter_detector.py` 的 `LETTER_KEYWORDS` / `is_letter_section` | `infra/llm` 包级转出无人用 | `agents/zhuge_liang.py:22` 直接从 `domain.letter_detector` 导入并在 `:149` 使用 |
| `agents/subpoints.py` 的 `cache_lookup`/`is_grounded`/`is_garbage_title`/`validate_points` | 各自外部引用 0 | 同文件 `extract_points`（`:153`）内部调用；`extract_points` 被 `zhang_heng.py:676` 使用 |
| `infra/llm/errors.py:273 reset_health` | 生产 0 | `tests/conftest.py:22,24` 的 autouse fixture 用它隔离冷却状态——**测试基础设施，应保留**（建议 docstring 已注明「测试用」，现状合格） |
| `orchestrator/graph.py:383 _route_after_pause`、`:192 _route_after_report` | 与死掉的 `_route_after_generate` 同名风格 | `graph.py:393`、`:445` 注册进 `add_conditional_edges` |
| `orchestrator/graph.py:69 GATE_PAUSE` + `:302` 节点 | 死函数 `_route_after_generate` 里提到它 | 真正入口是 `:362-377` 内联 `_generate_fanout` + `:383 _route_after_pause`；`tests/orchestrator/test_graph.py:354-392` 覆盖 |
| `backend/skills/diagram_design/scripts/{export_svg,self_check}.py` | 模块 import 计数 0 | 以**子进程 + 文件路径**调用：`services/diagram_svg.py:13-16`、`services/diagram_design.py:326-334`；不是 Python import 关系 |
| `backend/skills/diagram_design/` 整个目录 | 34 个文件、656K，像「静态资产」 | 一半资产一半可执行：`SKILL_DIR`（`services/diagram_design.py:27`、`diagram_svg.py:9`）在运行时读取 `references/*.md` 并 spawn `scripts/*.py`。**结论：是活资产，不是死目录**；只有 `export_png.py` 未接线（D17） |
| `scripts/bench_server.py`（未跟踪） | 仓库内无产品代码引用、`git status` 显示 `??` | 被 `packages/dsh-video-bench/index.js:21`（同为未跟踪）用 `join(workspace,'scripts/bench_server.py')` 拉起；`packages/dsh-video-bench/client.js:121` 还有报错提示。**属于「依赖未入库目录的活文件」**，提交前需与 `packages/` 一起决策 |
| `scripts/{analyze_motion,bench_render,make_qr}.py`（未跟踪） | 无产品引用 | 仅被设计稿引用：`docs/design/参考片动效拆解-CodePilot.md:6`、`docs/design/视频逐帧工作台-方案.md:143`、`docs/design/宣传片-投放包.md:57`。**属开发/营销工具**，不是产品代码，也不是死代码；不应放产品 `scripts/` 混在 `check.sh` 旁边 |
| `domain/review/repository.py` 的 `ReviewRepository`/`WorkflowRunRepository` | 类内 3 个方法死（D10）容易被整类误判 | `routes/review.py:26`、`orchestrator/nodes.py:1050`、`routes/projects.py:20`、`orchestrator/runner.py:31` 均在用 |
| `infra/parser/outline_builder.py` 的其余 900+ 行 | 同文件有 4 个死符号（D11） | `render_outline_text` 之外的主体被 `services`/`agents` 使用（模块级 import 计数 >0） |
| `tests/conftest.py` 的 `_db_schema_template` / `_isolate_db` | 看起来像「测试基建冗余」 | 每个用例把 `db.DB_PATH` 指向 tmp 副本（`conftest.py:65`），是 2026-10-13 生产库被测试污染事故后的修复，**不要动** |
| `pytest.ini` 基线 | `addopts = -m "not slow"` + 仅 1 个 `@pytest.mark.slow` | 现场 `pytest --collect-only` = **1147/1148 收集，1 deselected**（`tests/e2e/test_golden_sample.py:60`），基线正常；`asyncio_mode = auto` 依赖 `pytest-asyncio`（在 `requirements-dev.txt:5`，不入生产镜像，合理） |

---

## 4 不确定项

| # | 项 | 不确定点 | 建议下一手 |
|---|---|---|---|
| Q1 | `scripts/` 下 4 个未跟踪脚本（`analyze_motion`/`bench_render`/`bench_server`/`make_qr`） | 它们是视频/营销线的工具，与投标产品无关；`bench_server.py` 被同样未跟踪的 `packages/dsh-video-bench/` 依赖。是否入库、入哪个目录，属产品/仓库治理决策，审计无法判定 | 与 `packages/`、`marketing/`、`docs/design/视频*` 一起决定：要么整体入库并移到 `scripts/video/`，要么整体不入库。**不要半提交**（只提交 `scripts/` 而不提交 `packages/` 会留下悬空引用） |
| Q2 | `backend/skills/diagram_design/scripts/export_png.py`（D17） | 属 vendored skill（`VERSION` 2.6，最后更新 2026-10-30 `3d21618`）。上游 skill 可能要求「45 种图的 CLI 三件套」完整；手删会导致下次 `git subtree`/升级冲突 | 保留但在 `NOTICE` 标注「export_png 未接线」；或确认上游 `SKILL.md` 是否把它列为必需入口 |
| Q3 | `backend/skills/diagram_design/scripts/export_svg.py` 的 3 个死函数（D18） | 同上，vendored 代码可能被上游 `self_check.py` 以字符串/反射方式引用（本次只做了标识符统计） | 改上游版本前不动；若要动，先跑 `self_check.py` 全链路 |
| Q4 | X05 旧上传端点的「外部消费方」 | 仓库内 0 消费方已确定；但仓库外的客户端（旧版前端部署、第三方脚本、用户书签）无法从代码判断 | 上线前查一次访问日志：`/api/upload`、`/api/parse/stream/*` 的 7 日 QPS；为 0 再删 |
| Q5 | X06 第②段 `packages.display_name` 回填 | 本机库已 0 行待回填，但其他环境（客户现场、Railway 卷 `/data`）状态未知 | 在目标环境执行 `SELECT COUNT(*) FROM packages WHERE display_name='' OR display_name LIKE '标的%'` 后再删 |
| Q6 | `_drop_empty_sections`（D19） | 已随 `299073c` 入库，但**仍未接线**（全仓 0 调用）。可能是下一步「删空小节」的准备，也可能是写完忘了接；`purge()` 目前不做空小节清理 | 确认作者意图：要「没有的内容就不要体现」（提交 message 的原话）就把它接进 `purge()`；否则删掉，别让第 2 个「写好了没人调」进主干 |
| Q7 | `placeholder_purge` 新正则（已随 `299073c` 入库）把 `缺失`、`待定` 也纳入占位符 | 是否会把正常正文里的「缺失率」「待定标段」误删，属行为正确性，不在本次「死代码/无用代码」范围 | 建议单独做一次回归：对 `backend/data` 里 `blocks.content` 抽样（只读）跑 `purge` 对比 diff |
| Q8 | `db.py` 新增 `block_purged_items`（已随 `299073c` 入库） | **审计期间本机库已变**：01:26 首查时 `sqlite_master` 无此表，01:31 复查已有表 + `idx_purged_block` + **293 行**数据（本机 `127.0.0.1:8015` 有活服务，重启后 `init_db` 应用了未提交 DDL，随后真实保存路径写入了净化留痕）。因此「表未建」的兼容分支已在本机走完，但其他环境仍可能先跑旧 DDL；`block_store.py:477` 的 `DELETE ... except: pass` 在「表未建」时会静默跳过 | 建议把 `except: pass` 收窄到 `sqlite3.OperationalError` 并加一条 debug 日志，避免掩盖真实错误；部署顺序本身无风险（`init_db` 先建表） |

---

## 5 只读验证命令

以下命令均已在本次审计中实际执行，可复现全部关键结论。**不写库、不启服务、不改文件**。

```bash
cd /Users/jianghe/projects/ge-solution
# 说明：全部在仓库根执行；需要 backend 的步骤用子 shell，不会把当前目录带走。

# ── 0. 工作区与测试基线 ─────────────────────────
# 审计开始时：4 个 M（db.py + chapter_studio.py + block_store.py + placeholder_purge.py）
# 01:31 后：这 4 个已提交为 299073c，工作区只剩未跟踪目录/脚本
git status --porcelain=v1
git log --oneline -3             # 299073c（审计期间提交）/ a91b1e9（审计基线）/ f896eb5
git rev-parse HEAD               # 299073cfa1715ec531639b71139885b7aac9e03f
( cd backend && ../.venv/bin/python -m pytest ../tests/ -q --collect-only --no-header )
#   → 1147/1148 tests collected (1 deselected) in 1.39s

# ── 1. X01 缺依赖 + 启动阻断复现（核心）──────────
grep -n "jieba\|rank" requirements.txt requirements-dev.txt ; echo "exit=$?"
#   → 无输出，exit=1（两个包都没声明）
.venv/bin/python -c "import jieba,rank_bm25;print('本地有，所以本地复现不出来')"
# 触发条件复现（不装卸任何包，仅用 import 钩子模拟缺失）：
.venv/bin/python - <<'PY'
import sys, builtins
blocked={"jieba","rank_bm25"}; real=builtins.__import__
def guard(n,*a,**k):
    if n.split(".")[0] in blocked: raise ModuleNotFoundError(f"No module named {n!r}")
    return real(n,*a,**k)
builtins.__import__=guard; sys.path.insert(0,"backend")
import routes.materials
PY
#   → ModuleNotFoundError: No module named 'jieba'
sed -n '11,13p;47,50p' Dockerfile                  # 只装 requirements.txt / CMD uvicorn main:app
sed -n '6p' railway.toml; sed -n '4p' render.yaml   # 两个部署面都指向同一 Dockerfile

# ── 2. X02 缺失 re-export ───────────────────────
PYTHONPATH=backend .venv/bin/python -c "from infra.llm import dispatch_package_merge_json"
#   → ImportError（修复前）；修复后应打印 ok
grep -rn "dispatch_package_merge_json" --include=*.py .
#   → dispatcher.py:1096 定义 / routes/materials.py:743 import、:748 调用；__init__.py 0 命中
sed -n '739,750p' backend/routes/materials.py       # try/except 吞异常

# ── 3. X03 硬编码开关 ───────────────────────────
grep -rn "MATCH_MODE" --include=*.py backend tests
#   → nodes.py:563 定义（常量）/ :648 恒真 return / :655 旧路径注释；test_nodes.py:231 monkeypatch
sed -n '646,660p' backend/orchestrator/nodes.py

# ── 4. X05 旧上传管线 ───────────────────────────
grep -rn "api/upload\|parse/stream\|supported-formats" frontend tests | wc -l   # → 0
grep -rn "upload_router\|routes\.upload" --include=*.py backend
#   → main.py:18（import）、main.py:243（include_router）
grep -rn "utils\.validators\|validate_uuid" --include=*.py backend tests  # → 只有 upload.py 与自身

# ── 5. 死代码批量核验 ───────────────────────────
# 命中 1 = 只有定义行；命中 2 = 定义 + __all__ 条目（unhealthy_snapshot / review_points_of）
for sym in _route_after_generate get_sink unhealthy_snapshot review_points_of \
           match_spec_nodes VALID_KINDS _CIRCLED_DIGITS _NUM_PREFIX_RE _ARTIFACT_RE \
           _drop_empty_sections _migrate_review_v3; do
  printf "%-24s %s\n" "$sym" "$(grep -rn "\b$sym\b" --include=*.py backend tests | wc -l)"
done
grep -rn "\bmerge_state\b" --include=*.py backend tests | grep -v test_state.py
#   → 只有 state.py 自身（生产 0）

# ── 6. domain 三套 repository 零生产引用 ────────
for cls in SpecRepository ProposalRepository MaterialRepository; do
  echo "== $cls =="; grep -rn "$cls" --include=*.py backend | grep -v "^backend/domain/.*repository\.py"
done
#   → 每个类只剩自身包的 __init__.py（纯转出）；无任何实例化

# ── 7. 未使用 import（每行应为 0 = 除 import 行外无使用）──
for f in backend/infra/llm/dispatcher.py backend/infra/retrieval/rerank.py \
         backend/agents/wang_anshi.py backend/agents/bao_zheng.py; do
  printf "%-46s json.= %s\n" "$f" "$(grep -c 'json\.' "$f")"
done
printf "%-46s os.= %s\n" backend/scripts/recut_old_materials.py \
  "$(grep -c 'os\.' backend/scripts/recut_old_materials.py)"
printf "%-46s re.= %s\n" backend/infra/parser/docx.py "$(grep -c 're\.' backend/infra/parser/docx.py)"
grep -n "Optional" backend/infra/parser/scope_index.py                  # → 只有 :22
grep -n "Awaitable\|asynccontextmanager" backend/orchestrator/runner.py # → 只有 :25,:26
grep -n "Finding" backend/orchestrator/nodes.py                        # → 只有 :31

# ── 8. X06 数据库只读核验（mode=ro；注意本机有活服务在写库）──
( cd backend && python3 - <<'PY'
import sqlite3, os
con=sqlite3.connect(f"file:{os.path.abspath('data/ge_solution.db')}?mode=ro", uri=True)
c=con.cursor()
for sql in ["PRAGMA user_version","PRAGMA foreign_keys",
            "SELECT COUNT(*) FROM packages WHERE display_name LIKE '%|%'",
            "SELECT COUNT(*) FROM workflow_runs WHERE package_id IS NULL",
            "SELECT COUNT(*) FROM workflow_runs",
            "SELECT COUNT(*) FROM sqlite_master WHERE name='block_purged_items'"]:
    print(sql, "=>", c.execute(sql).fetchone())
PY
)
#   → user_version=0 / foreign_keys=0 / 脏名=0 / NULL package_id=41/50 / 新表存在=1

# ── 9. 一次性脚本无入口 ─────────────────────────
for s in normalize_extract_fields recut_old_materials cleanup_pending_placeholders \
         normalize_diagram_blocks recover_gaps_from_revisions add_table_captions; do
  printf "%-34s %s\n" "$s" "$(grep -rn "$s" --include=*.sh --include=*.toml \
      --include=*.yml --include=*.yaml --include=*.json . 2>/dev/null | grep -v .venv | wc -l)"
done
#   → 全 0（无 cron / 无 CI / 无启动脚本引用）

# ── 10. vendored skill 的运行时接线 ─────────────
grep -n "SKILL_DIR\|scripts/" backend/services/diagram_svg.py backend/services/diagram_design.py
#   → diagram_svg.py:9,13（export_svg.py）；diagram_design.py:27,326（self_check.py）
grep -rn "export_png" --include=*.py backend | grep -v "backend/skills/"   # → 只有一条注释

# ── 11. 最后提交日期（判断「遗弃时间」）──────────
for f in backend/domain/spec/repository.py backend/domain/proposal/repository.py \
         backend/domain/material/repository.py backend/utils/sse.py \
         backend/agents/shen_kuo.py backend/infra/retrieval/rerank.py \
         backend/scripts/recut_old_materials.py; do
  printf "%-52s %s\n" "$f" "$(git log -1 --format='%ad %h' --date=short -- $f)"
done
```

---

### 附：本报告未覆盖（按分工）

- `backend/routes/*`、`backend/services/*` 的死端点/重复 SQL/死服务（另一名审计员）。本报告只在「某死代码的消费者在 routes/services」时做事实性引用（X02、X05、U07、X10）。
- `backend/main.py`（旧审计 C06 的 `_index_path` 等）不在本次目录清单内，未复核。
- 前端契约漂移（`frontend/assets/*`）仅用于判断旧路由是否有消费方，未做前端审计。
