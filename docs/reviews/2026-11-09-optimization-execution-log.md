# 代码优化执行记录（2026-11-09）

> 依据：[2026-11-09 代码审查报告](2026-11-09-code-review-dead-code-and-inconsistency.md)
> 基线：HEAD `299073c`；执行前后端 1152 passed / 前端 161 passed
> 结果：后端 1155 passed / 前端 161 passed；`git diff --check` 通过；端点 250 → 207

---

## 0. 总览

| 项 | 结果 |
|---|---|
| 变更文件 | 78 个，+172 / **-2,482** 行（另有 7 个文件重命名/移动） |
| 删除整文件 | 9 个（3 后端路由/工具 + 1 前端 no-op 模块 + 2 个损坏 gitlink + 2 个不可达整页 + 1 个孤儿页） |
| 删除端点 | **43 个**（250 → 207） |
| 删除后端行数 | 47,977 → 46,209（-1,768） |
| 删除前端行数（HTML+JS/MJS） | 42,916 → **30,402**（-12,514；其中旧整页 10,542、孤儿页 331、死函数/CSS 等其余） |
| 新增回归测试 | 3 个（大小上限、重名函数守卫、包级导出） |

---

## 1. P0：风险修复（全部完成）

| # | 修复 | 位置 | 验证 |
|---|---|---|---|
| 1 | 补 `jieba==0.42.1`、`rank-bm25==0.2.2` 声明 | `requirements.txt` | 版本与本地 `.venv` 一致；`keyword.py` 顶层 import 不再依赖偶然安装 |
| 2 | 补 `dispatch_package_merge_json` 转出与 `__all__`；顺带补 `dispatch_subpoints_json` | `backend/infra/llm/__init__.py` | 新增测试 `test_package_merge_json_exported_from_infra_llm_package` |
| 3 | 恢复上传 50MB 上限（`UploadFile.size` 预检 + 读取后字节兜底） | `routes/materials.py` | 新增测试 `test_oversized_upload_rejected`；注意未用 `Request`，因为 `chapter_studio` 会直接调用该函数 |
| 4 | 删除重名旧 `update_block_source`；`nodes.py` 改为先建 `block_id → 主键` 映射后按新签名写入 | `services/block_store.py`、`orchestrator/nodes.py` | 新增架构守卫 `test_no_duplicate_top_level_functions`；匹配依据现在会真正落库 |
| 5 | 恢复 `.dockerignore` 缺失排除项 | `.dockerignore` | 追加 `backend/data/`、`docs/`、`skill/`、`ge-solution/`、本地工具目录等 |
| 6 | 补 `.gitignore` 并移除损坏 gitlink | `.gitignore`、`.claude/worktrees/*` | `git rm --cached` 两条目；gitlink 不再进入索引 |

---

## 2. 删除的死代码

### 2.1 整模块

- `backend/routes/upload.py`（292 行，3 个零调用端点，旧上传管线）
- `backend/routes/changelog.py`（71 行，零调用，且依赖运行目录有 `.git`）
- `backend/utils/validators.py`（11 行，仅旧上传使用）
- `frontend/assets/context-panel.js`（70 行，运行时 no-op；DOM 挂点早已不存在）
- 同时删除 `main.py` 的 4 行 import/注册，`utils/sse.py` 收敛为只保留 `format_sse_event`（-82 行）

### 2.2 端点（43 个，250 → 207）

| 模块 | 删除端点 |
|---|---|
| upload / changelog / config | 3 + 1 + 7（旧单配置 5、reorder、PUT configs/{id}） |
| materials / blocks | 410 的 `POST /projects/{id}/outline`、`map-sources`、`blocks/{id}/material-search` |
| chapter_sse / chapter_studio | `rewrite-selection`；`self-heal`、GET outline、`section-results`、`align-outline`、非流式 `POST studio/section`、`GET projects/{id}/todo` |
| chapter_writing | 裸 `GET writing-plans/{id}`、`confirm-chapter`、`figures-plan`、`confirm`、`GET writing-jobs` |
| citations / material_library | `link`/`unlink`/`search`/`reject`；`recut`/`recut-all`/`citations/swap`、项目级 `materials/needs` |
| todo_v3 / review_batch / diagram_assets | `todo/chapter`、delivery-gate auto/fix；batch latest、issues summary；`regenerate` |
| projects / writing_settings / workflow | `basis`；GET 项目级 writing-settings；`analyze-points` |

### 2.3 前端死函数与死导出

- 删除 14 个零引用函数：`mode-outline.js`（`readConfirmedPkg` / `renderPackageCards` / `renderPointsAlignment` / `lastRunErrorMessage`）、`workspace.js`（`currentView`、`inPlace` 变量）、`diff-review.html`（`fmtTime`）、`chapter-studio.mjs`（9 个：`materialRequestsHtml`、`refreshPlanRequirements`、`openDesignDiagramDialog`、`openDeploymentDiagramDialog`、`openWritingPlan`、`showProposalBar`、`restoreLastRevision`、`todoPaneHtml`、`gapGroupsHtml`）
- 删除 7 个零外部引用导出：`setSelectedRow`、`currentMode`、`REVIEW_PATH`、`materialRoleLabels/Hints`、`EVIDENCE_SOURCE_LABELS`（保留内部使用）、`CHAPTER_STAGES`；`openWritingSpecs/openMaterialsHub` 去掉多余 `export`
- 删除 `mode-dispatch.js` 的失效点击委托（`a[data-mode]` 永不存在）
- 删除 `mode-outline.js` 两个死变量（`currentReader`、`pendingRenameId`）
- 删除 9 个未使用 import（mode-editor / mode-outline / workbench）
- 删除 `frontend/.DS_Store`

### 2.4 后端死符号与死 import

- 删除 20 组零引用符号：`recut_material`、`project_summary`、`list_materials_by_ids`、`_CITE_IN_BODY`、`MODE_LABELS`、`NO_MATERIAL_HINT`、`LEVEL_ORDER`、`ZONE_H`、`REQUIREMENT_PREFIX`、`_ACTION_LABEL`、`_parent_key`、`rollup_totals`、`_edge`、`_is_legal_block_start`、`coverage_for_section`、`execution_outline`、`_row_dict`、`uninstall`、`_migrate_review_v3`
- 删除 `_index_path`（`main.py`）
- 批量删除 41 个未使用 import（其中修复过一次相对导入被误改的问题：`from .toc import`）

---

## 3. 非删除类修复

| 项 | 处理 |
|---|---|
| `tests/frontend/module-imports.test.mjs` 版本守卫盲区 | 两个文件数组改为**动态枚举** `frontend/*.html` 与 `assets/*.{js,mjs}`，新增页面/模块不再漏检 |
| README 与代码不一致 | 更新端点/模块数（21 模块 / 207 端点 / workflow 22）、agent 描述（5 核心 + 章节团队）、前端主路径（补 `landing`）、补 `requirements-dev.txt` 安装步骤 |
| `.gitignore` / `.dockerignore` 缺口 | 补 `backend/data/`、本地工具目录、`.claude/worktrees/`、`.pytest_cache/`、`.bench-cache/`、`.dsh-verify/` |

---

## 4. 验证结果

```bash
.venv/bin/python -m pytest -q          # 1155 passed, 1 deselected
node --test tests/frontend/*.test.mjs  # 161 passed
.venv/bin/python -m compileall -q backend tests
git diff --check                       # 通过
```

新增测试：

- `tests/test_no_duplicate_top_functions.py`：防止再次出现同模块重名顶层函数
- `tests/routes/test_materials_upload.py::test_oversized_upload_rejected`
- `tests/infra/test_llm_dispatcher.py::test_package_merge_json_exported_from_infra_llm_package`

---

## 5. 本轮未做（需产品/架构决策）

| 项 | 原因 | 关联报告项 |
|---|---|---|
| ~~删除两个旧整页~~ | **已完成（第二轮）**：删除前打 tag `pre-legacy-pages-removal` | FE-D01/D02 |
| ~~`diff-review.html` 孤儿页~~ | **已完成（第二轮）**：`/review` 页已有「与上次评审对比 / 章节版本」，按重复功能删除 | FE-I02 |
| ~~`bench.html` 迁出 `frontend/`~~ | **已完成（第二轮）**：移到 `tools/bench/index.html` + README | FE-D03 |
| domain 三套 repository（315 行 + 672 行测试） | Phase 7 去留未定 | BE-D01–D03 |
| rerank / doc_summary 链路（约 1,000 行含测试） | 是否保留回滚能力需产品确认 | BE-X03/X04 |
| 双评审表（`reviews` + `chapter_reviews`） | 需数据模型决策 | 报告 §4.5 |
| provider 默认值 4 份硬编码 | 需确定权威源与前端接线方式 | 报告 §4.4 |
| 753 块 / 2,446 行死 CSS | 无自动化视觉回归，需先抽 `three-mode.css` 并逐屏截图 | FE-D20–D26 |
| ~~一次性脚本 7 个~~ | **已完成（第二轮）**：6 个移入 `scripts/oneoff/` 并修正自举路径；`build_response_templates.py` 保留（有文档引用） | 报告 §2.6 |
| 剩余「仅测试调用」端点（coverage-review、studio/draft、diagram image/preview 等） | 需要确认是否补前端入口 | backend-a §3 |
| `placeholder_purge._drop_empty_sections`（新提交、无人调用） | 作者意图是"只有标题的小节整段删除"，接线会改变正文生成行为，留给下一批产品确认 | BE-B/D19 |
| `api.js` 11 个零调用客户端方法 | 需与后端是否保留对应能力一起决策 | FE-U01 |

---

## 6. 回滚建议

- 变更尚未提交；建议按主题拆分提交（P0 修复 / 端点删除 / 前端死代码 / 配置与测试）。
- 删除文件均走 `git rm`，可从历史恢复；如需整体回退：`git checkout -- .` 加 `git reset` 恢复索引（注意先用 `git stash` 保存需要的改动）。
- 删除死页前建议打 tag（若下一步执行 P2）。

---

## 7. 第二轮：继续处理（2026-11-09 下午）

### 7.1 删除两个不可达整页（-10,542 行）

- 删除前打 tag：`pre-legacy-pages-removal`（回退：`git checkout pre-legacy-pages-removal -- frontend/workbench.html frontend/project-init.html`）。
- `git rm frontend/workbench.html frontend/project-init.html`。
- 同步更新：`backend/main.py:58` 注释改为 `project-intake.html`；`tests/frontend/material-roles.test.mjs` 注释改指 `mode-outline.js`；`docs/design/UI一致性审计.md` 第 306/730 行标注已删除。
- 验证：`node --test tests/frontend/*.test.mjs` 161 passed（版本守卫已改为动态枚举，无需再维护白名单）。

### 7.2 删除孤儿页 `/diff-review`（-331 行）

- `review.html` 已有「与上次评审对比 / 章节版本」两个视图，`diff-review` 的功能被覆盖且全站零入口。
- 删除 `frontend/diff-review.html` 与 `backend/main.py` 的 `_diff_review_path`、`@app.get("/diff-review")`。
- 更新 `routes/revisions.py` 注释与 `workspace.js` 注释；README 前端主路径去掉 `diff-review`。

### 7.3 `bench.html` 移出产品资源树

- `frontend/bench.html` → `tools/bench/index.html`，并新增 `tools/bench/README.md` 说明它是**独立本地工具**、不参与产品部署。
- 同步修改本机未跟踪的 `scripts/bench_server.py` 路径（`BENCH_DIR = ROOT/'tools'/'bench'`、`index.html`）。
- 更新 3 份 `docs/design/视频逐帧工作台-*.md` 的路径引用。

### 7.4 一次性脚本归档到 `scripts/oneoff/`

- 移入 6 个：`normalize_extract_fields.py`、`recut_old_materials.py`（原 `backend/scripts/`）、`cleanup_pending_placeholders.py`、`normalize_diagram_blocks.py`、`recover_gaps_from_revisions.py`、`add_table_captions.py`。
- 修正每个脚本的 `sys.path` 自举（`parents[2]` / `SCRIPT_DIR.parent.parent/'backend'`）与 docstring 用法。
- 新增 `scripts/oneoff/README.md`；`scripts/README.md` 增加归档说明；历史设计文档中的命令路径同步更新。
- 验证：逐个 `--help` 可执行。
- 保留：`scripts/build_response_templates.py`（被文档引用）、`scripts/clear_data.py`（被 `clear_data.sh` 调用）、`check.sh`/`ui-selfcheck.py`。

### 7.5 `MATCH_MODE` 从"假开关"改为真环境变量

```python
MATCH_MODE = (os.environ.get("MATCH_MODE", "assign") or "assign").strip().lower()
if MATCH_MODE not in ("assign", "rerank"):
    MATCH_MODE = "assign"
```

- 默认行为不变（assign）；需要回滚旧 rerank 路径时设置 `MATCH_MODE=rerank`，无需改代码发版。
- 仍保留旧路径（约 430 行 + 测试），是否彻底删除留产品决策。

### 7.6 第二轮验证

```bash
.venv/bin/python -m pytest -q          # 1155 passed, 1 deselected
node --test tests/frontend/*.test.mjs  # 161 passed
python -m compileall -q backend scripts/oneoff
git diff --check                       # 通过
```

### 7.7 文档/引用同步

- `README.md`：主路径去掉 `diff-review`；端点/agent/依赖说明保持与代码一致。
- `docs/design/UI一致性审计.md`、`docs/design/视频逐帧工作台-*.md`、`docs/superpowers/plans/2026-09-21-*.md` 的相关路径已更新。
- 本轮新增的审计/执行记录本身也作为文档保留在 `docs/reviews/`。

---

## 8. 待处理项核实

第二轮完成后对剩余待处理项做了一次只读复核，结论见
[2026-11-09-pending-items-verification.md](2026-11-09-pending-items-verification.md)：
domain 三仓、doc_summary、双评审表、provider 默认值、死 CSS、`api.js` 未用方法、测试专用端点、
`_drop_empty_sections`、13 张 0 行表均仍成立；另新发现 `diagram-design/types` 端点零调用、
死 CSS 因删旧页增至约 827 块 / 3,980 行。

---

## 9. 第三轮：删除 `diagram-design/types`

- 删除 `GET /blocks/{id}/studio/diagram-design/types`（`backend/routes/diagram_assets.py`，核实为前端/测试 0 调用）。
- 同步从 `services.diagram_design` 的 import 列表移除 `SKILL_TYPES`（`recommend_type` 仍被 `create_diagram` 使用，保留）。
- 端点总数：207 → **206**；README 同步更新。
- 验证：后端 1155 passed / 前端 161 passed；全仓无 `diagram-design/types` 残留引用。

---

## 10. 第四轮：按决策清理（2026-11-09 下午）

### 10.1 domain 三仓删除

- 删除 `backend/domain/{spec,proposal,material}/repository.py`（315 行）及对应三个测试文件（672 行）。
- 更新三个 `__init__.py` 的文档串/转出/`__all__`，domain 只保留 models 与纯函数。
- 验证：`grep -rn "SpecRepository|ProposalRepository|MaterialRepository" backend tests` = 0；`tests/domain` 80 passed。

### 10.2 rerank 一次性删干净

- 删除 `backend/infra/retrieval/rerank.py`（含 `llm_rerank`）；`Match` 数据类迁入 `domain/material/models.py` 并从 `domain.material` 导出。
- `agents/shen_kuo.py` 重写：删除 `match()` 与 LLM 配置依赖，只保留 `retrieve_for`（BM25 关键词，零模型调用）。
- `orchestrator/nodes.py`：删除 `MATCH_MODE`、rerank 分支与 `agent` 参数；匹配固定走 `_match_by_assignment`；`graph.py` 同步调用签名。
- `agents/prompts.py` 删除 `RERANK_SYSTEM`/`RERANK_INSTRUCTIONS`/`build_rerank_user` 与 `__all__` 条目。
- 测试：`test_shen_kuo.py` 只保留 `retrieve_for` 用例；`test_retrieval.py` 删除重排用例；`test_prompts.py`/`test_prompt_cache_prefix.py` 删除重排断言；`test_nodes.py` 删除 rerank 用例。
- 说明：原先 `MATCH_MODE=rerank` 的回滚开关随之取消；如仍需旧重排，需从 git 历史恢复。

### 10.3 doc_summary 链路删除

- 删除 `dispatcher._generate_doc_summary` / `dispatch_doc_summary`，从 `infra.llm.__all__` 移除。
- 删除 `_stream_generate` / `dispatch_stream_generate` 的 `doc_summary` 形参与注入块；目录派生 payload 去掉 `doc_summary`。
- 删除 `SpecParseResult.doc_summary/doc_summary_error`、`WorkflowState` 两字段、张衡 parse/draft_outline 的参数与 payload、闸门 1 快照字段、blocks 路由传参。
- 测试同步删除/改写（`test_zhang_heng.py`、`test_nodes.py`、`test_graph.py`、`test_runner.py`、`test_state.py`、`test_prompts.py` 等）。
- 验证：`grep -rn "doc_summary" backend tests` = 0（不含历史文档）。

### 10.4 provider 前端单一来源

- 新增 `frontend/assets/model-config.mjs`：`PROVIDER_DEFAULTS`、`DEFAULT_MODEL_OPTIONS`、`DEFAULT_PROVIDER` 唯一定义。
- `mode-outline.js` 与 `projects.html` 删除各自硬编码表并改为 import；`renderDefaultModels` 共用同一档位列表。
- 统一后的 deepseek 预填值从两份不一致收敛为 `deepseek-v4-pro`（后端 `DEFAULT_MODELS` 仍是 `deepseek-chat`，二者是否对齐需产品确认）。
- 版本号统一为 `?v=20261016-v503`；`node --test` 161 passed。

### 10.5 死 CSS 直接清理

- 用规则级扫描（语料 = 现存 HTML + JS/MJS + 前端测试 + 后端生成的 `class="..."`）删除死规则，含 `@media` 递归。
- 外部 CSS：762 个死块，行数 **5,244 → 2,848**；其中 `styles.css` 2,728 → 942、`review.css` 653 → 359、`chapter-studio.css` 1,373 → 1,077。
- 内联样式：`three-mode.html` style 行 3,204 → 2,768（94 块）、`projects.html` 607 → 550（13 块）。
- 复扫剩余死块 = 0；花括号平衡；后端 1112 / 前端 161 测试通过。
- **注意**：本轮无浏览器视觉回归。建议上线前对 5 个页面做一次人工 smoke（尤其三模式/项目准备页）。

### 10.6 第四轮后指标

| 项 | 数值 |
|---|---|
| 变更 | 106 个文件，+564 / **−6,745 行** |
| 端点 | 206 |
| 后端代码 | 45,340 行 |
| 前端 HTML+JS | 5,588 + 19,082 行 |
| 前端 CSS | 2,848 行（原 5,244） |
| 测试 | 后端 1112 passed / 前端 161 passed |

### 10.7 仍待决策

- 双评审表（`reviews` + `chapter_reviews`）统一。
- `api.js` 15 个零调用方法、3 个测试专用端点。
- `placeholder_purge._drop_empty_sections` 接线或删除。
- 13 张 0 行表的功能验收。
- 前端 provider 表与后端 `DEFAULT_MODELS` 是否对齐、死 CSS 后的人工视觉回归。
