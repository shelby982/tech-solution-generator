# TASKS：写作三件事：就绪度可见、要求按节映射、写后证据结果卡

- 对应 spec：`specs/writing-ready-loop/spec.md`
- 对应 plan：`specs/writing-ready-loop/plan.md`
- 规则：一次只勾一项；每项完成必须跑验证命令；失败不得勾选。

## 任务清单

- [x] T1 读 spec + plan，列出待确认问题（先问再写）
- [x] T2 写失败测试（复现问题 / 定义新行为）
- [x] T3 实现最小改动让测试通过
- [x] T4 补齐边界与错误路径测试
- [ ] T5 跑 `node verify/verify-gate.mjs`，直到全绿
- [ ] T6 生成评审包 `node review/review-diff.mjs`，用评审子 Agent 过一遍
- [ ] T7 更新文档/AGENTS.md（如涉及约定变更）

## 验收结果（2026-11-05）

- 后端全量：`1113 passed, 1 deselected`
- 前端：`node --test tests/frontend/*.test.mjs` 通过（fail 0）
- Playwright：章头显示 readiness（当前章节为 `无大纲`）；无 console error。
- 新增测试：
  - `tests/test_writing_readiness.py`（状态/API）
  - `tests/test_section_results.py`（结果持久化）
  - `tests/test_section_outline.py`（requirement_ids 保留）
- 结果卡保存于 `section_write_results`，`GET /blocks/{id}/section-results` 可刷新读取。

## 阻塞与决策记录

| 时间 | 问题 | 结论 |
|---|---|---|
|  |  |  |
