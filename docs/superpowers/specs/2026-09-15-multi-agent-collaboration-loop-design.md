# 多 Agent 协同闭环设计（审核回灌 + 迭代控制 + 按需补料）

- 日期：2026-09-15
- 作者：江河（与 Claude 协作）
- 状态：待用户审阅
- 范围：在既有 5 agent 架构上补齐"协同闭环"，不改变角色划分与分层
- 前置文档：`docs/superpowers/specs/2026-06-16-multi-agent-bid-system-design.md`

## 1. 背景

2026-06-16 的设计已经把 ge-solution 从单体拆成了 5 个 agent（张衡/沈括/诸葛亮/王安石/包拯）+ LangGraph 编排 + 3 个人工闸门，并用 `WorkflowState` 作为 agent 之间的唯一通信介质。那次重构解决了"没有显式角色分工"和"没有评审环节"两个问题。

但落地后复盘发现：**角色是分好了，角色之间却没有真正闭环——当前是一张单向流水线，不是协同。**

具体表现为三处断裂：

1. **审核结果不回灌编写**。`regen_blocks` 分支看着像闭环，实际是盲跑：`proposal.regenerate_targets` 只携带 block_id 列表（`orchestrator/nodes.py:426-444`），`zhuge_liang_generate_node` 只读 `spec.outline_matrix` 与 `materials.matches`（`orchestrator/nodes.py:412-424`）。审核 agent 产出的 `issues[{severity, point, suggestion}]` 从未被读取，全部丢弃。所谓"重生"是拿同一个 prompt 重抽，不修复任何被指出的问题。审核角色目前是只读看板。
2. **收集角色是单向 producer**。沈括 `match()`（`agents/shen_kuo.py:52`）一次性算完即定稿。编写角色发现素材不足时，没有任何通道回头要料。
3. **无迭代上限、无重审、无通过门槛**。`regen_blocks` 打回 generate 后直接进 END（`orchestrator/graph.py:327-335`），不再复审；没有 `max_iterations`，没有分数阈值判定。

本次设计补齐这三处断裂，让"编写—收集—审核"真正形成闭环。

## 2. 目标与非目标

### 目标

- 审核意见回灌到编写 agent 的生成上下文，使每轮重写都针对性地修复被指出的问题
- 编写角色可主动发起补料请求，收集角色按需响应（不再只有闸门 2 之前的一次性批量匹配）
- 审核意见中"必须补料才能修复"的问题可自动转化为补料请求，避免让编写角色去编造不存在的证明材料
- 引入逐 block 收敛判定与迭代上限，收敛后或达上限后交人工终审
- 保持既有 3 个人工闸门、断点续跑、SSE 可视化不变

### 非目标

- **不合并 agent**。保留"技术评 / 合规评"双视角独立打分（前置文档 §2 明确将双视角列为目标，合并是负收益）
- **不抽象为通用协同框架**。角色仍绑定投标领域，不做可配置角色引擎
- **不引入 agent 之间直接对话**。agent 仍只通过 `WorkflowState` 通信，不互相 import（前置文档 §3 硬约束）
- **不引入向量检索**。补料沿用关键词检索 + LLM 重排
- 不改动 docx 导出与 changelog 模块
- 不改动闸门 1 / 闸门 2 的语义与前端交互

## 3. 现状缺口与本次修复对照

| 缺口 | 现状证据 | 本次修复 |
|---|---|---|
| A. 审核不回灌 | `nodes.py:426-444` 只传 block_id；`nodes.py:412-424` 不读 findings | 新增 `review.feedback` 字段 + `BUILD_FEEDBACK` 节点；诸葛亮 prompt 增加修订分支 |
| B. 收集单向 | `shen_kuo.py:52` `match()` 一次性定稿 | 新增 `proposal.material_requests` + `COLLECT_GAPS` 节点 + `ShenKuoAgent.retrieve_for()` |
| C. 无迭代控制 | `graph.py:327-335` 回修后不再复审 | 新增 `CHECK_CONVERGENCE` 节点 + `iteration` 计数 + 阈值/上限 |

## 4. 协同闭环设计

### 4.1 角色与职责（不变的部分略）

| 角色 | Agent | 读 | 写 |
|---|---|---|---|
| 收集 | 张衡 | 规范书文件 | `spec.toc`、`spec.outline_matrix` |
| 收集 | 沈括 | `spec.toc`、`spec.outline_matrix`、`materials.chunks`、补料请求 | `materials.matches` |
| 编写 | 诸葛亮 | `spec.outline_matrix`、`materials.matches`、`review.feedback` | `proposal.blocks`、`proposal.material_requests` |
| 审核 | 王安石 | `proposal.blocks`、`spec.outline_matrix` | `review.tech_findings` |
| 审核 | 包拯 | `proposal.blocks`、`spec.outline_matrix` | `review.compliance_findings` |

新增三个编排节点承担角色间的**调度与转译**，本身不产出业务内容、不调 LLM：

| 节点 | 承担角色 | 职责 |
|---|---|---|
| `collect_gaps` | 调度收集 | 把补料请求交给沈括，把新素材并回 `materials.matches` |
| `build_feedback` | 转译 | 把审核意见翻译成「给编写的修订指令」+「给收集的补料请求」 |
| `check_convergence` | 判定 | 决定继续回炉还是交人工终审 |

### 4.2 编排图

**主链**

```
START → zhang_heng_parse → zhang_heng_extract → [闸门 1：gate_outline]
      → shen_kuo_match → [闸门 2：gate_materials]
      → collect_gaps → zhuge_liang_generate
      → 评审扇出（wang_anshi_review ‖ bao_zheng_review）
      → aggregate_review → check_convergence
      → [闸门 3：gate_report] → approve → END
```

**回边（新增）**

```
check_convergence --refine--> build_feedback --> collect_gaps
```

**闸门 3 的三个出口（保持现状 + 人工回修）**

```
gate_report → approve       → END
            → regen_blocks  → zhuge_liang_generate（人工回修，见 §5.3）
            → abort         → abort_marker → END
```

**暂停侧路（保持现状，`_generate_fanout` / `_route_after_pause` 逻辑不变）**

```
zhuge_liang_generate --stage=="paused"--> [gate_pause]
                                            --resume----------> zhuge_liang_generate
                                            --skip_to_review--> 评审扇出
                                            --其它------------> END
```

关键点：**`collect_gaps` 位于 `zhuge_liang_generate` 之前**，而不是之后。这一点是设计的核心，理由见 §4.5。注意 `_generate_fanout` 因此**不需要修改**——它输出的仍是两个评审节点，`collect_gaps` 不在 generate 与评审之间。

### 4.3 节点契约

#### `collect_gaps`（新增）

- 位置：`gate_materials` 之后、`zhuge_liang_generate` 之前；迭代回边也汇入此节点
- 读：`proposal.material_requests`、`materials.chunks`
- 做：对每个 block 的请求调 `ShenKuoAgent.retrieve_for(requests, chunks)`，拿到新素材
- 写：`materials.matches`（与已有 matches **并集去重，追加而非覆盖**）、`proposal.material_requests = {}`
- 首次进入时 `material_requests` 为空 → 空操作直接通过（闸门 2 之前沈括已做过批量匹配，不重复劳动）
- 失败：检索抛错 → 记 `errors`，保留原 matches 继续，绝不阻断流程
- SSE：逐 block 推 `gaps_collecting` / `gaps_done`

#### `check_convergence`（新增）

- 位置：`aggregate_review` 之后
- 读：`review.tech_findings`、`review.compliance_findings`、`iteration`
- 做：纯判定，不写业务数据、不调 LLM
- 写：`review.convergence = {status, unconverged_blocks, reason}`
- 条件边返回：`"refine"` / `"max_iterations"` → `build_feedback`；`"converged"`（或无法判定）→ `gate_report`。两条未收敛路径都经 `build_feedback`，由它的出边再区分回炉还是直连闸门（见 §9 `_route_convergence` / `_route_feedback`）

#### `build_feedback`（新增）

- 位置：判定未达标时，或达上限时（见 §5.3）
- 读：两侧 findings、`proposal.blocks`
- 做：把 findings 拆成两路（见 §4.4），纯转译，不调 LLM
- 写：`review.feedback`、`proposal.material_requests`（追加）、`proposal.regenerate_targets`、`iteration += 1`
- 达上限场景下只写 `review.feedback` 与补料请求，**不递增 `iteration`、不写 `regenerate_targets`**（见 §9）

### 4.4 `build_feedback` 的两路拆分

对每个未达标 block：

```python
review.feedback[bid] = {                      # → 给编写角色
    "issues": [                               # 该 block 的全部 issue
        {"severity": ..., "point": ..., "suggestion": ...}
    ],
    "scores": {"tech": 55, "comp": 40},
}

proposal.material_requests[bid] = [           # → 给收集角色
    {"query": issue.material_query, "reason": issue.point}
    for issue in issues if issue.needs_material
]
```

两路是**正交的**：一条 issue 可以既进 `feedback`（告诉编写"这里有问题"）又进 `material_requests`（告诉收集"这里缺什么"）。编写角色拿到 `needs_material` 的 issue 时应当等补料，而不是硬写。

### 4.5 为什么 `collect_gaps` 必须在 `generate` 之前

补料请求有两个来源，且产生时机不同：

| 来源 | 谁写 | 何时写 |
|---|---|---|
| 编写主动要料 | 诸葛亮 `generate()` 内 | 第 N 轮生成时 |
| 审核意见要料 | `build_feedback` | 第 N 轮判定未达标时 |

两个来源**都必须在第 N+1 轮 `generate` 开始之前被消费**。

若把 `collect_gaps` 放在 `generate` 之后（初版设计的错误摆法）：

```
generate_N → collect_gaps_N → 评审_N → check_N → build_feedback_N → generate_{N+1}
```

- 诸葛亮的请求：`collect_gaps_N` 收走 → `generate_{N+1}` 可用 ✅
- `build_feedback_N` 的请求：下一轮 `collect_gaps_{N+1}` 才收走，**但 `generate_{N+1}` 在它之前已经跑完** ❌

结果是审核说"缺否决项证明材料"，重写完一遍，材料才到——整整迟到一轮，等于白写。把 `collect_gaps` 前移后，两个来源合流到同一个消费点，都在下一轮生成前就位。

### 4.6 迭代循环逐轮展开

**第 1 轮**

1. `collect_gaps`：空操作（无请求）
2. `zhuge_liang_generate`：逐 block 生成，每个 block 额外产出 `material_requests`
3. 评审扇出：王安石 + 包拯并行，逐 block 打分，`Issue` 带 `needs_material` / `material_query`
4. `aggregate_review`：合并为 `GlobalReport`（保持现状）
5. `check_convergence`：收敛 → 直连 `gate_report`；未达标或达上限 → 都去 `build_feedback`
6. `build_feedback`：
   - `refine` 模式：产出 `review.feedback` + `material_requests`，写 `regenerate_targets`，`iteration += 1`，回边至 `collect_gaps`
   - `max_iterations` 模式：只写 `review.feedback` + `material_requests`，直连 `gate_report`（见 §5.3）

**第 2 轮及以后**

1. `collect_gaps`：消费上一轮诸葛亮 + `build_feedback` 两处请求，调沈括补料，并回 matches
2. `zhuge_liang_generate`：只跑 `regenerate_targets` 中的 block；每个 block 同时拿到 `review.feedback[bid]` 与增补后的 `materials.matches[bid]`
3. 评审：**只复审重跑的 block**，未重跑的沿用上一轮 findings
4. 其余同上

**复审范围如何传递**：`zhuge_liang_generate_node` 在返回时把本轮实际生成的 block 集合写入 `proposal.updated_blocks`（首轮为全部 block，后续轮为 `regenerate_targets` 中实际跑完的）。评审节点只评审 `updated_blocks` 中的 block。

现有实现里 `zhuge_liang_generate_node` 在消费完 `regenerate_targets` 后会把它清空（`nodes.py:450`），所以不能靠它判断本轮范围——这也是必须新增 `updated_blocks` 的原因。

**为什么必须限定范围**：不只是省 token，更是稳定性要求。两位评审都是独立的 LLM 调用，同一段未改动的正文重复评审会因采样随机性给出不同分数。若第 2 轮复审了未改动的 block 且恰好打分偏低，该 block 会被误判为未达标并触发第 3 轮无意义的回炉，甚至在第 2、3 轮之间振荡。只复审本轮真正重跑过的 block 可以根除这个问题。

这与现有语义一致——`agents/wang_anshi.py:89` 的循环本来就是逐 block 独立调用，每个 block 只带自己的正文 + 矩阵行，不看其他 block。`review.tech_findings` 使用 `_merge_dict` reducer（`orchestrator/state.py:87-102`）按 block_id 合并，未重跑 block 的旧 finding 天然保留。

## 5. 收敛判定与终止条件

### 5.1 判定规则

```python
未达标 block := {
    bid | tech_score[bid] < REVIEW_SCORE_THRESHOLD
       或 comp_score[bid]  < REVIEW_SCORE_THRESHOLD
       或 两侧任一 finding 含 severity == "critical"
}
```

`REVIEW_SCORE_THRESHOLD` 默认 `80`，`MAX_ITERATIONS` 默认 `3`，均可用环境变量覆盖（沿用 `agents/zhuge_liang.py:39` `ZHUGELIANG_BLOCK_CONCURRENCY` 的 `os.getenv` 读取方式）。

结果：

| 条件 | `review.convergence.status` | 去向 |
|---|---|---|
| 未达标集合为空 | `converged` | `gate_report` |
| `iteration >= MAX_ITERATIONS` | `max_iterations` | `build_feedback` → `gate_report`（只写反馈，标注未收敛，见 §5.3） |
| 否则 | `refine` | `build_feedback` → `collect_gaps`（回炉） |

### 5.2 终止出口

| 出口 | 触发 | 后续 |
|---|---|---|
| 收敛 | 全部 block 双分 ≥ 阈值且无 critical | `gate_report`，正常态 |
| 达上限 | `iteration == MAX_ITERATIONS` 仍未收敛 | `gate_report`，报告标注未收敛 block 及剩余 issues |
| 人工回修 | 用户在闸门 3 选 block | → `zhuge_liang_generate`（现有 `runner.regenerate()` 路径） |
| 中止 | 用户 abort / 取消信号 | → `abort_marker` → `END` |

### 5.3 达上限时补跑一次 `build_feedback`

达上限时，`check_convergence` 先路由到 `build_feedback` 再进 `gate_report`，**仅写** `review.feedback` 与补料请求，不递增 `iteration`、不写 `regenerate_targets`、不形成回边。

理由：用户在闸门 3 手动回修走的 `runner.regenerate()`（`orchestrator/runner.py:143`）直连 `zhuge_liang_generate`，会**绕过 `collect_gaps`**。若不补跑，最后一轮的人工回修既没有评审意见也补不了料，与现状的裸跑无异。`build_feedback` 不调 LLM，补跑几乎零成本，而人工回修恰恰是用户最需要评审意见的时候。

## 6. WorkflowState 变更

```python
class ProposalState(TypedDict, total=False):
    blocks: dict[str, dict]
    regenerate_targets: list[str]
    updated_blocks: list[str]                  # 新增：本轮实际生成的 block（评审范围）
    material_requests: dict[str, list[dict]]   # 新增：block_id → [{query, reason}]

class ReviewState(TypedDict, total=False):
    tech_findings: dict[str, dict]
    compliance_findings: dict[str, dict]
    report: dict
    feedback: dict[str, dict]                  # 新增：block_id → {issues, scores}
    convergence: dict                          # 新增：{status, unconverged_blocks, reason}

class WorkflowState(TypedDict, total=False):
    project_id: int
    thread_id: str
    stage: StageLiteral
    user_choice: UserChoiceLiteral
    cancel_requested: bool
    iteration: int                             # 新增
    spec: Annotated[SpecState, _merge_dict]
    materials: Annotated[MaterialsState, _merge_dict]
    proposal: Annotated[ProposalState, _merge_dict]
    review: Annotated[ReviewState, _merge_dict]
    config: Annotated[UserConfig, _merge_dict]
    errors: Annotated[list[ErrorEntry], operator.add]
```

所有新字段都有默认空值语义，`total=False` 下老 checkpoint 反序列化不会炸。

### 字段写入约定（更新）

| Agent / 节点 | 只读字段 | 只写字段 |
|---|---|---|
| 张衡 | `project_id, config` | `spec.*` |
| 沈括 | `spec.toc, spec.outline_matrix, materials.chunks` | `materials.matches` |
| 诸葛亮 | `spec, materials, config, review.feedback, proposal.regenerate_targets` | `proposal.blocks`、`proposal.updated_blocks`、`proposal.material_requests` |
| 王安石 | `proposal.blocks, proposal.updated_blocks, spec.outline_matrix` | `review.tech_findings` |
| 包拯 | `proposal.blocks, proposal.updated_blocks, spec.outline_matrix` | `review.compliance_findings` |
| `collect_gaps` | `proposal.material_requests, materials.chunks, materials.matches` | `materials.matches`、`proposal.material_requests` |
| `aggregate_review` | `review.tech_findings, review.compliance_findings` | `review.report` |
| `check_convergence` | `review.*findings, iteration` | `review.convergence` |
| `build_feedback` | `review.*findings, proposal.blocks` | `review.feedback`、`proposal.material_requests`、`proposal.regenerate_targets`、`iteration` |

## 7. 数据模型变更

`backend/domain/review/models.py` 的 `Issue` 增加两个字段：

```python
@dataclass
class Issue:
    severity: str
    point: str
    suggestion: str = ""
    needs_material: bool = False     # 新增：必须补料才能修复，非重写可解决
    material_query: str = ""         # 新增：缺什么，直接当检索 query

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "point": self.point,
            "suggestion": self.suggestion,
            "needs_material": bool(self.needs_material),
            "material_query": self.material_query,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        return cls(
            severity=d.get("severity", "medium"),
            point=d.get("point", ""),
            suggestion=d.get("suggestion", ""),
            needs_material=bool(d.get("needs_material", False)),
            material_query=d.get("material_query", ""),
        )
```

**判据来源**：由评审 agent 在输出 JSON 里自己声明，而不是事后用 LLM 分类。包拯的视角本就包含"否决项是否覆盖、证明材料是否齐备"（`agents/bao_zheng.py:1-8`），王安石也查"技术指标响应程度"，两者天然知道某条问题是"没写"还是"没料可写"。让 `build_feedback` 再调一次 LLM 去分类，既多花钱又不如当事者准。

**不采用 `GlobalReport.missing_evidence` 作为补料信号源**：该字段现由 `aggregate_review_node`（`orchestrator/nodes.py:677-687`）启发式计算，判据是"矩阵中有 `evidence_required` 但正文**完全为空**"。正文写满但未引用任何证明材料时不会触发，漏报严重。补料一律走 `Issue.needs_material`。（是否修复该启发式见 §14 开放问题。）

## 8. Agent 层变更

### 诸葛亮（`backend/agents/zhuge_liang.py`）

- `generate()` 签名增加 `feedback: Optional[dict[str, list[Issue]]] = None`
- 每个 block 生成时，若 `feedback` 中有该 block，走**修订分支**：prompt 携带该 block 的 issues（severity + point + suggestion），明确要求逐条响应
- 生成结果增加 `material_requests` 输出：新增 `_collect_material_requests()` 或在正文生成后追加一次结构化抽取
- 修订分支与首次生成分支共用流式路径，仅在 prompt 构造处分流

### 沈括（`backend/agents/shen_kuo.py`）

- 新增 `async def retrieve_for(requests: list[dict], chunks: list[dict]) -> list[Match]`：对一组补料请求做关键词检索 + LLM 重排，返回合并去重后的 Match 列表
- 复用现有 `keyword_search` / `llm_rerank`（`infra/retrieval/`），不新建检索链路
- 无 LLM 配置时降级为纯关键词截断，与 `match()` 现有行为一致（`shen_kuo.py:133-147`）

### 王安石 / 包拯（`backend/agents/wang_anshi.py`、`backend/agents/bao_zheng.py`）

- 仅 prompt 变更：要求 JSON 输出的 `issues` 中每条带 `needs_material` 与 `material_query`
- `_parse_finding` 解析这两个字段（缺省 `False` / `""`）
- 评审逻辑本身不变

### prompts（`backend/agents/prompts.py`）

- 诸葛亮 prompt 增加"按评审意见修订"变体
- 两位评审 prompt 增加 `needs_material` / `material_query` 的输出说明与判据

## 9. LangGraph 编排变更

### 新增节点常量

```python
NODE_COLLECT_GAPS = "collect_gaps"
NODE_CHECK_CONVERGENCE = "check_convergence"
NODE_BUILD_FEEDBACK = "build_feedback"
```

### 边变更

```python
# ① gate_materials 的下一跳从 generate 改为 collect_gaps
builder.add_edge(GATE_MATERIALS, NODE_COLLECT_GAPS)

# ② collect_gaps → generate（新增）
builder.add_edge(NODE_COLLECT_GAPS, NODE_GENERATE)

# ③ generate → 评审扇出：完全不变
#    _generate_fanout（graph.py:282-287）与 _route_after_pause（graph.py:303-309）
#    保持现状。collect_gaps 不在 generate 与评审之间，因此这里不需要改。
#    注意：skip_to_review 仍然直连评审扇出，不会绕回 generate —— 这正是
#    collect_gaps 前移带来的好处（若它夹在中间，skip_to_review 会被错误地
#    送回 generate）。

# ④ aggregate_review 的下一跳从 gate_report 改为 check_convergence
builder.add_edge(NODE_AGGREGATE, NODE_CHECK_CONVERGENCE)

# ⑤ check_convergence 条件路由：refine 与 max_iterations 都先去 build_feedback
def _route_convergence(state):
    status = (state.get("review") or {}).get("convergence", {}).get("status", "")
    return NODE_BUILD_FEEDBACK if status in ("refine", "max_iterations") else GATE_REPORT

builder.add_conditional_edges(NODE_CHECK_CONVERGENCE, _route_convergence, {
    NODE_BUILD_FEEDBACK: NODE_BUILD_FEEDBACK,
    GATE_REPORT: GATE_REPORT,
})

# ⑥ build_feedback 条件路由：refine 回环补料，max_iterations 直连闸门 3
def _route_feedback(state):
    status = (state.get("review") or {}).get("convergence", {}).get("status", "")
    return NODE_COLLECT_GAPS if status == "refine" else GATE_REPORT

builder.add_conditional_edges(NODE_BUILD_FEEDBACK, _route_feedback, {
    NODE_COLLECT_GAPS: NODE_COLLECT_GAPS,
    GATE_REPORT: GATE_REPORT,
})
```

`gate_report` 的条件路由 `_route_after_report`（`graph.py:173-185`）**保持现状**，人工回修路径 `regen_blocks → zhuge_liang_generate` 不变。

`interrupt_after` 列表不变：`[GATE_OUTLINE, GATE_MATERIALS, GATE_PAUSE, GATE_REPORT]`。

**关于 ⑤ 的两点说明**：

- `max_iterations` 也走 `build_feedback`，是为了 §5.3 的"达上限时补跑一次"。由 ⑥ 的 `_route_feedback` 决定它写完就直连 `gate_report`，不形成回边。
- `build_feedback` 因此需要区分两种模式：`refine` 模式写全部四个字段；`max_iterations` 模式**只写** `review.feedback` 与 `proposal.material_requests`，不写 `regenerate_targets`、不递增 `iteration`。

### 迭代上限的防御

结构上已由 ⑥ 保证：`_route_feedback` 只有 `status == "refine"` 时才返回 `NODE_COLLECT_GAPS`，而 `refine` 仅在 `iteration < MAX_ITERATIONS` 时由 `check_convergence` 给出。因此不存在无限回环的图结构。

额外加一层断言式兜底，防止判定逻辑被改坏：`build_feedback` 在 `iteration >= MAX_ITERATIONS` 时拒绝写 `regenerate_targets`，并把该情况记入 `errors`（`{agent: "build_feedback", message: "迭代已达上限仍收到 refine", retryable: false}`）。这样即使 `check_convergence` 判定出错，最坏结果也只是重复生成一轮，不会失控。

## 10. SSE 协议变更

新增事件：

| 事件 | 触发节点 | 载荷 |
|---|---|---|
| `iteration_start` | `collect_gaps` | `{iteration, total_rounds}` |
| `gaps_collecting` | `collect_gaps` | `{block_id, query}` |
| `gaps_done` | `collect_gaps` | `{block_id, new_matches, total_matches}` |
| `convergence` | `check_convergence` | `{status, unconverged_blocks, iteration}` |
| `feedback_ready` | `build_feedback` | `{targets, material_request_count}` |

`gate_open` 的 `review_report` 快照（`graph.py:127-131`）增加 `convergence` 与 `iteration`，让前端在闸门 3 能显示"第 N 轮 / 未收敛 block 列表"。

前端影响：`review.html` 与 `frontend/assets/review.js` 需要展示迭代轮次与未收敛标注。这是本次唯一涉及前端的改动，但**不阻塞后端实现**——新事件前端不订阅也能正常跑完流程。

## 11. 错误处理与降级

| 场景 | 处理 |
|---|---|
| 评审本身失败（`Finding.error` 非空） | **不触发回炉**。这是基础设施故障不是内容问题，无限回炉会烧光迭代次数。判定时排除 `error` 非空的 finding，`convergence.reason` 记为 `review_failed`，该 block 直接交人工终审 |
| `collect_gaps` 检索抛错 | 记 `errors`，保留原 matches 继续，不阻断 |
| 补料返回空 | 丢弃该请求；`feedback` 仍回灌，编写角色按现有素材尽力重写 |
| 某个 block 的 `needs_material` issue 连续两轮补不到料 | 保留在 findings 中，最终在闸门 3 报告里暴露为"素材库无对应证明材料" |
| `iteration` 超限 | 见 §9 防御逻辑 |
| LLM 未配置 | `check_convergence` 视为无法判定，直接去 `gate_report`；不进入迭代 |
| 人工回修绕过 `collect_gaps` | 已知限制。缓解手段是 §5.3 的达上限补跑 |

## 12. 测试策略

沿用现有测试布局（`tests/orchestrator/`、`tests/agents/`、`tests/domain/`）。

### 新增单元测试

- `tests/orchestrator/test_nodes.py`
  - `check_convergence`：全部达标 → `converged`；单 block 低分 → `refine`；含 critical → `refine`；达上限 → `max_iterations`；finding 带 error → 不计入未达标但记 `review_failed`
  - `build_feedback`：issues 正确拆成 feedback + material_requests；`needs_material=False` 的 issue 不进请求；达上限模式下不递增 iteration、不写 regenerate_targets；`iteration >= MAX_ITERATIONS` 时拒绝写 `regenerate_targets` 并记 errors
  - `collect_gaps`：无请求时空操作；有请求时 matches 并集去重不覆盖；检索失败不阻断
- `tests/agents/test_zhuge_liang.py`
  - 带 feedback 生成时 prompt 含 issues
  - `material_requests` 正确产出
  - `updated_blocks` 在首轮为全部 block、回炉轮为实际跑完的 block
- `tests/agents/test_shen_kuo.py`
  - `retrieve_for()` 无 LLM 配置时降级为关键词；空请求返回空
- `tests/agents/test_prompts.py`
  - 评审 prompt 含 `needs_material` 输出说明
- `tests/domain/test_review_repository.py`
  - `Issue` 新字段 to_dict / from_dict 往返；老数据（无新字段）反序列化不炸

### 编排集成测试

- `tests/orchestrator/test_graph.py`
  - 未达标时图走 `check_convergence → build_feedback → collect_gaps → generate` 并停在 `gate_report`
  - 收敛时不经 `build_feedback` 直接到 `gate_report`
  - 达上限时经 `build_feedback`（只写 feedback）后到 `gate_report`
  - 人工回修路径 `regen_blocks → generate` 仍然可用
- `tests/orchestrator/test_runner.py`
  - 迭代过程中的 checkpoint / 恢复

### Mock 策略

沿用 `LLM_MODE=mock`（`infra/llm/_mock.py`）。收敛测试通过注入确定性的 stub agent 产出固定分数，不依赖真实 LLM。

## 13. 迁移路径

1. **数据模型层**：`Issue` 加字段（向后兼容，老数据可读）
2. **State 层**：`state.py` 加字段与写入约定
3. **Agent 层**：沈括 `retrieve_for()`、诸葛亮 feedback 分支 + material_requests、两评审 prompt
4. **节点层**：三个新节点 + 单测
5. **编排层**：`graph.py` 边变更 + 集成测试
6. **SSE + 前端**：新事件 + `review.html` 展示
7. **回归**：`LLM_MODE=mock pytest tests/` 全绿；`tests/e2e/test_golden_sample.py` 需 `LLM_MODE=real` 验证

**前置条件**：开工前须先提交工作区现有未提交改动（截至设计时点 77 个文件、+3135/−7324 行），否则闭环重构的回归失败无法与既有改动二分定位。

## 14. 开放问题

1. **阈值口径**。当前 `REVIEW_SCORE_THRESHOLD` 对技术分与合规分用同一阈值（80）。投标场景下合规分（否决项、证明材料）可能应高于技术分。是否需要双阈值？
2. **`missing_evidence` 启发式是否修复**。`nodes.py:677-687` 的判据（正文完全为空才触发）漏报严重。本次不拿它当补料信号源，但它仍出现在 `GlobalReport` 里。是否顺手修正判据，还是留着？
3. **补料的 LLM 调用成本**。每轮补料会触发沈括的 LLM 重排。若某轮补料请求很多，是否需要对每轮补料总量设上限？
4. **`material_requests` 的抽取方式**。诸葛亮产出补料请求是"生成完正文后追加一次结构化抽取"还是"生成过程中流式标注"。前者多一次 LLM 调用但实现简单，后者省调用但要改流式协议。
