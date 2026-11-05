# TASKS：评审 v2：可解释的得分与可验证的修复

- 对应 spec：`specs/review-v2/spec.md`
- 对应 plan：`specs/review-v2/plan.md`
- 规则：一次只勾一项；每项完成必须跑验证命令；失败不得勾选。

## 验收命令（本项目）

```bash
cd backend && LLM_MODE=mock ../.venv/bin/python -m pytest ../tests/ -q --no-header
node --test tests/frontend/*.test.mjs
bash scripts/check.sh          # 6 步；含界面自检
cd backend && LLM_MODE=mock ../.venv/bin/python -m pytest ../tests/test_review_v2.py -q --no-header
```

## 任务清单

### Phase C（issue 状态机，先做：纯新增、可独立验证）

- [ ] T1 建表 `review_issue_states`（`db.py`，`CREATE TABLE IF NOT EXISTS`）
- [ ] T2 新增 `backend/services/review_issues.py`：`normalize_point()` / `fingerprint()` / `sync_issues()` / `mark_fixed()` / `list_issues()` / `summary()`
- [ ] T3 单测 `tests/test_review_v2.py::test_issue_fingerprint_stable_and_sync`：指纹稳定 + 两轮 open→verified / 仍在→open / fixed→open
- [ ] T4 接入 `chapter_review.review_block()`：评审落库后 `sync_issues()`
- [ ] T5 接入 `review_rewrite.apply_patch()`：成功应用后 `mark_fixed(fingerprint)`
- [ ] T6 新接口 `GET /api/blocks/{id}/studio/review/issues`、`GET /api/projects/{pid}/studio/review/issues/summary`

### Phase A（加权覆盖）

- [ ] T7 单测 `test_weighted_estimate_and_pending`：3 章 ok/medium/high + 30 分 → 15；全未评 → null 且在 pending
- [ ] T8 改 `scoring_verdict.build_scoring_verdict()`：加权 `estimated_score` + `estimated_basis`；`verdict` 保留最差

### Phase B（否决风险）

- [ ] T9 单测 `test_fatal_risks_and_risk_level`：critical+否决词 → red；仅 high → yellow；全 ok → green
- [ ] T10 改 `scoring_verdict`：`fatal_risks` + `summary.risk_level`

### Phase UI（前端）

- [ ] T11 `review.js` + `review.html`：KPI 置顶风险等级、fatal 列表、「预估依据」tooltip
- [ ] T12 `chapter-studio.mjs` + css：issue 状态徽标（未修 / 已修待验 / 已验）
- [ ] T13 前端资源版本号 v444 → v445

### Phase 收尾

- [ ] T14 全量回归：`pytest`（1058+）全绿、`node --test`、`scripts/check.sh` 全过
- [ ] T15 手测：41828 应用一条补丁 → issues 状态 `open→fixed`；自动复评后 `verified`/`open`；`scoring-verdict` 有 `risk_level`/`fatal_risks`/`estimated_basis`
- [ ] T16 更新 spec 状态为「已批准/已实现」，勾选本清单，记录阻塞与决策

## 阻塞与决策记录

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | 是否改 `scoring_risk` 的 `risk`（取最差） | 不改。`risk` 只用于颜色/排序；分数改在 `scoring_verdict` 层 |
| 2026-11-05 | `fatal_risks` 判定是否要 LLM | 不要。首版用 `severity=critical` + 关键词启发式，零成本、可解释 |
| 2026-11-05 | 老数据回填 | 首次评审时按当前 issues 回填；不写迁移脚本（数据量小） |
| 2026-11-05 | 既有测试 `test_point_takes_worst_chapter` 断言 `estimated_score==0` | 该断言编码的是**旧口径**（整项归零），按 spec A 更新为 `verdict=high` + `estimated_score=10`（加权），并改测试名 `test_point_verdict_worst_but_score_weighted` |
