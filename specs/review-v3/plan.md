# PLAN：评审 v3：三层评审与统一交付判断

- 对应 spec：`specs/review-v3/spec.md`
- 日期：2026-11-05
- 状态：已实现

## 1. 方案概览

三块解耦实现：

- **A（L0 预检）**：纯正则/统计模块 `services/review_precheck.py`，八条规则，不发 LLM，实测 **48ms**。
- **B（分层调度）**：`review_block(tier=auto|fast|full)` 按 L0 结果选路；`fast` 只跑合规角色（实测 12 条问题 vs 完整 27 条，成本减半）；`tier` 落 `chapter_reviews.tier` 并在接口/UI 回传。
- **C（统一判断）**：`services/delivery_verdict.py` 把规则分/评审/L0/否决风险合成 `{score, level, label, can_submit, top3, explain}`，一条结论 + 三条必办。

## 2. 关键设计决策

| 决策 | 备选 | 理由 | 风险 |
|---|---|---|---|
| L0 用强词（待补充/待明确/【待…】）+ 统计规则 | 上 LLM 判"是否完整" | 0 成本、可解释、秒级 | 漏判语义型缺失 → 由 L1/L2 兜 |
| auto：L0 有 critical → 只跑 fast | 永远跑 full | 硬伤阶段精评无意义，省一半成本 | 用户误以为完整评审 → tier 在 UI 明示 |
| verdict 的 level 以硬条件（fatal/L0 critical/unsatisfied）为准 | 用加权分阈值 | 投标是一票否决逻辑，平均分会掩盖雷 | 分数仅作趋势参考，explain 全暴露 |
| score = L0×20 + 规则×40 + 评审×40 | 纯评审分 | 让"没评审"也有可读数字，且突出预检 | 权重凭经验 → explain 可解释 |

## 3. 改动面

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/services/review_precheck.py` | 新增 | A：八条规则 + `resolve_tier()` |
| `backend/services/delivery_verdict.py` | 新增 | C：统一判断 |
| `backend/services/chapter_review.py` | 修改 | B：tier 调度、tier 落库（含 ON CONFLICT 更新）、precheck 附在返回 |
| `backend/db.py` | 修改 | `chapter_reviews.tier` 列 + 老库 ALTER 迁移 |
| `backend/routes/chapter_studio.py` | 修改 | `POST review` 支持 `tier`；新增 `precheck`、`delivery-verdict` |
| `backend/services/scoring_verdict.py` | 修改 | 元问题词收紧、占位符纳入废标关键词 |
| `frontend/assets/chapter-studio.mjs` | 修改 | 统一判断条、L0 预检条、快评标注 |
| `frontend/assets/chapter-studio.css` | 修改 | 两套样式 |
| `frontend/review.html` / `review.js` | 修改 | v2 风险区延续 |
| `tests/test_review_v3.py` | 新增 | 8 项单测 |

## 4. 验证

- 单测：`tests/test_review_v3.py` 8 项（预检 5 / 分层 1 / 判断 3，含 fast 单角色合并）
- 回归：`pytest ../tests/` 1100 passed；`scripts/check.sh` 全过
- 手测：precheck 48ms；`tier=fast` 返回 200、只 1 角色（12 条问题）；`delivery-verdict` 返回 score/level/top3/explain

## 5. 实施顺序（实际）

1. L0 预检 + 8 项单测 → 2. tier 调度（含 fast 单角色）→ 3. 统一判断 → 4. 接口 → 5. 前端 → 6. 联调修 3 个 bug → 7. 回归

## 6. 回滚

- 纯新增模块与字段；前端只加不删。
- 若 L0 误报过多：`resolve_tier` 强制返回 `full`（一行改动）即可关闭分层。
- 若统一判断被质疑：`chapter-studio.mjs` 里 `deliveryVerdictHtml()` 返回空串即隐藏，不影响旧展示。
