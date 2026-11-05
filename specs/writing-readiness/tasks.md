# TASKS：编写就绪度：从素材缺口到可写/写后可核对的工作流

- 对应 spec：`specs/writing-readiness/spec.md`
- 对应 plan：`specs/writing-readiness/plan.md`
- 状态：待计划评审后执行
- 规则：一次只勾一项；每项完成跑验证命令；失败不得勾选。

## 任务清单

- [ ] T1 新增 `writing_readiness.py`：状态机（no_outline/no_evidence/partial/ready）+ 测试
- [ ] T2 新增 readiness API；整章写作入口接前置；无大纲返回 CTA 状态
- [ ] T3 节级写作 + `generic=true` 显式概括写法 + preflight 测试
- [ ] T4 写作结果返回 used_evidence/coverage_before/after；前端结果卡
- [ ] T5 大纲 section 支持 requirement_ids；section_context 按节过滤；旧计划回退
- [ ] T6 从需求上传/选库保留 need 上下文，采用后绑定 section+requirement
- [ ] T7 全量回归 + Playwright 冒烟 + 文档记录

## 验证命令

```bash
./.venv/bin/python -m pytest tests/test_writing_readiness.py tests/test_section_stream_contract.py -q
./.venv/bin/python -m pytest -q
node --test tests/frontend/*.test.mjs
```

## 阻塞与决策记录

| 时间 | 问题 | 结论 |
|---|---|---|
| 2026-11-05 | 0 证据是否阻断 | 不阻断，但必须显式 generic=true |
| 2026-11-05 | 旧大纲无 requirement_ids | 回退章节级并标记，不伪造节级 |
| 2026-11-05 | 节级写与整章并发 | 整章运行中禁用节级写 |
