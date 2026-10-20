# 自检流水线（代码 + 界面）

一条命令：`bash scripts/check.sh`（可选 `THREAD=<threadId> BLOCK=<blockId>` 环境变量）

| 步骤 | 内容 | 工具 |
|---|---|---|
| 1/6 | 后端语法 | `python -m compileall` |
| 2/6 | 前端语法 | `node --check` |
| 3/6 | 资源版本一致性 | grep `?v=YYYYMMDD-vNNN` |
| 4/6 | 后端单测 | `pytest`（LLM_MODE=mock） |
| 5/6 | 前端单测 | `node --test` |
| 6/6 | **界面自动审查** | `scripts/ui-selfcheck.py`（Chrome CDP + 设计稿规则） |

## 界面自动审查怎么工作

1. 用 Chrome 无头 + 远程调试（`--remote-debugging-port=9333`）打开编写页
2. 自动点：进入编写阶段 → 选中目标章节 → 点「本章编写计划」
3. 在真实 DOM 上按**设计稿规则**逐条断言（13 条）：
   - 区块齐备（待办 / 交付物 / 折叠 / 底部操作）
   - 主操作唯一且是主色（蓝底）
   - 交付物只展开一项、展开区在标题下方
   - 状态词不重复；折叠摘要带数字；ⓘ 只有一个
   - 弹窗宽度 680±60；底部 sticky；无横向溢出
   - 主操作文案与待办条数一致
4. 截图到 `docs/screenshots/ui-selfcheck.png`，任一条 FAIL 则退出码非 0

## 启动前置

```bash
# 后端（写死 8015）
cd backend && ../.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8015
# Chrome 调试端口（审查界面用）
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --user-data-dir=/tmp/ui-check --remote-debugging-port=9333 --remote-allow-origins=* about:blank &
```

缺 Chrome 或后端时，第 6 步自动 SKIP（不影响前 5 步）。
