# 技术方案生成助手

把投标技术规范书自动转写成完整方案，5 个领域 agent 协作生成 + 双视角评审。

## 架构

5 层 + LangGraph 编排：

| 层 | 目录 | 职责 |
|---|---|---|
| 路由 | `backend/routes/` | FastAPI 端点，`/api/workflow/*` 7 端点 + `/api/review/{tid}` |
| 编排 | `backend/orchestrator/` | LangGraph StateGraph + 3 个人工闸门 + SSE 事件流 + SQLite checkpointer |
| Agent | `backend/agents/` | 5 个 agent：张衡（解析）/ 沈括（匹配）/ 诸葛亮（撰写）/ 王安石（技术评）/ 包拯（合规评） |
| 领域 | `backend/domain/` | spec / material / proposal / review 聚合 + repository |
| 基础设施 | `backend/infra/` | parser / llm / retrieval / docx 纯 IO |

## 工作流

```
上传规范书 → 张衡解析 → [闸门 1：确认大纲] →
沈括匹配素材 → [闸门 2：确认素材] →
诸葛亮逐 block 生成 → 王安石+包拯并行评审 →
[闸门 3：批准 / 重生 / 作废] → 下载 docx
```

每个闸门用 LangGraph `interrupt_before` 实现，state 持久化在 SQLite，断点可续跑。

## 启动

```bash
bash start.sh                  # 默认模式
LLM_MODE=mock pytest tests/    # 跑测试
```

## 关键路径

- 入口：`backend/main.py`
- 工作流：`backend/orchestrator/runner.py`
- 前端：`frontend/{projects,project-init,workbench,review}.html`
- 黄金样本：`tests/e2e/test_golden_sample.py`（需 `LLM_MODE=real` + `tests/fixtures/sample_*.docx`）
