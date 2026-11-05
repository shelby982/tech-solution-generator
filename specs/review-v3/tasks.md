# TASKS：评审 v3：三层评审与统一交付判断

- 对应 spec：`specs/review-v3/spec.md`
- 规则：一次只勾一项；每项完成必须跑验证命令。

## 任务清单

- [x] T1 `services/review_precheck.py`：八条规则 + `precheck()` + `resolve_tier()`
- [x] T2 `tests/test_review_v3.py`：预检 5 项（占位符/空表+无数字/正常通过/短节+截断）
- [x] T3 `chapter_review.review_block(tier=…)`：auto/fast/full 调度；fast 只跑合规角色
- [x] T4 `chapter_reviews.tier` 列 + 老库 ALTER 迁移 + ON CONFLICT 更新 tier
- [x] T5 `services/delivery_verdict.py`：`build_delivery_verdict()`（level/score/top3/explain）
- [x] T6 `tests/test_review_v3.py`：分层 1 项 + 判断 3 项
- [x] T7 路由：`POST review` 支持 tier；`GET precheck`；`GET delivery-verdict`
- [x] T8 前端：统一判断条（一个数字 + 三条必办）、L0 预检条、快评标注
- [x] T9 元问题词收紧 + 占位符纳入废标关键词（减少 fatal 误报）
- [x] T10 全量回归：1100 passed、`check.sh` 全过
- [x] T11 手测：precheck 48ms；`tier=fast` 200（12 条问题）；`delivery-verdict` 正常

## 实测记录

```
GET /api/blocks/41828/studio/review/precheck      → warn · high=1(empty_table) · 48ms（无 LLM）
POST /api/blocks/41828/studio/review {tier:fast}  → 200 · 单角色 · 综合 55 · 问题 12（完整双评审为 27）
GET /api/blocks/41828/delivery-verdict           → 74「不可交」red · fatal 2 · explain 全暴露
```

## 修掉的 bug

| bug | 现象 | 修复 |
|---|---|---|
| fast 路径 `UnboundLocalError: comp` | `tier=fast` 500 | 先取 tech/comp 再回填；补单测 |
| `ON CONFLICT` 未更新 `tier` | 重复评审后 tier 仍为空 | UPDATE 子句补 `tier=excluded.tier` |
| 元问题被当废标 | fatal 误报 12 条 | META_PATTERNS 收紧 + 占位符纳入关键词（现 fatal 2） |

## 阻塞与决策

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | L0 阈值（短节 120 字 / 全章 400 字）是否过严 | 保留；实测 19401 字的章不误报，测试内容需 >400 字才 pass（符合实际） |
| 2026-11-05 | 统一判断的 score 权重 | 20/40/40，explain 全暴露；level/can_submit 以硬条件为准 |
