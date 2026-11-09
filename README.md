# 技术方案生成助手

把投标技术规范书自动转写成完整方案：5 个核心领域 agent + 章节团队协作生成，配合双视角评审与证据链校验。

## 架构

5 层 + LangGraph 编排：

| 层 | 目录 | 职责 |
|---|---|---|
| 路由 | `backend/routes/` | FastAPI 端点：21 个路由模块 / 197 个端点（`/api/workflow/*` 22 个） |
| 编排 | `backend/orchestrator/` | LangGraph StateGraph + 3 个人工闸门 + SSE 事件流 + SQLite checkpointer |
| Agent | `backend/agents/` | 5 个核心 agent（张衡解析 / 沈括匹配 / 诸葛亮撰写 / 王安石技术评 / 包拯合规评）+ 章节团队（`chapter_team.py`）与评分点子模块（`subpoints.py`） |
| 领域 | `backend/domain/` | spec / material / proposal / review 聚合与纯函数（目前仅 review 的 repository 在服务层使用） |
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
pip install -r requirements-dev.txt   # 开发/测试依赖（含 pytest）
bash start.sh                         # 默认模式
LLM_MODE=mock pytest tests/           # 跑测试
```

### 设计版图表（Diagram Design）

「插入内容 → 图片 → AI 生成图表（设计版）」需要 Chromium 渲染 HTML→PNG：

```bash
pip install -r requirements.txt
playwright install chromium
```

未安装时设计版会明确失败，不影响快速结构图与素材图。详见
`docs/design/生图-DiagramDesign技能接入-方案.md`。

## 关键路径

- 入口：`backend/main.py`
- 工作流：`backend/orchestrator/runner.py`
- 前端主路径：`frontend/{projects,project-intake,three-mode,review,landing}.html`
- 旧 `/project-init`、`/workbench` 路径会跳转到项目准备页，避免绕过标包确认门控。
- 黄金样本：`tests/e2e/test_golden_sample.py`（需 `LLM_MODE=real` + `tests/fixtures/sample_*.docx`）
