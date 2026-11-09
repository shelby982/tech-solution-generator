# 双评审表问题分析：reviews vs chapter_reviews（2026-11-09）

> 结论先行：这不是"两张表内容重复"那么简单，而是**两条评审管线各写一张表、各被一半消费者读取**。
> 当前快照 44 条 `reviews`（22 个章×轮次组合 × 2 个 agent）与 20 条 `chapter_reviews`，
> 20 条章评审全部有对应 `reviews`，但有 2 个 `reviews` 轮次没有章评审——两条链路已经出现分叉。
> 统一的前提是先定"工作流评审是否仍是正式入口"和"是否保留 per-agent 明细"。

## 1. 两张表是什么

### `reviews`（旧：per-agent、per-run 明细）

DDL：`backend/db.py:631-641`（后经 `db.py:956` 追加 `criterion_text/satisfied/covered_points/missing_points`）。

| 字段 | 含义 |
|---|---|
| `thread_id` | 工作流 run；`reviews` 按 run 隔离 |
| `block_id` | **章节业务编码**（如 `s1.1.3.1`），不是 `blocks.id` |
| `agent` | `wang_anshi` / `bao_zheng` |
| `score` | 单个 agent 的 0-100 分 |
| `issues` / `strengths` | 单 agent 的 JSON |
| `satisfied` | 单 agent 满足度 |
| 无版本、无 tier、无 project_id | 无法判断内容是否已变、快慢评、项目范围 |

索引只有 `(thread_id, block_id)`，**没有唯一约束**。

### `chapter_reviews`（新：章级聚合、当前态）

DDL：`backend/db.py:362-376`（后经 `db.py:679/691` 追加 `tier/content_version/evidence_version`）。

| 字段 | 含义 |
|---|---|
| `block_id` | **`blocks.id` 主键**（INTEGER），与 `reviews.block_id` 同名但语义不同 |
| `tech_score` / `comp_score` / `score` | 技术分 / 合规分 / 两者均值 |
| `satisfied` / `criterion_text` | 章级满足度与评分口径 |
| `covered_points` / `missing_points` | 合并后的得分点覆盖 |
| `issues` | 合并后的 issues（带 `agent` 标记） |
| `needs_material` / `tier` | 缺素材清单 / fast·full·skip |
| `content_version` / `evidence_version` | 评审时的内容/证据版本，用于 stale 判定 |
| 无 `thread_id` | 章级当前态，不按 run 隔离 |

`block_id` 是主键，天然 upsert；历史轮次在 `review_history`，问题状态机在 `review_issue_states`。

## 2. 谁写、谁读

```
工作流评审（LangGraph review 节点）
  └─ ReviewRepository.add_finding()  ──► reviews（只写这张）
  └─ aggregate_review 只在 state 里聚合，不落 chapter_reviews

章节评审（编写页「评审本章」/批量评审）
  └─ chapter_review.review_block()
       ├─ review_history（每轮一条）
       ├─ chapter_reviews（upsert 当前态）
       └─ _write_back_reviews() ──► reviews（仅当 still_current）
```

读取侧：

| 消费者 | 读哪张表 | 用途 |
|---|---|---|
| `services/scoring_risk.py:54-79` | **`reviews`** 为主；`chapter_reviews` 只用来判 freshness | 评分点风险、覆盖矩阵 |
| `services/scoring_verdict.py:154-172` | **`reviews`** | 否决风险（fatal risks） |
| `services/project_progress.py:122-127` | **`reviews`** | 第 4 阶段"评审与交付"是否完成 |
| `routes/review.py` | **`reviews`** | 旧 `GET /api/review/{thread}`（前端已不用，仅测试） |
| `services/chapter_review.load_review()` | **`chapter_reviews`** | 编写页评审面板 |
| `routes/chapter_studio.py:1127-1160` delivery-verdict | **`chapter_reviews`**（`load_review`） | 统一交付判断、评审分 |
| `material_library/evidence_needs/writing_plans/review_rewrite` | **`chapter_reviews`** | 章节级缺素材、改写 |
| `run_full_review.py` | **`chapter_reviews`** | 选择"未评审"章节 |

**任何一边只写一张表，都会让另一半消费者看不到这次评审。**

## 3. 当前数据快照（本地开发库，2026-11-09 09:00）

| 指标 | 值 |
|---|---|
| `reviews` 行数 | 44 |
| 去重 `(thread_id, block_id, agent)` | 44（当前无重复，但没有唯一索引兜底） |
| 去重 `(thread_id, block_id)` | 22 |
| `chapter_reviews` 行数 | 20 |
| 有章评审但无 `reviews` | 0 |
| 有 `reviews` 但无章评审 | 2 个轮次：`53d6533d.../s1`、`tid/s1`（工作流/测试写入） |
| `chapter_reviews` 缺 `content_version` 或 `evidence_version` | 14 / 20（freshness = unknown） |
| `review_history` 覆盖章数 | 16（4 条 chapter_reviews 无历史） |
| `review_issue_states` | 305 行（只由章节评审同步） |

分数一致性样例：

- `41838`（full）：`chapter_reviews` tech=68 / comp=55 / avg=62，`reviews` 两 agent 分别 68 / 55，一致。
- `41829`（fast）：`chapter_reviews` tech=30 / comp=30 / avg=30，`reviews` 两行也都是 30（快评单角色被复制成两侧）。
- 多数 fast 章两 agent 分数完全相同，说明 `reviews` 里"两个 agent"并不总是两个独立判断。

## 4. 具体问题

### P0：同一份评审，两个页面看到不同结论

- 工作流评审只写 `reviews`：评分点风险/否决风险/阶段进度能看到；编写页 `load_review` 与 delivery-verdict 看不到（没有 `chapter_reviews` 行）。
- 章节评审优先写 `chapter_reviews`：编写页/交付判断能看到；评分点风险只有在 `_write_back_reviews` 成功时才看到。

### P0：stale 判定只覆盖一半数据

- `chapter_reviews` 有 `content_version/evidence_version`，`reviews` 没有。
- `scoring_risk.py:74-79` 只对"有 chapter_reviews 行的块"做 freshness 判断；工作流评审没有 chapter_reviews 行，**内容改过后旧 review 仍被当作最新**。
- 反过来，14 条缺版本号的 chapter_reviews（unknown）会被 `scoring_risk` 丢掉；`delivery_verdict.py:42-45` 也把 `unknown` 当"无评审"，但编写页仍把这条评审显示出来——同一行数据在三个地方三种待遇。

### P0：写入顺序/重复没有约束

- 工作流 `add_finding` 是纯 INSERT；`ReviewRepository.clear_thread()` 有定义但**从未被调用**（`repository.py:54`）。
- `chapter_review._write_back_reviews` 先 DELETE 再 INSERT（`chapter_review.py:314-327`），会清掉同块同 thread 的两 agent 行。
- 两条链路交替执行时，谁后写谁覆盖；`reviews` 无唯一索引，重复评审会累积多行。当前 44 行恰好没有重复，是数据巧合，不是 schema 保证。

### P1：`block_id` 同名不同义

- `reviews.block_id` = 业务编码 `s1.1.3.1`；`chapter_reviews.block_id` = `blocks.id` 主键。
- 所有跨表查询必须经 `blocks` 再映射一次（`scoring_risk` 已小心处理，但这是持续踩坑点）。
- `review_history`、`review_issue_states` 也是 `blocks.id`，与 `reviews` 不同。

### P1：快评/全评语义丢失

- `chapter_reviews.tier` 记录 fast/full/skip；`reviews` 没有 tier。
- fast 模式只跑合规角色，却把同一 finding 复制成 `wang_anshi` + `bao_zheng` 两行（`chapter_review.py:175-180`），下游无法区分"真双评"与"单评复制"。
- `reviews` 的 agent 行数在 fast 下仍是 2，`project_progress` 按行数统计会高估评审量。

### P1：`satisfied` / `missing_points` 口径不唯一

- `chapter_reviews.satisfied`：优先 tech/comp 的 satisfied，否则按分数兜底（`chapter_review.py:100-106`）。
- `_write_back_reviews` 写 `satisfied = worst(tech, comp)`，`missing_points` 用合并清单复制给两个 agent（`chapter_review.py:292-313`）。
- 边界情况下两表可能不同；`scoring_risk` 用 reviews 的 satisfied，delivery 用 chapter 的 satisfied。

### P1：reset-analysis 清理不完整

`projects.py:72-78` 删除 `reviews/blocks/...`，但**不删** `chapter_reviews`、`review_history`、`review_issue_states`、`chapter_materials`、`chapter_studio`。`blocks.id` 是 AUTOINCREMENT，旧 ID 不会复用，暂时不会张冠李戴，但会长期留下孤儿行与错误历史。当前快照孤儿为 0，只是尚未在评审后执行过 reset。

### P2：无 thread 时的跨项目串读

`scoring_risk.py:62-63` 与 `scoring_verdict.py:160-161` 在 `thread_id=None` 时执行 `SELECT * FROM reviews`，再按 `block_id` 取"每章最后一条"——不同项目若出现相同章节编码，会互相覆盖。当前调用多带 thread，属于潜在问题。

### P2：旧 API 已无前端消费者

`GET /api/review/{thread}` 只被 `tests/routes/test_review.py` 使用；前端评审页走 batch/chapter 接口。它继续暴露 per-agent 旧模型，增加"两套契约"的维护成本。

### P2：issue 状态机只跟章节评审同步

`review_issue_states` 只由 `review_block` 的 `sync_issues` 更新；工作流评审不更新。若工作流评审仍是正式入口，出现"评审结果变了、问题状态没变"。

## 5. 统一模型候选

### 方案 A：保留两表，明确"聚合 + 明细"职责（改动最小）

- `chapter_reviews` = 当前章级聚合（唯一当前态）。
- `reviews` = per-agent 明细/审计（append-only 或 upsert）。
- 强制所有写入走同一个 `review_store` 服务，一次事务写两表；给 `reviews` 补 `thread_id`（已有）、`content_version/evidence_version/tier/project_id`，加唯一键 `(thread_id, block_id, agent)`。
- 所有读者显式选择：风险/交付/UI 都先读 `chapter_reviews`，per-agent 明细只做下钻。

优点：保留 per-agent 审计；迁移量小。
缺点：仍是两张表，必须靠代码纪律保证同步。

### 方案 B：`chapter_reviews` 唯一当前态 + `review_findings` 子表（推荐，若需要审计）

```
chapter_reviews (id, project_id, block_id UNIQUE, thread_id,
                 tech_score, comp_score, score, satisfied, criterion_text,
                 covered_points, missing_points, issues, needs_material,
                 tier, content_version, evidence_version, updated_at)
review_findings (id, review_id, agent, score, satisfied,
                 issues, strengths, covered_points, missing_points,
                 UNIQUE(review_id, agent))
review_history (保留，按轮次快照)
```

- 删除 `reviews`（或改名 `reviews_legacy` 只读保留一个版本）。
- 工作流 review 节点调用同一个 `upsert_review()`，不再直接 INSERT。
- 所有消费者只读 `chapter_reviews` + freshness；需要 per-agent 时 join `review_findings`。

优点：单一当前态、freshness 统一、无同名 `block_id` 歧义。
缺点：需要迁移 44 行旧数据 + 2 个工作流-only 轮次；改 4 个消费者。

### 方案 C：单表 + JSON 明细（更轻）

- 不建 `review_findings`，把 per-agent 明细放进 `chapter_reviews.findings` JSON。
- 适合"per-agent 明细只用于展示、不需要 SQL 查询"的场景。
- 迁移最简单，但以后要做 agent 维度统计会回到 JSON 解析。

### 方案 D：保留 `reviews` 作为唯一表，章级聚合变成查询/视图

- 风险/交付/UI 都从 `reviews` 聚合；`chapter_reviews` 退化为视图。
- 需要给 `reviews` 补版本、tier、project_id，并解决 fast 复制问题。
- 适合"工作流评审是主入口、章节评审只是子集"的产品判断。

## 6. 推荐路线

**先做产品判断，再动 schema：**

1. **工作流评审（LangGraph 的 review 节点）是否仍是正式入口？**
   - 是 → 选 B/D，统一由一个服务写；工作流和章节评审共用 freshness/历史。
   - 否（只保留编写页/批量评审）→ 直接选 B，工作流节点只读或下线。
2. **是否需要 per-agent 明细（strengths / 单 agent 分数）？**
   - 需要 → B；不需要 → C。
3. **`unknown` freshness 算不算已评审？**
   - 建议：不算"已评审"，但 UI 要显示"评审版本未知，请复评"，不能静默当无评审。

**推荐目标（B）的迁移步骤：**

1. 新增 `thread_id/content_version/evidence_version/tier` 到统一表；`block_id` 统一用 `blocks.id`，业务编码另存 `block_code`。
2. 建 `review_findings`（或 `findings JSON`），回填 44 条 `reviews`：用 `(block_code, thread_id)` 映射 `blocks.id`；2 个工作流-only 轮次补建 `chapter_reviews`（版本未知）。
3. 抽 `services/review_store.py`：`upsert_review(block_pk, thread_id, tier, findings, versions)` 一次事务写当前态 + 明细 + history；工作流节点与 `review_block` 都调用它。
4. 改消费者：`scoring_risk` 直接读 `chapter_reviews`（按 block_pk，不再 join 代码）；`scoring_verdict` 读统一 issues；`project_progress` 按章计数；旧 `/api/review` 兼容映射。
5. 加约束：`chapter_reviews.block_id UNIQUE`、`review_findings(review_id, agent) UNIQUE`、必要时 FK；`reset_analysis` 清理新表。
6. 测试：同一章"工作流评审 → 编写页可见"、"章节评审 → 风险页可见"、"内容改动后两边都 stale"、"reset 后无孤儿"。

## 7. 需要你拍板的 4 个问题

1. 工作流评审（review 节点/报告闸门）是否还作为正式入口保留？
2. per-agent 明细（单 agent 分数/strengths）是否需要长期保存并查询？
3. `unknown` 版本的历史评审，交付判断里按"无评审"还是"待复核"处理？
4. `reset-analysis` 是否应该连同评审历史/问题状态一起清空（当前只清 `reviews`）？

拍板后我可以按 B/C/D 出具体迁移脚本与改动清单。

---

## 8. 产品口径补充：终评与「编写时逐章评审」合并

> 用户口径：现在实际只有两种评审——**终评（对最后一轮/整篇内容的评审）**，以及**各章在编写时对应的章节评审**；
> 这两者应该合成一套结果，而不是两套各看一半。

### 8.1 为什么现在终评只覆盖"最后一轮改过的章节"

`_run_review` 有一个刻意的过滤（`backend/orchestrator/nodes.py:931-937`）：

```python
updated = proposal.get("updated_blocks")
if updated:
    blocks = {bid: blk for bid, blk in blocks.items() if bid in updated_set}
```

注释说明原因：避免未改正文被重复评审、LLM 采样随机导致分数振荡。但终评节点复用的就是这同一个 `_run_review`，
所以**终评只拿到最后一轮 `updated_blocks`**；其他章节只有编写页的章节评审（`chapter_reviews`），
而终评写的是 `reviews`。两套结果因此天然对不上。

### 8.2 目标：一套「章节当前评审」+ 一套「评审轮次历史」

```
编写时评审（单章） ─┐
                    ├─► review_store.upsert_chapter_review(stage, findings, versions)
终评（整篇/跨章） ──┘        │
                             ├─ chapter_reviews（每章当前态，1 行）
                             │    stage = writing | final
                             │    tech/comp/score/satisfied/issues/versions
                             └─ review_history（每轮 1 行，含 stage）
                                  └─ review_findings（per-agent 明细，可选）
```

- **终评不再只写 `reviews`**：它读取已有 `chapter_reviews` 作为未改章节的基线；
  只对"内容/证据已变或从未评审"的章节重新调用 LLM；跨章一致性由终评的全局 pass 负责。
- **终评结果按章写回**同一个当前态，`stage='final'`，并保留 `writing` 轮次到历史。
- 编写页、评审页、评分点风险、交付判断都只读这一套当前态 + 版本 freshness，
  不再出现"风险页看到终评、编写页看到编写评审"。
- 全局报告（GlobalReport）仍是一个项目级聚合，单独存 `review_reports`（或继续放 state），
  但它的 per-chapter 依据来自同一套 `chapter_reviews`。

### 8.3 合并冲突规则（建议）

| 场景 | 建议 |
|---|---|
| 章节有终评，也有编写评审 | 当前态取**终评**（交付口径），编写评审保留在历史，可在章节页切换查看 |
| 终评未覆盖该章（未重跑） | 当前态保留最近的编写评审，`stage='writing'`，并标记"未经终评" |
| 终评与编写评审分数不同 | **不平均**；终评权威，历史保留两者，避免假精确 |
| 内容在评审后又被修改 | `freshness=stale`，风险页/交付页都按"待复审"，不静默采用旧分 |

如果产品希望"更保守"，可把规则改成 `worst(终评, 编写)`；但那会让"终评已修复"的章节仍被旧问题拖住，不建议默认。

### 8.4 终评覆盖范围：不要每次都全量重跑

推荐终评的 LLM 范围：

1. **未评审章节**：调用章节评审（可复用编写时同一套 prompt）。
2. **内容/证据版本已变的章节**：重新评审该章。
3. **未变章节**：直接采用其最近一次评审，不重复调用 LLM。
4. **全局一致性**：一次轻量全局 pass，只读各章评审摘要/问题清单，检查跨章矛盾、重复、口径漂移；
   产出一个项目级 `review_reports` 记录（不是 96 次全量重评）。

这样既满足"终评覆盖整篇"，又不会把成本放大到全量重跑，也避免未改章节分数随机振荡。

### 8.5 实施顺序（建议）

1. **先修终评范围**：给 `_run_review` 增加 `review_stage`/`scope`；终评阶段不再只取 `updated_blocks`，
   改为"changed/unreviewed + 已有章节评审基线"。
2. **抽 `review_store`**：`upsert_chapter_review()` 一次事务写 `chapter_reviews` + `review_history`（+ 可选 findings）；
   工作流终评和章节评审都调用它。
3. **迁移**：`reviews` 的 22 个轮次回填为 `stage='final'` 的章级记录；已有 `chapter_reviews` 标 `stage='writing'`；
   2 个工作流-only 轮次补建章级当前态（版本 unknown）。
4. **改消费者**：`scoring_risk`/`scoring_verdict`/`project_progress` 改读统一当前态；旧 `/api/review` 兼容映射。
5. **UI**：章节评审面板显示"当前（终评/编写）"+"评审历史"时间线；评审页的章节行与全局报告同源。
6. **修 reset**：连 `chapter_reviews`、`review_history`、`review_findings`、`review_issue_states` 一起清。
7. **测试**：终评覆盖所有章（含未改章）；编写评审→终评前可见；终评后风险/交付/编写页一致；
   内容改动后两边都 stale；reset 无孤儿。

### 8.6 需要确认的 3 件事

1. 终评是否采用"已评审章节复用 + 未评审/已改章节重评 + 全局一致性 pass"（推荐），还是每次全量重评？
2. 冲突时终评权威（推荐），还是取 worst？
3. 章节页是否要同时展示"编写时评审"和"终评"两栏，还是只显示当前 + 可展开历史？
