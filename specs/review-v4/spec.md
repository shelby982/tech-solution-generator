# SPEC：评审 v4：达标线 / 证据链 / 写法回流

- slug：review-v4
- 日期：2026-11-05
- 状态：已实现（A/B/C 全部落地，实测见 tasks.md）
- 上游：`specs/review-v2`（加权得分/否决风险/issue 状态机）、`specs/review-v3`（L0 预检/分层/统一判断）

## 1. 背景与问题

v2/v3 解决了"分数算对、风险看见、修复可验证、判断统一"。还剩三个"评审价值没落地"的问题：

1. **评审说"缺量化"，但没说"补到什么程度算够"**
   用户拿到「现状描述缺少量化事实」只能猜。缺**达标线（rubric）**：按章类型给出"满分要素清单"（如现状类章 = 系统数量/数据量/用户数/等保级别/并发）。
2. **评审意见不可审计**
   issue 只有「问题 + 建议」，没有「依据哪段原文 / 命中哪条评分口径」。投标场景要能回答"你凭什么说这章不行"。
3. **评审好经验不沉淀**
   评审每轮都会产出 `strengths`（写得好的地方），但只展示一次；没有回流成"本节写法"，下一章/下一项目还要重新摸索。

## 2. 目标（可验证）

### 目标 A：达标线（rubric）按章类型给出满分要素

- A1 `services/chapter_rubric.py::rubric_for(title, content)`：
  - 识别章类型：`status`（现状/背景）/ `plan`（方案/设计/架构）/ `impl`（实施/进度/计划）/ `team`（团队/组织）/ `policy`（政策/依据）/ `accept`（验收/质量）/ `generic`
  - 每类给 3~6 条要素：`{key, name, need, check: L0 规则名 or 文本特征, hint, weight}`
- A2 `check_rubric(content, rubric)` → 每条 `{key, name, need, met: bool, evidence: 命中片段, hint}`
- A3 接口 `GET /api/blocks/{id}/studio/review/rubric` → `{chapter_type, items, met_count, total, ratio}`
- A4 前端：编写页评审卡片下方「达标线 x/y」+ 逐条 ✓/✗ + 未达点的提示（点击可定位/重写）

### 目标 B：评审证据链（issue 可审计）

- B1 `services/review_issues.py::attach_evidence(block_content, issues, criterion_text)`：
  为每条 issue 补 `evidence = {quote, section, criterion, locatable}`
  - `quote`：从 issue.point 的引用串/关键词定位到的**原文片段**（复用 `review_rewrite.locate`）
  - `criterion`：本章评分口径（`criterion_text`）里与 issue 文本最相关的一句
- B2 `GET /api/blocks/{id}/studio/review/issues` 每条返回 `evidence`
- B3 前端：issue 行可展开「依据」：原文片段（高亮）+ 评分口径句；不可定位的标注"无法定位原文"

### 目标 C：写法回流（strengths → 本节写法候选）

- C1 `services/practice_recycle.py::candidates_from_review(review, practices)`：
  从评审 `strengths`（及高分章节的 satisfied 判定）生成 1~3 条**写法候选**：
  `{section, text, source: "review_strength", applied: bool}`
- C2 接口 `GET /api/blocks/{id}/studio/practice-candidates`（只读候选）
  + `POST /api/blocks/{id}/studio/practice-candidates/apply` {section, text} → 写入 `section_practices`（用户显式采用才落库）
- C3 前端：计划面板「写法」旁显示"评审建议写法 N 条 · 采用"（点击写入该节写法）

## 3. 非目标

1. 不做 rubric 的模型判定（v1 用 L0 规则 + 文本特征，保证可解释、零成本）。
2. 不做跨项目写法库共享（只在项目内 `section_practices`）。
3. 不做全自动改写法（必须用户点"采用"）。
4. 不改评审 agent prompt（strengths 已是既有字段）。
5. 不做证据链的 LLM 语义匹配（用定位 + 字面匹配）。

## 4. 约束与依赖

- 技术：A/C 纯本地；B 复用 `review_rewrite.locate`（已有三级定位，无 LLM）。
- 兼容：`issues` 接口新增 `evidence` 字段（只增不改）；`section_practices` 复用既有表。
- 回归：`pytest` 基线 1100 不回归；`check.sh` 全过。

## 5. 验收标准（DoD）

- [ ] `tests/test_review_v4.py` 全绿，覆盖：
  - [ ] rubric：现状类章识别 + 缺数字时不达「量化事实」；方案类章识别 + 缺"目标-手段-指标"不达标
  - [ ] evidence：issue 引用串能在正文定位 → `locatable=True` 且 quote 命中；无对应原文 → `locatable=False`
  - [ ] practice 候选：strengths 生成候选；apply 后 `load_practices` 能读到
- [ ] `pytest ../tests/` 全绿、`check.sh` 全过
- [ ] 手测 `GET /api/blocks/41828/studio/review/rubric`：返回章类型 + 达标项
- [ ] 手测 `GET /api/blocks/41828/studio/review/issues`：每条带 evidence
- [ ] 手测 `GET /api/blocks/41828/studio/practice-candidates` + apply

## 6. 风险与待决

| 风险 | 影响 | 缓解 |
|---|---|---|
| rubric 用规则判"达标"可能误判（如用文字描述代替数字） | 用户被误导 | 每条要素标 `check` 依据；未达标只给"建议补"而非"错误" |
| 证据链定位不到原文（issue 描述泛） | evidence 空 | 明确返回 `locatable=False`，UI 不假装有依据 |
| 写法官腔化（把某段好句子抽象成规则） | 写法质量差 | 候选只来自 strengths 原文，附"来源轮次"，用户决定是否采用 |
| 章类型识别错（标题不含关键词） | rubric 给错清单 | 兜底 `generic`（通用四要素），并允许前端显示"通用清单" |

## 7. 人审记录

- 评审人：用户（2026-11-05「继续」）
- 结论：按 P2 路线执行
