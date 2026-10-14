# UI 设计一致性审计：当前产品

> 方法：不靠印象，全部结论对应代码事实（grep 计数、样式文件行数、token 使用次数）。
> 统计口径：`frontend/assets/*.css` + `frontend/*.html` 内联样式 + `frontend/assets/*.js` 里的模板字符串。

---

## 一、当前 UI 实际遵循的设计逻辑

产品**没有**共享设计系统，实际遵循的是：**"每个功能自带样式 + 每个页面自成一个世界"**。

```
① token 层      frontend/assets/styles.css      29 个变量（--bg/--ink/--accent/--radius-* …）
② 组件层        没有。每个视图自己写一套（.sr-* / .pc-* / .match-* / .ced-* / .quality-* / .mw-btn …）
③ 页面层        6 个页面，其中 4 个仍在用（three-mode / review / projects + project-init / workbench）
```

证据：`styles.css` 定义了 `--radius-sm/md/lg`，但全站只用了 **2 / 3 / 2 次**，而硬编码圆角出现了 **10 种值**。
`--shadow` 只用了 1 次。→ token 层是"装饰性"的，没有约束力。

---

## 二、一致性审计（带证据与严重度）

| # | 维度 | 现状 | 证据 | 严重度 |
|---|---|---|---|---|
| 1 | **同一对象两个名字** | 「评分点」与「评审点」并存 | 评分点 113 次 / 评审点 39 次 | **高** |
| 2 | 同一动作两个词 | 「拆分」与「拆解」 | 拆分 142 / 拆解 62 | 中 |
| 3 | **我引入的"标段"与产品既有"标包"冲突** | 标包 44 / 标的 2 / 标段 1（后者是我加的） | grep | **高（我方引入）** |
| 4 | 同一层级两个名 | 左侧导航叫「产物点」，面板头叫「产物」 | 产物点 9 / 产物 36 | 中 |
| 5 | 空态三套措辞 | 「暂无…」21 / 「还没有…」13 / 「尚未…」10 | grep | 中 |
| 6 | **颜色 token 未贯彻** | 硬编码 hex：styles 148 / review 81 / **workspace 44（0 个 var）** | grep | **高** |
| 7 | **共享壳与主 token 不同色** | 壳用 `#dce3ed` / `#e2e8f0` / `#172338`，主 token 是 `--line:#dde4ee`、`--bg:#f4f6f9` | 近似的两种灰做同一件事 | **高** |
| 8 | 圆角无尺度 | 10 种值（2/4/5/6/7/8/9/10/14/999），--radius-* 几乎不用 | grep | 中 |
| 9 | 字号无尺度 | 12 种值，含 10.5/11.5/12.5/13.5 半像素 | grep | 中 |
| 10 | **按钮无统一系统** | ≥8 套类名：`mw-btn`(32) `btn-status-primary`(20) `btn-status-secondary`(22) `chap-btn`(15) `ws-button`(8) `mat-refresh-btn` `ced-footer-btn` `edit-modal-btn` | grep | **高** |
| 11 | 主按钮主色不统一 | 黑 `var(--text)`/`var(--ink)`（工作台、状态行）vs 蓝 `#2563eb`（共享壳、质量检查） | grep | 中 |
| 12 | **弹窗两套** | 应用内 `openDialog` 10 次 **＋ 原生 `alert()` 8 次 / `confirm()` 5 次** | grep（用户此前已抱怨过 alert） | **高** |
| 13 | 进度呈现 6 种 | `ring-fill` / `status-progress-bar` / `sr-loading` / `gen-status-bar` / `outline-progress-bar` / `ced-gen-progress` | grep | 中 |
| 14 | **同一对象多处各画一遍** | **评分点**：`.pc-card`（拆分面板）/ `.band`（分值标签）/ `.verdict-row`（评审页）/ `.cp-point`（编写章头）<br>**素材**：`.mat-row`（材料条）/ `.chunk`（片段）/ `.matched-card`（匹配卡）/ `.cite-bar`（引用）/ `.mat-drawer`（容器） | grep | **高** |
| 15 | 内联样式 | `three-mode.html` 64 处 `style="…"` | grep | 中 |
| 16 | **页面体系冗余** | `/project-init` 仍有 4 个入口、`/workbench` 1 个入口（与 three-mode 重复的旧 UI） | grep href | **高** |

---

## 三、合理性审计（信息架构 / 交互逻辑）

### 3.1 一条主线被"页面边界"切断
五阶段刚统一（拆分评审点 → 待确认 → 产物 → 编写 → 评审），但：
- ④ 编写在 `?view=editor`（同一页面，原地切换 ✅）
- ⑤ 评审在 `/review`（**整页跳转** ✗）

后果：从评审看到"某章缺要点"，想回编写页改 → 整页跳转、上下文（章节/选中项）丢失，再回来要重新找。
**同一件事（一条主线）用了两种导航模型。**

### 3.2 两套"确认"语义，用户无法判断主从
- 面板「**待确认**」= 范围 / 类别 / 主题归属 → 「确认，生成产物」
- 状态行「**确认目录，开始提炼要点**」= 闸门 1

实测踩过：我用状态栏路径启动运行 → 目录已派生、已在提炼，而面板「产物点」仍显示"**还没确认**，产物还没到出现的时候"。**界面同时给了两个互相矛盾的真相。**

### 3.3 双写者（同一个 DOM 被两个模块写）
- 状态信息曾在 `#workspace-status` 与 `#status-bar` **双写**（已合并）
- 评审区 `#req-bar-review` 由 `workbench.js` 写，而 req-bar 其余部分由 `mode-editor.js` 写
（代码注释自己承认："这里不写，避免两个写者互相覆盖" —— 这是设计层面的信号。）

### 3.4 同一动作多个入口，命名不区分
- 生成正文：`AI 生成` / `生成未写章节` / `重新提炼` / `继续提炼（530 节）`
- 评审：`按意见修订` / `仅复审` / `全部重新评审` / `对缺口重写`

**"提炼" vs "生成"** 是产品关键区别（提炼=抽取得分要求，生成=写正文），但界面上两个词都出现 594 / 168 次，
用户很难从字面分辨"我现在点的这个会花多少钱、花多久"。

### 3.5 反馈层级混乱
- 破坏性/打断式反馈：原生 `alert` / `confirm`（阻断、样式不可控）
- 无反馈：状态行的主动作点击后（只换文案）
- 自定义 flash：只有面板内我用过（`.sr-flash`），没有全局 toast

### 3.6 不可逆动作缺保护，但关键动作又要求确认
- 删除材料、清空项目：直接执行（无统一确认）
- 「确认即生成」：一次点击即消耗模型额度（有闸门但语义含糊）

### 3.7 状态真相不同步（数据层影响 UI）
实测：`workflow_runs.stage = paused`，而检查点 `stage = generating`。
界面若读前者，会显示"已暂停"而实际在生成 → **用户看到的阶段可能是错的**。

---

## 四、成因（为什么会这样）

1. **增量叠加**：功能先做、样式就地写。每加一个面板/页面就起一套类名（`.sr-*`、`.pc-*`、`.cp-point`…）。
2. **合并壳吞并旧页但没下线**：three-mode 合并了"要求与大纲/素材库/方案编辑"三页，但 `project-init`、`workbench` 仍在，且仍有入口 → 三套 UI 并存。
3. **缺契约**：没有组件清单（什么场景用哪个组件）、没有术语表（同一对象叫什么）、没有尺度（圆角/字号/间距）。
4. **样式与逻辑同文件长大**：`three-mode.html` 3944 行里混着 64 处内联样式与大量组件样式。

---

## 五、整改建议（按投入产出排序）

### P0 — 一天内可做，收益最大
1. **术语表定稿**（一处定义，全站替换）：
   `评分点`（废弃"评审点"）· `拆分评审点`（二选一）· `产物`（废弃"产物点"）·
   **`标包`（废弃我引入的"标段"）** · 统一"素材"（废弃"材料/原始素材"混用）
2. **主按钮收敛为一种**（建议沿用既有蓝 `#2563eb`），其余动作一律 ghost 样式。
3. **空态一句话规范**：统一「暂无 X」+ 可操作指引（禁用"还没有/尚未"）。
4. **把 `workspace.css` 的 44 个硬编码色并入 token**（`#dce3ed` → `var(--line)` 等）。

### P1 — 2~4 天，结构一致性
5. **组件最小集**（8 个）：`Button(4 变体)` `Card` `Table` `Chip/Pill` `Drawer` `EmptyState` `ProgressBar` `Modal`；
   在 three-mode / review / projects 三个页面替换，删除重复实现（评分点 4 套 → 1 套；素材 5 套 → 2 套）。
6. **尺度收敛**：radius 3 档（6/10/14）、font-size 5 档（11/12/13/14/16）、间距 4 的倍数。
7. **消灭原生 `alert/confirm`**，统一 `openDialog`；破坏性动作统一二次确认。

### P2 — 信息架构
8. **主线原地切换**：把 ④编写 ⑤评审 收进 three-mode（stage 4/5 已在导航里具备），取消整页跳转；
   或至少保持 `threadId/blockId` 上下文 + 提供"返回主线"。
9. **旧页下线或明确标注**：`/project-init`、`/workbench` 与 three-mode 三选一。
10. **合并两套"确认"**：面板待确认与目录确认合成一个 gate（或明确主从关系并统一文案）。
11. **状态真相单一来源**：界面只读检查点 stage；或后端在写入时同步 `workflow_runs.stage`。

---

## 六、诚实备注：其中几条是我这几轮改动引入的

| 问题 | 归属 |
|---|---|
| 引入「**标段**」与产品既有「标包」冲突 | **我**（原型与架构文档） |
| 新增又一套组件样式：`.pc-card/.band/.cp-point/.verdict-row/.sr-flash` | **我**（视觉接近但未并入 token） |
| 「产物 vs 产物点」并存 | 我保留了旧标签、新文档又用「产物」 |
| 单条状态行合并、材料条置顶、五阶段导航 | 我（**改善了**一致性，但引入了新类名） |

→ 我后续的改动应遵守同一套 token/组件，而不是继续"就地写一套"。

---

## 七、整改进展（P0 已落地）

### 7.1 布局一致性（用户强调的硬要求）
**左侧五步 + 右侧对应内容，全部在同一个壳内，不做整页跳转。**

```
1 评分点 · 2 待确认 · 3 产物 · 4 编写 · 5 评审
```

| 阶段 | 右侧内容 | 之前 |
|---|---|---|
| ① 评分点 | 结构化评分口径（商务/技术/规范书要求 子页签） | 同 |
| ② 待确认 | 范围 / 类别 / 主题归属 / 分值自检 | 同 |
| ③ 产物 | 目录 + 映射矩阵 + 覆盖 | 同 |
| ④ 编写 | **章节列表 + 章头（本章回应：评分点·分值·得分口径·需举证）+ 正文编辑 + 按需素材** | ❌ 原来整页跳到 `?view=editor` |
| ⑤ 评审 | **得分预估 / 缺口 / 举证 / 成本的 KPI + 评分点 verdict 表 + 章节下钻** | ❌ 原来整页跳到 `/review` |

实测：依次点 5 个阶段，`location.pathname` 始终是 `/three-mode`，`data-stage` 正确切换，`__bootErrors=[]`。

### 7.2 术语统一（在线资产已清零）
| 统一为 | 废弃 | 处理 |
|---|---|---|
| **评分点** | 评审点（39 → 0） | mode-outline / review / three-mode 全量替换 |
| **产物** | 产物点（9 → 0） | 同上 |
| **标包** | 标段（我引入的 1 → 0） | review.js「本标包累计」 |
| **拆分** | 拆解（62 → 0，含注释） | 技术规范书拆解 → **技术规范书要求** |
| 空态「暂无 X」/「未 X」 | 还没有 / 尚未 | 在线资产已统一 |

### 7.3 主按钮统色
`--accent`（#2563eb）成为唯一主色：`btn-status-primary` / `btn-extract` / `ms-primary` /
`btn-api-save` / `btn-generate-all` / `ced-footer-save` / `suggestion-btn.accept` 全部改为 `var(--accent)`；
实测 `.btn-status-primary` 计算值 `rgb(37,99,235)` ✅。剩余的黑色仅 `logo` / `model-dot` /
`ced-step-badge` / 分段控件激活态（非主按钮）。

### 7.4 token 化
- `workspace.css`：**44 个硬编码色 → 0**（33 处 `var()`），并把 `#1d4ed8` 提升为 `--accent-strong`。
- 新增的 ④⑤ 面板样式**全部走 token**（`var(--line/--accent/--success-soft/--warn-soft/…）`），
  不再就地写一套颜色 —— 这是我在审计里给自己定的约束。

### 7.5 仍待做（P1/P2）
组件最小集（评分点 4 套 → 1 套、素材 5 套 → 2 套）· 尺度收敛（radius 3 档 / 字号 5 档）·
消灭原生 alert/confirm · 合并两套"确认"语义 · 旧页 `/project-init` `/workbench` 下线 ·
状态单一来源（`workflow_runs.stage` 与检查点不同步）。

---

## 八、整改进展（P1 已落地）

### 8.1 组件最小集：`frontend/assets/ui.css`（一份定义 + 历史类名映射）
在三个在线页面（three-mode / review / projects）**最后加载**，因此能统一覆盖各页内联样式里
的历史定义，不必逐页重写模板。

| 组件 | 规范类名 | 映射的历史类名（示例） |
|---|---|---|
| Button（4 变体） | `.ui-btn` / `--primary` / `--danger` / `--sm` | `ws-button` `mw-btn` `chap-btn` `ced-footer-btn` `btn-status-*` `sr-btn` `btn-extract` `ws-primary` `btn-new-project` `btn-distill` … |
| Card | `.ui-card` | `sr-box` `mw-card` `matched-card` `ref-card` `pc` |
| Table | `.ui-table` | `sr-table` `v-chapters` |
| Pill / Chip | `.ui-pill` + `is-ok/is-warn/is-bad/is-none` | `sr-vpill` `v-pill` `band` `ws-count` `ws-chapter-status` `sr-wev` |
| EmptyState | `.ui-empty` | `mw-empty` `matched-empty` `blocks-empty` |
| ProgressBar | `.ui-progress` | `status-progress-*` `gen-status-bar*` `outline-progress-*` `ced-gen-progress*` |
| Modal | `.ui-modal` | `ws-dialog` |
| Drawer | `.ui-drawer` | `mat-drawer-wb` `context-pane` |

统计：**ui.css 定义 24 个规范类，映射 73 个历史类名** → 同一套视觉，两种写法并存但不再分叉。

### 8.2 尺度收敛（token 化）
`:root` 新增并强制组件层使用：`--r-1/2/3`（6/10/14）· `--fs-1..5`（11/12/13/14/16）·
`--sp-1..5`（4/8/12/16/24）。组件层已全部引用 token；**散落在旧页面里的硬编码值仍在（P2 清理）**。

### 8.3 消灭原生弹窗
新增 `uiAlert()` / `uiConfirm()`（基于 `openDialog`，非模块脚本可用 `window.__ui`）：
- `mode-outline.js` 4 处 `window.confirm` → `await uiConfirm(...)`
- `mode-editor.js` 6 处 `alert` → `uiAlert`；1 处同步 `confirm` → `uiConfirm` + 抽出 `switchBlockDiscarding`
- `workbench.js` / `context-panel.js` → `window.__ui?.alert ?? alert`

复查：**原生 `alert(` 0 处、原生 `confirm(` 0 处**（在线资产）。

### 8.4 效果复核（CDP 实测）
```
three-mode 面板：sr-box 圆角 10px(=--r-2) ✅ 主按钮 rgb(37,99,235) ✅ __bootErrors=[] ✅
review 页：verdict 8 行 + KPI 正常；「已写正文 5/121」✅
projects 页：#btn-new-project 计算值 rgb(37,99,235) ✅（原为黑）
后端 751 passed / 前端 102 passed
```

### 8.5 仍待做（P2）
合并两套"确认"语义 · 旧页 `/project-init` `/workbench` 下线 · 状态单一来源 ·
清理旧页面里散落的硬编码字号/圆角 · 素材从"全量预匹配"改成"编写时按需"（产品侧 P2）。

---

## 九、整改进展（P2-1：状态单一来源）

### 问题
界面读 `workflow_runs.stage`，但运行期 stage 只有 **extract / match** 两个节点会写。
于是"回修生成"（`workspace_action`）期间表里一直是上一版值 —— 实测：检查点 `generating`，
表里还是 `paused`，**界面显示的阶段是错的**。

### 改法
1. 把"下一个节点 → 运行期 stage"抽成**单一来源** `orchestrator.runner.next_phase_stage()`，
   并补齐全部节点（parse / outline / extract / match / gate_materials / generate /
   五个评审节点 / gate_report）。
2. 多候选时按**图上推进顺序**取（不能按字典序）：`{generate, aggregate_review}` 必须选
   `generating`，字典序会误选 `reviewing`（实测踩到）。
3. 顺手修一个真 bug：后端重启后 checkpoint 可能停在运行期 stage（如 `generating`），
   而 `workspace_action` 只接受 settled stage → **这个 thread 再也操作不了**。
   现在"没有 task 在跑"时按可恢复处理（从确定入口 GATE_MATERIALS/NODE_GENERATE 重入）。

### 实测
```
修复前：workspace-action → 409「请先完成大纲和素材确认，或等待当前运行结束」（重启后永久卡死）
修复后：workspace-action → 200；workflow_runs.stage 从 paused → 被持续写入（不再停滞）
```

### 残留（已写进代码注释，不假装已解决）
两个 store 语义不同：`workflow_runs.stage` = **图接下来要做什么**（界面读它，是阶段单一来源）；
`checkpoint.stage` = 最后一次节点结束/patch 写入的值，可能滞后。实测同一条 thread 上
出现过"表里 reviewing / 检查点 generating"。要判断"还在不在跑"，应看 `_runs[tid].task.done()`。

### 测试
- 新增 `tests/test_run_stage_mapping.py`（5 条：全节点映射 / 评审循环 / 空与未知 / 值必须合法 /
  多节点优先级）
- 更新旧测试 `test_mark_next_phase_writes_runtime_stage`（原来断言"parse 不写 stage"，
  与新口径冲突 —— parse 解析 164 页要几分钟，属于长阶段）
- 后端 **756 passed**

---

## 十、整改进展（P2-2：合并两套"确认"语义）

### 问题
同一个决策有**两次确认**，而且互相矛盾（实测）：
1. 面板「待确认」→ 范围/类别/归属 → 「确认，生成目录与映射矩阵」
2. 状态行闸门 → 「**确认目录，开始提炼要点**」

用状态栏路径启动时，目录已派生、正在提炼，而面板「产物」却显示"**还没确认**，产物还没到出现的时候"
—— 界面同时给了两个互相矛盾的真相。

### 判断
范围/类别的确认与目录结构的确认是**同一次决策**，不该让用户确认两次。

### 改法
- 面板点「确认并生成目录」时记下 `autoConfirmOutlineAt`（带 15 分钟有效期）；
- 图到达闸门 1 时，若刚在面板确认过 → **自动放行**，状态行只显示
  「目录已按你的确认生成」+「重新拆分目录」；
- 用户主动「重新拆分目录 / 重新拆分评分点 / 重新提炼」→ 清除该标记（尊重第二次确认）；
- 保留的手工路径（主动重拆目录后）仍显示「确认目录，开始提炼要点」——那次确实是新决策。

### 实测（拦截 fetch，不产生真实副作用）
```
setAutoConfirm(true) → statusBar('gate_outline', …)
结果：POST /resume 调用 1 次（闸门被自动放行）✅  状态行切到"提炼中"（带取消）✅  __bootErrors=[]
```

### 顺带一条纪律
`node --check xxx.js` **没有**抓出我这次插入造成的 ESM 语法错误（参数列表里多了语句）。
改用 `node --check /tmp/x.mjs`（ESM 解析）才拦住 —— 后续前端语法校验都用这个方式。

---

## 十一、整改进展（P2-3：旧页下线）

### 问题
`/project-init`（要求与大纲、素材库）与 `/workbench`（方案编辑）已被 three-mode 合并，
但仍有 **6 处入口**指向它们（URL 生成器 + 视图跳转 + 提示横幅）—— 同一个活有三个入口，
用户会在两套 UI 之间被来回搬运。

### 改法
1. **统一 URL 生成**：`workspaceUrl()` 与三处 `VIEW_URLS` 改为 `/three-mode?…&view=…`；
   两处 `location.href = /workbench…` 改指 `/three-mode?…&view=editor`。
2. **旧页保留重定向**（不删文件）：`project-init.html` / `workbench.html` 顶部加跳转脚本，
   按 `view` 参数落到对应视图；**`?legacy=1` 可绕过**（仅供排查历史问题）。
3. `diff-review.html` 的「材料撰写」标签改指 `/three-mode?view=editor`。

### 实测
```
/project-init?projectId=26&threadId=…  →  /three-mode?…&view=outline   ✅
/workbench?projectId=26&threadId=…     →  /three-mode?…&view=editor    ✅（编辑器布局正常渲染）
在线资产里指向旧页的链接：0 处
```

---

## 十二、整改进展（P2-4：素材按需检索）

### 问题
原来的素材是**全量预匹配**：跑一次匹配把所有章节的 `block.source` 都填满。
于是 ① 不需要素材的章节也被塞依据；② 没跑过匹配的章节界面只能显示"暂无匹配素材"；
③ `GET /matched-sources` 返回的是**已存**结果，不是按需检索。

### 改法
1. 新服务 `services/material_search.py`：给定章节 → 从它的 `evidence_required` /
   `key_points` / 标题抽关键词（**确定性**，不调模型）→ 直接在 `material_chunks` 里打分检索。
   - 抽取分三层：短语优先（≤8 字整段，权重 ×3）→ 长短语补 2~4 字滑窗 → 去包含；
     含虚词（的/及/对…）的滑窗片段直接丢（否则会出现"及偏差说"这种半截词）。
   - 打分 = 命中词数（长词加权）× 词频^0.5 ÷ √片段长度；同一素材最多给 2 段。
2. 新接口 `GET /api/blocks/{id}/material-search`（**不写库、不预计算**）。
3. 面板 ④编写 的「按需素材」改走这条通道，并**如实标注用的是哪条**：
   按需检索命中 → "按本章举证要求检索 · 命中 N 段（关键词：…）"；
   没命中 → 回落到「已匹配依据」；都没有 → "本章暂无相关素材…"（有举证要求时提示"可在素材库补充"）。

### 实测
```
接口：block 36873 → query_source=evidence_required，
      关键词 ['标文件','偏差说明','投标文件','无偏差','招标文件','表头']  ✅
UI：④编写 → 按需素材 → 「本章暂无相关素材：按需检索没命中，也没有预匹配依据。
      本章有举证要求，可在素材库补充。」  __bootErrors=[]  ✅
测试：tests/services/test_material_search.py 5 条（抽取/打分/排序/回落/只读）
```

### ⚠️ 顺带发现一个数据完整性问题（未修，需你决定）
项目 26 的 `material_chunks` = **0**，且 `backend/uploads/` **目录不存在** —— 素材文件已从磁盘丢失，
而库里两行仍写着 `parse_status='done'`。所以按需检索在本项目"没有东西可搜"（行为正确、
提示诚实），但也说明：**素材缺失时界面没有"文件已丢失，请重新上传"这类状态**。
建议后续给素材加"文件存在性校验"（列表里标红 + 一键重传）。

---

## 十三、修复：界面下方为什么还展示目录（用户提问）

### 核实结论
下方那份列表是**旧的章节目录树**（`#blocks-list`，`.block-card`），由 `loadExistingBlocks()` 渲染。
旧代码在"目录已生成"分支里**既把树亮起来、又打开面板**：

```js
} else if (totalBlocks) {
  blocksList.hidden = false;   // ← 旧目录树
  ... openSplitReport();       // ← 面板（产物阶段里已经有目录）
```

于是同一份目录在**面板内外各出现一次**（用户反馈："界面下方为什么还展示目录"）。

### 改法
- 目录已由面板「产物」阶段承载 → **面板存在时一律收起下方旧目录树与空态**
  （`hideLaneTreeWhenPanelShows()`，在 `loadExistingBlocks` 的已生成分支 + `openSplitReport` 入口都调用，幂等）。
- 面板移除后会重新拉取（`openSplitReport` 路径），不会出现"树也不见了、面板也没有"的空档。

### 实测
```
treeVisible=false（.block-card 仍在 DOM 但已隐藏）✅  面板「产物」阶段正常展示目录 ✅  __bootErrors=[]
```

---

## 十四、顺手抓到的两个**真 bug**（评审链路）

### 14.1 合规评审从未跑起来（严重）
```
本次运行失败：BaoZhengAgent.review() got an unexpected keyword argument 'chain_by_block'
```
- `orchestrator.nodes._run_review` 对**技术/合规两个 agent 传同一套参数**（含 `chain_by_block`），
  但只有 `WangAnshiAgent.review` 收了它 → 包拯直接 `TypeError` → **整个评审节点失败**、
  运行被标 failed、`reviews` 表始终 0 行 → **评审页永远显示"未评审"**。
- **为什么单测没抓到**：`tests/orchestrator/test_nodes.py` 里的 `_RecordingReviewer` **桩**自带
  `chain_by_block`，把契约漂移掩盖了。

修法：`BaoZhengAgent.review` 接收 `chain_by_block` 并**真正使用**（评分口径 + 规范要求进 prompt，
与技术评审同源）；`build_compliance_review_user` 增加 `criterion` / `spec_requirements` 参数。
新增 2 条**用真实 agent 类**的契约测试（不再用桩）。

**实测（真实调用）**：
```
修复前：workspace-action(review) → 运行 failed，reviews=0
修复后：workspace-action(review) → reviews=2（bao_zheng 94 / wang_anshi 76）
        verdict：得分预估 5.0/100 · 部分满足 1 · 缺口 5
        「技术条款差异性 10 分 7 章 部分满足 → 5.0 分」
```

### 14.2 `failed` / 陈旧运行期 stage 不允许重试
运行失败后 `workspace_action` 一律 409（"请先完成大纲和素材确认，或等待当前运行结束"）——
**失败正是最该能重试的状态**。改为 `failed` + 运行期 stage 在"没有 task 在跑"时都按可恢复处理。

### 14.3 闸门态被标成 `done`
`_sync_stage(graph_done=True)` 会把停在闸门的运行写成 `done`（实测 `report_review` → `done`），
于是界面显示"完成"而其实在等用户确认。改为：`paused / points_review / outline_review /
materials_review / report_review` 都保持原值。

### 如实说明一个未解项
checkpoint 的 `errors` 是**累加**语义（reducer），patch 传 `[]` 清不掉 → 重试成功后 `/state.errors`
里仍留着上一次的报错。前端只在 `stage === 'failed'` 时渲染失败横幅，所以**不会误导用户**；
但状态数据本身不干净（要彻底清需要另设一次性重置键）。

---

## 十五、按用户要求去掉状态栏模块

### 用户判断
那条状态栏（`本次运行失败：…` + 进度条 + 进度环 + 动作 + 三个工具）**与现在的五阶段面板逻辑重复**，
要求去掉。

### 处理（区分"重复信息"与"必需能力"，后者不丢）
| 原状态栏里的东西 | 处理 | 去哪了 |
|---|---|---|
| 失败/阶段文案（技术报错原文） | **删除** | 原因保留在「运行详情」里 |
| 进度条 + 进度环 + `N/M` 计数 | **删除** | 面板头 `#sr-run-state` 只在真在跑时显示一句（"已提炼 33/121 章节"） |
| 动作按钮（取消 / 确认目录 / 继续提炼 / 重新提炼 / 重新拆分目录…） | **保留** | 由 `mountStatusActions()` 把 `#status-actions` **挂进面板头**——动作只有一处，条件逻辑零改动 |
| 三个工具（处理待办 / 版本记录 / 运行详情） | **保留** | 移到**顶栏**（项目级工具，不属于阶段动作） |
| 空壳容器 | 隐藏（`#status-bar{display:none}`） | — |

### 实测
```
statusBar display=none、高度 0 ✅  动作区父节点 = sr-head-actions ✅
面板头动作：[重新提炼 | 重新拆分目录 | 继续提炼（88 节）] [重新拆分评分点]（无重复）
顶栏工具：处理待办 / 版本记录 / 运行详情 ✅   __bootErrors=[] ✅
```
截图：docs/screenshots/statusbar-removed.png

---

## 十六、按用户要求删除面板头右侧按钮组

### 用户判断
面板头右侧那组按钮（重新提炼 / 重新拆分目录 / 继续提炼（N 节） / 重做 ▾）多余。

### 处理
- **面板标题「拆分评分点」删除**：面板已承载五阶段，这个名字既过时又与左竖条重复
- **右侧按钮整组删除**：`重做 ▾` 菜单 + 由 `mountStatusActions()` 挂进来的状态动作区
  （动作仍渲染在已隐藏的状态栏 DOM 里，逻辑不动；只是不再露出）
- **阶段内容头改为实时状态**（与左竖条互补而非重复）：
  | 阶段 | 左竖条（定位） | 内容头（实时状态） |
  |---|---|---|
  | ① | 评分点 · 拆商务/技术/规范书 | 商务评分点 8 · 技术评分点 8 · 技术规范书要求 153 |
  | ② | 待确认 · 确认后进入产物 | 待确认 N 项；确认完才会输出产物 |
  | ③ | 产物 · 目录 · 映射矩阵 | 目录 N 章 · 映射 M 条 |
  | ④ | 编写 · 按章 · 按需举证 | 已写 x / y 章 |
  | ⑤ | 评审 · 按评分点把关 | 得分预估 a / b · 缺口 n 项 |

### ⚠️ 如实告知：有 4 个动作因此失去 UI 入口
| 动作 | 是否还有入口 |
|---|---|
| 「继续提炼」（resume-extract，**唯一**能接着跑完被暂停的提炼） | ❌ 无（编辑器里的「继续生成」是生成阶段的事） |
| 「重新提炼」 | ❌ 无 |
| 「重新拆分目录」 | ✅ 面板「待确认」阶段的「确认并生成目录」会重新派生 |
| 「重新拆分评分点」 | ❌ 无 |

**建议**：若之后需要，把「继续提炼」作为**唯一**的上下文主按钮放回面板头（只在真有暂停/闸门时出现）——
这样既保留入口，又不回到"一排按钮"的状态。要恢复我随时可加。

### 实测
```
面板头 = 「左侧选阶段：…」+ 运行状态（空）✅   headButtons = 0 ✅
内容头 ① = 商务评分点 8 · 技术评分点 8 · 技术规范书要求 153（= 实时状态，非重复）✅
__bootErrors=[] ✅
```

---

## 十七、按新信息架构清理「提炼」这个动作与用词

### 用户判断
> 按照现在的信息架构，不存在提炼的动作了。

同意：新 IA 只有 **评分点 → 待确认 → 产物 → 编写 → 评审** 五阶段。
逐章抽取（8 字段）是 产物→编写 之间的**内部准备环节**，既不该有按钮，也不该出现在文案里。

### 处理
1. **用户可见文案里的「提炼」全部替换**（前端复查为 0 处）：
   | 旧 | 新 |
   |---|---|
   | 确认目录，开始提炼要点 | **确认目录** |
   | 提炼要点中（x/y） | **编写准备中（x/y）** |
   | 提炼到一半 | 编写准备到一半 |
   | 提炼完成，待确认内容 | 编写准备完成，待确认内容 |
   | 张衡提炼章节要求中… | 正在准备编写依据… |
   | 已提炼 x/y 章节 | 已准备 x/y 章节 |
   | 重新提炼 / 继续提炼 | （动作已从界面移除） |
   | 「要求与大纲」页（页名已不存在） | 面板 |
2. **不给按钮，改成系统自动接上**：新增 `POST /api/workflow/{tid}/resume-auto`，
   后端按图的 `next` 决定续跑哪一步（extracting→resume-extract / matching→rerun-match /
   generating→resume-generation / reviewing→skip-to-review）；页面加载时调用一次。
   - 只在**没有 task 在跑**时生效；
   - **用户主动取消（paused）不自动续跑**（尊重意图，也避免白烧额度）；
   - 已跑完 → 返回 `nothing_to_do`。

### 实测
```
接口：已完成运行 → {"resumed":"nothing_to_do"} ✅   未知线程 → 404 ✅
页面：document.body.innerText 里已无「提炼」✅   面板头 0 个按钮 ✅
      head = 左侧选阶段：评分点 → 待确认 → 产物 → 编写 → 评审
      内容头 ① = 商务评分点 8 · 技术评分点 8 · 技术规范书要求 153
测试：新增 test_resume_auto_only_acts_on_preparation_stages（钉住"只在无 task 时生效 +
      paused 不续跑 + 四个可续跑阶段"）
后端 764 passed / 前端 102 passed
```
截图：docs/screenshots/ia-no-extract-term.png

---

## 十八、每个标包是单独的内容（按标包确认 → 五阶段针对该标包）

### 用户要求
> 选择完标包之后，需要有一个按钮确认，然后根据该标包的内容进行后续的五个步骤，
> 每一个标包是一个单独的内容。

### 原来的问题（数据模型层面）
| 事实 | 后果 |
|---|---|
| `workflow_runs` **没有标包列** | 一条运行无法归属/检索到标包 → 前端无法"按标包找运行" |
| `purge_blocks_except_run(keep=None)` 在 `runner.start` 里**清全项目的 blocks** | 跑标包 1 会把标包 2 已拆好的目录**清掉** —— 与"每个标包独立"直接冲突 |
| `projects.scope_*` 只有一份 | 项目上只记得最后一次的范围 |

### 改法
**后端**
1. `workflow_runs` 增加 `target` / `package_no`（幂等 ALTER），`create()` 记录归属；
   新增 `set_scope()` / `latest_for_scope(project, target, package_no)`。
2. `runner.start(config)` 读 `config.target/package_no` 写进运行，并新增
   **`purge_scope_blocks()`** —— 只清理**同一标包**的旧 blocks（跨标包互不影响）。
3. `PUT /projects/{id}/scope` 现在**同时把当前运行归属到该标包**（确认标包时调用）。

**前端**
4. 筛选行加「**确认标包**」按钮 + 已确认状态（`已确认：标的1 / 标包2`）。
5. **未确认 → 不出阶段内容**，只给一句指引（避免把默认范围/别的标包的内容误当成这个标包的）。
6. 点确认：写范围（含运行归属）→ 该标包**有运行就切过去**、没有就**为它新建一条**
   （带 `target/package_no` + `points_only` 先拆评分点）→ 面板重开在①评分点。
7. 确认结果写进 URL（`?pkg=标的1|2`）+ localStorage，刷新后仍记得。

### 实测
```
场景 A（未确认）：hasGate=true、rail 不渲染、确认按钮在、状态文案 = 选好标包后点「确认标包」… ✅
场景 B（?pkg=标的1|2 且该标包已有运行）：hasGate=false、rail 在、状态 = 已确认：标的1 / 标包2
       阶段① 实时状态 = 商务评分点 8 · 技术评分点 8 · 技术规范书要求 153，16 张卡片 ✅
__bootErrors=[] ｜ 后端 765 passed / 前端 102 passed
```

### 未验到的路径（如实说明）
**"该标包还没有运行 → 新建一条"** 这条没在真机上点过（会真跑一次解析+拆分，有成本）。
逻辑已由后端测试覆盖（运行按标包归属/检索、跨标包不串），但真机路径待一次实际点击验证。

### 顺带修掉一个测试隐患
新加的用例失败时没走到 `await conn.close()` → aiosqlite 后台线程吊住解释器，
表现为**整个测试套件卡住不退出**。改成 fixture（yield + close）后套件恢复 16.7s。

---

## 十九、材料条的信息架构重做（简洁 + 真实）

### 核实到的两个问题
1. **提示是假的**：材料条写着「可一次选多个；上传即识别类型」，但代码里
   `fileInputReq.files[0]` **只取一个文件**，且**完全没用** `inferRoleFromFilename`。
2. **信息密度高 + 两个类型入口**：第一行同时有分组计数、上传前的类型下拉、长提示、管理入口；
   第二行每个文件还各带一个类型下拉。默认两行都占着高度（116px）。

### 改法（摘要 + 按需展开）
```
材料 2 · 技术规范书 1 · 招标文件 1     [＋ 添加文件] [明细 ▾]     多选 · 类型按文件名建议，可在「明细」里改
──────────────────────────────（点「明细」才展开）──────────────────────────────
[DOCX] 4_高可靠技术专… [技术规范书▾] 235 KB ✕    [PDF] 1_广东电网有限责… [招标文件▾] 5.7 MB ✕
```
1. **第一行只留摘要 + 两个动作**：计数/分组构成 + `＋ 添加文件`（主）+ `明细 ▾`（次要，展开文件列表）。
   上传前的类型下拉从界面移除（保留在 DOM 里兼容旧代码）。
2. **明细默认折叠**（`collapsed`），点「明细」展开成一行、再点收起；文件多时行内仍可换行。
3. **让提示成真**：文件选择框加 `multiple`，逐个上传；类型用 `inferRoleFromFilename`
   **按文件名建议**（识别不出落回兜底），可在明细里逐个改。
   ⚠️ 实测推断会误判 —— 例如 `1_广东电网…技术招标文件.docx`（其实是主招标文件）被判成
   `spec`（因为文件名含"技术招标文件"）。所以文案从"自动识别"改为"**建议**"，并保留逐文件修改。

### 实测
```
折叠：strip 高度 116px → **56px**；collapse=true、明细 display:none ✅
展开：点「明细」→ 明细可见、按钮变「收起 ▴」、高度 116px ✅
多选：file-input-req.multiple = true ✅
推断：'4_高可靠…技术规范书.docx' → spec ｜ '招标公告.docx' → null（落回兜底）✅
__bootErrors=[] ｜ 后端 765 passed / 前端 102 passed
```

### 自己引入又当场修掉的一个 bug
`inferRoleFromFilename` 返回的是 `{role, reason}` **对象**（不是字符串）—— 我第一版直接
`role = inferRoleFromFilename(name) || fallback`，会把对象写进角色字段。已改为取 `inferred.role`。

---

## 二十、修复：从项目列表进来直接落到评审页（没走五步流程）

### 核实
`restoreSettledThread()` 里有三处**整页跳转**：`report_review` / `done` → `location.href = /review`；
生成阶段暂停 → `/review`；素材匹配入口的"返回质量检查" → `/review`。
所以从项目列表点进来（`/three-mode?projectId=X`）时，一旦运行已到评审/完成，
页面就被搬到**独立的评审页** —— 那正是用户看到的"没走到五个步骤页面上"。

### 改法
1. **三处整页跳转全部去掉**（在线资产里 `location.href = /review` 已 0 处）：
   - `report_review` / `done` → 留在面板，`panelStage='5'`（评审就是第⑤步，同一套壳）
   - 生成阶段暂停 → 留在面板，`panelStage='4'`（继续撰写）
   - 素材匹配返回 → 直接落面板⑤
2. **按运行阶段对齐面板**（新增 `syncPanelStageFromRunOnce()`，进去后一次性）：
   读后端权威阶段 → 映射到第几步 → 对齐。解决"boot 的几条路径各自把面板停在①"的问题。
   ```
   points_review/outline_review → ① ｜ extracting/matching/materials_review/paused → ③
   generating → ④ ｜ reviewing/report_review/done → ⑤
   ```
3. 首访没有记住标包时：沿用**最新运行所属的标包**；只有一个标包时自动采用（多标包仍需确认）。

### 实测
```
/three-mode?projectId=26&pkg=标的1|2 →
  url 仍是 /three-mode（不再跳 /review）✅
  stage = ⑤评审、rail 在、无门控、KPI = 5 / 100
  verdict：技术条款差异性 10 分 7 章 部分满足 5 分；技术方案 30 分 13 章 未评审 待评 ✅
__bootErrors=[] ｜ 后端 765 passed / 前端 102 passed
```

### 顺带说明「状态同步失败：Failed to fetch」
来自 `workspace.js` 的 `refreshWorkspace()` 抓取失败（我验证时反复重启后端会触发），
**会自动重试**（busy 4s / 空闲 10s），不是数据问题。它插在页头下方、样式显眼（`.ws-error`），
若觉得打扰可以：连续失败 N 次才显示，或降级成小角标（未做，等你定）。

---

## 二十一、真正的入口在项目列表页（上一处修复没覆盖到）

### 核实（第二次）
上一轮我修的是**面板内部**的三处 `/review` 跳转。用户反馈"从项目列表点进来还是评审页" ——
真正的原因在 **`projects.html` 自己的按阶段分流**：
```js
case 'reviewing': case 'report_review': case 'done':
  location.href = `/review?threadId=${enc}${backendQ}`;   // ← 直接跳质量检查页，且没带 projectId
```
（这个 switch 上方的注释还写着"统一进三模式工作台"，但评审那几个 case 是旧逻辑遗留。）

### 改法
1. **`projects.html`**：`reviewing / report_review / done` 也走 `threeMode('outline')`；
   面板自己按运行阶段定位到⑤评审。在线资产里 `/review?` 跳转 **0 处**。
2. **标包兜底顺序**（避免从列表进来先撞「先确认标包」门控）：
   ① 老运行没有标包归属时，用**项目级范围** `projects.scope_*`（这个项目一直在做的标包）；
   ② 只有一个标包时用它；③ 多标包且项目范围为空 → 仍要用户确认（正确）。
3. **`/review` 页加「← 作业面板」**（带 projectId/threadId/pkg）—— 已经在评审页的人一键回路；
   否则停在 `/review` 上刷新永远是那一页（用户反馈的"刷新还是旧的"）。

### 实测（复现用户路径：项目列表 → 点项目）
```
project list 生成的地址 = /three-mode?projectId=26&threadId=…&backend=done&view=outline
落点：url=/three-mode ✅  rail 在 ✅  stage=⑤评审 ✅  无门控 ✅
      「已确认：标的1 / 标包2」✅  KPI 5 / 100 ✅  __bootErrors=[] ✅
```

---

## 二十二、去掉「按运行阶段定位」，阶段交回用户

用户要求：「按运行阶段定位，这个逻辑可以去掉了」。同意 —— 阶段应由用户自己点，
自动定位会让"我点了确认标包，怎么跑到评审去了"这种意外（实测）。

### 删除
- `syncPanelStageFromRunOnce()`（进项目后按后端 stage 自动跳到③/④/⑤）+ `panelStageSynced` 标志
  + 它在 `loadExistingBlocks` 末尾的触发
- `restoreSettledThread` 里 `panelStage='5'/'4'` 的指定 → 留在面板但**从①开始**

### 保留
- "不要跳到 /review 独立页"这条（面板内三处 + 项目列表的按阶段分流都已去掉整页跳转）
- `/review` 页保留「← 作业面板」链接（非自动重定向：重定向会与「处理待办」冲突）
- 半途进度补到筛选行右侧：`已准备 33/121 章节（未跑完）`（状态栏模块去掉后，这句信息没地方放了）

### 实测
```
进项目（backend=done）→ url=/three-mode、stage=①、rail 在、__bootErrors=[] ✅
点「确认标包」→ stage=①、「已确认：标的1 / 标包2」✅
筛选行右侧 = 已准备 33/121 章节（未跑完）✅
后端 765 passed / 前端 102 passed
```

---

## 二十三、检修（2026-10-14）

### 检修范围与结果
| 项 | 方法 | 结果 |
|---|---|---|
| **静态：JS 引用的 DOM id 是否还在** | 抽取 `getElementById('x')`，与 HTML + JS 模板里的 id 求差 | 19 处已不存在的 id → **逐个核实全部有保护**（`if (x)` / `x?.` / 提前 `return`）→ 无崩溃风险 ✅ |
| **运行时：各页面/视图** | CDP 打开并读 `window.__bootErrors` | 面板 / 编辑器(v=editor) / 素材库(v=matching) / 评审明细(detail=1) / 项目列表 → **全部 `[]`** ✅ |
| **后端：迁移幂等** | 同库连续跑两次 `init_db` | 不报错；`workflow_runs` 得到 `target`/`package_no` ✅ |
| **死代码** | 扫描近期新增前缀的类是否被引用 | 只有 `ui-card`/`ui-card--flat` 未被引用（组件集里预留）✅ |
| **测试** | 全量 | 后端 765 passed / 前端 102 passed ✅ |

对比参考：今天早些时候 `#btn-extract`、`#req-count` 的两处**无保护**访问让整页脚本不执行
（启动自检面板报 `Cannot read properties of null`）。现在这类引用都已加保护 —— 这条检查值得
固化成习惯：**删 DOM 之后必须扫一遍引用**。

### 检修中修掉的两个真问题
1. **技术规范书要求看不到具体要求**（用户反馈）：
   - 「要求」列渲染的是 `r.title`（章节路径），**原文 `text` 完全没展示**；
   - 该单元格还有 `.slice(0, 3)` —— **每个主题只显示 3 条**，153 条要求绝大多数看不到。
   → 改为结构化清单：主题分组行（主题 · 条数 · 来源文件）+ 逐条**原文（完整、不截断）**
   + 章节路径小字 + 「响应要求」徽标（mandatory→必须响应 / required→应响应 / informational→参考）。
2. **④编写「生成本章」在运行已停住时静默无效**：`/regen` 从已停住的检查点继续会走回原节点、
   什么都不发生。→ 分两条通道：运行中走 `/regen`；已停住（paused/done/materials_review…）
   走 `workspace-action(action=revise)`（从确定入口重入，只针对选中章节）。

### 未修的遗留（需决策）
1. **素材文件丢失**：`materials` 行写 `parse_status='done'`，但 `backend/uploads/` 目录不存在
   → 界面没有"文件已丢失，请重新上传"状态；按需素材检索也因此无 chunk 可搜。
   建议：素材列表加**文件存在性校验**（标红 + 一键重传）。
2. **`checkpoint.errors` 累加不清**（reducer 语义）：前端只在 `stage==='failed'` 时渲染失败横幅，
   不误导用户，但数据本身不干净。
3. **「状态同步失败」横幅**对一次网络抖动反应过度（整条红条）。建议连续失败 N 次才显示，或降级成小角标。
4. **旧页文件仍在磁盘**（`project-init.html` / `workbench.html`，带重定向）：入口已清零，
   真正下线需要删文件。
5. **项目 26 的端到端链路**仍停在「准备 33/121 + 正文 5 章」，未走完 ⑤评审全量。

---

## 二十四、确认后要能查到确认结果（用户反馈）

### 问题
待确认面板的 ③ 技术规范书主题归属只从**还没作答的项**（`pending`）里渲染：
```js
pending.filter(c => c.answer_key === 'topic_placement').map(confItem)
```
一旦确认，该项不再 `pending` → **整块消失**，只剩兜底文案「没有未归属的主题。」
（而 `answers.topic_placement` 里明明存着 19 个主题 → 章节的映射）。用户："
确认进入对应目录后需要能够查到确认的结果情况，目前确认完直接不展示了"。

### 改法
1. 确认面板改为渲染**全部确认项**（`allConfs`），不再只渲染 pending：
   - 未确认 → 控件（原样）
   - **已确认 → 展示结果**：③ 主题归属给出「技术规范书主题 → 归属章节」表格
     （来自 `answers.topic_placement`；未指定的显示"默认：技术规范书要求应答"）；
     其他项展示 answer 摘要；每项都给一个「**修改**」按钮展开控件
2. 「修改」按钮：`confirmEditOpen[key]=true` → 重开面板到②，控件重新出现，
   且主题下拉**预选当前值**（点修改能看到现状，不是空白）

### 实测
```
② 待确认：8 个确认项全部展示（徽标：已确认 ×3 / 已识别 ×4 / 待确认 ×1）
③ 技术规范书主题归属：[已确认] 规范书要求主题该挂到哪些章节？
   「19 个主题、153 条要求；6 个评分点名下没有挂到要求」
   ┌ 技术规范书主题            │ 归属章节 ┐
   │ 1.4.1组织保证             │ 技术方案可行性 │
   │ …（19 行）                 │ …             │
   「修改」按钮 ×3（已确认项各有）✅   __bootErrors=[]
```
截图：docs/screenshots/confirm-shows-result.png

---

## 二十五、两级左导航对齐（用户反馈「这个 UI 没有对齐」）

### 核实
量了两组导航的几何（CDP）发现**规格不一致** —— 嵌套两步导航是我自己另写的一套：

| | 顶层 `.sr-rail-item` | 嵌套 `.sr-sub2-item`（修前） |
|---|---|---|
| 圆点 | `.sr-dot` 20×20 | `.n` **18×18** |
| gap | 9px | **8px** |
| padding | 10px 14px | **8px 12px** |
| 连接线 left | 23px | **20px** |
| 标签字号 | 13px | **12.5px** |

差一点点，叠起来就是肉眼可见的"没对齐"。

### 改法：两级**共用同一套规则**（而不是再抄一份调好的数值）
- 嵌套导航的内部 class 改成与顶层相同（`.sr-dot` / `.sr-lab` / `.sr-sub`）
- CSS 里把两组选择器写在一起：
  ```css
  .sr-rail-item,.sr-sub2-item{…gap:9px;padding:10px 14px}
  .sr-rail-item:not(:last-of-type)::after,.sr-sub2-item:not(:last-of-type)::after{…left:23px…}
  .sr-rail-item.on .sr-dot,.sr-sub2-item.on .sr-dot{…}
  ```
  以后改一处，两级都跟着走 —— 不会再出现"差一点点"。

### 实测（CDP 量几何）
```
顶层   dot=[47,20]  lab.x=76   pad=14px  gap=9px  连接线=23px
嵌套①  dot=[235,20] lab.x=264  pad=14px  gap=9px  连接线=23px
嵌套②  dot=[235,20] lab.x=264  pad=14px  gap=9px  （末项无连接线 ✅）
圆点 20×20、标签偏移 = 圆点 + 20 + 9 —— 两级完全一致 ✅
```

---

## 二十六、删除章节两个 bug（用户反馈「确认按钮没显示 / 点了确认没删除」）

### 核实到的三个独立问题
| 现象 | 根因 |
|---|---|
| 弹窗里「确认」按钮**看不见** | `workspace.css` 的 `.ws-dialog button{background:var(--panel)}` 是**后代选择器**（特异性 0,1,1），压过 `.ui-btn--primary`（0,1,0）→ 背景被刷回白色，而 `color:#fff` 仍生效 → **白底白字** |
| 点确认后**目录没变化** | `removeOutlineSubtree()` 返回 **`{nodes, removed}` 对象**，面板当成数组用 → `Array.isArray` 为 false → `saveStructureFromPanel` **静默 return** |
| 服务端删了、界面还显示 | `applyStructureToModel` 只**更新**存在的项，不删除已移除的项 → `blocksById` 里残留被删节点，重绘又画出来 |

### 改法
1. 弹窗按钮用同等特异性覆盖：`.ws-dialog button.ui-btn--primary{background:var(--accent);color:#fff}`
   （外加 danger 变体）。实测按钮变成 `color:#fff / background:rgb(37,99,235)` ✅
2. 按契约解构：`const { nodes: next, removed } = removeOutlineSubtree(...)`，并把删除节数写进提示
   「删除 1 节 · 已保存：改名/调整 120 · 删除 1」✅
3. `applyStructureToModel` 增加**同步删除**（`blocksById` 里不在新结构中的键删掉）→ 界面行数正确变化 ✅
4. **静默 return 改成响亮失败**：`saveStructureFromPanel` 收到非数组时 `console.error` + 界面提示
   「内部数据结构不对」—— 同类"点了没反应"已经吃过两次亏（另一次是接口 200 静默重建目录）

### 新增契约测试
`outline-structure.test.mjs`：钉住 `removeOutlineSubtree / moveManyOutlineNodes /
indentManyOutlineNodes / outdentManyOutlineNodes` 返回的是 **`{nodes, …}` 对象**而不是数组。

### 实测
```
点删除 → 弹窗按钮 color=#fff / bg=rgb(37,99,235)（可见）✅
点确认 → 请求 {"created":0,"updated":120,"removed":1} ✅
        界面 121 → 120 行、被删行消失 ✅ 提示「删除 1 节 · 已保存：改名/调整 120 · 删除 1」
还原（原始目录 POST）→ created:1 / updated:120 → 121 行 / 5 章正文 ✅
后端 768 passed / 前端 124 passed
```

---

## 二十七、左导航连接线穿进圆圈（用户反馈）

### 根因
连接线写的是死值 `top:30px`，但**圆点在行内垂直居中**、行高随副标题行数变化：
一行标签时行高 ≈44px（圆点 12–32），两行时 ≈60px（圆点 20–40）——
于是 `top:30px` 就落进圆圈里，看起来"线从圆点中间穿出来"。

### 改法：相对中线定位（不依赖行高）
```css
.sr-rail-item:not(:last-of-type)::after,.sr-sub2-item:not(:last-of-type)::after{
  top:calc(50% + 12px); bottom:-14px; }   /* 圆点 20px → 中线 +10 是下沿，再留 2px 间距 */
```

### 实测（CDP 量 dot.bottom 与线顶的距离）
```
顶层 5 项：gapBelowDot = 2,2,2,2（最后一项无连接线）
嵌套 2 项：gapBelowDot = 2（最后一项无连接线）
线宽 1.5px ✅ —— 线与圆点不再相交
```

---

## 二十八、危险确认框的默认焦点（发现并修掉一个真实隐患）

### 经过
批量删除做完后做收尾校验，发现目录从 121 行变成 **113 行**（少的正好是我恢复时新建的 8 行）。
日志看不出是哪次请求；于是做**受控实验**：还原 → 只打开面板（不点任何东西）→ 仍是 121 行 ✅
→ 说明**页面加载不会丢行**，问题出在"弹窗被确认了"。

### 根因（真实隐患，不只是测试问题）
`uiConfirm()` 里我写的是：
```js
dialog.querySelector('[data-ok]').focus();   // 把焦点默认给了「确认」
```
`showModal()` 的弹窗里，焦点在确认按钮上 → **按一次 Enter 就直接执行了危险操作**。
我的自动化截图流程里正好带了 Enter/默认按键 ✗ —— 于是"只是截图"就删掉了几行。

对用户同样成立：批量删除的弹窗一出现，误按 Enter 就删了。**危险操作不该有"默认执行"的入口。**

### 改法
```js
// 危险操作把默认焦点给「取消」，普通操作给「确认」
(danger ? dialog.querySelector('[data-cancel]') : dialog.querySelector('[data-ok]')).focus();
```

### 状态
最终校验：121 行 / 缺失 [] / 5 章正文 ✅（结构与正文与事故前一致）
后端 768 passed / 前端 127 passed

---

## 二十九、去掉「最多 4 级」的限制

用户要求：「级数限制不要仅限于四层…不要限制层级」（每次只能降一级这条保留）。

### 改动
| 位置 | 原来 | 现在 |
|---|---|---|
| 后端 `domain/spec/structure.py` | `MAX_LEVEL = 4` | `MAX_LEVEL = 12`（**只是挡异常数据**，不是产品限制） |
| 前端 `workspace-model.mjs` | 6 处 `Math.min(4, …)` + `level >= 4` | 统一用导出常量 `MAX_LEVEL = 12` |
| `indentOutlineNode` 守卫 | `level >= 4` | `level >= MAX_LEVEL` |
| `insertChildOf` 守卫 | `level >= 4` | `level >= MAX_LEVEL` |
| `levelRangeAt`（Notion 式拖动的最深限制） | `min(4, 前一个节点+1)` | `min(MAX_LEVEL, 前一个节点+1)` |
| 结构行渲染 | `l${Math.min(level,3)}`（>3 级都画成 l3） | **按层级动态 `padding-left`**（`10 + (level-1)*16`），≥3 级弱化色 |
| ④编写 章节列表 | 同上 | 同上（`8 + (level-1)*16`） |
| 拖动缩进常量 | 18px | 16px（与行缩进对齐） |

保留不变的规则：**每次只能降一级**（`clamp_levels` 的"不得跨级"）—— 这是层级语义，
不是数量限制；缩进仍要求"上一个节点是同级"（即成为前一个同级兄弟的子级）。

### 实测
```
POST 一条 1..7 级的合法链 → {"created":0,"updated":121,"removed":0}
库里层级 = [1,2,3,4,5,6,7,…]  ✅（4 级上限已不存在）
面板渲染：data-level = 1..7，padding-left = 10/26/42/58/74/90/106px ✅ 阶梯正确
还原原始目录 → {"created":0,"updated":121} → 121 行 / 5 章正文 / 最高层级回到 4 ✅
后端 768 passed / 前端 127 passed
```

> 注：改完后端常量后**必须重启 uvicorn** —— 第一次验证时库里仍是 `[1,2,3,4,4,4]`，
> 因为进程还在跑旧代码（不是逻辑错，是没重启）。

---

## 三十、面板上方留白收紧 + 一次成批删除的排查

### 留白（用户反馈「上面留白的上下间距缩短一点」）
量出来是**三个值叠加**：材料条 `margin-bottom 10px` + `#blocks-scroll padding-top 20px`
+ `#split-panel-inline margin-top 8px` = **38px**。
改：`blocks-scroll` 内边距 `20px → 10px 20px`；面板上边距 `8px → 0`。
结果 **gap 38 → 20px** ✅（`.sr-two` 自己的 8px 间距保留 —— 那是我第一次改错对象，已还原）。

### ⚠️ 排查：一次成批删除（未完全定位，如实记录）
收尾校验时发现库里的行数从 **121 → 99**（少了 22 行），少的正好是"库里存在、
但**提交的列表里没有**"的那些。日志里**没有任何 resume / sync 请求**，
删除全部来自 `POST /outline-structure`（也就是**面板自己的保存**）——
说明某个时刻面板的节点列表比库里少，保存时把差额当成了删除。

- 已排除：单纯打开面板 10s 不会丢行（受控实验，仍 121 行 ✅）
- 未能定位：究竟是哪一次点击/加载让面板列表变少
- 已加**服务端告警**：一次保存移出的节点超过 `max(5, 20%)` 时 `logger.warning`
  打出"原有 N → 提交 M"与被移出的 block_id 列表 —— 不阻塞合法批量删除，但留痕
- 数据已用原始目录恢复：**121 行 / 5 章正文** ✅

**建议**（下一步该做的）：前端保存前**先核对节点数与最近一次加载是否一致**（不一致就先重载再保存），
从根上避免"列表比库少 → 把差额删掉"。这比服务端拒绝更准（不会误伤合法的批量删除）。

---

## 三十一、「原地往右拖一级不生效，刷新才到下一级」（用户反馈）—— 根因找到并修复

### 复现
勾选 3 行 → 往右拖一点（水平位移表示"降一级"）→ 蓝色指示线与标签都正确显示
「成为子级（第 4 级）」，**松手后没有任何请求**，层级不变；刷新后才看到已经变了。

### 根因
`saveStructureFromPanel` 的上游：
```js
const next = ids.length > 1 ? moveOutlineNodesToLevel(nodes, ids, target, lvl) : …;
if (!next) return;            // 静默 return
```
而 `moveOutlineNodesToLevel` 内部先调 `moveOutlineNodesTo(...)`：
**位置没变时它返回 null**（"位置与层级都没变"的判定），我把这个 null 当成了"非法拖动"，
于是**整条保存路径被丢弃** ✗ —— 而"原地往右拖一级"恰恰是**位置不变、只改层级**，
是这个交互最常用的用法（我的第一次拖动测试也是这样被吞掉的）。

### 改法（模型层，有 node --test）
把"位置"与"层级"解耦：位置部分返回 null（原地）**不再是错误**，只要层级有变化就返回新结构。
```js
const reordered = moveOutlineNodesTo(nodes, ids, targetIndex) || nodes;   // null = 原地，允许
… if (delta === 0 && reordered === nodes) return null;                    // 真的什么都没变才 null
```
单个版本 `moveOutlineNodeToLevel` 同样处理。新增 3 条用例钉住"位置不变、只降一级"。

### 实测（真机 pointer）
```
勾选 s2.1.2 / s2.1.3（都是 L3）→ 往右拖 4 格
  拖动中标签 =「成为子级（第 4 级）」
  松手 → 发出保存 ✅ 响应 {"created":0,"updated":121,"removed":0}
  界面 **立即** 变成 L4 / L4 ✅（不用刷新）
  提示「拖动 2 项（第 4 级） · 已保存：改名/调整 121」
还原层级 → {"created":0,"updated":121} → 121 行 / 5 章正文 ✅
```

### 同时补上的安全校验（对应上一节那次成批删除）
`saveStructureFromPanel` 现在做**保存前一致性校验**：
- 记录 `removedByUser`：只有**用户明确删除**过的 id 才允许从列表里消失
- 若少了"没被用户删过"的节点 → 判定本地列表不可信，**拒绝保存** + 自动重载 + 明确提示
  （「本地目录与服务器不一致（少 N 节），已重新加载，请重试」）
- 服务端另有告警：一次保存移出 > max(5, 20%) 节点时 `logger.warning` 留痕

> 调试期间库被削过两次：**-8 行**、**-33 行**（都是"面板列表比库里少，保存把差额删了"）。
> 两次都已用原始目录恢复（121 行 / 5 章正文），并补上了上面两道闸。

---

## 三十二、撤销（用户要求）+ 数据事故的**闭环修复**

### 撤销
- 「目录确认与调整」操作行加 `↶ 撤销`（无历史时置灰，title 显示将撤销哪一步）
- 快捷键 **Cmd/Ctrl+Z**（在输入框里不拦，避免抢掉改标题的原生撤销）
- 每次保存**之前**把"完整结构快照 + 操作名"压栈（模型层 `pushHistory`，最多 20 步）；
  撤销 = 再存一次那份快照（`fromHistory: true`，自身不再入栈）
- 撤销"删除"时会把恢复的 id 从 `removedByUser` 移出（否则它仍被当作"允许消失"）
- 实测：`L3 → 缩进 → L4 → 撤销 → L3` ✅ 提示「撤销「缩进」 · 已保存：改名/调整 121」

### 数据事故的闭环（前几节反复出现"库被削掉 8 行 / 33 行"）
**机制**：`POST /outline-structure` 会用提交的节点**重建 `spec.toc`**；提交方（面板）列表
一旦比库里少，差额先被当删除（削库），toc 也随之变小；之后**页面加载触发 resume-auto**，
图节点 `sync_outline_placeholders(toc)` 再按小 toc 删一轮 —— 目录被静默削掉。

**两道闸（根治）**
1. **服务端**：请求新增 `removed_ids`（客户端声明"我主动删了哪些"）；
   `runner.update_outline_structure` 校验 `库里 - 提交 - 声明` 若超过 `max(5, 20%)` → **400 拒绝**
2. **客户端**：`saveStructureFromPanel` 保存前比对本地模型，少了"没被用户删过"的节点就
   **拒绝保存 + 自动重载**并提示「本地目录与服务器不一致（少 N 节），已重新加载，请重试」

**恢复与验收**
```
/tmp/recover_outline.py：blocks 88 → 121 行（有正文 5）＋ 检查点 spec.toc 88 → 121 项
重启后端 → 加载页面 12s（会触发 resume-auto，就是原来删库那条路）
  → 库仍 121 行、无缺失 ✅（反馈环闭合）
编辑 + 撤销 → 两次保存都是 {"created":0,"updated":121,"removed":0}；库 121 行 / 5 章正文 ✅
后端 768 passed / 前端 133 passed
```

> 教训（第 5 次同类）：`POST` 的响应体 200 不等于"没改数据"；**写接口的副作用要覆盖到
> "提交内容本身可能不可信"**。这次把"不可信"显式建模成 `removed_ids` 才闭环。
