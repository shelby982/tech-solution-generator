# 代码审查（第二轮，2026-11-09）

> 范围：当前 HEAD（`b0397c5`）的后端 45,697 行 Python、前端 27,497 行 JS/HTML/CSS、测试 24,353 行。
> 基线：后端 **1126 passed** / 前端 **164 passed**；端点 **197**；无未提交的跟踪文件。
> 方式：静态扫描（未用 import、零引用符号、路由/客户端调用）+ 针对评审统一改造与安全边界的代码走查。
> 本报告只列需要动作的问题；本轮已修的放在第 6 节。

## 1. P0/P1：安全边界

### 1.1 CORS 通配 + 绑定 0.0.0.0 + 无认证（P0，条件触发）

- `backend/main.py:220-227`：注释写"仅允许同源"，实际 `allow_origins=["*"]`、`allow_methods=["*"]`。
- `backend/main.py:388-393`：`python main.py` 默认 `host="0.0.0.0"`，且 `reload=True`。
- 全站无认证；`/api/configs` 可增删模型配置，`/api/projects/*` 可读写项目、启动工作流、导出文件。

影响：如果服务被暴露到 LAN/公网，任何客户端都可读项目、消耗模型额度、删除配置。即使是本机，恶意网页也可借 `Origin: *` 直接访问 `127.0.0.1:8000`（CORS 通配允许读取响应）。

建议：
- 默认 `host="127.0.0.1"`；需要局域网时显式用命令行参数。
- 删除 CORS 中间件（页面同源）或只允许实际前端 origin。
- 至少加一个本地 token/会话认证；`/api/configs`、`/api/workflow/*`、文件下载优先。
- `reload=True` 只留在 dev 启动脚本，不要放 `__main__` 默认路径。

### 1.2 前端 88 处动态 `innerHTML`，缺少系统性 XSS 验证（P2）

- `frontend/assets/*.js` 有 88 处 `innerHTML = ...${...}`；`esc()` 覆盖不完整时，模型生成内容/上传文件名会进入 DOM。
- 抽查 `intake.js`、`review.js` 的主要插值已用 `esc`，但 `chapter-studio.mjs`/`mode-outline.js` 体量大，未逐条验证。

建议：加一个"所有 API 文本进入 innerHTML 前必须过 esc"的约定/测试；至少对评审问题、素材文件名、章节标题做 XSS 冒烟。

## 2. P1：评审统一后的正确性/一致性

### 2.1 评审期间编辑 → 旧结果被标 current（**本轮已修**）

`_run_review` 原先在 aggregate 阶段取"完成时"版本，模型跑的过程中用户改正文会把旧评审写成 current。
已改为在 `_run_review` 记录 `review_versions`（评审开始版本），aggregate 用该版本落库；失败时 freshness=stale。
回归测试：`test_aggregate_stamps_versions_captured_at_review_start`。

### 2.2 `project_progress` 仍是 N+1（P2）

`backend/services/project_progress.py:122-145` 对每个 `chapter_reviews` 行调用 `review_freshness`，后者会查
chapter_materials / material_chunks / spec_requirements / review_points / project_writing_facts。百章项目每次状态栏轮询可能几十到上百次查询。

建议：批量计算本章版本（一次查 chapter 级证据），或缓存 `(block_id, content_version, evidence_version)` 的 freshness。

### 2.3 `scoring_risk` 仍是 N+1（P2）

`backend/services/scoring_risk.py:71-79` 每章一次 `review_freshness` + `evidence_hash`。`scoring_verdict` 已复用它的
`chapter_freshness`，但 `scoring_risk` 自身在百章项目仍慢。

### 2.4 `reviews` 无唯一约束（P2）

`backend/db.py` 的 `reviews` 只有普通索引 `(thread_id, block_id)`。`ReviewRepository.add_finding` 仍是纯 INSERT，
并发/重复调用可能产生同 `(thread_id, block_id, agent)` 多行；当前数据恰好无重复。

建议：清理后加唯一索引，或把所有写路径统一到 delete+insert 的 `review_store.save_agent_finding`。

### 2.5 历史记录缺"终评采纳"事件（P3）

复用一个 writing 评审时只提升 `chapter_reviews.stage=final`，不写 `review_history`。
当前 UI 显示当前态 final + 历史 writing，但没有"何时被终评采纳"的时间线。
如果审计需要，补一条 `stage='final', source='adopted'` 的历史行。

### 2.6 全局一致性是确定性检查（P3）

`review_consistency.py` 只查重复 issue、satisfied/score 冲突、critical 未收口；不能发现跨章语义矛盾，
且"同一问题在多章重复"可能误报。需要 LLM pass 才能做语义一致性。

## 3. P2：数据/迁移/运行时

### 3.1 没有 schema 版本，迁移靠大量 ALTER + 吞异常

- `db.init_db` 里几十个 `ALTER TABLE`，多个 `except Exception: pass`（如 `db.py:675/726/746`）。
- 没有 `PRAGMA user_version` 或 `schema_migrations`；无法判断库已迁到哪一版，失败也不阻断启动。

建议：建 `schema_migrations`，每次迁移记版本；失败必须显式报错/回滚，不要静默继续。

### 3.2 外键未开启，存在孤儿数据

`PRAGMA foreign_keys` 默认 OFF；`chapter_reviews/review_history/review_issue_states` 只靠 reset 清理。
`blocks` 是 AUTOINCREMENT，暂不会错挂，但 `reviews.thread_id` 等 FK 只是注释。

### 3.3 `funnel_events` 表遗留

功能已删除（代码不建表/不写入），但本地库仍有 `funnel_events` 表与 3 条测试事件。
建议 `DROP TABLE IF EXISTS funnel_events;` 或在下次迁移中清理。

### 3.4 13 张 0 行表

见 [数据治理待办](2026-11-09-data-governance-backlog.md)；不是代码 bug，但 schema 里长期保留会干扰判断。

## 4. P3：死代码/未使用符号

AST + 调用扫描确认（排除路由 handler）：

| 位置 | 符号 | 说明 |
|---|---|---|
| `backend/services/block_store.py:80` | `update_project` | patch/lock 端点删除后无调用 |
| `:194` | `update_material_parsed` | 无调用 |
| `:931` | `create_snapshot` | lock 端点删除后无调用 |
| `:1000` | `list_chunks_by_project` | 无调用 |
| `:1091` | `list_chunks_by_materials` | 无调用 |
| `backend/infra/llm/usage.py:52` | `get_sink` | 无调用 |
| `backend/infra/parser/outline_builder.py:329` | `match_spec_nodes` | rerank 清理后无调用 |
| `backend/routes/todo_v3.py:156` | `GateFixBody` | 无引用请求模型 |
| `backend/routes/chapter_sse.py:259` | `SelectionBody` | 无引用 |
| `backend/routes/material_library.py:664` | `SwapCitationBody` | 无引用 |
| `backend/routes/chapter_writing.py:39/52/131` | `FiguresPlanBody/PlanConfirmRequest/ConfirmChapterBody` | 无引用 |
| `backend/routes/chapter_studio.py:344/459` | `SectionBody/AlignBody` | 无引用 |
| `backend/domain/review/repository.py:54` | `clear_thread` | 无调用；`add_finding` 仅测试用 |

另有 **2 个测试专用端点**：
- `GET /api/review/{thread_id}`（`routes/review.py:19`）—— 前端 0 调用，仅 `tests/routes/test_review.py`。
- `GET /api/projects/{id}/diff`（`routes/export.py:75`）—— 前端 0 调用，仅 `tests/routes/test_package_scope.py`。

建议：删除未引用符号；两个测试端点若要保留，标 `deprecated` 或补明确 API 契约说明。

## 5. 测试/前端缺口

- 无端到端/浏览器自动化测试；评审页的「终评/编写评审」标签、全局一致性栏只有手工 smoke。
- 无 100+ 章性能测试；`scoring_risk`/`project_progress` 的 N+1 未压测。
- 投递/导出、上传解析等长链路仍以单测为主。
- 前端 `innerHTML` XSS 无自动检查。

## 6. 本轮已修

| 问题 | 提交 |
|---|---|
| 评审期间编辑导致旧结果 current | `b0397c5` |
| 老 checkpoint 落库/提升 | `7704b34` |
| 终评 issue 状态同步 | `37f2440` |
| 旧无版本章评审只重评一次 | `1740d8f` |
| 旧 checkpoint/进度/freshness/history 版本 | `c1d1165` |
| 终评范围/去重 | `d86c9ec` |

## 7. 建议顺序

1. **安全边界**：host/CORS/auth（1.1）。
2. **数据迁移版本化 + FK**（3.1/3.2）。
3. **N+1 压测与优化**（2.2/2.3）。
4. **死符号/测试端点清理**（4）。
5. **XSS 冒烟 + 浏览器回归**（1.2/5）。


---

## 8. 处理状态（2026-11-09 第十一轮）

- 1.1 安全边界：已处理。CORS 移除，默认 127.0.0.1，新增可选 `APP_AUTH_TOKEN`，`start.sh` 同步。
- 2.2/2.3 N+1：已做内容优先短路径 + specs/points/facts 请求级 cache；仍有逐章 cards/materials 查询，
  极端百章项目可继续压测。
- 3.1 schema 版本：已加 `schema_migrations`/`user_version`。
- 3.2 外键：已加 `APP_ENFORCE_FK` 开关；默认仍关闭，因为历史库有孤儿行，需先清理。
- 4 死符号：已删；两个测试端点已标 `deprecated=True`。
- 1.2 XSS：仅做了标题注入抽查，未做全量 `innerHTML` 自动检查，仍是待办。


---

## 9. 第十二轮处理结果

- 1.1 安全边界：已处理（CORS 移除、127.0.0.1、可选 token、start.sh）。
- 1.2 XSS：已加 `innerhtml-escape.test.mjs` 针对性检查；全量 sanitizer 仍未做。
- 2.2/2.3 N+1：cards/materials 已批量化，specs/points/facts 有请求级 cache；仍有极端项目压测空间。
- 3.1 schema：已加 `schema_migrations`/`user_version=2`。
- 3.2 外键：已清理 8 条孤儿行，默认开启，实库 `foreign_key_check` 为空。
- 3.3 `funnel_events`：已 DROP。
- 4 死代码/端点：死符号已删；`/api/review`、`/api/projects/{id}/diff` 已删，端点 195。
- 5 测试：后端 1130 / 前端 165。
