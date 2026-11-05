# TASKS：素材证据工作台：需求动作、覆盖真相、需求驱动采用

- 对应 spec：`specs/material-evidence-workbench/spec.md`
- 对应 plan：`specs/material-evidence-workbench/plan.md`
- 状态：已完成（T1–T6）
- 规则：一次只勾一项；每项完成跑验证命令；失败不得勾选。

## 任务清单

- [x] T1 新增 `evidence_need_actions` 表 + evidence_needs 忽略/恢复状态 + 测试
- [x] T2 无大纲章节级 fallback + 覆盖率 null + API 测试
- [x] T3 新增需求 action API；采用强制 section+requirement_id；测试
- [x] T4 推荐需求驱动 + 过滤评分标准/模板/目录 + `matched_need_id`；测试
- [x] T5 前端需求卡动作 + 空状态上传/选库/检索 + 采用带 section/requirement + 0 覆盖率显示
- [x] T6 全量回归 + Playwright 冒烟 + 文档记录

## 验证命令

```bash
./.venv/bin/python -m pytest tests/test_evidence_needs.py tests/test_material_library_api.py tests/test_material_match_filter.py -q
./.venv/bin/python -m pytest -q
node --test tests/frontend/*.test.mjs
```

## 验收结果（2026-11-05）

- 后端全量：`1087 passed, 1 deselected`
- 前端：`node --test tests/frontend/*.test.mjs` 通过（fail 0）
- Playwright：素材页显示“章节级（暂无大纲）/ 需求 3 / 覆盖 0 · 缺 3”，需求卡有上传/选库/联网/忽略；无 100% 假覆盖；无 console error。
- 新增/更新测试：
  - `tests/test_evidence_needs.py`（忽略/恢复、写作指令过滤）
  - `tests/test_material_library_api.py`（action API、章节级回退、coverage null、matched_need_id）
  - `tests/test_material_match_filter.py`（评分标准/表格过滤）

## 阻塞与决策记录

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | 忽略是否持久化 | 持久化到 evidence_need_actions，按 block+section+need 幂等 |
| 2026-11-05 | 旧绑定无 requirement_id | 保留可读，不计入覆盖，提示重新绑定 |
| 2026-11-05 | 无大纲 | 章节级需求，明确标注，不伪造节名 |
