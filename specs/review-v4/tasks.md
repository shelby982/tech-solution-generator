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
