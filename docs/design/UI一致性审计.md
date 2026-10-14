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
