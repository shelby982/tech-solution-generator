# PLAN：评审 v2：可解释的得分与可验证的修复

- 对应 spec：`specs/review-v2/spec.md`
- 日期：2026-11-05
- 状态：待批准

## 1. 方案概览

只动"评审结果怎么聚合、怎么分级、怎么追踪"，**不动评审 agent 的 prompt 与打分**，因此成本零增加、口径不变，纯粹把已有数据算对、看全、追住。

- **A 加权覆盖**：改 `scoring_verdict.build_scoring_verdict`，预估分从"最差章节决定整项"改为"按已评章节系数平均 × 分值"，并输出 `estimated_basis` 解释依据。`scoring_risk` 的 `risk`（取最差）保留用于颜色，不改。
- **B 否决风险单列**：在 `scoring_verdict` 内基于 `reviews` 最近一轮 issues 计算 `fatal_risks` 与 `summary.risk_level`，前端 KPI 置顶。
- **C issue 状态机**：新增 `review_issue_states` 表 + `services/review_issues.py`（指纹、迁移、状态流转）；`chapter_review.review_block` 评审后调用 `sync_issues()`；`review_rewrite.apply_patch` 应用补丁后把对应指纹置 `fixed`；新增两个查询接口；前端徽标。

三层分工清晰：A/B 是"读时计算"（无状态，可随时重算），C 是"写时追踪"（有状态，需落库）。二者解耦，A/B 出问题不影响 C。

## 2. 关键设计决策

| 决策 | 备选方案 | 选择理由 | 风险 |
|---|---|---|---|
| A：按已评章节平均，未评不计入分母 | ①按全部章节（未评算 0）②取最差 | ①会低估（未写章节不该判 0）②正是当前 bug；平均 + 未评单列 `score_pending` 与现有 KPI 语义一致 | 大量未评时估计偏高 → 由 `estimated_basis.ratio` 明示 + `risk_level` 独立判定 |
| A：`verdict` 保留"最差" | 改成平均后的档位 | 颜色语义是"这一项有没有雷"，最差合理；分数语义是"预计能拿几分"，平均合理。两者分开 | 前端需同时展示，避免用户混淆 |
| B：`risk_level` 以最差风险为准，不用平均 | 用综合分阈值 | 投标是"一票否决"逻辑，不能用平均掩盖 | 关键词启发式误报 → 全量 critical 也计入，UI 标注"仅供参考" |
| C：指纹 = severity + 归一化 point（数字→#，去标点，取前 40 字） | ①LLM 语义匹配 ②完全字符串 | ①要额外调用、成本高 ②措辞变就失配。归一化折中且零成本 | 同义改写漏配 → 阈值/关键词可调，先用 41828 两轮真实数据标定 |
| C：状态存独立表，不写进 `reviews.issues` JSON | 在 JSON 里塞 status | 独立表可跨轮追踪、可聚合、可索引；JSON 是"某轮快照"，语义不同 | 增加一次 upsert，性能可忽略（每章 ≤30 条） |
| C：应用补丁即置 `fixed`，复评才 `verified` | 只在复评时判定 | 区分"我改了"和"系统确认改好了"，避免自我认证 | 复评失败时停在 `fixed`，UI 显示"待验证" |

## 3. 改动面

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/services/scoring_verdict.py` | 修改 | A：加权 `estimated_score` + `estimated_basis`；B：`fatal_risks` + `risk_level` |
| `backend/services/review_issues.py` | 新增 | C：指纹、`sync_issues()`、`mark_fixed()`、`list_issues()`、`summary()` |
| `backend/db.py` | 修改 | C：`review_issue_states` 建表 |
| `backend/services/chapter_review.py` | 修改 | C：评审落库后调用 `sync_issues()` |
| `backend/services/review_rewrite.py` | 修改 | C：`apply_patch()` 成功后 `mark_fixed(fingerprint)` |
| `backend/routes/chapter_studio.py` | 修改 | C：`GET /blocks/{id}/studio/review/issues` |
| `backend/routes/review_batch.py` | 修改 | C：`GET /projects/{pid}/studio/review/issues/summary` |
| `frontend/assets/review.js` | 修改 | A/B：KPI 风险等级 + fatal 列表 + 预估依据；A：分数说明 |
| `frontend/review.html` | 修改 | B：风险区块容器 |
| `frontend/assets/chapter-studio.mjs` | 修改 | C：issue 状态徽标（未修/已修待验/已验） |
| `frontend/assets/chapter-studio.css` | 修改 | 徽标样式 |
| `tests/test_review_v2.py` | 新增 | A/B/C 单测 |
| `specs/review-v2/{spec,plan,tasks}.md` | 修改 | 状态与勾选 |

## 4. 验证方案

- 单测（新增 `tests/test_review_v2.py`）：
  - A：3 章 ok/medium/high、分值 30 → 15 分；未评章节不进分母；全未评 → `null` 且 `score_pending` 含该分值
  - A：`estimated_basis` 的 `reviewed/total/avg` 正确
  - B：critical + 否决关键词 → `risk_level=red`、`fatal_risks` 非空；仅 high → `yellow`；全 ok 且已评 → `green`
  - C：`fingerprint()` 对"缺量化指标" 与 "缺量化指标。" 一致；对换数字的两句一致
  - C：`sync_issues()` 两轮（消失 → `verified`；仍出现 → `open`；fixed 后又出现 → `open`）
- 集成/手动：
  - `GET /api/projects/29/scoring-verdict?thread_id=…` 看 `risk_level / fatal_risks / estimated_basis`
  - 章 41828：应用一条补丁 → `GET /api/blocks/41828/studio/review/issues` 该条 `fixed` → 自动复评后 `verified` 或 `open`
- 回归命令：
  - `cd backend && LLM_MODE=mock ../.venv/bin/python -m pytest ../tests/ -q --no-header`
  - `node --test tests/frontend/*.test.mjs`
  - `bash scripts/check.sh`

## 5. 实施顺序

1. **C-1** 建表 + `services/review_issues.py`（指纹/流转/查询）+ 单测（无 UI，可独立验证）
2. **C-2** 接入 `chapter_review.sync_issues()`、`review_rewrite.mark_fixed()`、两个查询接口
3. **A** `scoring_verdict` 加权覆盖 + `estimated_basis` + 单测
4. **B** `fatal_risks` + `risk_level` + 单测
5. **前端** 评审页 KPI/列表 + 编写页状态徽标（版本号 +1）
6. **联调**：真实跑 41828 一轮（应用补丁 → 复评），核对 `open→fixed→verified`
7. **收尾**：`pytest` + `check.sh` + 手测记录，勾选 tasks

## 6. 回滚方案

- 纯新增字段/表，不改既有字段语义 → 前端出现问题可只回退前端资源版本号。
- 若 C 的指纹策略导致误判泛滥：`REVIEW_ISSUE_MATCH_MIN` 常量改为 1.0（完全一致才匹配）即可降级为严格模式，无需改表结构。
- 若 A 引起用户困惑：`scoring_verdict` 加回退开关（环境变量 `SCORING_ESTIMATE_MODE=worst|weighted`，默认 weighted）——本 spec 不实现开关，仅在需要时作为回滚手段。
- 数据层只增不改：`review_issue_states` 可整表删除重建，不影响 `reviews/chapter_reviews`。
