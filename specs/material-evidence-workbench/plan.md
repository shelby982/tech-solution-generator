# PLAN：素材证据工作台：需求动作、覆盖真相、需求驱动采用

- 对应 spec：`specs/material-evidence-workbench/spec.md`
- 日期：2026-11-05
- 状态：已实施（T1–T6，验证见 tasks.md）

## 1. 方案概览

在现有“证据供应链”上做最后一公里，不重写素材库：

```
evidence need（可忽略/可操作）
  → 上传 / 选库 / 检索 / 忽略（动作 + 状态）
  → 采用（section + requirement_id）
  → 覆盖真相（0 需求不显示 100%）
  → 推荐（matched_need_id + 过滤评分表/模板）
```

无大纲时回退为章节级需求；空状态直接给上传/选库/检索主 CTA。

## 2. 关键设计决策

| 决策 | 备选 | 理由 | 风险 |
|---|---|---|---|
| 新增轻量 `evidence_need_actions` 表 | 复用 plan requirements | 忽略/恢复需要持久化，不能污染计划 | 多一张小表；主键幂等 |
| 需求动作由前端调用统一 action API | 每个动作单独接口 | 行为一致、便于审计/测试 | 需要定义 action 枚举 |
| 采用必须带 requirement_id | 兼容旧 section='' | 否则覆盖永远对不上 | 旧素材需要重新绑定；显示“历史绑定未关联要求” |
| 无大纲回退章节级 need | 不显示 | 避免 0 需求/推荐漂浮 | 章节级需求会重复到每节；明确标注“章节级”，有大纲后切换 |
| 推荐先按 need 过滤再排序 | 继续章节画像 | 解决“评分标准当证据” | 召回变少；保留“从素材库选” |
| 空状态主 CTA 用现有上传接口 | 新建上传页 | 复用 `/blocks/{id}/studio/materials` | 上传后需等待解析，再选片段 |
| 覆盖率分母 0 返回 null | 返回 1.0 | 不允许假完成 | 前端需要处理 null |

## 3. 改动面

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/db.py` | 修改 | 新表 `evidence_need_actions`；幂等迁移 |
| `backend/services/evidence_needs.py` | 修改 | 章节级 fallback；返回动作/忽略状态；`coverage=None` 语义 |
| `backend/routes/material_library.py` | 修改 | 新 action API；evidence/board 返回 `chapter_level`/`actions`/`matched_need_id`；覆盖率 null |
| `backend/services/material_match.py` | 修改 | 过滤评分标准/模板/目录；支持 need query；返回 matched need |
| `backend/routes/plan_materials.py` | 修改 | suggestions 传递 need_id；推荐结果按 need 归属 |
| `frontend/assets/chapter-studio.mjs` | 修改 | 需求卡动作、空状态上传/选库/检索、推荐卡采用带 section+requirement、0 覆盖率显示 |
| `frontend/assets/chapter-studio.css` | 修改 | 动作行/状态/空状态样式 |
| `tests/test_evidence_needs.py` | 修改 | 章节 fallback、忽略状态、动作 |
| `tests/test_material_library_api.py` | 修改 | 采用绑定 requirement、覆盖率 null、action API、推荐字段 |
| `tests/test_material_match_filter.py` | 修改 | 评分标准/模板过滤 |
| `tests/frontend/*.test.mjs` | 修改 | 如有纯函数测试则补；无则手工 Playwright |

## 4. 验证方案

- 后端：
  - `pytest tests/test_evidence_needs.py tests/test_material_library_api.py tests/test_material_match_filter.py -q`
  - `pytest -q`
- 前端：
  - `node --test tests/frontend/*.test.mjs`
  - Playwright：素材页空状态有上传/选库/检索；需求卡动作；0 需求不显示 100%；采用后需求变已覆盖。
- 回归：素材库弹窗、上传、引用、计划建议、导出不变。

## 5. 实施顺序

1. T1：`evidence_need_actions` 表 + evidence_needs 动作/忽略状态 + 测试。
2. T2：无大纲章节级 fallback + 覆盖率 null + API 测试。
3. T3：action API + bind 强制 requirement_id + 测试。
4. T4：推荐需求驱动 + 过滤评分表/模板/目录 + matched_need_id + 测试。
5. T5：前端需求卡动作 + 空状态主 CTA + 采用带 section/requirement + 0 覆盖率显示。
6. T6：全量测试 + Playwright 冒烟 + 文档记录。

## 6. 回滚方案

- 新表/接口全部 additive；前端功能可用 feature flag `evidence_workbench=0` 回退到只读证据页。
- `requirement_id` 绑定保持可空，旧数据不迁移、不删除；回滚不影响旧绑定。
- 推荐过滤是纯函数，回滚只需恢复旧 `recommend` 分支。
