# 多 Agent 协作架构重构设计（5 位历史人物）

- 日期：2026-06-16
- 作者：江河（与 Claude 协作）
- 状态：待用户审阅
- 范围：将现有 ge-solution 单体重构为 5 个 agent 协作的领域驱动架构（方案 B 全量重构）

## 1. 背景

当前工程是 FastAPI + 原生前端的单体应用：能从技术规范书提取 8 字段响应矩阵、检索素材、流式生成 block 正文，并支持公文与写作大纲的专用生成。能力点散落在 `backend/services/` 与 `backend/routes/`，缺乏显式的角色分工，**且没有任何评审环节**。

业务上，应标流程实际上是 5 个角色协作：解析规范书、整理素材、撰写应答、技术评审、合规评审。把这五个角色显式化为 5 个 agent，配上 LangGraph 编排与 3 个人工闸门，能让每个环节独立优化、独立测试，并把"评审"这个目前缺失的能力补上。

## 2. 目标与非目标

### 目标
- 把现有能力收敛为 5 个 agent，每个 agent 单一职责、独立 prompt、互不直接耦合
- 引入 LangGraph 编排 5 agent 工作流，支持断点续跑（agent 中途崩溃可恢复）
- 在大纲、素材、评审三处设人工闸门
- 评审 agent 全新建设：技术视角（王安石）+ 合规视角（包拯）逐 block 打分 + 全局汇总
- 沈括引入"检索 + LLM 重排"提升素材匹配质量
- 同步重构前端三页面，新增评审看板页

### 非目标
- 不引入向量检索（沿用关键词检索 + LLM 重排）
- 不引入 Agent 互相对话（agent 间只通过 state 通信）
- 不做多服务部署（同进程 + LangGraph）
- 不动现有 docx 导出与 changelog 模块（后续再迁移）

## 3. 5 位 Agent 命名与职责

| 历史人物 | 角色 | 输入 | 输出 |
|---|---|---|---|
| **张衡** | 规范书解析专家 | 规范书文件 | TOC + 8 字段响应矩阵 + 项目概述 |
| **沈括** | 素材匹配专家 | TOC + 素材切片 | 每 block 的 top-N 素材表（含分数与理由） |
| **诸葛亮** | 应答生成专家 | 响应矩阵 + 素材表 + 配置 | 每 block 的写作大纲 + 正文 + 引用 |
| **王安石** | 技术评审专家 | 应答正文 + 响应矩阵 | 每 block 技术分数 + 问题清单 |
| **包拯** | 合规评审专家 | 应答正文 + 响应矩阵 | 每 block 合规分数 + 问题清单 |

### 张衡（规范书解析专家）
- 解析 PDF/DOCX，提取严格按目录顺序的 `Section[]`
- 对每章节做 8 字段提炼：`requirement / key_points / veto_items / bonus_items / score_items / evidence_required / constraint_level / indicators`
- 生成项目概述（项目名/甲乙方/核心要求/硬约束/建设范围）
- 提炼对 ★/▲ 标记章节强制写入 veto/bonus 字段
- 单章节失败时该 row 留空 + error 字段，不阻断后续章节

### 沈括（素材匹配专家）
- 对每 block 用 `requirement + title` 做关键词检索 top-k=10
- 让 LLM 对每个候选 chunk 给出 0-10 匹配分 + 理由 + 命中的 requirement 要点
- 取 top-N=5 作为该 block 的最终素材池
- chunks 为空时返回空 matches，不抛异常

### 诸葛亮（应答生成专家）
- 按 block 类型分支：
  - 普通技术 block → 先生成"写作大纲"（含表格样例 + 图占位符）→ 再生成正文
  - `is_letter_section()=True` → 走公文模板（含落款占位）
  - 含图占位符的 block → 在 markdown 嵌入 `【架构图：…】` `【流程图：…】` 占位，由人工后续替换
- 每生成一个 block 推一次 SSE，正文流式逐 token 推送
- 重生路径：`proposal.regenerate_targets` 非空时只跑这些 targets，跑完清空字段

### 王安石（技术评审专家）
- 视角：技术架构、可行性、实施风险、专业术语准确度、技术指标响应程度
- 逐 block 给 0-100 分 + `issues: [{severity, point, suggestion}]` + `strengths`

### 包拯（合规评审专家）
- 视角：响应合规性、否决项是否覆盖、加分项是否遗漏、公文证明材料是否齐备、量化指标逐条响应
- 输出结构同王安石

### 评审汇总（orchestrator 节点）
- 合并两位 findings 为 `per_block` + `global` 报告
- `global = {total_score, top_risks, missing_evidence, missing_bonus}`
- 单 block 评审失败时该项写 error 不参与全局打分，不阻断另一位

### Agent 间契约
- 严格通过 state 通信，不允许 agent 互相 import
- 每个 agent 有自己的 `prompts.py`（system + user 模板），独立调优
- 每个 agent 是一个类，对外暴露 `async def run(state) -> state_patch`

## 4. 分层架构

```
routes/         FastAPI 路由（薄壳）
  └─ workflow.py / projects.py / materials.py / config.py
orchestrator/   LangGraph 编排
  ├─ graph.py          StateGraph 定义
  ├─ state.py          WorkflowState（TypedDict）
  ├─ events.py         SSE 事件协议
  └─ checkpointer.py   SQLite checkpointer 配置
agents/         5 个 agent + prompt
  ├─ zhang_heng/   __init__.py、prompts.py、agent.py
  ├─ shen_kuo/
  ├─ zhuge_liang/
  ├─ wang_anshi/
  └─ bao_zheng/
domain/         4 个聚合 + repository
  ├─ spec/         Section、TocEntry、OutlineMatrixRow、SpecRepository
  ├─ material/     Material、Chunk、MaterialRepository
  ├─ proposal/     Block、BlockOutput、ProposalRepository
  └─ review/       Finding、Report、ReviewRepository
infra/          底层依赖（与业务无关）
  ├─ llm/          provider 客户端、轮询/fallback、prompt 调用
  ├─ parser/       PDF/DOCX 解析（移自 services/parser.py）
  ├─ retrieval/    关键词检索 + LLM 重排
  └─ docx/         docx 导出（移自 services/docx_generator.py）
```

### 分层规则
- **Routes** 不持业务，做参数校验、调 orchestrator、把事件转成 SSE
- **Orchestrator** 不直接调 LLM，只编排节点和 state 切片
- **Agent** 持 system prompt 与 user prompt 模板，工具调用走 infra
- **Domain** 是纯数据 + repository（CRUD），不含业务逻辑
- **Infra** 与业务无关，可被任意上层调用

### 上下游约束
- routes 只能调 orchestrator + repository（读快照）
- orchestrator 只能调 agents
- agents 只能调 infra + 读取传入的 state
- domain 不依赖 orchestrator/agents/infra

## 5. WorkflowState 模型

LangGraph 的 `WorkflowState` 是 5 个 agent 的唯一通信介质。设计原则：每个 agent 只读自己依赖的字段、只写自己产出的字段。

```python
# orchestrator/state.py
class WorkflowState(TypedDict, total=False):
    # ── 元信息 ───────────────────────────────────
    project_id: int
    thread_id: str                     # LangGraph thread id（断点续跑用）
    stage: Literal[
        "idle", "parsing", "outline_review",
        "matching", "materials_review",
        "generating", "reviewing",
        "report_review", "done", "aborted"
    ]
    user_choice: Literal[
        "approve", "edit", "regen_blocks", "abort", ""
    ]
    cancel_requested: bool

    # ── 张衡产出 ─────────────────────────────────
    spec: SpecState
    # SpecState = {
    #   doc_id: str, doc_title: str, doc_summary: str,
    #   toc: list[Section],
    #   outline_matrix: dict[block_id, OutlineMatrixRow],
    # }

    # ── 沈括产出 ─────────────────────────────────
    materials: MaterialsState
    # MaterialsState = {
    #   chunks: list[Chunk],
    #   matches: dict[block_id, list[Match]],
    # }
    # Match = {chunk_id, score: float, reason: str, hit_points: list[str]}

    # ── 诸葛亮产出 ───────────────────────────────
    proposal: ProposalState
    # ProposalState = {
    #   blocks: dict[block_id, BlockOutput],
    #   regenerate_targets: list[block_id],
    # }
    # BlockOutput = {
    #   kind: "tech"|"letter",
    #   needs_diagram: bool,
    #   outline: str,
    #   content: str,
    #   sources: list[Source],
    # }

    # ── 评审产出 ─────────────────────────────────
    review: ReviewState
    # ReviewState = {
    #   tech_findings: dict[block_id, Findings],
    #   compliance_findings: dict[block_id, Findings],
    #   report: GlobalReport,
    # }
    # Findings = {score: int, issues: [{severity, point, suggestion}], strengths: list[str]}
    # GlobalReport = {
    #   per_block: dict[block_id, {tech_score, comp_score, severity_counts}],
    #   total_score: float,
    #   top_risks: list[str],
    #   missing_evidence: list[str],
    #   missing_bonus: list[str],
    # }

    # ── 用户配置 ─────────────────────────────────
    config: UserConfig
    # UserConfig = {tone, target_words, doc_template}

    # ── 错误收集 ─────────────────────────────────
    errors: list[ErrorEntry]
```

### 字段写入约定

| Agent | 只读字段 | 只写字段 |
|---|---|---|
| 张衡 | `project_id, config` | `spec.*` |
| 沈括 | `project_id, spec.toc, spec.outline_matrix, materials.chunks` | `materials.matches` |
| 诸葛亮 | `spec, materials, config, proposal.regenerate_targets` | `proposal.blocks` |
| 王安石 | `proposal, spec.outline_matrix` | `review.tech_findings` |
| 包拯 | `proposal, spec.outline_matrix` | `review.compliance_findings` |
| Orchestrator 汇总节点 | `review.tech_findings, review.compliance_findings` | `review.report` |

### 持久化
- LangGraph SQLite checkpointer 复用 `backend/db.py` 的 SQLite 实例
- 新增 `workflow_runs(thread_id PK, project_id FK, stage, created_at, updated_at, finished_at)` 表
- `block_id` 沿用张衡产出的 `Section.id`（如 `s1`、`s1.1`），后续所有 agent 沿用，避免 id 漂移

## 6. LangGraph 编排流程

```
START
  → zhang_heng_parse              # 文档解析
  → zhang_heng_extract            # 8 字段提炼（最多 5 路并发）
  → interrupt: review_outline     # 闸门 1
  → shen_kuo_match                # 检索 + LLM 重排
  → interrupt: review_materials   # 闸门 2
  → zhuge_liang_generate          # 逐 block 生成
  → fan-out:
       wang_anshi (技术评审)
       bao_zheng  (合规评审)
  → aggregate_review              # 汇总报告
  → interrupt: review_report      # 闸门 3
  → user_choice?
       approve     → END (done)
       regen_blocks → zhuge_liang_generate (只跑 targets)
       abort       → ABORT (aborted)
```

### 节点编排细节
1. **张衡内部 2 个节点**：parse 与 extract 分开，因为 8 字段提炼是耗时并发 LLM 调用，分开后续跑可跳过解析阶段
2. **王安石 + 包拯 fan-out**：generate 完成后用一条 super-step 同时触发，aggregate 等两边都完成才触发；LangGraph 内置 fan-in 语义
3. **回修循环**：用户在闸门 3 选 regen_blocks 时，把待回修 block_id 写入 `proposal.regenerate_targets`，对应 review.findings 作为 extra_prompt 传给诸葛亮；诸葛亮节点检测该字段非空时只跑 targets，跑完清空字段，再次进入完整 fan-out 评审
4. **闸门实现**：用 LangGraph `interrupt(value)` 在节点内抛出，前端 SSE 收到 `gate_open`，提交后 routes 用 `graph.update_state(thread_id, ...)` + `graph.invoke(None, thread_id)` 续跑
5. **取消**：路由层调 `POST /api/workflow/{tid}/abort` 设 `state.cancel_requested=True`；agent 节点循环开头检查该字段，触发后图走到 ABORT；已完成 block 保留

### 节点执行模式
- 张衡 parse、沈括 match、aggregate：非流式（一次性出结果，闸门前推进度事件）
- 张衡 extract：逐章节流式（每提炼完一章推一次 SSE）
- 诸葛亮 generate：逐 block + 逐 token 流式（不合并）
- 王安石/包拯：逐 block 流式（每评完一个 block 推一条 finding）

### 评审回修语义
回修时再次走完整 fan-out 评审（每次回修都重评审，确保问题确实被修掉）。

## 7. 数据流与 SSE 协议

### 后端 → 前端 SSE 事件

所有事件统一走 `GET /api/workflow/{thread_id}/stream`：

| 事件 | 触发节点 | 载荷 |
|---|---|---|
| `stage_change` | 任意 | `{stage, thread_id}` |
| `parse_progress` | 张衡 parse | `{step, current, total}` |
| `outline_extract` | 张衡 extract | `{block_id, title, matrix}` |
| `match_progress` | 沈括 | `{block_id, matches}` |
| `block_start` | 诸葛亮 | `{block_id, kind, title}` |
| `block_token` | 诸葛亮 | `{block_id, token}` |
| `block_done` | 诸葛亮 | `{block_id, content, sources, outline}` |
| `review_finding` | 王安石/包拯 | `{block_id, agent, score, issues}` |
| `report_ready` | aggregate | `{report}` |
| `gate_open` | 任意闸门 | `{gate, snapshot}` |
| `error` | 任意 | `{agent?, block_id?, message, retryable}` |
| `checkpoint` | 任意 | `{thread_id, stage}` |
| `done` | END | `{download_url}` |
| `aborted` | ABORT | `{reason}` |

### 前端 → 后端的指令端点

| 端点 | 何时调用 | 作用 |
|---|---|---|
| `POST /api/workflow/start` | 用户点"开始" | 创建 thread_id，触发张衡 parse |
| `POST /api/workflow/{tid}/resume` | 闸门处提交 | 注入 user_choice + 修改后的 state，续跑 |
| `POST /api/workflow/{tid}/regen` | 闸门 3 选回修 | 写入 `proposal.regenerate_targets`，续跑回到诸葛亮 |
| `POST /api/workflow/{tid}/abort` | 用户取消 | 设 cancel flag，图走到 ABORT |
| `POST /api/workflow/{tid}/recover` | 用户点"恢复"（崩溃续跑） | 从 checkpointer 恢复 state 续跑 |
| `GET /api/workflow/{tid}/state` | 前端刷新页面 | 返回最新 state 快照 |
| `POST /api/projects/{pid}/materials` | 项目初始化 | 上传素材，存 chunks（独立于工作流） |

### 前端三页面的角色重新分配

| 页面 | 旧职能 | 新职能 |
|---|---|---|
| `projects.html` | 项目列表 + API 配置 | 不变 |
| `project-init.html` | 上传规范书+素材+大纲手编 | **改造**：上传 → 张衡进度 → 闸门 1（编辑 outline_matrix） → 闸门 2（编辑素材匹配） |
| `workbench.html` | 单 block 重生 | **改造**：诸葛亮逐 block 生成进度 → 评审报告页跳转 |
| `review.html` | — | **新建**：评审报告专属看板（按 block 卡片展示双视角分数与问题清单，回修勾选） |

### 数据持久化

- **保留表**：`projects`、`materials`、`material_chunks`、`blocks` —— 应用层事实数据
- **新增表**：`workflow_runs(thread_id PK, project_id FK, stage, created_at, updated_at, finished_at)` —— 工作流元数据
- **新增表**：LangGraph SQLite checkpointer 自带 `langgraph_checkpoints` —— 存 state 快照
- **存储约定**：state 是易失中间态（每次工作流跑产出的 outline_matrix/matches/findings 都进 checkpoint）；最终 `proposal.blocks` 完成后写回 `blocks` 表作为正式数据

### 续跑触发流程

**刷新页面续跑（闸门处天然支持）**：
```
用户刷新页面
  → GET /api/workflow/{tid}/state 返回 {stage: "outline_review"}
  → 前端读 stage 跳到对应 UI（闸门 1 编辑器）
  → SSE 重新订阅 stream
  → 后端发现 stage 是 interrupt 状态，回放历史事件 + 推 gate_open
```

**后端崩溃续跑**：
```
后端重启 → workflow_runs 表里有未完成记录
  → 前端展示"恢复"按钮
  → 用户点"恢复" → POST /api/workflow/{tid}/recover
  → 后端用 thread_id 从 checkpointer 恢复 state
  → 调 graph.invoke(None, thread_id) 继续从最后一个完成的 super-step 后跑
```

不自动恢复，需用户显式触发，避免重启风暴造成 LLM 调用堆积。

## 8. 错误处理与降级

### 错误分级

| 级别 | 例子 | 策略 |
|---|---|---|
| **L1 瞬时错误** | LLM 超时、网络抖动、限流 | 自动重试 2 次，间隔 3s（沿用现有 generate.py 重试逻辑）；仍失败则降级 L2 |
| **L2 单 block 失败** | 某 block 评审/生成全部 API 都失败 | **不阻断流程**，该 block 标记为 failed，state.errors 追加，前端显示警示，工作流继续；用户可在闸门重生 |
| **L3 阶段性失败** | 张衡解析失败（PDF 损坏、目录提取不到任何条目）、沈括所有 chunk 都没匹配 | **阻断**，发 SSE error + stage_change 到 error_review，进入闸门让用户决定 |
| **L4 致命错误** | API 全部未配置、数据库不可用、磁盘写入失败 | **直接 ABORT**，工作流标记 aborted，前端弹错误对话框 |

### Agent 级降级

| Agent | 失败时的降级 |
|---|---|
| 张衡 parse | TOC 提取失败 → 自动回退到 heading scan；仍无标题 → 全文兜底单 section |
| 张衡 extract | 单章节 8 字段提炼失败 → 该 row 字段留空，error 字段写入失败原因，不阻断后续 |
| 沈括 | 整体匹配失败 → 该 block matches 为空；诸葛亮收到空素材时仍按 outline_matrix 生成 |
| 诸葛亮 | 单 block 流式中途失败 → 重试 2 次；仍失败 → 标记 failed，让用户在评审页重生 |
| 王安石/包拯 | 单 block 评审失败 → 该 block findings 写 error，不参与全局打分；不阻断另一位 |
| Aggregate | 不会失败（纯数据合并） |

### Checkpoint 颗粒度

每个 super-step 后写 checkpoint：
- 张衡 parse 完成后
- 张衡 extract 每完成 1 章节后
- 沈括 match 每完成 1 block 后
- 诸葛亮每完成 1 block 后
- 王安石/包拯每完成 1 block findings 后
- aggregate 完成后
- 闸门处（自然 checkpoint）

崩溃恢复时已经完成的 block 不重跑——LangGraph state 里已有结果，节点开头检查 `if block_id in proposal.blocks: continue`。

### 取消语义

- 用户点"中止"或浏览器关闭 → `POST /api/workflow/{tid}/abort` → 设 `state.cancel_requested=True`
- agent 节点循环开头检查 → `if state.cancel_requested: raise GraphInterrupt(reason="user_cancel")`
- LangGraph 自动写 abort checkpoint，stage 改为 `aborted`
- 已完成的 block 仍保留（不删除中间产物，让用户能下载部分结果）

### 用户可见的错误展示

- 闸门 1 页面顶部：extract 阶段失败的章节用红色标记，可手动补字段
- 闸门 2 页面：未匹配上素材的 block 显示"无匹配，将仅依赖 outline 生成"提示
- 评审报告页：failed block 用单独色块展示，附"立即重生"按钮
- 全局 toast：L4 错误用模态框；L1/L2/L3 用 toast + state.errors 历史栏

## 9. 测试策略

### 测试金字塔

| 层级 | 范围 | 工具 | 占比 |
|---|---|---|---|
| 单元测试 | infra/parser、infra/retrieval、各 agent 的 prompt 构造、state 切片合并 | pytest | 70% |
| 集成测试 | LangGraph 节点执行、checkpointer 续跑、闸门 interrupt 注入 | pytest + 内存 SQLite + LLM mock | 20% |
| 端到端测试 | 一份样本规范书 + 一份样本素材 → 整流程跑通 → 验产物 | pytest + 真 LLM（标 `@slow`） | 10% |

### 关键测试用例

**张衡**
- TOC 优先路径命中（DOCX SDT TOC、手动目录、heading scan 兜底）
- 8 字段提炼对 ★/▲ 标记的章节强制写入 veto/bonus
- 单章节 LLM 失败时该 row 留空，不影响其他章节
- PDF 扫描件触发 OCR 兜底

**沈括**
- 检索 top-k=10 命中正确的 chunk
- LLM 重排时，对一个虚构 block 的 reason 字段非空且关联到 hit_points
- chunks 为空时返回空 matches，不抛异常

**诸葛亮**
- 普通 block：先生成 outline 再生成 content，sources 字段不为空
- 公文 block（标题含"承诺书"）：走 letter 分支，输出含 `致：【…】` 抬头与落款占位
- 含图占位符的 block：content 中保留 `【架构图：…】`
- 重生路径：proposal.regenerate_targets 非空时只跑 targets，跑完清空

**王安石 / 包拯**
- 单 block findings 结构完整（score/issues/strengths）
- 评审失败时 findings 写 error，不阻断另一位

**Aggregate**
- per_block 同时含 tech_score 与 comp_score
- top_risks 来自两位 findings 的 high severity 项

### LangGraph 编排测试

- **Fan-out 并行**：mock 两位评审 agent，验证两者 start 时间差 < 100ms（确认并行）
- **闸门 interrupt**：触发 gate1 后 stage == "outline_review"，graph 暂停；调 update_state + invoke(None) 后恢复
- **Checkpoint 续跑**：在张衡 extract 跑到第 3/10 章节时杀进程，重启后从第 4 章继续
- **回修循环**：在 gate3 提交 regen_blocks=[s1,s3]，验证只重跑这 2 个 block，再次跑完整评审

### 前端测试

- `project-init.html`：上传 → SSE 接收 outline_extract 事件 → 闸门 1 编辑器渲染 → 提交后阶段切换
- `review.html`：收到 review_finding 事件后卡片渲染分数与问题清单；勾选 block 后调 `/regen`
- `workbench.html`：诸葛亮逐 block 流式 token 显示

前端测试方式：手工测试为主，不引入额外测试框架（保持当前原生 JS 风格）。

### 回归基线

新建 `tests/fixtures/` 放 1 份"标准技术规范书 + 标准素材"作为黄金样本：
- 第一次跑通后保存产出（outline_matrix / matches / blocks / report）作为 baseline
- 后续每次重构跑同一样本，对关键字段做相似度对比（容忍 LLM 输出抖动，但结构必须稳定）

### LLM mock

封装 `infra/llm/_mock.py`：根据 prompt 关键词返回固定 fixture：
- 含"提炼以下八项内容" → 返回固定 8 字段 JSON
- 含"撰写投标承诺书" → 返回固定公文文本
- 含"评审" → 返回固定 findings JSON

环境变量 `LLM_MODE=mock` 切换，CI 默认开 mock。

## 10. 迁移路径

按依赖顺序逐层重构，每步可独立验证：

1. **基础设施层** —— `services/parser.py` → `infra/parser/`，`services/retrieval.py` → `infra/retrieval/`，`services/llm.py` → `infra/llm/`（保留所有现有调用契约）
2. **领域层** —— 创建 `domain/spec`、`domain/material`、`domain/proposal`、`domain/review` 四个聚合 + repository，把 `block_store.py` 拆解
3. **Agent 层** —— 实现 5 个 agent 类，prompt 从现有 `llm.py` 中提炼（张衡/沈括/诸葛亮的 prompt 复用现有，王安石/包拯新建）
4. **编排层** —— 实现 LangGraph StateGraph、checkpointer、SSE 事件协议
5. **路由层** —— 替换 `routes/generate.py` 为 `routes/workflow.py`，保留 `routes/projects.py`、`routes/config.py`
6. **前端** —— 改造三页面 + 新建 `review.html`，更新 `assets/api.js` 字段名
7. **回归** —— 跑黄金样本，对比 baseline；删除旧 `services/` 文件

每步完成后跑测试 + 黄金样本，确保不退化。最后一步删除旧代码避免双轨。

## 11. 开放问题

- **黄金样本**：需要用户提供 1 份脱敏的真实规范书 + 配套素材，作为 fixtures。第一次跑通后冻结输出做 baseline。
- **LLM mock 录制回放**：第一次真实调用录下来，后续回放——可降低 CI 成本，但实现成本中等；本期不做，第一期用 hand-crafted fixture。
- **图占位符替换**：`【架构图：…】` 暂时只是占位，未来可接入 Mermaid 渲染或图床上传，本期不做。


