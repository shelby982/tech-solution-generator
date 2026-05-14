# 计划：配置规划历史 Hook + 提炼等待期间内嵌互动小游戏

## Context

**任务 1（Hook）**：用户希望每次 ExitPlanMode 时自动将当前 plan 文件存档到历史目录，便于回溯每次规划。

**任务 2（游戏）**：提炼规范书核心内容通常需要 10-60 秒，用户在 `extractLoading` 区域只能看到 spinner + 进度条，体验枯燥。参考 `/Users/jianghe/Desktop/hello world .html` 中的互动特效，在等待区域内嵌一个轻量小游戏，让用户在等待时有事可做，同时进度条始终保持可见。

---

## 任务 1：配置 ExitPlanMode 历史存档 Hook

### 目标文件
- `~/.claude/settings.json`（当前内容：model=haiku, enabledPlugins, effortLevel）

### Hook 配置

在 `PostToolUse` 事件、matcher `ExitPlanMode` 上挂载 command hook：

```bash
mkdir -p /Users/jianghe/.claude/plans/history && cp /Users/jianghe/.claude/plans/splendid-wondering-crayon.md "/Users/jianghe/.claude/plans/history/$(date '+%Y-%m-%d_%H-%M-%S').md" 2>/dev/null || true
```

合并后 `~/.claude/settings.json` 示例：
```json
{
  "model": "haiku",
  "effortLevel": "high",
  "enabledPlugins": { "warp@claude-code-warp": true },
  "extraKnownMarketplaces": { ... },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "ExitPlanMode",
        "hooks": [
          {
            "type": "command",
            "command": "mkdir -p /Users/jianghe/.claude/plans/history && cp /Users/jianghe/.claude/plans/splendid-wondering-crayon.md \"/Users/jianghe/.claude/plans/history/$(date '+%Y-%m-%d_%H-%M-%S').md\" 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

### 验证
- 写入后 `jq -e '.hooks.PostToolUse[]' ~/.claude/settings.json` 返回 hook 对象
- 下次 ExitPlanMode 调用后，`ls /Users/jianghe/.claude/plans/history/` 出现新 .md 文件

---

## 任务 2：气泡小游戏

### 关键文件
- `/Users/jianghe/Claude/ge-solution/frontend/index.html`（唯一修改文件）

---

## 当前 extractLoading 结构（行 243–260）

```html
<div id="extractLoading" class="flex-1 flex flex-col items-center justify-start pt-20 gap-5">
  <!-- spinner -->
  <!-- 文字说明 -->
  <!-- 进度条（extractProgressBar / extractProgressPct） -->
</div>
```

---

## 游戏方案：「点击消消乐」—— 点击跳出的气泡得分

### 选型理由
- hello world .html 的核心玩法是「点击」+「粒子爆炸」+「连击」+「成就」，节奏轻快，单手可玩
- 改造为「点击随机弹出的彩色气泡」：气泡从区域随机位置飘起，点击爆炸得分，简单有趣
- 不需要键盘，触屏也能玩
- 与等待场景契合：越打越爽，结果来了自然停

### 布局结构（替换 extractLoading 内容）

```
extractLoading（flex-1, flex flex-col）
├── 顶部状态条（shrink-0）
│   ├── spinner + 「正在提炼...」文字
│   └── 进度条（extractProgressBar / extractProgressPct）← 保留原有 id，JS 逻辑不变
│
└── 游戏区（flex-1, 相对定位，overflow-hidden）
    ├── 分数显示（右上角绝对定位）
    ├── 连击提示（居中绝对定位，动画）
    ├── 气泡容器（铺满，气泡在此随机出现）
    └── 底部提示文字「点击气泡打发时间～」
```

### 游戏逻辑（内联 `<style>` + `<script>` 块嵌入 HTML）

**气泡生成**：每 800ms 在游戏区随机位置生成一个圆形气泡，大小 36-60px，颜色随机（indigo/violet/blue/emerald/amber 系），向上漂浮 3-5s 后消失（未点击）。同时存在气泡数上限 12 个。

**点击气泡**：触发粒子爆炸（复用 hello world 的 `createParticles` 思路，但粒子限制在游戏区内避免污染页面其他元素）、得分 +1、连击判定（1s 内连续点击触发连击文字动画）。

**成就里程碑**：5/15/30 分时弹出小提示（使用 Tailwind 行内样式的简单 toast，不依赖外部 CSS）。

**提炼完成时**：`showExtractResult()` 调用前，停止气泡生成计时器（clearInterval），清除游戏区所有气泡 DOM，避免内存泄漏。

---

## 实现细节

### CSS 动画（内联 `<style>` 注入到 `<head>` 末尾）

```css
/* 气泡飘起 */
@keyframes bubble-rise {
  0%   { transform: translateY(0) scale(1);   opacity: 0.9; }
  100% { transform: translateY(-120%) scale(0.6); opacity: 0; }
}
/* 点击爆炸粒子 */
@keyframes burst {
  0%   { transform: translate(0,0) scale(1); opacity: 1; }
  100% { transform: translate(var(--tx), var(--ty)) scale(0); opacity: 0; }
}
/* 连击文字弹出 */
@keyframes combo-pop {
  0%   { opacity:0; transform:translate(-50%,-50%) scale(0.5); }
  30%  { opacity:1; transform:translate(-50%,-50%) scale(1.3); }
  100% { opacity:0; transform:translate(-50%,-80%) scale(1); }
}
```

### JS（IIFE 包裹，变量不污染全局）

```javascript
(function startBubbleGame() {
  const gameArea = document.getElementById('bubbleGameArea');
  let score = 0, combo = 0, lastHit = 0, spawnTimer = null, bubbles = new Set();

  function spawnBubble() { /* 生成气泡 */ }
  function popBubble(el, x, y) { /* 点击爆炸 */ }
  function stopGame() { clearInterval(spawnTimer); gameArea.innerHTML = ''; }

  spawnTimer = setInterval(spawnBubble, 800);

  // 暴露给 showExtractResult 调用
  window._stopBubbleGame = stopGame;
})();
```

### 与现有 JS 的集成点

| 位置 | 改动 |
|------|------|
| `startExtract()` 函数（行 ~1555）| 切换到 extractLoading 后调用 `startBubbleGame()` 启动游戏 |
| `showExtractResult()` 函数（行 ~1599）| 调用 `window._stopBubbleGame?.()` 停止游戏、清理 DOM |
| `extractProgressBar` / `extractProgressPct` | **保留原有 id**，位置移到顶部状态条，JS 模拟进度逻辑完全不变 |

---

## 修改点清单

| # | 位置 | 内容 |
|---|------|------|
| 1 | `<head>` 末尾 | 注入气泡/爆炸/连击 CSS 动画（`<style>` 块） |
| 2 | 行 243–260 `extractLoading` 内部 | 重构为「顶部状态条 + 游戏区」两段布局 |
| 3 | `</body>` 前 | 注入游戏 JS（IIFE），包含 `startBubbleGame` 和 `_stopBubbleGame` |
| 4 | `startExtract()` 末尾（`extractLoading` 显示后） | 调用 `window.startBubbleGame?.()` |
| 5 | `showExtractResult()` 开头 | 调用 `window._stopBubbleGame?.()` |

---

## 注意事项

- 粒子和气泡 DOM 全部挂在 `#bubbleGameArea` 下，`stopGame` 用 `innerHTML = ''` 一次性清理，无内存泄漏
- 不引入任何外部依赖，纯 Vanilla JS + 内联 CSS
- 游戏区 `overflow-hidden` 防止气泡飘出边界影响页面滚动
- `pointer-events: none` 确保飘走中的气泡（即将消失）不拦截点击

---

## 验证方式

1. 点击「提炼规范书核心内容」进入 loading 状态
2. 确认顶部显示 spinner + 进度条，进度百分比正常爬升
3. 游戏区随机出现气泡，点击气泡触发爆炸粒子 + 得分 +1
4. 快速连击触发连击文字动画
5. 提炼完成后游戏停止，界面正常切换到 extractResult
