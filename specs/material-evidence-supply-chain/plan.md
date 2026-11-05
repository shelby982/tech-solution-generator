# PLAN：素材证据供应链：从文件库到需求-证据-使用闭环

- 对应 spec：`specs/material-evidence-supply-chain/spec.md`
- 日期：2026-11-05
- 状态：待计划评审

## 1. 方案概览

不重写素材上传/解析底层，也不新造一套素材库；在现有 `materials` / `material_chunks` / `citation_chunks` / `chapter_material_chunks` 之上补一层**证据编排**：

```
Requirement / spec clause / review point
        ↓
EvidenceNeed（本节要什么证据，动态派生）
        ↓
EvidenceCard（可引用片段 + 来源/定位/可信度/使用状态）
        ↓
section bind（chapter_material_chunks，按 section）
        ↓
SectionContext / 正文引用 / 覆盖摘要
```

前端把素材页从「文件+缺口+引用混排」改为「本章证据（主） + 素材库/上传/检索（次）」；后端补齐节级需求、节级绑定不回退、证据卡字段、覆盖与检索降级。

## 2. 关键设计决策

| 决策 | 备选方案 | 选择理由 | 风险 |
|---|---|---|---|
| EvidenceNeed 动态派生，不新增需求表 | 新建 evidence_needs 表 | 现有 plan requirements/spec_requirements/review points 已是事实源，避免双写不一致 | 每次查询多几张表；用服务层聚合 |
| 证据最小单位沿用 chunk，不新建证据表 | 新建 evidence 表 | `material_chunks`/`citation_chunks` 已有原文、页码、状态；只需补展示/绑定 | 不同来源字段不齐，用统一 DTO 归一 |
| 节级无绑定返回空，不回退整章 | 保留整章 fallback | 回退会制造“本节有证据”的错误上下文；宁可明确缺口 | 旧章节可能突然无素材；用“推荐 3 条”兜底 |
| 推荐复用 `material_match` + `semantic_index` | 新写 reranker | 已有章节画像/同义词/语义分；只补“需求驱动 query”和解释 | 现有 match 以章节画像为主，需要补 spec/requirement 字段 |
| 上传状态先做“阶段/数量/失败原因”，不做细粒度进度 | 新增任务队列/进度表 | 当前解析同步/半异步，先让状态可见 | 大文件仍可能等待；后续加 SSE 进度 |
| 联网检索未配置时禁用入口 | 保留假入口 | 诚实降级，避免用户点了没结果 | 需要前端能拿到配置状态 |
| 物料覆盖检查只展示不阻断 | 低覆盖阻断生成 | 证据不足是常态，强阻断会让产品不可用 | 用户可能忽略提示；用待办/红色标记提升可见性 |

## 3. 改动面

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/services/evidence_needs.py` | 新增 | 从 chapter plan requirements + spec clauses + review needs 派生本节证据需求；返回统一 DTO |
| `backend/services/material_context.py` | 修改 | section 无绑定时不再回退整章；返回 `section` 状态/缺口 |
| `backend/services/material_cards.py` | 新增 | 统一 file/web 证据卡 DTO：原文、来源、页码、可信度、使用状态、适用要求 |
| `backend/routes/material_library.py` | 修改 | `/blocks/{id}/materials/needs` 增加 coverage/section；新增 `/blocks/{id}/materials/evidence`；library 返回 card 字段；上传状态/重试 |
| `backend/routes/plan_materials.py` | 修改 | suggestions 支持按 evidence need 排序/解释；最多 3 条/节 |
| `backend/routes/chapter_studio.py` | 修改 | section coverage/citation summary 接入；素材页主视图所需聚合接口 |
| `backend/services/coverage_review.py` | 修改 | 覆盖结果增加 `section`/`missing_need`，供素材页展示 |
| `frontend/assets/chapter-studio.mjs` | 修改 | 素材页签重排：覆盖摘要 → 本节证据卡 → 推荐（≤3）→ 引用/未标来源；素材库/上传折叠 |
| `frontend/assets/chapter-studio.css` | 修改 | 证据卡、覆盖条、上传状态、空状态样式 |
| `frontend/assets/chapter-tree.mjs` / `mode-outline.js` | 修改 | 版本号更新；如有项目级素材入口则保持兼容 |
| `tests/test_evidence_needs.py` | 新增 | 需求派生、无需求/无证据、节级隔离 |
| `tests/test_material_cards.py` | 新增 | 文件/网页卡字段、可信度、状态 |
| `tests/test_material_context.py`（或现有） | 修改 | 无节绑定不回退整章 |
| `tests/test_material_library_api.py` | 新增 | evidence/needs API 契约、搜索降级 |
| `tests/frontend/*.test.mjs` | 修改 | 素材页 HTML/交互的纯函数测试 |

## 4. 验证方案

- 单测：
  - `pytest tests/test_evidence_needs.py tests/test_material_cards.py tests/test_material_library_api.py -q`
  - `pytest tests/test_section_context.py tests/test_coverage_review.py -q`
- 集成：
  - 用测试库造 1 章 3 节，节 1 有绑定、节 2 无绑定：节 2 上下文必须为空素材；节 1 只出现自己的绑定。
  - 上传/解析状态：mock parse_status，library 返回阶段/失败原因/待核对数。
  - 未配置搜索源：search 入口返回 `available=false`。
- 前端：
  - `node --test tests/frontend/*.test.mjs`
  - 本地 Playwright 打开章节计划/素材页，检查：覆盖摘要、证据卡、推荐≤3、无假检索入口。
- 回归：
  - `./.venv/bin/python -m pytest -q`
  - `node --test tests/frontend/*.test.mjs`

## 5. 实施顺序

1. **T1 证据需求服务 + DTO**：`evidence_needs.py`、`material_cards.py`，先用测试锁定字段。
2. **T2 节级隔离**：改 `material_context`，新增回归测试；确保旧的整章 fallback 只允许 section 为空时。
3. **T3 API 聚合**：`materials/needs`、`materials/evidence`、library card 字段、搜索可用状态。
4. **T4 前端主视图**：素材页覆盖摘要 + 证据卡 + 推荐 ≤3；素材库/上传折叠。
5. **T5 上传状态与失败重试**：API/UI 展示阶段、切片、待核对、失败原因。
6. **T6 推荐/检索解释**：需求驱动 query，reason 展示；未配置搜索源禁用入口。
7. **T7 覆盖/引用汇总**：素材页展示规范覆盖与未标来源，跳转定位。
8. **T8 回归与浏览器验收**：全量测试 + Playwright 检查 + 文档记录。

## 6. 回滚方案

- 新增服务/API 全部 additive；前端用 feature flag/入口回退到旧素材页。
- `material_context` 的“不回退整章”改动保留旧函数分支开关 `ALLOW_CHAPTER_FALLBACK=1`，紧急回滚无需改数据结构。
- 不新增不可逆数据迁移；`chapter_material_chunks` 现有绑定不动。
- 素材卡是查询层 DTO，不落新表；删除新增文件即可回退。
