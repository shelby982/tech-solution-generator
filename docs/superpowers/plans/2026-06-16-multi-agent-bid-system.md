# Multi-Agent Bid System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 ge-solution 单体重构为 5 个 agent（张衡/沈括/诸葛亮/王安石/包拯）协作的领域驱动架构，引入 LangGraph 编排与 3 个人工闸门，新增双视角评审能力。

**Architecture:** 5 层（routes / orchestrator / agents / domain / infra），LangGraph StateGraph + SQLite checkpointer 编排，agents 间严格通过 WorkflowState 通信，前端三页面改造 + 新增评审看板。

**Tech Stack:** FastAPI、LangGraph、SQLite、原生 JS、pytest

**Spec:** `docs/superpowers/specs/2026-06-16-multi-agent-bid-system-design.md`

---

## 全局约定

- **TDD**：每个任务先写失败测试，再实现，再验证通过，最后 commit
- **Commit 风格**：沿用现有 `feat(scope): xxx` / `refactor(scope): xxx` / `test(scope): xxx`
- **测试运行**：`cd backend && pytest tests/<path> -v`
- **环境变量**：开发期默认 `LLM_MODE=mock` 跑测试；端到端测试用 `LLM_MODE=real`
- **代码风格**：保持当前工程风格（中文注释、函数命名、文件组织规范）
- **每 Phase 末尾必有一次回归运行**：跑全量 pytest 确保不退化

---

## Phase 1：基础设施层（infra/）

**目标**：把 `services/parser.py`、`retrieval.py`、`llm.py`、`docx_generator.py` 平移到 `infra/` 下，按职责拆分。**逻辑不变，纯搬家**，老代码暂保留。

### Task 1.1：搭建 infra/llm 骨架 + LLM mock

**Files:**
- Create: `backend/infra/__init__.py`
- Create: `backend/infra/llm/__init__.py` — 暴露 `LLMConfig`、`dispatch_*` 公开 API
- Create: `backend/infra/llm/clients.py` — OpenAI/Claude 客户端 + `_verify_*` + `_stream_*` + `_generate_oneshot_*`
- Create: `backend/infra/llm/dispatcher.py` — `dispatch_stream_generate`、`dispatch_doc_summary`、`dispatch_outline_json`、`dispatch_block_write`、`generate_section_outline`、`generate_letter_content`、`is_letter_section`
- Create: `backend/infra/llm/_mock.py` — 按 prompt 关键词匹配返回 fixture（"提炼以下八项内容" / "撰写投标承诺书" / "评审"）
- Create: `tests/infra/test_llm_dispatcher.py`

- [ ] 写 mock 路由测试：`LLM_MODE=mock` 时调 `dispatch_outline_json` 返回固定 8 字段 JSON
- [ ] 把 `services/llm.py` 内容按职责拆到 clients.py / dispatcher.py
- [ ] 在 clients.py 入口判断 `os.environ.get("LLM_MODE")=="mock"`，命中则走 `_mock.respond()`
- [ ] 跑测试：`pytest tests/infra/test_llm_dispatcher.py -v`
- [ ] commit：`refactor(infra): extract llm module from services`

### Task 1.2：搭建 infra/parser

**Files:**
- Create: `backend/infra/parser/__init__.py` — 暴露 `parse_document`、`ParsedDocument`、`Section`
- Create: `backend/infra/parser/docx.py`、`pdf.py`、`toc.py`（`HEADING_PATTERNS`、`extract_special_marks` 等公共工具）
- Create: `tests/infra/test_parser.py`

- [ ] 用现有 fixture（任意 docx）写 TOC 提取测试 + heading scan 兜底测试
- [ ] 把 `services/parser.py` 按 docx/pdf/toc 拆分
- [ ] 跑测试通过
- [ ] commit：`refactor(infra): extract parser module from services`

### Task 1.3：搭建 infra/retrieval（关键词 + 重排接口预留）

**Files:**
- Create: `backend/infra/retrieval/__init__.py` — 暴露 `keyword_search`
- Create: `backend/infra/retrieval/keyword.py` — 移自 `services/retrieval.py`
- Create: `backend/infra/retrieval/rerank.py` — 新增 `async def llm_rerank(chunks, query, top_n=5) -> list[Match]`，调 `infra.llm.dispatcher` 的重排 prompt
- Create: `tests/infra/test_retrieval.py`

- [ ] 写关键词检索测试（top-k 命中正确 chunk）
- [ ] 写 rerank 测试（mock LLM 返回固定 score+reason）
- [ ] 实现 keyword.py（直接复制）
- [ ] 实现 rerank.py：构造重排 prompt → 调 LLM → 解析 JSON → 返回 `Match[]`
- [ ] commit：`feat(infra): add retrieval rerank module`

### Task 1.4：搭建 infra/docx

- 平移 `services/docx_generator.py` → `infra/docx/__init__.py`，逻辑不变
- 加 1 个 smoke test：导出一个小 dict 不报错
- commit：`refactor(infra): extract docx exporter`

### Task 1.5：Phase 1 回归

- [ ] `pytest tests/ -v`：所有现存测试 + 新加测试全过
- [ ] 启动服务跑一轮端到端（上传 docx → 生成 → 下载），确认 routes 仍能调老 services（暂时双轨）
- [ ] commit：`chore: phase 1 regression pass`

---

## Phase 2：领域层（domain/）

**目标**：拆 `block_store.py` 为 4 个聚合的 repository，建立纯数据模型。

### Task 2.1：domain/spec

**Files:**
- Create: `backend/domain/spec/models.py` — `Section`、`OutlineMatrixRow`（dataclass，对应 spec §5 8 字段）
- Create: `backend/domain/spec/repository.py` — `SpecRepository`：保存/读取项目维度的 toc + outline_matrix（用现有 `projects` 表 JSON 字段或新加 `project_specs` 表）
- Create: `tests/domain/test_spec_repository.py`

- [ ] 写"保存 → 读取"往返测试
- [ ] 实现 dataclass + repository
- [ ] commit：`feat(domain): spec aggregate + repository`

### Task 2.2：domain/material

- `Material`、`Chunk` dataclass
- `MaterialRepository`：从 `block_store.list_chunks_by_project` 拆出 + 兼容现有表
- 测试：上传素材 → 切片入库 → 按项目读取
- commit：`feat(domain): material aggregate + repository`

### Task 2.3：domain/proposal

- `Block`、`BlockOutput`、`Source` dataclass
- `ProposalRepository`：拆 block_store 中 blocks 表的 CRUD
- 测试：保存 BlockOutput → 读取 → 字段对齐
- commit：`feat(domain): proposal aggregate + repository`

### Task 2.4：domain/review + workflow_runs

**Files:**
- Create: `backend/domain/review/models.py` — `Finding`、`Issue`、`GlobalReport`
- Create: `backend/domain/review/repository.py` — `ReviewRepository`（reviews 表）+ `WorkflowRunRepository`（workflow_runs 表）
- Modify: `backend/db.py` — 加 `init_workflow_runs()` 与 `init_reviews()` CREATE TABLE IF NOT EXISTS

- [ ] 写表初始化测试 + repository 往返测试
- [ ] 实现 dataclass + repository + db.py 表创建
- [ ] commit：`feat(domain): review aggregate + workflow_runs table`

### Task 2.5：letter detector

- 移 `is_letter_section` + `LETTER_KEYWORDS` 到 `backend/domain/letter_detector.py`
- 测试：标题含/不含关键词的判定
- commit：`refactor(domain): extract letter detector`

### Task 2.6：Phase 2 回归

- 所有测试通过 + 端到端冒烟（生成→下载仍能跑通，老 services 与新 domain 双轨共存）
- commit：`chore: phase 2 regression pass`

---

## Phase 3：Agent 层（agents/）

**目标**：5 个 agent 类，每个对外暴露 `async def run(state) -> state_patch`，prompt 集中在 `agents/prompts.py`。

### Task 3.1：prompts 集中

**Files:**
- Create: `backend/agents/__init__.py`
- Create: `backend/agents/prompts.py` — 把 spec §3 提到的所有 prompt 集中：
  - `OUTLINE_EXTRACT_SYSTEM/USER`（移自 llm.py 的 `_OUTLINE_SYSTEM_PROMPT`）
  - `SECTION_OUTLINE_SYSTEM/USER`（写作大纲）
  - `BLOCK_WRITE_USER`（正文生成）
  - `LETTER_SYSTEM/USER`（公文）
  - `RERANK_SYSTEM/USER`（沈括 LLM 重排，新增）
  - `TECH_REVIEW_SYSTEM/USER`（王安石，新增）
  - `COMPLIANCE_REVIEW_SYSTEM/USER`（包拯，新增）
- Create: `tests/agents/test_prompts.py` — 保证模板字符串非空 + 包含必要占位符

- [ ] 把 `infra/llm/dispatcher.py` 内联的 prompt 改为 `from agents.prompts import ...`
- [ ] 跑测试确保未破坏现有 LLM 调用
- [ ] commit：`refactor(agents): centralize prompts`

### Task 3.2：ZhangHengAgent

**Files:**
- Create: `backend/agents/zhang_heng.py` — 类含 `parse(file_path) -> SpecState` 与 `extract(toc) -> dict[block_id, OutlineMatrixRow]`
- Create: `tests/agents/test_zhang_heng.py`

- [ ] 测试：parse 命中 TOC 路径返回正确 Section[]；extract 对 ★/▲ 标记章节强制写入 veto/bonus；单章节 LLM 失败时该 row 留空 + error 字段
- [ ] 实现 parse：调 `infra.parser.parse_document` 包装为 SpecState
- [ ] 实现 extract：调 `infra.llm.dispatcher.dispatch_outline_json`，转成 OutlineMatrixRow dict
- [ ] commit：`feat(agents): zhang_heng (spec parser)`

### Task 3.3：ShenKuoAgent

- `match(toc, outline_matrix, chunks) -> dict[block_id, list[Match]]`
- 实现：每 block 用 `requirement + title` 调 `keyword_search` 取 top-10 → `llm_rerank` 取 top-5
- 测试：chunks 为空时返回空 matches；mock 重排返回固定结果
- commit：`feat(agents): shen_kuo (material matcher)`

### Task 3.4：ZhugeLiangAgent

- `generate(state) -> dict[block_id, BlockOutput]`，按 block 类型分支：
  - `is_letter_section` → 调 `generate_letter_content`
  - 否则 → 先 `generate_section_outline` 再 `dispatch_block_write`
- 流式：通过事件回调向上发 SSE token（事件回调签名见 Task 4.2）
- 重生路径：检查 `state.proposal.regenerate_targets`，非空时只跑 targets，跑完清空
- 测试：letter 路径输出含 `致：【…】`；普通路径 sources 非空；regen targets 路径只跑 1 个 block
- commit：`feat(agents): zhuge_liang (proposal writer)`

### Task 3.5：WangAnshiAgent

- `review(blocks, outline_matrix) -> dict[block_id, Findings]`
- prompt 视角：技术架构、可行性、实施风险、专业术语、技术指标响应
- 输出 0-100 分 + issues + strengths
- 测试：findings 结构完整；单 block 失败写 error 不阻断后续
- commit：`feat(agents): wang_anshi (tech reviewer)`

### Task 3.6：BaoZhengAgent

- 同 3.5，prompt 视角：合规、否决项覆盖、加分项遗漏、证明材料齐备、量化指标响应
- 复用相同的输出结构与失败处理
- commit：`feat(agents): bao_zheng (compliance reviewer)`

### Task 3.7：Phase 3 回归

- 跑所有 `tests/agents/`
- commit：`chore: phase 3 regression pass`

---

## Phase 4：编排层（orchestrator/）

**目标**：LangGraph StateGraph 把 5 agent 串起来，加 SQLite checkpointer 与 SSE 事件协议。

### Task 4.1：State + 依赖

**Files:**
- Modify: `requirements.txt` — 加 `langgraph` + `langgraph-checkpoint-sqlite`
- Create: `backend/orchestrator/__init__.py`
- Create: `backend/orchestrator/state.py` — 完整定义 `WorkflowState`（参考 spec §5 全部 TypedDict）
- Create: `tests/orchestrator/test_state.py`

- [ ] `pip install -r requirements.txt`
- [ ] 写 state 切片合并测试（确保 partial state 能正确 merge）
- [ ] 实现 TypedDict
- [ ] commit：`feat(orchestrator): workflow state model + langgraph dep`

### Task 4.2：SSE 事件协议

- `backend/orchestrator/events.py` — 13 种事件构造函数（参考 spec §7 事件表）
- `EventEmitter`：节点内向上发事件的回调对象（异步队列）
- 测试：每种事件序列化为 SSE 格式后包含正确 `event: xxx\ndata: {...}\n\n`
- commit：`feat(orchestrator): SSE event protocol`

### Task 4.3：Checkpointer

- `backend/orchestrator/checkpointer.py` — 复用 `backend/db.py` 的 SQLite 路径，封装 `AsyncSqliteSaver`
- 测试：保存 state → 关闭 saver → 重开 → 按 thread_id 读回相同 state
- commit：`feat(orchestrator): sqlite checkpointer`

### Task 4.4：Nodes

- `backend/orchestrator/nodes.py` — 把每个 agent 包装为 LangGraph 节点函数：
  - `zhang_heng_parse_node(state) -> state_patch`
  - `zhang_heng_extract_node`
  - `shen_kuo_match_node`
  - `zhuge_liang_generate_node`
  - `wang_anshi_review_node`
  - `bao_zheng_review_node`
  - `aggregate_review_node`
- 每个节点开头检查 `state.cancel_requested`，触发则 `raise GraphInterrupt(reason="user_cancel")`
- 已完成 block 跳过逻辑：`if block_id in state["proposal"]["blocks"]: continue`
- 测试：每个节点单测（mock 对应 agent，验证 state_patch 字段写入正确）
- commit：`feat(orchestrator): nodes wrapping agents`

### Task 4.5：Graph 构建

- `backend/orchestrator/graph.py` — `build_graph(emitter)` 返回 compiled StateGraph
- 边：parse→extract→interrupt(review_outline)→match→interrupt(review_materials)→generate→fan-out(wang_anshi, bao_zheng)→aggregate→interrupt(review_report)→conditional(approve→END / regen_blocks→generate / abort→ABORT)
- 测试：
  - 闸门测试：触发 interrupt 后 graph 暂停在正确 stage
  - Fan-out 并行测试：mock 两 agent，验证两者 start 时间差 < 100ms
  - 续跑测试：在 extract 第 3 章节时杀任务 → 重起 → 从第 4 章继续
  - 回修循环测试：gate3 提交 regen_blocks=[s1,s3] → 验证只重跑这 2 个 → 再次完整评审
- commit：`feat(orchestrator): langgraph state graph`

### Task 4.6：Runner

- `backend/orchestrator/runner.py` — `WorkflowRunner` 类暴露：
  - `start(project_id, config) -> thread_id`
  - `resume(thread_id, user_choice, edits)`
  - `regen(thread_id, block_ids)`
  - `abort(thread_id)`
  - `recover(thread_id)`
  - `state(thread_id) -> WorkflowState`
  - `stream(thread_id) -> AsyncIterator[SSEEvent]`
- 测试：start → resume 路径走通；abort 后 stage="aborted"
- commit：`feat(orchestrator): workflow runner facade`

### Task 4.7：Phase 4 回归

- 跑全 `tests/orchestrator/`
- commit：`chore: phase 4 regression pass`

---

## Phase 5：路由层（routes/）

**目标**：用 `routes/workflow.py` 替代 `routes/generate.py`，保留 projects/materials/config/upload 不变，新增 review 路由。

### Task 5.1：routes/workflow.py

- 7 个端点（spec §7 表）
- 内部全部委托给 `WorkflowRunner`
- SSE 端点 `/stream/{tid}` 复用 `_sse_event_generator` 模式
- 测试：用 `httpx.AsyncClient` 验证每个端点的状态码 + 返回字段
- commit：`feat(routes): workflow endpoints`

### Task 5.2：routes/review.py

- `GET /api/review/{thread_id}` 返回评审报告快照（直接读 ReviewRepository）
- 测试：mock 一份 review state → 调端点 → 验证返回 JSON 字段
- commit：`feat(routes): review snapshot endpoint`

### Task 5.3：main.py 接入

- 注册 workflow + review 路由，**暂保留** generate/blocks/revisions 兼容老前端
- 启动时 `init_workflow_runs()` + `init_reviews()` + checkpointer 初始化
- commit：`feat(main): wire up workflow routes`

### Task 5.4：Phase 5 回归

- 跑 `tests/routes/`
- 用 curl 跑一遍 workflow start→resume→done 闭环
- commit：`chore: phase 5 regression pass`

---

## Phase 6：前端

**目标**：改造 project-init/workbench，新建 review.html。

### Task 6.1：assets/api.js 增加 workflow API

- `workflowStart/Resume/Regen/Abort/Recover/State/Stream` 客户端方法
- commit：`feat(frontend): workflow api client`

### Task 6.2：project-init.html 改造

- 上传规范书 → 张衡进度条
- 闸门 1：渲染 outline_matrix 编辑器（沿用现有 8 字段表格）→ 提交 resume
- 闸门 2：渲染素材匹配表（每 block 显示 top-5 chunks 的 score + reason）→ 可剔除 → 提交 resume
- 错误展示：失败章节红色标记
- 手工测试通过
- commit：`feat(frontend): project-init wired to workflow`

### Task 6.3：workbench.html 改造

- 诸葛亮逐 block 流式 token 显示
- 完成后跳转 review.html
- commit：`feat(frontend): workbench wired to workflow`

### Task 6.4：review.html 新建

**Files:**
- Create: `frontend/review.html`、`assets/review.js`、`assets/review.css`

- 接收 `review_finding` 事件 → 卡片渲染 block_id / tech_score / comp_score / issues / strengths
- `report_ready` → 顶部全局报告（total_score / top_risks / missing_evidence / missing_bonus）
- 勾选若干 block + "重新生成" 按钮 → 调 `/regen`
- 三按钮：批准下载 / 重新生成 / 整体作废
- "恢复"按钮：检测到未完成 workflow_runs 时显示
- 手工测试
- commit：`feat(frontend): review dashboard`

### Task 6.5：Phase 6 回归

- 启动服务，从 projects.html 走完整流程到 review.html 下载
- 修复手工测试发现的问题（不在 plan 里展开）
- commit：`chore: phase 6 manual regression pass`

---

## Phase 7：清理与黄金样本回归

### Task 7.1：删除老代码

- 删除 spec §10 列出的旧文件（services/parser.py、llm.py、retrieval.py、block_store.py、docx_generator.py、task_store.py、routes/generate.py、blocks.py、revisions.py、utils/sse.py）
- `services/config_store.py` 保留（用户决议）
- main.py 移除老路由注册
- 跑全量 `pytest tests/ -v`
- commit：`refactor: remove legacy services and routes`

### Task 7.2：黄金样本

**前置**：用户提供 1 份脱敏规范书（`tests/fixtures/sample_spec.docx`）+ 1 份素材（`tests/fixtures/sample_materials.docx`）

- `tests/e2e/test_golden_sample.py`：用 `LLM_MODE=real`，跑完整 workflow → 保存输出到 `baseline.json`
- 标 `@pytest.mark.slow`，CI 不默认跑
- 后续重构跑同样本对比 baseline 的关键字段（block 数量、score 在合理区间、文件结构稳定）
- commit：`test(e2e): golden sample regression baseline`

### Task 7.3：文档收尾

- 更新工程根 `CLAUDE.md` 或 `README` 简述新架构入口（写一段就够）
- commit：`docs: update architecture overview`

---

## 自检

**Spec 覆盖**：
- §3 五 agent → Phase 3
- §4 分层 → 文件结构 + 各 Phase 落位
- §5 State → Task 4.1
- §6 LangGraph 流程 → Task 4.5
- §7 SSE 协议与 7 端点 → Task 4.2 + Task 5.1
- §8 错误处理与降级 → 散布在各 agent task（已显式提到失败分支）
- §9 测试策略 → 各 Task 内 TDD 步骤 + Task 7.2 黄金样本
- §10 迁移路径 → 7 个 Phase 一一对齐
- §11 开放问题 → Task 7.2 显式依赖用户提供 fixtures

**Placeholder 扫描**：无 TBD/TODO；所有任务都给出文件路径与测试预期。

**类型一致性**：dataclass 名（Section/OutlineMatrixRow/Chunk/Match/BlockOutput/Findings/GlobalReport）在 Phase 2/3/4 一致使用。
