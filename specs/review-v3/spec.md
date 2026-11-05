# SPEC：评审 v3：三层评审与统一交付判断

- slug：review-v3
- 日期：2026-11-05
- 状态：已实现（A/B/C 全部落地，实测见 tasks.md）
- 上游：`specs/review-v2/`（A 加权得分 / B 否决风险 / C issue 状态机 已落地）

## 1. 背景与问题

v2 让"分数算对了、风险看得见、修复可验证"。但仍有两个产品级问题：

1. **成本与反馈时延不匹配**
   每章评审 = 双角色 LLM（实测 11,801 token / 30~60 秒）。96 章全量 ≈ **113 万 token、1 小时**。
   但其中**大部分问题规则就能判**（占位符、空表、无数字、无来源、段落过短）—— 用 LLM 判这些是浪费。
   且用户写完一节就想知道"有没有硬伤"，等 60 秒不合理。

2. **两套结论互相矛盾**
   编写页同时显示：`评审 58`、`就绪度 71 · 接近可交付`、`红线/黄线风险`、`待办 4 件`。
   用户不知道该信哪个、到底能不能交。缺**一个统一判断 + 三条必办**。

## 2. 目标（可验证）

### 目标 A：L0 规则预检（零成本、秒级）

- A1 新增 `services/review_precheck.py::precheck(content)`，返回
  `{level: pass|warn|fail, counts: {critical, high, medium}, items: [{code, severity, message, section, hint, quote}]}`。
- A2 规则集（v1 八条）：

  | code | 判定 | severity |
  |---|---|---|
  | `placeholder` | 正文含 待补充/待明确/TBD/TODO/【待…】 | critical |
  | `empty_table` | 表格行过半为空单元格 | high |
  | `no_numbers` | 全章阿拉伯数字 < 5 个 | high |
  | `no_source` | 无「【来源」且无「（第 N 页」 | high |
  | `short_section` | 某节正文 < 120 字 | medium |
  | `dup_paragraph` | 相邻两段完全相同（>40 字） | high |
  | `broken_tail` | 全文末尾不是句末标点 | critical |
  | `too_short` | 全章 < 400 字 | high |

- A3 接口 `GET /api/blocks/{id}/studio/review/precheck` → A1 结构（不发 LLM）。
- A4 前端：编写页评审卡片上方显示 L0 结果条（「规则预检：2 个硬伤 · 3 个提醒」+ 可展开定位）。

### 目标 B：分层调度（auto / fast / full）

- B1 `POST /api/blocks/{id}/studio/review` 新增 `tier` 参数：
  - `auto`（默认）：接 L0 → **L0 有 critical 则只跑 L1**（先修硬伤）；否则跑 L2（双角色）
  - `fast`：只跑 L1（单角色 = 包拯/合规 + 便宜模型），成本约 1/2
  - `full`：现有双角色（王安石 + 包拯）
- B2 `chapter_reviews` 新增列 `tier`（记录 auto-fast/auto-full/fast/full），接口回传。
- B3 L1 使用**非 reasoning 模型**（避开 v2 踩过的 pro 空输出坑），失败自动换模型。
- B4 返回值包含 `tier`；UI 明确标注「本次=快评（L1）」。

### 目标 C：统一交付判断（一个数字 + 三条必办）

- C1 新增 `services/delivery_verdict.py::build_delivery_verdict(...)`：
  输入 = 规则分、评审结果（可空）、L0 结果、fatal_risks；输出：
  ```
  {score, level: red|yellow|green, label, can_submit: bool,
   top3: [{title, why, action, locatable}],
   explain: {rule, review, precheck, fatal}}
  ```
- C2 判定口径：
  - `red`：有 fatal_risks / L0 有 critical / 评审 unsatisfied
  - `yellow`：无 red 条件，但（无评审 / L0 有 high / 评审 partial）
  - `green`：以上都不成立且评审 satisfied
  - `can_submit`：green 且 need_user 无 export/completion 硬门
- C3 `score` 合成：`L0(0/1)×20 + 规则分×40 + 评审分×40`；无评审时归一到 100
- C4 接口 `GET /api/blocks/{id}/delivery-verdict`
- C5 前端：编写页顶部一个判断条 `交付判断 82 · 接近可交（黄）` + 必办 1/2/3（可点执行）

## 3. 非目标

1. 不改评审 agent 的 prompt 与评分口径。
2. 不做 rubric 达标线库（P2）。
3. 不做写法库/反例库回流。
4. 不做"批量 auto 分层"改造（review_jobs 不变；tier 仅单章生效）。
5. 不做 L0 规则可配置化（v1 写死八条）。

## 4. 约束与依赖

- 技术：L0 纯正则/统计，不调 LLM；L1 单角色；模型选择复用 v2 非 pro 策略。
- 兼容：`POST /studio/review` 参数可选（默认 `tier=auto`），老调用方不改；`chapter_reviews` 加列用 ALTER 容错。
- 回归：`pytest` 基线 1087 不回归；`check.sh` 全过。

## 5. 验收标准（DoD）

- [ ] `pytest tests/test_review_v3.py` 全绿，覆盖：
  - [ ] L0：占位符→critical、空表→high、无数字→high、短节→medium、正常→pass
  - [ ] tier：L0 critical → `fast`；L0 干净 → `full`
  - [ ] verdict：red（fatal）/ yellow（无评审）/ green（满足无雷）；`top3` ≤3；`can_submit` 与 level 一致
- [ ] `pytest ../tests/ -q` 全绿、`node --test tests/frontend/*.test.mjs`、`scripts/check.sh` 全过
- [ ] 手测 `GET /api/blocks/41828/studio/review/precheck`：无 LLM、秒级返回
- [ ] 手测 `POST /studio/review?tier=fast`：单角色，返回 `tier=fast`
- [ ] 手测 `GET /api/blocks/41828/delivery-verdict`：返回 score/level/top3/explain

## 6. 风险与待决

| 风险 | 影响 | 缓解 |
|---|---|---|
| L0 规则误报（正文正当用"XX 年"）| 噪音 | 强词（待补充/待明确/TBD/TODO）+ `【待…】`；提示可忽略 |
| auto 分层让"只跑 L1"被误认为完整评审 | 信任问题 | 返回值与 UI 标注「本次=快评」，并给「跑完整评审」 |
| 统一判断权重（20/40/40）凭经验 | 数字不权威 | `explain` 暴露三项输入；level/can_submit 以规则为主 |
| `no_numbers` 对纯文字章误判 | 噪音 | severity=high 但不进 fatal、不 block 交付 |

## 7. 人审记录

- 评审人：用户（2026-11-05「继续」）
- 结论：按 P1 路线执行
