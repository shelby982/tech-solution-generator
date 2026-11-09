# 待处理项核实报告（2026-11-09）

> 目的：在执行完两轮清理后，逐项核实"待处理清单"是否仍然成立，避免按过期结论继续动手。
> 快照：HEAD `299073c` + 工作区未提交优化改动；数据库为本地开发库 `backend/data/ge_solution.db`（09:00 快照，`?mode=ro` 只读）。
> 基线：后端 1155 passed / 前端 161 passed；端点 207 个；页面路由 9 条。
> 说明：本报告是 2026-11-09 上午的只读核实快照。当天下午已按决策执行了其中
> 第 1/2/3/5/6 项（domain 三仓、rerank、doc_summary、provider 前端、死 CSS），
> 执行细节见 [2026-11-09-optimization-execution-log.md](2026-11-09-optimization-execution-log.md) §10；
> 下面表格保留核实时的原始结论。

---

## 0. 结论速览

| # | 待处理项 | 核实结论 | 建议 |
|---|---|---|---|
| 1 | domain 三套 repository | ~~仍成立~~ → **已删除（315 + 672 行）** | 已完成 |
| 2 | rerank 旧匹配 | ~~部分缓解~~ → **已删除**（含 `rerank.py`、`match()`、prompts、测试） | 已完成 |
| 3 | doc_summary 停用链路 | ~~仍成立~~ → **已删除**（函数/形参/state/闸门快照/测试） | 已完成 |
| 4 | 双评审表 | **仍成立**：同一次评审双写，实库 44 / 20 行 | 需数据模型决策，风险最高 |
| 5 | provider 默认值 | **前端已收敛为一份**（`assets/model-config.mjs`）；后端默认值仍不同，待确认是否对齐 | 部分完成 |
| 6 | 死 CSS | ~~仍成立~~ → **已清理**：外部 762 块 + 内联 107 块；行数 5,244 → 2,848 | 已完成；待人工视觉 smoke |
| 7 | `api.js` 零调用方法 | **仍成立**：15 / 61 个方法零调用 | 与后端契约一起决策 |
| 8 | 仅测试调用的端点 | **部分成立**：3 个测试专用；新发现的 `diagram-design/types` 已在本轮删除 | 其余 3 个需产品确认 |
| 9 | `_drop_empty_sections` | **仍成立**：只有定义，无调用 | 接线并补测试，或删除 |
| 10 | 13 张 0 行表 | **仍成立**：实库 13 张 0 行 | 功能验收/清理，非代码删除 |

---

## 1. 已完成项复核（确认没有被回退）

| 项 | 当前事实 |
|---|---|
| 两个旧整页 | `frontend/workbench.html`、`frontend/project-init.html` 已不存在；tag `pre-legacy-pages-removal` 可回退 |
| `/diff-review` | 页面与路由均已删除；`review.html` 保留「与上次评审对比 / 章节版本」 |
| `bench.html` | 已移到 `tools/bench/index.html`；`frontend/` 仅 5 个产品页 |
| 一次性脚本 | 6 个已在 `scripts/oneoff/`，逐个 `--help` 可运行 |
| `MATCH_MODE` | 已支持 `MATCH_MODE=rerank` 回滚（实测导入正常），默认 assign |
| 端点/模块 | 207 端点 / 21 路由模块；`upload.py`、`changelog.py`、`validators.py`、`context-panel.js` 均已删除 |
| 测试 | 后端 1155 passed / 前端 161 passed；`git diff --check` 通过 |

---

## 2. 逐项核实详情

### 2.1 domain 三套 repository：仍为零引用死层

- `backend/domain/spec/repository.py` 120 行、`proposal/repository.py` 84 行、`material/repository.py` 111 行，合计 **315 行**。
- `grep -rn "SpecRepository|ProposalRepository|MaterialRepository" backend scripts`（排除 `backend/domain/`）= **0**；生产无实例化。
- 唯一消费者：`tests/domain/test_spec_repository.py` 173 行 + `test_proposal_repository.py` 274 行 + `test_material_repository.py` 225 行 = **672 行测试**。
- 三个仓仍反向 `from services.block_store import ...`（分层倒挂）；`domain/review/repository.py` 是唯一在用的仓。
- **结论**：与上轮一致。若不继续 Phase 7，删除可回收 987 行；若继续，需要先接线并删 block_store 重复 SQL。

### 2.2 rerank 旧匹配：开关已真，代码未删

- `backend/orchestrator/nodes.py:563-566`：
  ```python
  MATCH_MODE = (os.environ.get("MATCH_MODE", "assign") or "assign").strip().lower()
  if MATCH_MODE not in ("assign", "rerank"):
      MATCH_MODE = "assign"
  ```
  已从写死常量改为环境变量；`MATCH_MODE=rerank` 实测可导入。
- 旧路径仍在：`agents/shen_kuo.py:52 match()`（`shen_kuo.py` 共 256 行）、`infra/retrieval/rerank.py:49 llm_rerank()`（126 行）、`nodes.py` rerank 分支约 40 行。
- 测试：`tests/agents/test_shen_kuo.py` 332 行 + `tests/infra/test_retrieval.py` 110 行；`tests/orchestrator/test_nodes.py:226-231` 用 monkeypatch 切回 rerank。
- **结论**：不再是"不可达假开关"，但默认路径不执行。是否彻底删除是产品回滚能力决策；保留则建议在 README 写回滚 SOP。

### 2.3 doc_summary 停用链路：仍完整保留

- `backend/agents/zhang_heng.py:380` 直接 `doc_summary = ""`；注释（`:370-379`）写明"恢复需接回 `dispatch_doc_summary` 并给足 max_tokens 或换非推理模型"。
- 链路仍在：`infra/llm/dispatcher.py:109-190`（`_generate_doc_summary` 约 86 行）、`:427-460`（`dispatch_doc_summary` 约 36 行）、`__init__.py` 仍转出；`agents/prompts.py:354/387-388` 仍有 `doc_summary` 形参与拼接；`state.py`/`nodes.py` 字段未清。
- 生产调用：0（只有 `__init__.py` 转出与 `tests/agents/test_zhang_heng.py:73-88` 的"已停用"断言/ spy）。
- **结论**：已停止 7 周，属"停用但保留"。建议恢复或一次性删干净（涉及 10+ 文件，需一次收敛）。

### 2.4 双评审表：仍双写，且两表都有数据

| 表 | 实库行数 | 写入/读取 |
|---|---:|---|
| `reviews` | 44 | 写：`services/chapter_review.py:318`；读：`domain/review/repository.py:33/68/79` |
| `chapter_reviews` | 20 | 写：`services/chapter_review.py:140/215`；读：`chapter_review.py:237` |

- 同一次评审在 `chapter_review.py` 内先写 `chapter_reviews`，随后又写 `reviews`；字段集不同（`chapter_reviews` 有 tech/comp score/tier/content_version，`reviews` 有 agent/thread_id）。
- 下游取数分叉：`delivery_verdict.py` 与 `practice_recycle.py` 从 `review.get("tier")` 取，只对其中一代有效。
- **结论**：与上轮一致，是真双写而非迁移残留；需要数据模型决策，不能机械删表。

### 2.5 provider 默认值：2 份前端副本 + 1 份后端权威，仍分叉

| 来源 | deepseek | claude | kimi | minimax |
|---|---|---|---|---|
| 后端 `config_store.py:77-86`（权威） | `deepseek-chat` | `claude-3-5-sonnet-20241022` | `moonshot-v1-32k` | `MiniMax-M2.7` |
| `frontend/assets/mode-outline.js:594-598` | **`deepseek-v4-pro`** | `claude-sonnet-4-6` | **`moonshot-v1-8k`** | **`abab6.5s-chat`** |
| `frontend/projects.html:1023-1027` | `deepseek-chat` | `claude-sonnet-4-6` | **`moonshot-v1-8k`** | **`abab6.5s-chat`** |

- 删除旧页后，硬编码副本从 4 份降到 2 份，但**两个前端页面之间 deepseek 默认值仍不一致**，且 3/4 个 provider 与后端不一致。
- `GET /api/config/providers` 已随旧配置端点删除，当前没有"权威 provider 表"下发接口。
- **结论**：仍待处理。建议前端从 `/api/configs` 或新增只读接口取默认值，或至少把两份副本收敛为一份共享模块。

### 2.6 死 CSS：重新扫描为 827 块 / 3,980 行（口径与上轮不同）

用当前工作区重新扫描（语料 = 现存 HTML + JS/MJS + 前端测试；规则块级判定）：

| 位置 | 死块 | 死行 |
|---|---:|---:|
| `frontend/assets/styles.css` | 254 | 1,959 |
| `frontend/assets/chapter-studio.css` | 374 | 783 |
| `frontend/assets/review.css` | 50 | 365 |
| `frontend/assets/response-outline.css` | 31 | 72 |
| `frontend/assets/workspace.css` | 14 | 29 |
| `frontend/assets/ui.css` | 1 | 2 |
| `three-mode.html` 内联 | 90 | 680 |
| `projects.html` 内联 | 13 | 90 |
| **合计** | **827** | **3,980** |

- 比上轮 753 块 / 2,446 行更多：一是删除旧页后，原先被旧页引用的样式变成死规则；二是本轮扫描按规则块跨行计，口径更宽。结论不变：死 CSS 规模仍大。
- 前置仍是 `three-mode.html` 内联 CSS（约 3,300 行）未抽出；无自动化视觉回归。
- **结论**：可清理，但必须按文件分批 + 5 页截图对比，不能机械全删。

### 2.7 `api.js` 零调用方法：15 / 61

精确按 `api.<group>.<method>(` 扫描（排除 `api.js` 自身）：

| 方法 | 行 | 对应后端现状 |
|---|---|---|
| `projects.createPackage` | 44 | 端点仍在（`packages.py`） |
| `projects.patch` | 95 | 端点仍在 |
| `projects.lock` | 101 | 端点仍在（实库 `project_snapshots` 0 行） |
| `blocks.clear` | 140 | 端点仍在 |
| `blocks.update` | 142 | 端点仍在 |
| `blocks.ai` | 179 | 端点仍在 |
| `blocks.updateRequirement` | 185 | 端点仍在 |
| `revisions.list` | 194 | 端点仍在 |
| `revisions.restore` | 196 | 端点仍在 |
| `revisions.listByProject` | 200 | 端点仍在 |
| `export.diff` | 205 | 端点仍在 |
| `export.applyDiff` | 207 | 端点仍在 |
| `export.word` | 213 | 端点仍在（页面用 `<a href>` 直链导出） |
| `workflow.getSplitAnswers` | 256 | 端点仍在 |
| `review.get` | 410 | 端点仍在（只有测试调用） |

- 比上轮 12 个多：`materials.outline`/`materials.mapSources` 已随端点删除，但扫描补出了 `blocks.clear`、`revisions.listByProject`、`export.*` 等此前漏计项。
- **结论**：仍待与后端契约一起决策，不建议只删前端。

### 2.8 仅测试调用 / 零调用端点

| 端点 | 前端 | 测试 | 判定 |
|---|---:|---:|---|
| `POST /blocks/{id}/studio/coverage-review` | 0 | 1 | 仅测试调用（保留需确认） |
| `POST /blocks/{id}/studio/draft` | 0 | 2 | 仅测试调用 |
| `POST /blocks/{id}/studio/deployment-diagrams` | 0 | 1 | 仅测试调用 |
| ~~`GET /blocks/{id}/studio/diagram-design/types`~~ | 0 | 0 | **已删除（2026-11-09）** |
| `POST /chapter-writes/{id}/cancel`、`retry` | 1 / 2 | 0 / 1 | **不是死端点**：前端 `chapter-studio.mjs:4785/4790` 在用 |
| `GET /blocks/{id}/materials/evidence*` | 1 | 7 | 活 |

- `diagram_types` 端点依赖 `recommend_type`/`SKILL_TYPES`；删端点前需确认这些函数没有别的调用方。
- **结论**：`diagram-design/types` 可优先删除；其余 3 个测试专用端点需要产品确认"是否补前端入口"。

### 2.9 `_drop_empty_sections`：新提交即死

- `backend/services/placeholder_purge.py:198-220` 定义 23 行；全仓仅定义处 1 命中，`purge()` 未调用。
- 若接线，会在每次写正文时删除"只有标题、没有正文"的小节——属于内容生成行为变化，需单独验证（可能影响目录与正文一致性）。
- **结论**：仍待处理；建议要么补调用 + 单测，要么删除，不要长期悬空。

### 2.10 13 张 0 行表：仍为 13 张

实库 51 张表（含 LangGraph `checkpoints`/`writes`），其中 **13 张 0 行**：

```
project_snapshots, chapter_materials, citations, citation_links, citation_searches,
need_snippets, writing_model_settings, chapter_material_chunks, material_images,
diagram_settings, evidence_need_actions, leads, project_writing_facts
```

- 每张都有代码写入路径（3-14 个文件引用），说明"已接线但从未跑通"，不是 schema 错误。
- 实库另存：`user_version = 0`（无 schema 版本）、`foreign_keys = 0`、`workflow_runs.package_id IS NULL` 41/50。
- **结论**：属功能验收与数据治理项，不是单次代码删除能解决的。

---

## 3. 本轮新发现

1. ~~**`GET /blocks/{id}/studio/diagram-design/types` 零前端、零测试调用**~~：已删除，端点总数 207 → 206。
2. **死 CSS 因删除旧页而增加**：从 753 块增至 827 块（口径差异 + 旧页样式失效），清理优先级不变但工作量更大。
3. **`api.js` 零调用方法由 12 增至 15**：补齐了 `blocks.clear`、`revisions.listByProject`、`export.*` 等上轮漏计项。

---

## 4. 建议处理顺序

1. ~~**无争议、可立即做**：删除 `diagram-design/types` 端点~~：已完成；`recommend_type`/`SKILL_TYPES` 在 `create_diagram` 等处仍有使用，未产生孤儿。
2. ~~**需一次决策**：domain 三仓、rerank、doc_summary~~：均已按"删除"执行。
3. **需产品/数据决策**：双评审表、provider 单一来源、测试专用端点、`api.js` 未用方法。
4. ~~**需视觉回归**：死 CSS 分批清理~~：已清理，视觉 smoke 待做。
5. **需功能验收**：13 张 0 行表、`_drop_empty_sections` 接线。

---

## 5. 复核命令（只读）

```bash
cd /Users/jianghe/projects/ge-solution

# domain 死仓
grep -rn --include='*.py' -E "SpecRepository|ProposalRepository|MaterialRepository" backend scripts | grep -v '^backend/domain/'
wc -l backend/domain/{spec,proposal,material}/repository.py tests/domain/test_{spec,proposal,material}_repository.py

# rerank / doc_summary
sed -n '558,568p' backend/orchestrator/nodes.py
grep -rn --include='*.py' "dispatch_doc_summary" backend tests | grep -v '__pycache__'

# 双评审表（只读）
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('file:backend/data/ge_solution.db?mode=ro',uri=True)
for t in ('reviews','chapter_reviews'): print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
PY

# 零调用端点
grep -rn "diagram-design/types" frontend tests

# 13 张 0 行表
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('file:backend/data/ge_solution.db?mode=ro',uri=True)
tabs=[r[0] for r in c.execute("select name from sqlite_master where type='table'")]
print([t for t in tabs if c.execute(f'select count(*) from "{t}"').fetchone()[0]==0])
PY
```
