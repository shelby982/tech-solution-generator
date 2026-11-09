# 数据治理待办：13 张「有写路径、实库 0 行」表（2026-11-09）

> 口径：本地开发库 `backend/data/ge_solution.db` 只读快照（2026-11-09 09:00）。
> 单库样本，不等于线上从未使用；本清单用于安排功能验收与数据治理，不直接删表。

## 1. 0 行表清单与写入路径

| 表 | 写入/读取代码 | 关联功能 | 0 行可能原因 |
|---|---|---|---|
| `project_snapshots` | `services/block_store.py:create_snapshot`（原 lock/导出快照）；`projects.py` reset 时 DELETE | 项目/标包快照 | `lock` 入口从未被 UI 调用；导出走 job 快照而非此表 |
| `chapter_materials` | `routes/chapter_studio.py` 绑素材；触发器维护 | 章节级素材关联 | 新素材库改走 `chapter_material_chunks`/证据板，旧表未清 |
| `chapter_material_chunks` | `db.py` ALTER 补列；chapter 级绑定 | 章节级素材片段 | 同上 |
| `citations` | `services/citations.py` save/list；`material_library.py` | 外部引用/证据 | 引用功能未在生产路径完整走通 |
| `citation_links` | `citations.py` link/unlink（端点已删除） | 引用→章节关联 | link 端点无前端入口 |
| `citation_searches` | `citations.py` remember_search/search_history | 引用检索历史 | 前端未接检索历史 |
| `need_snippets` | `services/evidence_needs.py` | 需求片段 | 证据工作台未产生该形态数据 |
| `writing_model_settings` | `routes/writing_settings.py` PUT | 编写模型分工 | 页面有入口但实际未保存（需前端排查） |
| `material_images` | `material_library.py` 图片读取/上传 | 素材图片 | 未上传过带图素材 |
| `diagram_settings` | `routes/diagram_assets.py` GET/PUT | 图表设置 | 未保存过图表偏好 |
| `evidence_need_actions` | `material_library.py:/materials/needs/action` | 证据需求动作 | 前端有按钮但未实际点击落库 |
| `leads` | `routes/leads.py` POST（landing 表单） | 官网线索 | 落地页未投放或未提交 |
| `project_writing_facts` | `db.py` 建表；`chapter_studio.py` facts GET/PUT | 写作事实 | 未走事实编辑入口 |

## 2. 相关数据质量问题（同一快照）

- `PRAGMA user_version = 0`：没有 schema 版本表，迁移不可追踪。
- `PRAGMA foreign_keys = 0`：DDL 的 `REFERENCES` 只是注释性约束。
- `workflow_runs.package_id IS NULL`：41 / 50，且这些行 `target/package_no` 也为空，启动期回填无法修复。

## 3. 建议治理步骤（不删表）

1. **按功能验收**：对每张表找到对应 UI 入口，用真实项目走一遍，确认"点击后是否有行"。
   - 优先：`writing_model_settings`、`evidence_need_actions`、`project_writing_facts`、`leads`。
2. **生产只读核查**：在真实环境跑同样的 `count(*)`（只读连接），区分"开发库没数据"与"功能从未使用"。
3. **决定保留/下线**：连续两个版本 0 行且无产品计划的表，再走删除迁移；删除前先移除代码写路径。
4. **补 schema 版本**：新增 `schema_migrations` 表，`user_version` 随迁移递增；再评估开启外键（需先清理 41 条孤儿 run）。
5. **加可观测性**：关键功能入口记录一次 `feature_used` 事件（或 usage 表），避免"有代码、无数据"长期不可见。

## 4. 不在本轮范围

- 不删除任何表、不修改生产数据。
- 不启用外键（会立刻违反 41 条历史 run）。
