# SPEC：评审 v2：可解释的得分与可验证的修复

- slug：review-v2
- 日期：2026-11-05
- 状态：待审（人审后改为「已批准」）

## 1. 背景与问题

评审链路已打通（章节双评审 → 写回 `reviews` → 评分点聚合 → 得分预估 → 历史/定点重写/待办/成本预估），但存在三个**影响决策可信度**的问题：

1. **聚合失真（数字是错的）**
   `scoring_risk.summarize_scoring_risk` 里评分点 verdict 取「名下**最差**章节」(`backend/services/scoring_risk.py:113`)，
   再由 `scoring_verdict.SCORE_RATIO` 折算预估分。
   实际效果：30 分的「技术方案」下挂 8 章，只要 1 章 `unsatisfied`，整项预估 = **0 分**。
   投标决策会因此严重低估，且与"能拿多少分"的真实含义不符（应允许部分得分）。

2. **否决风险被淹没**
   废标/否决类问题与"缺量化指标"等并列在 issues 里，强度相同、无颜色分级。
   投标的第一优先级是"会不会废标"，产品上必须先看见它。

3. **修复不可验证**
   应用定点补丁 / 整节重写后，只能看**分数**有没有变（自动复评）。
   分数从 58→61 但同一条问题仍在 = **假修复**；反之问题修好但总分被别的章拉低 = 被误判。
   缺少"issue 级"的闭环状态。

不做会怎样：用户拿一个偏低的假分数做"能不能投"的判断；评审越用越不敢信；"改完"与"真好"之间没有证据。

## 2. 目标（可验证）

### 目标 A：评分点预估改为"加权覆盖"，允许部分得分

- A1 `scoring-verdict` 的 `points[].estimated_score` 按权重折算：
  `estimated = 分值 × Σ(章节系数) / 已评章节数`，系数沿用 `ok=1.0 / medium=0.5 / high=0.0`；
  **未评审章节不计入分母**（保持"待评"语义），全部未评 → `estimated_score = null`、计入 `score_pending`。
- A2 `points[].verdict` 仍保留（用于颜色），但不再等价于"整项零分"。
- A3 新增 `points[].estimated_basis`（覆盖度说明）：`{"reviewed": 3, "total": 8, "ratio": 0.375, "avg": 0.5}`，供前端解释"为什么是 18 分而不是 30"。

### 目标 B：否决风险单列 + 三色分级

- B1 `scoring-verdict` 新增 `fatal_risks: [{block_id, chapter, point, severity, source}]`：
  从最近一轮评审 issues 中筛出否决类（`severity=critical` 且命中否决/废标/实质性响应关键词，
  或 `needs_material` 且影响资格证明材料），最多 20 条。
- B2 `summary.risk_level ∈ {red, yellow, green}`：
  - `red`：存在 `fatal_risks`；
  - `yellow`：无 fatal 但存在 `high` 未闭合问题，或 `reviewed_ratio < 50%`；
  - `green`：无 fatal、无 high 未闭合、且已评章节全部 `ok/medium`。
- B3 前端评审页 KPI 第一块显示 `risk_level`（红/黄/绿 + 一句话），并把 `fatal_risks` 列表置顶。

### 目标 C：issue 状态机（open → fixed → verified / reopened）

- C1 新增表 `review_issue_states`：
  `(id, block_id, fingerprint, status, severity, point, first_seen_at, last_seen_at, fixed_at, verified_at)`，
  `status ∈ {open, fixed, verified}`，`fingerprint` 唯一。
- C2 **指纹口径**：`sha1(severity + "|" + normalize(point))`，
  `normalize` = 去空白/标点、取前 40 字、去数字差异（数字统一替换为 `#`）。
- C3 每次章节评审完成后：
  - 命中的 issue → `open`（若已 `verified` 则 `reopened` 视为 `open` + `fail_count+1`）；
  - 上一轮 `open` 但本轮**未**命中 → 置 `verified`（有 `fixed_at`）或 `verified`（从未修过但消失了，也要记）；
  - 已 `fixed` 但本轮仍命中 → 回到 `open`（记录 `reopened_at`）。
- C4 应用定点补丁时，把对应 `issue_index` 的指纹置 `fixed`（`fixed_at=now`）。
- C5 新接口 `GET /api/blocks/{id}/studio/review/issues` 返回带状态的 issue 列表与计数
  `{open, fixed, verified}`；`GET /api/projects/{pid}/studio/review/issues/summary` 给项目级计数。
- C6 前端：
  - 编写页评审卡片每条 issue 显示状态徽标（未修 / 已修待验 / 已验）；
  - 「按此重写」应用后该条变"已修待验"；自动复评后按 C3 变"已验"或仍"未修"。

## 3. 非目标（明确不做）

1. **不做**三层评审（L0 规则预检 / L1 单角色 / L2 双角色）——留到 spec-review-v3。
2. **不做** rubric 达标线库（按章类型的"满分要素清单"）。
3. **不做**写法库/反例库回流（section_practices 沉淀）。
4. **不改**评审 agent 的 prompt 与打分口径（王安石/包拯），本 spec 只改"聚合与追踪"。
5. **不改**批量评审任务与成本预估机制（除 B 需要的 fatal 过滤读取外）。
6. **不做**跨项目/跨包评审，不做评审结果导出。

## 4. 约束与依赖

- 技术约束：
  - 全部为本地计算 + SQLite 读写，**不新增 LLM 调用**（成本零增加）。
  - 兼容老数据：`scoring-verdict` 只**新增**字段，不改既有字段语义（`points[].verdict` 保留）。
  - 新表用 `CREATE TABLE IF NOT EXISTS`，随 `db.py` 初始化创建（与 `chapter_reviews` 同处）。
  - 指纹用 `hashlib.sha1`，与 `todo_tasks._task_id` 同风格。
- 兼容性要求：
  - 现有 `tests/`（1058 passed）不得回归；`scripts/check.sh` 6 步全过。
  - 前端资源版本号 `?v=` 递增（当前 v444）。
- 时间/人力：单人，预计 1.5~2 天（A 半天、B 半天、C 一天）。

## 5. 验收标准（Definition of Done）

- [ ] `cd backend && LLM_MODE=mock ../.venv/bin/python -m pytest ../tests/ -q --no-header` 全绿，且新增测试覆盖：
  - [ ] A：3 章（ok/medium/high）+ 分值 30 → `estimated_score = 15`；全未评 → `null` 且进 `score_pending`
  - [ ] B：构造 critical+否决关键词 issue → `risk_level=red` 且 `fatal_risks` 非空；仅 high → `yellow`；全 ok → `green`
  - [ ] C：评审两轮（issue 消失 / 仍在）→ 状态 `open→verified` / `fixed→open`；指纹对同义改写稳定
- [ ] `bash scripts/check.sh` 全过（含界面自检 ui-selfcheck）
- [ ] `node --test tests/frontend/*.test.mjs` 全过
- [ ] 手测：`GET /api/projects/29/scoring-verdict?thread_id=…` 返回 `risk_level`、`fatal_risks`、`points[].estimated_basis`
- [ ] 手测：应用一条补丁后 `GET /api/blocks/41828/studio/review/issues` 该条状态由 `open` → `fixed`，自动复评后 → `verified`（或 `open` 表示未修好）
- [ ] 文档/配置变更已同步（本 spec + plan + tasks 状态更新）
- [ ] 评审结论为 approve

## 6. 风险与待决问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| issue 指纹对 LLM 措辞变化不稳定 → 误判"未修复" | C 的闭环失真 | 归一化去数字/标点 + 相似度阈值；把阈值提成常量，先用真实数据（41828 两轮 27→27 条）标定 |
| 加权覆盖会让"严重不满足"被平均掉 → 虚高 | 用户误判能投 | 保留 A2 的 `verdict` 颜色 + B 的 `fatal_risks` 硬否决；`summary.risk_level` 以"最差风险"为准，不用平均 |
| `fatal_risks` 关键词启发式漏判/误判 | 红色误报或漏报 | 关键词表集中定义、可配置；首版由 `critical` 全量 + 关键词双重条件，宁多勿漏并在 UI 标注"仅供参考" |
| 老数据没有 `review_issue_states` | 首次查询空 | 首次评审时全量回填（当前仅 5 章有评审，成本可忽略） |
| 前端新增状态徽标打乱现有密度 | 可读性 | 徽标仅 2 字、灰度样式，不新增行高 |

## 7. 人审记录

- 评审人：
- 结论：
- 修改要求：
