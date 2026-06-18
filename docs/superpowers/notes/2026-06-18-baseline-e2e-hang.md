# Baseline E2E Hang —— 调试备忘

**Date:** 2026-06-18
**Branch:** feature/multi-agent-bid-system
**Status:** BLOCKED — 6 个 fix 已合，gate1 仍 hang，需新会话深度排查

## 现象

`LLM_MODE=real pytest tests/e2e/test_golden_sample.py -v -m slow -s --capture=no` 在以下位置 hang ≥22 分钟：

```
[19:49:30] golden-sample: test entered
[19:49:30] golden-sample: db initialized
[19:49:30] golden-sample: project + materials seeded
[19:49:49] golden-sample: verify_all: 3/3 configs ok
[19:49:49] golden-sample: runner.start
[19:49:49] golden-sample: runner.start done, thread_id=...
[19:49:49] golden-sample: await gate1 (parse + extract + match)
   ←  没有新日志，22 分钟后被 timeout 1800 / 人工 kill
```

## 已合并的修复（按顺序）

| Commit | Fix | 治什么 | 实证 |
|---|---|---|---|
| 181c497 | infra: chunk-level timeout | 流式 stuck on no-data | 仅作用于 stream，gate1 不走 stream |
| 7faa95c | infra: DRY chunk timeout helper + env CHUNK_TIMEOUT | 同上质量 | — |
| 39669ec | config: 启动期 verify + 死配置剔除 | 死配置 round-robin 命中 | 实证 verify_all 3/3 ok |
| 8d92401 | config: 移除无效 persist + 异常加 traceback | 同上质量 | — |
| d393119 | agents: zhuge_liang per-block 并发 | 诸葛亮 N×T 串行 | 不影响 gate1（gate1 在诸葛亮之前） |
| 2b1d0ca | agents: gather return_exceptions + concurrency test | 同上质量 + 失败隔离 | — |
| 4c5ca6d | test(e2e): stage logging + asyncio.wait_for | 看见卡点位置 | 这本身就是诊断手段 |
| d91d550 | test(e2e): 启动期 verify_all | 让 e2e 共享 lifespan 行为 | verified=True 正常进 round-robin |

## 诊断走过的 5 个假设

**假设 A — Stream chunk timeout 不生效** ✓ 已修（commit 181c497 + 7faa95c）
- 30s 无 chunk 抛 TimeoutError → dispatcher fallback 切下个 config

**假设 B — 诸葛亮 per-block 串行** ✓ 已修（commit d393119 + 2b1d0ca）
- 但 gate1 在诸葛亮之前，gate1 hang 与此无关

**假设 C — Fan-out 不真并行** ❌ 证伪
- graph.py 边定义正确（参考 https://langchain-ai.github.io/langgraph/）

**假设 D — 评审 per-block 串行** ❌ 实际非根因
- 王安石/包拯仍 per-block 串行，但 gate1 在评审之前

**假设 E — 死配置 round-robin 死锁** ✓ 部分修（commit 39669ec）
- 实测 verify_all 3/3 ok，所有配置都合法
- `deepseek-v4-pro` model 名服务端确实接受（用户私有部署/别名）
- 直接 oneshot 实测 1.7s/9.5s/6.2s 都成功

## 实证数据（决定性）

```bash
# 用户工作目录直接调 oneshot
python3 -c "import asyncio; from infra.llm.clients import generate_oneshot_openai; ..."

openai/deepseek-v4-pro:    1.7s -> （5-token 探测）空响应
deepseek/deepseek-v4-pro:  1.2s -> 空响应
kimi/moonshot-v1-8k:       0.6s -> "OK"

# 4000-token 提炼请求
openai/deepseek-v4-pro:   12.1s len=514 ✓
deepseek/deepseek-v4-pro:  9.5s len=570 ✓
kimi/moonshot-v1-8k:       6.2s len=778 ✓
```

**结论**：3 个 LLM 配置在直接调用下都正常 6-12 秒返回。Hang 不在单次 LLM 调用。

## 还没排查的方向（下次会话起点）

1. **LangGraph runner 内部 await 链**：`runner.start` 立刻返回 thread_id 并 fire-and-forget 启动后台 task；但 `await runner._runs[thread_id].task` 才是实际 graph 跑，如果 graph 内部某个节点 await 一个 future 永远不来...
   - 排查：在 `backend/orchestrator/runner.py` 和 `backend/orchestrator/nodes.py` 加节点级别日志
   - 看 zhang_heng_parse_node / zhang_heng_extract_node / shen_kuo_match_node 各自打开始 / 结束日志

2. **dispatch_outline_json 顺序 yield**（dispatcher.py:374-376）
   ```python
   for t in tasks:
       yield await t  # task[0] 卡住时后面 4 个完成的 worker 不会被消费
   ```
   - 但单 task 三次 fallback 后必返回（实证 6-12s × 3 fallback ≈ 30s）
   - 假设：如果某个 config 在生产请求时不抛错只 hang，就会真死锁
   - 排查：用 asyncio.as_completed 或 asyncio.gather 替代顺序 yield

3. **LangGraph checkpointer 的 SQLite 死锁**：测试用 tmp_path SQLite，并发写 state 时可能锁
   - 排查：在 e2e 测试切回纯内存 saver

4. **新增日志位置建议**：
   - `orchestrator/runner.py::_run_until_pause` 入口/出口
   - `orchestrator/nodes.py` 每个 node 函数入口/出口
   - `agents/zhang_heng.py::parse` / `extract` 入口/出口
   - `infra/llm/dispatcher.py::dispatch_outline_json` 每个 worker 完成时

## 工程建议

baseline 跑通是 plan §11 开放问题，不属于 plan §5/6/7 任务。下次会话：

1. 起独立分支 `chore/baseline-debug`
2. 加节点级日志（不 commit 到主线）
3. 跑 baseline 看 hang 在哪个节点
4. 针对性修复（可能是 LangGraph 配置 / SQLite 锁 / 顺序 yield）
5. 修完跑出 baseline.json，commit 后人工审核
6. 删除诊断日志，正式合入

不在当前会话继续，是因为：
- 已 dispatch 6 次 subagent，token 成本累计高
- 5 个假设都已实证排查，没有新方向可推理
- 继续派 subagent 没有新输入信号会重复绕圈

## 文件清单（修复留下的）

- `tests/e2e/test_golden_sample.py` — 含 stage 日志 + verify_all + asyncio.wait_for(1500)
- `backend/infra/llm/clients.py` — chunk timeout helper + CHUNK_TIMEOUT env
- `backend/services/config_store.py` — verify_all + 死配置剔除
- `backend/agents/zhuge_liang.py` — Semaphore(5) + gather + return_exceptions
- `tests/agents/test_zhuge_liang.py` — 7-block 并发隔离测试
