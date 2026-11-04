# 产品图标库（评标图，内联 SVG）

只在关键节点/层带使用，最多 3 个；18–22px；`fill="none"`、`stroke="currentColor"`、`stroke-width="1.6"`；不要用外链图片。

```svg
<!-- 数据库 -->
<g data-icon="database" fill="none" stroke="currentColor" stroke-width="1.6">
  <ellipse cx="9" cy="4" rx="6" ry="2.2"/>
  <path d="M3 4v9c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2V4"/>
  <path d="M3 9c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2"/>
</g>

<!-- 服务/应用 -->
<g data-icon="server" fill="none" stroke="currentColor" stroke-width="1.6">
  <rect x="3" y="3" width="12" height="12" rx="2"/>
  <path d="M6 7h6M6 10h6M6 13h3"/>
</g>

<!-- 接入/网关 -->
<g data-icon="gateway" fill="none" stroke="currentColor" stroke-width="1.6">
  <rect x="2" y="4" width="14" height="10" rx="2"/>
  <path d="M5 8h8M9 6v4"/>
</g>

<!-- 用户/终端 -->
<g data-icon="user" fill="none" stroke="currentColor" stroke-width="1.6">
  <circle cx="9" cy="6" r="3"/>
  <path d="M3 16c0-3 2.7-5 6-5s6 2 6 5"/>
</g>

<!-- 云/网络 -->
<g data-icon="cloud" fill="none" stroke="currentColor" stroke-width="1.6">
  <path d="M5 14h8a3 3 0 0 0 .5-6A5 5 0 0 0 4 9a3 3 0 0 0 1 5z"/>
  <path d="M7 13h4"/>
</g>

<!-- 安全/边界 -->
<g data-icon="shield" fill="none" stroke="currentColor" stroke-width="1.6">
  <path d="M9 2l6 2v5c0 4-3 6-6 7-3-1-6-3-6-7V4z"/>
  <path d="M6.5 9l1.8 1.8L12 7.3"/>
</g>
```
