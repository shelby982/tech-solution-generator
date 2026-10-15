# ge-solution 全面代码审查报告

> 按《2026-10-15-code-review-plan.md》执行。审查覆盖当前工作区，包括两处未提交前端修改；未修改业务代码和真实数据库。

## 1. 结论与门禁

| 项目 | 结果 |
|---|---|
| P0 | 0 |
| P1 | 6 |
| P2 | 8 |
| P3 | 1 |
| 发布门禁 | 不通过；P1 清零后再复审 |

最关键的问题是：主招标文件和技术规范书上传后没有生成正文切片，而标包识别只读取正文切片。因此当前“标包驱动”主流程在真实上传条件下无法工作。

## 2. P1：发布前修复

### P1-1 主招标文件和技术规范书不生成正文切片

- 证据：`backend/routes/materials.py:415-435` 只为 `source/scoring/evaluation` 创建解析任务，`main_rfp/spec` 直接标记为 `done`。
- 下游：`backend/services/package_analysis.py:232-248` 只从 `material_chunks` 读取标包事实。
- 真实数据：项目 26 两份要求类文件均显示 `done` 且磁盘存在，但全库 `material_chunks=0`。
- 测试缺口：`tests/services/test_package_analysis.py:96-125` 手工插入 chunks，未覆盖“上传→解析→标包分析”。
- 影响：界面持续显示“还没有解析出的正文”，标包识别无法产出。
- 建议：统一要求类文件的解析管线；`done` 只表示正文已经落库。

### P1-2 project_id 与 thread_id 未校验归属

- 证据：`backend/routes/materials.py:646-698` 读取任意 thread checkpoint，却写入 URL 中的项目。
- 同类问题：`backend/routes/materials.py:147-176` 可用任意 threadId 更新运行范围，缺失时静默选择最新运行。
- 影响：项目 B 的解析事实可以写进项目 A，形成跨项目污染。
- 建议：服务层统一执行 `require_run(project_id, thread_id)`，禁止路由自行猜测运行。

### P1-3 前端非 2xx 响应被当作成功

- 证据：`frontend/assets/api.js:25-206` 多数方法只执行 `r.json()`；只有部分 workflow 动作检查 `response.ok`。
- 直接路径：`frontend/assets/mode-outline.js:6220-6245` 在 reset 失败时仍清本地状态并启动新运行。
- 影响：旧数据未清理，界面却进入新运行，造成重复和脏状态。
- 建议：建立统一 `request()`；所有非 2xx 抛出结构化错误；reset 失败必须停止重跑。

### P1-4 外键没有启用，删除遗留孤儿记录

- 证据：`backend/db.py:349-359` 未执行 `PRAGMA foreign_keys=ON`；`backend/services/block_store.py:154-190` 删除材料时没有清理 chunks 和 material_packages。
- 真实数据：6 条孤儿 block revisions、2 条孤儿 reviews，`PRAGMA foreign_keys=0`。
- 建议：清理历史孤儿后开启外键；明确从属表使用 `ON DELETE CASCADE`；删除操作放入同一事务。

### P1-5 测试 checkpointer 仍可能写真实数据库

- 证据：`tests/conftest.py:49-66` 只 patch `db.DB_PATH`；`backend/orchestrator/checkpointer.py:27-46` 在导入时复制路径。
- 反证：`tests/orchestrator/test_checkpointer.py:82-96` 必须额外 patch checkpointer 模块。
- 风险路径：`tests/e2e/test_golden_sample.py:131` 创建默认 WorkflowRunner。
- 建议：运行时读取 `db.DB_PATH` 或显式注入 provider；测试进程增加“禁止真实库路径”守卫。

### P1-6 clear_data.py 错误宣称清空全部数据

- 证据：`scripts/clear_data.py:1-58` 只清早期六张表。
- 遗漏：workflow_runs、reviews、review_points、spec_requirements、split_answers、package 关系、llm_usage、checkpoints、writes。
- 影响：重置序列后仍保留大量历史引用，用户误以为环境干净。
- 建议：完整依赖顺序清理，支持 `--db`、`--dry-run`，结束后逐表输出剩余行数。

## 3. P2：近期结构整改

### P2-1 三模式页面仍全量启动所有模块

- `frontend/three-mode.html:4035-4045` 无条件加载 outline/editor/workbench/workspace。
- `mode-outline.js` 约 7,354 行，`mode-editor.js` 约 2,845 行。
- 隐藏模式仍发请求、绑定全局事件并修改共享状态。
- 建议：按 view 懒加载，模块提供 `mount/unmount`，共享状态集中管理。

### P2-2 旧页面保留约一万行重复实现

- `frontend/project-init.html:895-904`、`frontend/workbench.html:2061-2070` 启动后立即跳转到 `/three-mode`。
- 两个页面仍保留合计 10,542 行完整业务实现。
- 建议：旧 URL 改服务端 307/308，删除页面副本，通过 Git tag 承担回退。

### P2-3 旧上传 API 无产品调用且存在 SSE 抢断

- 当前产品使用 `/projects/{id}/materials`，旧 `/api/upload` 与 `/api/parse/stream` 没有前端或测试调用。
- `backend/routes/upload.py:178-191` 每次连接覆盖唯一 queue；`228-235` 生产者向最新 queue 写入。
- 建议：无外部用户则删除；需要兼容则标记 deprecated，并改为多订阅者或事件回放。

### P2-4 WorkflowRunner 运行时注册表不回收

- `backend/orchestrator/runner.py:172-187` 保存 `_runs` 与 `_workspace_locks`。
- 完成、失败和取消只关闭 emitter，没有删除字典项。
- `has_run()` 因此对历史任务永久为 true，alive 语义失真。
- 建议：终态回收 task/lock；SSE 回放使用有 TTL 和容量上限的独立缓存。

### P2-5 不存在的项目也可以创建运行

- `backend/routes/workflow.py:57-60` 只验证 project_id 为正数。
- `backend/orchestrator/runner.py:191-235` 未查项目就写 workflow_runs。
- 外键关闭时会先返回成功，再由后台任务失败。
- 建议：在创建 run 的同一事务中校验项目存在。

### P2-6 新旧前端路由契约漂移

- `frontend/diff-review.html:107` 把正确的 `/three-mode` 链接覆盖为 `/workbench`。
- `frontend/assets/workbench-modes.mjs:52-65` 仍生成 `/project-init`。
- `tests/frontend/workbench-modes.test.mjs:30-38` 还在保护旧路径。
- 建议：统一到 `/three-mode`，删除无生产调用的死 API 和错误契约测试。

### P2-7 数据库迁移缺少版本和完整事务

- `backend/db.py:243-346` 在应用启动时以 `table_info + ALTER` 完成迁移。
- 没有 schema version、迁移历史、checksum 和可靠回滚。
- 建议：引入编号迁移表；每次迁移记录版本和状态，并提供升级测试。

### P2-8 高频查询缺少组合索引

只读 EXPLAIN 显示以下路径存在全表扫描：

- `material_chunks(material_id, chunk_index)`
- `review_points(project_id, run_thread_id)`
- `spec_requirements(project_id, run_thread_id, order_idx)`
- `material_packages(project_id, material_id)`
- `workflow_runs(project_id, target, package_no, created_at)`

建议先基于真实查询建立组合索引，再用 EXPLAIN 和基准数据验证。

## 4. P3：边界输入

### P3-1 projectId 会从任意字符串中截取数字

- `mode-outline.js:34-45`、`mode-editor.js:52-56`、`workspace.js:15-20` 使用 `match(/\d+/)`。
- `abc26xyz` 会被解释为项目 26。
- 建议：使用 `^\d+$` 严格校验，非法链接显示明确错误。

## 5. 架构评价

当前名义分层是 `routes → services/domain → infra`，实际存在职责穿透：

- 路由直接执行 SQL、读取 checkpoint 和组装模型调用。
- domain repository 反向依赖 `services.block_store`。
- runner 同时负责状态机、DB、SSE、并发、恢复和工作区动作。
- block_store 混合多个领域的 CRUD、迁移和数据修复。

核心大文件：

| 文件 | 行数 |
|---|---:|
| frontend/assets/mode-outline.js | 7,354 |
| backend/orchestrator/nodes.py | 1,538 |
| backend/orchestrator/runner.py | 1,300 |
| backend/services/block_store.py | 1,270 |
| backend/routes/materials.py | 745 |

建议按用例拆出 `RunLifecycleService`、`OutlineService`、`MaterialService`，repository 负责 SQL，路由只负责 DTO 与 HTTP 映射。

## 6. 真实数据审查

| 指标 | 结果 |
|---|---:|
| SQLite integrity_check | ok |
| 数据库大小 | 约 91 MB |
| workflow_runs | 41 |
| aborted runs | 38 |
| 长期停留在 points_review/extracting | 2 |
| llm_usage | 824 |
| 失败 LLM 调用 | 564 |
| 指向已删除项目或运行的 usage | 569 |
| material_chunks | 0 |
| 孤儿 revisions/reviews | 8 |

当前业务表未发现逻辑主键重复。数据库体积主要来自 checkpoints 和 writes，而不是有效业务正文。

## 7. 验证结果

| 检查 | 结果 |
|---|---|
| Python compileall | 通过 |
| 后端 pytest | 795 passed，1 deselected |
| 前端 Node tests | 142 passed |
| git diff --check | 通过 |
| SQLite integrity_check | ok |

测试全绿仍未覆盖上传到标包识别的真实契约，也未覆盖三模式页面的真实浏览器启动和隐藏模块副作用。

## 8. 整改顺序

1. 修复要求类文件解析切片，并补真实上传集成测试。
2. 绑定 project_id/thread_id，统一前端非 2xx 错误处理。
3. 修复外键、级联删除、测试数据库隔离和清理脚本。
4. 清理旧页面、旧上传 API 和错误路由契约。
5. 引入运行时回收、编号迁移和数据库保留策略。
6. 拆分 God modules，并增加三模式浏览器 smoke test。

## 9. 已知但本轮不重复立项

长期记忆中已经确认“零 block 时空集被判定为全部收敛”，用户此前决定暂不修改。本报告将其保留为已接受风险，不计入本次新增缺陷。
