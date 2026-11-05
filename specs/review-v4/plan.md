# PLAN：评审 v4：达标线 / 证据链 / 写法回流

- 对应 spec：`specs/review-v4/spec.md`
- 日期：2026-11-05
- 状态：已实现

## 1. 方案概览

三块都是"把评审的价值落地"，全部零 LLM 成本：

- **A 达标线**：`services/chapter_rubric.py` 按章类型（现状/方案/实施/团队/政策/验收/通用）给 3~4 条满分要素，
  用规则核对（数字数、来源标注、痛点词、技术手段词、指标正则…），输出逐条 met + 命中片段。
- **B 证据链**：`services/review_evidence.py` 复用 `review_rewrite.locate` 三级定位给 issue 补原文片段，
  用 2-gram 重合从评分口径里挑最相关一句；定位不到明确 `locatable=False`。
- **C 写法回流**：`services/practice_recycle.py` 把评审 `strengths` 变成写法候选，用户点「采用」才写入
  `section_practices`（`_chapter_` 为整章写法）。

## 2. 关键设计决策

| 决策 | 备选 | 理由 | 风险 |
|---|---|---|---|
| rubric 用规则核对 | LLM 判"达不达标" | 零成本、可解释、与 L0 同源 | 语义型达标会漏 → 每条只给"建议补"，不判错 |
| 章类型按标题关键词 + 正文兜底 | 用计划里的 type | 标题最稳定；计划 type 不一定有 | 识别错 → 兜底 generic 通用清单 |
| 证据链用 locate + 2-gram | LLM 语义匹配 | 复用已验证的定位器，零成本 | 泛化 issue 定位不到 → 明示不可定位 |
| 写法必须用户显式采用 | 自动写入 practices | 避免把个别好句子当成规则污染提示词 | 用户不采用则不生效（可接受） |

## 3. 改动面

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/services/chapter_rubric.py` | 新增 | A：类型识别 + 6 类清单 + 规则核对 |
| `backend/services/review_evidence.py` | 新增 | B：证据链 |
| `backend/services/practice_recycle.py` | 新增 | C：候选生成 + 采用写入 |
| `backend/routes/chapter_studio.py` | 修改 | issues 带 evidence；新增 rubric / practice-candidates / apply |
| `frontend/assets/chapter-studio.mjs` | 修改 | 达标线卡、issue 依据、写法候选 |
| `frontend/assets/chapter-studio.css` | 修改 | 三处样式 |
| `tests/test_review_v4.py` | 新增 | 7 项单测 |

## 4. 验证

- 单测：7 项（类型识别 1 / 达标线 2 / 证据 2 / 写法 2）
- 回归：`pytest ../tests/` 1110 passed；`check.sh` 全过
- 手测：rubric 4/4（现状类，261 处数字）；issues 24 条其中 18 verified、4 条带可定位证据；候选接口正常（本次 strengths 为空 → 0 条，符合预期）

## 5. 回滚

- 纯新增模块 + 接口只增不改；前端三个 block 若出问题，对应 `xxxHtml()` 返回空串即隐藏。
- 写法采用是显式动作，未采用则不写库，无副作用。
