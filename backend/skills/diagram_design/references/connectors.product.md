# 评标图连接线规范（产品）

## 先区分“包含”和“关系”

- 层带/区域表示**包含**：组件放在层带里，不用箭头表示“属于这一层”。
- 箭头只表示**跨组件/跨层的关系**：调用、数据流、支撑、异步、只读、安全边界。
- 每个层带之间至少要有 1 条清晰关系；整图至少 2 条跨层箭头，每条箭头必须有关系标签。

## 线型语义

| 关系 | 线型 | 箭头 | 典型标签 |
|---|---|---|---|
| 调用 / 主数据流 | 实线 1.4px | 实心三角 `arrow-call` | 调用、请求、下发 |
| 支撑 / 异步 | 虚线 `5,4` | 空心 V 形 `arrow-support` | 支撑、同步、异步 |
| 只读 / 审计 | 点线 `2,4` | 小圆点 `arrow-read` | 读取、审计、留痕 |
| 双向依赖 | 实线/虚线 | 两端都有箭头 `arrow-double` | 双向同步、联动 |

颜色：主路径品牌蓝，其他灰；一条图最多 3 种线型，不要每条关系换一种颜色。

## SVG markers（直接复用）

```svg
<defs>
  <marker id="arrow-call" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 0 L8 4 L0 8 z" fill="currentColor"/>
  </marker>
  <marker id="arrow-support" markerWidth="9" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 1 L8 4 L0 7" fill="none" stroke="currentColor" stroke-width="1.4"/>
  </marker>
  <marker id="arrow-read" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
    <circle cx="3.5" cy="3.5" r="2.2" fill="none" stroke="currentColor" stroke-width="1.2"/>
  </marker>
  <marker id="arrow-double" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 0 L8 4 L0 8 z" fill="currentColor"/>
  </marker>
</defs>
```

双向关系使用 `marker-start="url(#arrow-double)"` + `marker-end="url(#arrow-double)"`。

## 画法约束

1. 箭头从**源组件边缘**或**层带边缘**出发，进入目标组件边缘；不要从文字/卡片中心穿过。
2. 箭头标签放在线段中段，带白色/paper 底遮罩，字号不小于 9px。
3. 层带之间用**垂直主干 + 分叉**，不要在层带内部画满横向箭头。
4. 模块与上下层的关系要能追溯：
   - 上层模块调用下层：从上层模块底部到下层模块顶部；
   - 下层支撑上层：反向箭头，使用 dashed + support 箭头；
   - 数据层到上层：read/dashed，标签写“读取”。
5. 不允许所有箭头都是同一种实线三角；至少出现 2 种线型（例如调用 + 支撑）。
6. 如果箭头太多：优先保留 2–4 条关键路径，其余用“等 N 个调用”文字说明，不要画蜘蛛网。
