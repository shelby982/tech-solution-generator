# PLAN：编写就绪度：从素材缺口到可写/写后可核对的工作流

- 对应 spec：`specs/writing-readiness/spec.md`
- 日期：2026-11-05
- 状态：待计划评审

## 1. 方案概览

新增一层 `writing_readiness`，把“大纲是否存在 + 本节证据需求/覆盖 + 是否允许概括写法”变成写作前置状态；写作入口、节级写作、写后反馈都读同一份状态，避免素材页和写作页各说各话。

```
chapter/section
  → writing_readiness（大纲? 需求? 覆盖? generic?）
  → 写作入口（先生成大纲 / 补证据 / 按概括写 / 写本节）
  → section stream（只写本节）
  → used_evidence + coverage_before/after
  → 结果卡片（使用证据 N 条 · 覆盖 X/Y）
```

## 2. 关键设计决策

| 决策 | 备选 | 理由 | 风险 |
|---|---|---|---|
| 新增只读 readiness 服务/API | 前端自行判断 | 状态必须一致，可测试 | 多一次查询 |
| `no_outline` 不算错误，返回状态 | 继续 4xx | 写作入口应给 CTA，不是 toast | 旧调用需兼容 |
| 0 证据写作必须 `generic=true` | 静默允许 | 让用户知道“这段没有证据” | 多一次确认；保留效率 |
| 节级写作复用现有 section stream | 新写接口 | 已有落库/替换逻辑，避免重复 | 需补 preflight |
| used_evidence 从实际注入的 context 计数 | 从正文猜引用 | 统计可信 | 需要把 context 传到响应层 |
| requirement_ids 加在大纲 section 上 | 新建映射表 | 简单、随计划走 | 旧计划无字段需回退 |
| 上传/选库沿用 pendingNeed | 新建上传任务表 | 不做大改 | 跨刷新丢失；首期先会话内闭环 |
| 旧大纲无映射时标“章节级” | 伪造节级 | 避免错误覆盖 | 用户需重新生成大纲 |

## 3. 改动面

| 路径 | 类型 | 说明 |
|---|---|---|
| `backend/services/writing_readiness.py` | 新增 | 章节/节 readiness、状态、缺口、generic 标记 |
| `backend/routes/chapter_studio.py` | 修改 | `GET /blocks/{id}/writing-readiness`；section stream 增加 generic/preflight；done 返回 used_evidence/coverage |
| `backend/routes/chapter_sse.py` | 修改 | 整章写作前 readiness；无大纲/未就绪的 plan 事件 |
| `backend/services/chapter_sse.py` | 修改 | 节级写/整章写记录 used_evidence；generic 标记 |
| `backend/services/section_context.py` | 修改 | 支持 section requirement_ids 过滤；返回实际注入证据 |
| `backend/services/writing_plans.py` | 修改 | 大纲 section 预留/保存 `requirement_ids` |
| `frontend/assets/chapter-studio.mjs` | 修改 | 写作按钮状态、无大纲 CTA、节级写、generic 确认、结果证据卡 |
| `frontend/assets/chapter-studio.css` | 修改 | readiness/used evidence 样式 |
| `tests/test_writing_readiness.py` | 新增 | 状态机、无大纲、0 证据、partial/ready |
| `tests/test_section_stream_contract.py` | 新增/修改 | generic/preflight、only section replaced、used_evidence |
| `tests/test_material_library_api.py` | 修改 | pending need 绑定保留 section/requirement |

## 4. 验证方案

- 单测：
  - `pytest tests/test_writing_readiness.py tests/test_section_stream_contract.py -q`
  - `pytest -q`
- 前端：`node --test tests/frontend/*.test.mjs`
- Playwright：
  - 无大纲章节 → 点击写作 → 出现“先生成大纲”；
  - 有大纲 0 证据 → 出现“补证据/按概括写”；
  - 节级写一节 → 只有该节变化；
  - 生成后显示“使用证据 N · 覆盖 X/Y”；
  - 无证据写 → 显示“未使用证据（概括写法）”。
- 回归：素材页、上传、引用、整章写、导出。

## 5. 实施顺序

1. T1 readiness 服务 + 测试。
2. T2 readiness API + 整章/节级写作前置。
3. T3 节级写作 + generic 显式模式。
4. T4 used_evidence + coverage_before/after + 结果展示。
5. T5 大纲 requirement_ids + section context 过滤 + 旧计划回退。
6. T6 上传/选库 pending need 闭环。
7. T7 全量/Playwright/文档。

## 6. 回滚方案

- readiness 服务只读，删除即回退；写作入口保留旧按钮作为 fallback。
- generic 是可选参数，旧调用不传时保持现状；紧急回滚可恢复静默写入。
- 大纲 requirement_ids 可空，旧计划不受影响。
- used_evidence 只加响应字段，不改正文结构。
