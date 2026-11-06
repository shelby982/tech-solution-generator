# TASKS：评审 v4：达标线 / 证据链 / 写法回流

- 对应 spec：`specs/review-v4/spec.md`
- 规则：一次只勾一项；每项完成必须跑验证命令。

## 任务清单

- [x] T1 `services/chapter_rubric.py`：章类型识别（7 类）+ 6 套清单 + `check_rubric()`
- [x] T2 `tests/test_review_v4.py`：类型识别 / 现状类缺数字不达标 / 方案类缺手段不达标
- [x] T3 `services/review_evidence.py`：`attach_evidence()`（原文定位 + 口径 2-gram 匹配）
- [x] T4 `tests/test_review_v4.py`：可定位 / 不可定位 / 口径匹配
- [x] T5 `services/practice_recycle.py`：候选生成 + `apply_candidate()` 写入 section_practices
- [x] T6 `tests/test_review_v4.py`：候选生成 + 采用写入（内存库）
- [x] T7 路由：issues 带 evidence；`GET rubric`；`GET/POST practice-candidates`
- [x] T8 前端：达标线卡（x/y + 逐条 ✓✗ + 建议）、issue 依据、写法候选「采用为写法」
- [x] T9 全量回归：1110 passed、`check.sh` 全过
- [x] T10 手测：rubric 4/4；issues 带证据；候选接口

## 实测记录

```
GET /api/blocks/41828/studio/review/rubric
  → status 类 · 达标 4/4 · 加权 100%（量化事实：261 处数字）
GET /api/blocks/41828/studio/review/issues
  → 计数 {open:6, fixed:0, verified:18, total:24} · 4 条带可定位证据
GET /api/blocks/41828/studio/practice-candidates
  → {"candidates": []}（本次快评 strengths 为空，符合预期）
```

## 阻塞与决策

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | rubric 是否用 LLM 判定 | 不用；规则判定可解释且零成本，未达标只给"建议补" |
| 2026-11-05 | 写法候选章节归属 | 无指定小节时写 `_chapter_`（整章写法），避免错挂小节 |

## 全量跑批验证（2026-11-06，project 29 / thread 24840930…）

**结果：14/14 章覆盖（100%），8/8 新增评审成功，耗时 3 分 52 秒（并发 2）**

```
待评 8 章：s1.2.1.2 · s1.2.1.4 · s1.2.1.5 · s1.2.1.6 · s1.2.1.7 · s1.3.1 · s1.3.2 · s1.4.1.2
✓ s1.2.1.4  项目重难点        tier=fast  60 分 · 18 条 · 24s
✓ s1.2.1.2  建设范围          tier=full  42 分 · 22 条 · 118s
✓ s1.2.1.6  建设难点和痛点    tier=fast  55 分 · 20 条 · 21s
✓ s1.2.1.5  业务需求及特点    tier=fast  65 分 · 12 条 · 122s
✓ s1.2.1.7  解决措施          tier=fast  55 分 · 32 条 · 26s
✓ s1.3.1    顶层设计遵循      tier=full  62 分 · 20 条 · 58s
✓ s1.3.2    方案总体架构      tier=full  56 分 · 22 条 · 60s
✓ s1.4.1.2  项目管理方法      tier=full  58 分 · 29 条 · 27s
```

**聚合结论**
- 均分 47.9（2~65）· 15 章全部 `unsatisfied`
- issue 状态机：178 条（open 160 / verified 18 = 10%）· critical 31 / high 60 / medium 63 / low 24
- 否决风险 20 条 → 项目 `risk_level = red`
- 分层生效：8 章里 4 章走 fast（L0 有硬伤）→ 单章 21~26s，比 full 快 3~5 倍

**发现并修复的问题**
1. **0 字章节被评 2 分**（s1.1.3「响应数值横向排名」、s1「技术条款差异性」正文为空）
   → 新增 `should_skip_llm()`：正文 < 200 字不调 LLM，直接落 L0 结论（`tier=skip`）
2. **脚本跑批不记账**：独立脚本未 `usage_store.install()` → llm_usage 无记录
   （按历史均值折算本次 ≈ 6.9 万 token；HTTP 路径记账正常）
