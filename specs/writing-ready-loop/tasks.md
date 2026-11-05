# TASKS：写作三件事：就绪度可见、要求按节映射、写后证据结果卡

- 对应 spec：`specs/writing-ready-loop/spec.md`
- 对应 plan：`specs/writing-ready-loop/plan.md`
- 状态：已完成
- 规则：一次只勾一项；每项完成跑验证命令；失败不得勾选。

## 任务清单

- [x] T1 章头/大纲 readiness：API 接入、章头 chip、节行状态、点击定位素材
- [x] T2 大纲 requirement_ids：prompt 带 requirement ID、normalize 保留、过滤/兜底分配、计划面板显示
- [x] T3 写后结果持久化：`section_write_results` 表、section stream 保存、`GET /section-results`
- [x] T4 写后结果卡：侧栏每节显示 context/cited/coverage/generic，刷新后保留
- [x] T5 全量测试：后端 `1113 passed`；前端 `node --test` fail 0
- [x] T6 浏览器冒烟：章头 readiness 可见；无 console error

## 验收结果（2026-11-05）

- 后端全量：`1113 passed, 1 deselected`
- 前端：`node --test tests/frontend/*.test.mjs` 通过（fail 0）
- Playwright：章头显示 readiness（当前章节为 `无大纲`）；无 console error。
- 新增/更新测试：
  - `tests/test_writing_readiness.py`（状态/API）
  - `tests/test_section_results.py`（结果持久化）
  - `tests/test_section_outline.py`（requirement_ids 保留）

## 已知边界

- 旧大纲/旧计划没有 requirement_ids 时仍回退章节级；需要重新生成大纲或补映射。
- 结果卡只在下一次节级/章级写作后出现；历史正文没有写后结果。
- 上传→解析→候选闭环不在本 spec 范围。

## 阻塞与决策记录

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | 缺少 requirement_ids 的旧计划 | 回退章节级，明确标记，不伪造节级 |
| 2026-11-05 | 结果卡存储 | 每节保留最近一次，additive 表 |
| 2026-11-05 | context/cited 区分 | 结果卡分别展示上下文证据与正文引用 |
