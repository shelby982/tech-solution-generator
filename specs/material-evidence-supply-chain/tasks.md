# TASKS：素材证据供应链：从文件库到需求-证据-使用闭环

- 对应 spec：`specs/material-evidence-supply-chain/spec.md`
- 对应 plan：`specs/material-evidence-supply-chain/plan.md`
- 状态：已完成（T1–T9）
- 规则：一次只勾一项；每项完成必须跑验证命令；失败不得勾选。

## 任务清单

- [x] T1 新增 `services/evidence_needs.py`：从本章 requirements / spec clauses / review needs 派生本节证据需求，新增 DTO 测试
- [x] T2 新增 `services/material_cards.py`：统一 file/web 证据卡字段，新增测试
- [x] T3 修改 `material_context.py`：节级无绑定不回退整章；补齐回归测试
- [x] T4 扩展 `materials/needs` 与新增 `materials/evidence` API，增加 coverage / section / card 字段
- [x] T5 上传状态与失败重试：library 返回阶段、切片、待核对、失败原因；前端可重试
- [x] T6 推荐与检索降级：suggestions 按证据需求/解释排序，≤3；未配置搜索源返回 `available=false`
- [x] T7 前端素材页重排：覆盖摘要 → 本节证据卡 → 推荐 ≤3 → 引用/未标来源；素材库/上传折叠
- [x] T8 覆盖/引用汇总接入素材页，缺失项可定位
- [x] T9 全量回归 + Playwright 浏览器验收 + 文档记录

## 每项验证命令

```bash
# 后端（按任务取子集）
./.venv/bin/python -m pytest tests/test_evidence_needs.py tests/test_material_cards.py tests/test_material_context.py -q

# 全量
./.venv/bin/python -m pytest -q

# 前端
node --test tests/frontend/*.test.mjs
```

## 验收结果（2026-11-05）

- 后端全量：`1078 passed, 1 deselected`
- 前端：`node --test tests/frontend/*.test.mjs` 通过（fail 0）
- Playwright 素材页冒烟：无 console error；显示“本章证据 / 覆盖 / 未配置联网检索”主视图
- 新增测试：
  - `tests/test_evidence_needs.py`
  - `tests/test_material_cards.py`
  - `tests/test_material_context.py`
  - `tests/test_material_library_api.py`
  - `tests/test_material_match_filter.py`

## 阻塞与决策记录

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | 素材页主视图是否保留旧弹窗 | 保留旧弹窗一版，主视图先重排，后续再下线 |
| 2026-11-05 | 是否新建 evidence 表 | 不新建，动态派生 + chunk 复用 |
| 2026-11-05 | 无小节绑定时行为 | 返回空证据，明确缺口；不回退整章 |
