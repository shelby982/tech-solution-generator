# TypeSafe System One（Jev）集成设计

- 日期：2026-09-20
- 作者：江河（与 DSH 协作）
- 状态：待用户审阅
- 范围：给 ge-solution 引入 TypeSafe System One（Jev 模型）作为**第二类模型服务**——与现有生成型 LLM 并存、分工不同：生成型负责"写"，System One 负责"判断"
- 依据：typesafe-ai skill（已共享安装于 `~/shared-agent/skills/typesafe-ai/`）+ 官方文档（docs.typesafe.ai，API 端点 `POST /v1/systemone` 已核实）

## 1. 背景：为什么是它

现有链路有两处"用生成型 LLM 干判断活"或"规则缺校准"的结构性短板：

1. **素材重排**（`infra/retrieval/rerank.py`）：每个 block 把 10 个候选 chunk 全文塞进 prompt，让生成模型输出 0-10 分 JSON。问题：分数无校准口径；JSON 不闭合 / 空响应时该 block 匹配直接置空（`llm_rerank` 失败返回 `[]`）；慢且贵（N block × 全量 prompt + 生成 token）。
2. **收敛判定**（`orchestrator/nodes.py:check_convergence_node`）：纯规则——双评审分数 ≥ `REVIEW_SCORE_THRESHOLD(80)` 且无 critical 即达标。规则明确，但"79 分 vs 81 分"的边界完全取决于王安石/包拯的打分口径，没有校准层。

TypeSafe System One（Jev）的定位恰好是**可组合的判断原语**：输入 `state` + 类型化问题（Choice / Noul / Score），返回结构化答案（概率 + confidence），不生成文本。按 skill 的工作流（找形状 → 设计判断 → 组合验证），两个用例都能落成"便宜的窄判断"。

## 2. 目标与非目标

### 目标
- Provider 接入与现有配置同轨：`config_store` 新增 `typesafe` provider，凭据仍走 `.api_config.json`，UI 可配
- 落地用例 A：**素材重排**（Jev 优先、生成型 LLM 兜底、关键词截断保底）
- 落地用例 B：**收敛临界带复核**（规则判定不动，临界分数带叠加 Jev 校准）
- `LLM_MODE=mock` 全覆盖；**未配置 TypeSafe 时行为与现状逐字一致**
- 降级链全程不抛错（沿用项目"单点失败不阻断"惯例）

### 非目标
- 不改王安石/包拯评审 prompt（他们继续产出 score + issues）
- 不用 Jev 做 8 字段提炼、正文生成（生成任务，不是判断任务）
- 不做 confidence 驱动的前端交互（后续）
- 不做多 provider 高可用（TypeSafe 失败直接降级到现有路径）

## 3. Provider 接入

| 项 | 方案 |
|---|---|
| `LLMConfig.provider` | 新增枚举值 `"typesafe"`；**不进** `OPENAI_COMPATIBLE_PROVIDERS`（systemone 不是 chat 协议） |
| 默认 URL / 模型 | `https://api.typesafe.ai/v1` / `jev-latest`（`DEFAULT_BASE_URLS` / `DEFAULT_MODELS` 各加一行） |
| 新客户端 | `infra/llm/typesafe_client.py`：`evaluate(config, state, questions) -> answers`，httpx 直连 `POST {base_url}/systemone`，`Bearer` 认证，超时 15s；带 `_warn_empty_answer` 风格的诊断日志（沿用 `clients.py` 惯例，不含凭据） |
| mock | `LLM_MODE=mock` 时返回确定性答案：Noul=0.9、Choice 取首项、Score 取中间级——保证测试可断言 |
| 前端 | `projects.html` 模型配置区的 provider 下拉加"TypeSafe（判断型）"选项，提示"仅用于重排/校准，不参与生成" |

## 4. 用例 A：素材重排（建议先做）

### 形状（skill 第 2 步：从行为倒推判断）

行为 = "每个 block 给出 top-5 真正相关的素材"。倒推：不是分类问题，是**逐候选的相关度等级 + 证据性闸门**。已知规则（BM25 召回、截断、去重）留在代码，只把"相关吗"这个语义判断交给 Jev。

### 判断设计（skill 第 3 步）

**一次 systemone 调用评完一个 block 的全部候选**（skill：independent questions over the same state together——共用 state、并行、互不可见）：

```json
{
  "state": {
    "query": "<title + requirement 拼接，即现有 rerank 的 query>",
    "requirement": "<该 block 8 字段中的 requirement 原文>",
    "candidates": [
      {"idx": 0, "title": "…", "excerpt": "<chunk 正文截 600 字>"}
    ]
  },
  "model": "jev-latest",
  "questions": {
    "rel_0": {
      "type": "score",
      "instructions": "candidate[0] 对满足 requirement 的相关程度",
      "criteria": ["与本章要求无关联", "仅背景相关", "部分覆盖要求", "直接覆盖关键要求点", "直接命中且含可引用数据/案例"]
    },
    "evidence_0": {
      "type": "noul",
      "instructions": "candidate[0] 含具体事实、数据或案例（非泛泛而谈）"
    }
  }
}
```

（`rel_1`…`rel_9`、`evidence_1`…`evidence_9` 同构生成；`idx` 是代码侧键，不发语义。）

### 组合与验证（skill 第 4 步）

- 排序键：`rel_score − (evidence_p < 0.5 ? 0.5 : 0)`（无证据的素材降权）
- 取 top-5 且 `rel_score` ≥ "部分覆盖"级；不足 5 条就少给（宁缺毋滥，现状是凑满 5 条）
- `hit_points`：Jev 是判断模型不产文本 → 留空，`reason` 写 `Jev rel=0.82 ev=0.71`（机器可解析）；如需命中要点展示，phase 2 对最终 top-5 补一次轻量 LLM 调用（可选）
- 阈值（0.5 / 降权量）先按默认上线，**拿到真实项目数据后校准**（skill 明确要求）

### 降级链（关键：比现状更稳）

```
Jev 可用且成功          → Jev 结果
Jev 未配置 / 调用失败    → 现有 llm_rerank（逐字不动）      ← 现状是直接置空，这里变好了
llm_rerank 也失败        → Match[] 空列表（现状行为）
无任何 LLM 配置          → BM25 top_n 截断 + score=0 标识（现状行为，不动）
```

### 成本对比

| | 现状（生成型） | Jev |
|---|---|---|
| 每 block 调用 | 1 次生成调用 | 1 次 systemone |
| 输入 | 10 候选全文 prompt（~2-3K tok） | 同量级 state |
| 输出 | 1-2K 生成 token（还要解析 JSON） | 结构化答案（无生成 token，接口即类型） |
| 失败模式 | JSON 不闭合 → 整 block 置空 | 协议层错误 → 降级链 |

## 5. 用例 B：收敛临界带复核

### 形状

`check_convergence_node` 的规则输出**不动**（spec §5.1 它是纯判定节点）；Jev 只在规则说不清的**临界带**上叠一层校准——这是 skill「confidence-gated routing」模式的落地：规则告诉你 What，校准告诉你 Whether to act。

### 判断设计

- **触发条件**：block 的 tech/comp 分数落在 `[75, 85)`（阈值 ±5 带宽，环境变量可调）且无 critical——只有这种 block 值得花判断预算；远离阈值的和有 critical 的维持规则结论
- 一次调用一个 block：

```json
{
  "state": {
    "requirement": "<该 block 的 requirement + key_points>",
    "content_digest": "<正文前 1500 字>",
    "review_issues": [{"severity": "critical", "text": "…"}]
  },
  "questions": {
    "substantively_failed": {
      "type": "noul",
      "instructions": "综合 review_issues 与 requirement，该 block 是否实质性未满足其应标要求（而非措辞、格式类可快速修正的问题）"
    }
  }
}
```

### 组合

- `P(yes) > 0.7` → 进 `unconverged`（维持回炉）
- `P(yes) < 0.3` → 该 block 视为达标（从 unconverged 移出）
- 中间值 → **维持规则判定结果**（skill：ignore uncertainty on unused branches——不确定时不推翻规则）
- `convergence` 结构新增 `calibration: {block_id: {p, used}}`，前端评审页可选展示（非必须）

### 为什么是 Noul 而非 Score

"是否实质性未达标"是单命题成立与否（skill：Noul = use one per label），等级化没有业务意义。预算上只对临界带 block 调用（离线量过占比），`MAX_ITERATIONS` 兜底机制不变。

## 6. 文件改动清单

| 文件 | 改动 |
|---|---|
| `backend/services/config_store.py` | provider 枚举 + 两个默认映射 |
| `backend/infra/llm/typesafe_client.py` | 新增：systemone 调用、mock 短路、诊断日志 |
| `backend/infra/retrieval/rerank.py` | `typesafe_rerank()` + 三级降级链 |
| `backend/agents/shen_kuo.py` | `match()` / `retrieve_for()` 优先走 `typesafe_rerank` |
| `backend/orchestrator/nodes.py` | `check_convergence_node` 临界带校准（用例 B） |
| `frontend/projects.html` | 配置 UI provider 选项 |
| `tests/infra/test_typesafe_client.py` | 新增：client 协议 / mock / 空响应诊断 |
| `tests/infra/test_rerank.py` | 新增：组合逻辑 / 降级链 / 阈值 |
| `tests/orchestrator/test_nodes.py` | 新增：临界带校准三分支 |
| `tests/agents/test_shen_kuo.py` | 新增：Jev 优先路径注入 |

## 7. 验证方案

1. **单测**：`LLM_MODE=mock` 全链路与现有 451 基线合流；重点覆盖降级链的每一级
2. **离线实测**（配 key 后）：项目 8 的真实素材 chunk + 10 章人工标注样本，对比 Jev top-5 与现有 llm_rerank top-5 的命中差——**这一步通过才全量上线**（skill：validate performance in the target domain）
3. **成本实测**：systemone 答案自带 usage，记录 token 与延迟对比表
4. **回归**：不配 TypeSafe key 时，全链路输出与改造前逐字一致（golden 路径）

## 8. 风险与决策点

| 风险 | 对策 |
|---|---|
| Jev 对中文应标域判断质量未知 | 先 10 章离线标注对比，通过再全量（§7.2） |
| 临界带参数（±5、0.7/0.3）经验值 | 环境变量可调 + 数据校准（skill 要求） |
| `hit_points` 缺失影响引用展示 | reason 机器可解析 + phase 2 可选补齐 |
| api.typesafe.ai 网络可达性 | 15s 超时 + 降级链保底，失败只损失质量不损失可用性 |

**待用户拍板的决策点：**
1. 先做 A（重排）还是 B（收敛复核）？**建议 A**：收益直接、验证面窄、失败模式比现状更稳
2. B 的临界带校准是否本轮就做？可以 A 验证通过后再启动
3. 命中要点（hit_points）是否需要 phase 2 补齐？
