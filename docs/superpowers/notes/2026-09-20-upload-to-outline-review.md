# 审查报告：上传要求文件 → 按提炼要求派生目录 → 目录调整

- 日期：2026-09-20
- 审查人：DSH（glm）+ 两个只读子代理，**全部结论由我独立复核**（标注了「我复现」「子代理称」）
- 范围：`上传 → 解析 → 按提炼拆分要求定位/派生 → 闸门 1 → 目录结构调整 → 确认 → 下游（提炼/导出）`
- 方法：读代码 + 在真实文件上跑解析 + 复现关键纯函数。**未改任何被审逻辑**（本轮只修了审查中发现的 3 处自己引入的问题 + 2 处交界缺陷，见 §4）

## 0.1 修复进度：**八组全部完成**（本轮）

| 项 | 状态与做法 |
|---|---|
| **F7** 重复 block_id（含 **F6** parent 语义） | ✅ `build_sections` 按真实父 id + 每父子序号生成 id；`normalize_nodes` 加 `raw_index → accepted` 映射；+4 测试（含 200 次随机形状 fuzz） |
| **A-pptx** 上传白名单 / role（+A-role/A-filename/A-task） | ✅ 后缀只留 pdf/docx/doc；`ALLOWED_ROLES` 上传即校验；落盘名 basename 净化 + uuid 前缀；**并修由此带出的路径缺陷**（后台解析/下载按 `file_path` 解析、删材料连盘删）；后台任务留强引用；+7 测试 |
| **F2** 跨文件切段 | ✅ `Section.source_file` 打通 parse→state→flat；`segment.py` 按文件分别切段；无锚点文件按章节给未选中段；+3 测试；实测 digest 只含标包2 正文、不含无关文件正文、仍列出其 20 个章节标题 |
| **F1** 要求未生效提示 | ✅ 前端 `appliedInstruction` + 输入框下方「已修改、需重新拆分」提示 + 确认目录前追问；`instructionDirty` 提成纯函数并测试 |
| **F3** 范围未命中要说清 | ✅ `diagnose_scope_miss()` 区分「无锚点 / 编号对不上」，经 `_locate_for_digest` 的 note 写进 `outline_error`；+4 测试 |
| **level** 编号判据 | ✅ 改为「点分段数 = 级别」+ 各族显式分级（章1/节2/一、·（一）2/（1）3/①4）；删除因此变死的 `HEADING_PATTERNS`、`_SECTION_NUMBER_RE`、`_looks_like_section_number`；**并修掉删除旧模式表时漏改的 `_extract_numbering_prefix`（静默 NameError，无测试覆盖）**；真实 PDF 解析结果与改前一致（16 节） |
| **F5** 截断标记 | ✅ 抢救路径带 `_truncated_nodes` → `outline_error` 写明「仅保留前 N 个章节，建议重新拆分」；+2 测试 |
| **A-parse** 单文件隔离 | ✅ 单份失败只跳过并记入 `SpecParseResult.failed_files`（全失败才抛 `RequirementParseError`）；**并修掉跳过导致 `zip(docs, sources)` 与文件名错位的连带缺陷**；闸门展示未参与的文件；+3 测试 |
| **A-ocr** 共锁 | ✅ `_ocr.py` 加线程锁（覆盖上传后台解析与工作流两条入口，此前只有前者受保护） |
| 导出与新增章节 | ✅ 删除章节时让用户选「仅移除（保留正文）」/「移除并删除正文」（`purge` 参数，全链路透传 + 测试）；**没有原文的章节（用户新增/纯目录节点）不再发模型调用**（旧行为是发空内容块，可能凭空编出应标要求） |
| 清理项 | ✅ **F9** `degraded` 契约整体删除（字段/事件/注释/测试）；**F4** 文件名定位从死链改为**确定性匹配** `named_files_in()`（连模型调用都省）+ 模型返回的 `files` 也生效；**A-order** 角色优先级排序（loader 与 `list_materials` 共用同一 SQL 片段）；**A-deadpath** 删除不可能命中的兜底；**A-doc-title** 多文件用文件名拼接 |

**回归**：后端 **557 passed**（起点 529）、前端 **44 passed**（起点 43）、`compileall` 通过、真实要求文件解析结果与改前逐项一致。

## 0. 结论摘要

链路主干是通的：要求文件按 role 取全量 → 拼接重编号 → 按提炼要求定位/切段 → 进 prompt 派生 → normalize/sections → 写 `spec.toc` → 闸门 1 可编辑结构 → 确认后 extract 读 `spec.toc`。**但有 3 个高危缺陷 + 一批中危不一致**，其中 2 个高危在「派生」这一环、1 个在「上传」。

**必须先修的三个**：

| # | 缺陷 | 后果 | 状态 |
|---|---|---|---|
| **F7** | `build_sections` 能产出**重复 block_id** | 一节被静默覆盖丢失（表/前端/matrix 三处都按 id 索引） | **我复现** ✅ |
| **F2** | 切段跨文件吞内容 | 8000 字预算被无关文件占满 → 按错误材料结构派生 | **我复现**（既有已知缺口） |
| **A-pptx** | `.ppt/.pptx` 允许上传但解析器拒绝 | 上传成功、点拆分即整次 run failed | **我核实** ✅ |

---

## 1. 高危

### F7 [高] `build_sections` 忽略 `node.parent`，合法输出即可撞出重复 block_id
`backend/domain/spec/outline_draft.py:117-143`：`last_id_at_level` 在遇到新一级节点时**不清空浅层记录**，`counters` 却清零 → 父 id 可能是上一棵子树遗留的。

**我的复现**（完全合法的模型输出，`normalize_nodes` **零告警**）：
```
输入: A(L1,B(L2,C(L3)),D(L2,E(L3)),F(L1,G(L3,parent=3))
输出 id: ['s1','s1.1','s1.1.1','s1.2','s1.2.1','s2','s1.2.1']   ← G 与 E 撞号且被挂进 A 子树
```
影响链：`sync_outline_placeholders` 按 id upsert → 覆盖一节；前端 `blocksById.set` 互相覆盖而 `blocksByOrder` 仍两条 → 卡片错乱；`extract` 的 matrix 以 id 为键 → 直接少一节。
**建议**：`build_sections` 用 `sections[node.parent].id` 作父 id，计数器改 `dict[父id, 子序号]`，并加 `used_ids` 兜底。

### F2 [高] 多文件拼接后切段跨文件（既有已知缺口，仍未修）
`infra/retrieval/segment.py:114-123` 把各章 content 直接 `"\n".join`，**丢掉文件边界**；`:159-161` 最后一段的 `end = len(full)`。
子代理复现：文件 A（有 `标包1/标包2` 锚点）+ 文件 B（无锚点）→ 选中段 **20275 字符**，其中只有 ~200 字符属实。该段以「【已定位到的材料（请据此编排章节）】+ `FOLLOW_STRUCTURE_RULE`（强制复刻材料结构）」身份进 prompt → 按错误结构拆目录。
**建议**：`parse` 给 section 带 `file_index` → `_cut` 的段末取 `min(下一个锚点, 本文件末尾)`。

### A-pptx [高] `.ppt/.pptx` 放行但解析器不支持 → 必然把 run 打挂
`routes/materials.py:39` `ALLOWED_SUFFIXES` 含 `.ppt/.pptx`；`infra/parser/__init__.py:46-48` 只分发 pdf/docx/doc，其余 `raise ValueError("不支持的文件格式")`；要求类角色上传时不解析（直接 `parse_status=done`）→ 到拆分目录时才炸。
**建议**：上传时按 role 校验后缀（要求类只允许 pdf/docx/doc），或从白名单移除 ppt/pptx。

---

## 2. 中危

| # | 缺陷 | 证据 | 影响 |
|---|---|---|---|
| **F6** | `normalize_nodes` 把 `parent` 当作**过滤后**列表下标 | `outline_draft.py:71-77`；**我复现**：空标题节点被丢后，其后 `parent=1` 的节点被降级重挂（附一条假告警） | 层级/父子错位；同级重名去重是常见路径，一旦发生会级联 |
| **F5** | 输出截断的"抢救成功"**不带标记** | `dispatcher.py:466-479` 只 `logger.warning`；`draft_outline` 的 error 只来自 normalize 告警 | 用户看到「派生完成」，实际少章节而不自知 |
| **F3** | 点名范围但认不出时**静默**退回章级定位 | `segment.py:200-207` 仅 `logger.info`；`_SCOPE_RE/_ANCHOR_RE` 不认「标包（2）/第2包/A包」，锚点强制要冒号 | 「按标包2 拆」没生效，界面零提示 |
| **F1** | 闸门 1 主按钮**不提交**编辑后的要求，回填优先级与注释不符 | `project-init.html` `confirmOutlineGate` 只提交 matrix；`restoreOutlineInstruction` 是「框里非空就不覆盖后端快照」 | 改完要求直接点主按钮 → 不生效；刷新后框里显示未生效文本 |
| **A-redraft** | `redraft`/`recover` 都跳过 parse，**新上传的要求文件被静默忽略** | `runner.py:272-283`（`as_node=NODE_PARSE` 跳过 parse）、`recover` 从 checkpoint 续 | 传了新文件点「重新拆分」→ 仍用旧 `source_toc`，无提示 |
| **A-parse** | 单份文件解析异常**终止全部**（无隔离） | `zhang_heng.py:220-231` 循环无 try/except | 规范书+评分表+评审要素里坏一份 → 全部白解析 |
| **A-ocr** | 工作流解析**绕开 OCR 串行锁** | 锁只在 `materials.py:45,59`；`zhang_heng.py:223`/`nodes.py:161` 不取 | 上传扫描件解析中点拆分 → 两路 OCR 并发（注释明说 mac arm64 易段错误） |
| **A-role** | 上传**不校验 role** | `materials.py:116` 无校验（PATCH 有） | 拼错 role 的文件入库落盘但两个面板都不显示（僵尸记录） |
| **A-filename** | 文件名**未净化 + 同名覆盖** | `materials.py:136-140` 直接 `dest_dir / file.filename` | (a) `../../` 可写出上传根；(b) 同名重传覆盖首份字节，库留两条指向同一路径 |
| **A-zero** | 单份文件解析出 **0 章节静默** | `zhang_heng.py:239-249` 空 sections 只不追加 | 用户以为该文件参与了派生 |
| **F8** | `_ground_sections` 用**全量** `source_toc`，而 prompt 只喂收窄区段 | `zhang_heng.py:313-320` vs `:349` | ⚠️ **这是 2026-09-17 的决策记录取舍**（收窄会让「没写标包号的那份文件」挂不上料），非疏漏。可选精修：定位成功时用 selected 段，未命中保持全量 |
| **level** | `detect_level_from_numbering` 错档，与字号规则打架 | **我复现**：`1. 项目概述`→L2、**`1.1 项目背景`→L4**、`1.1.1`→None、`（2）…`→L3、`（一）…`→None | 同一视觉层级得到不同 level、出现跳级。STATE.md 已列为既有缺陷 |
| **导出** | 删除但有正文的章节**仍会被导出** | `block_store.apply_outline_structure` 保留有正文的行（约定）；`routes/export.py:166-176` 取全量 blocks（只过滤 `status!='empty'`） | 页面看不到、.docx 里还在 |
| **空章节提炼** | 用户新增章节无原文，**提炼仍按"从原文提炼"发问** | **我验证**：`build_outline_extract_user(title, content="")` 渲染出 `---\n\n---`（空内容块） | 模型可能凭空编造 requirement/veto_items，且白花一次调用 |

---

## 3. 低危 / 清理项

- **F9** `OutlineDraftResult.degraded` 已成死参数（本轮改成抛异常后恒 False），`events.outline_draft` 形参与三处注释仍在描述「沿用原始目录」的旧行为 → 建议删掉或接进文案。
- **F4** 范围兜底的按文件名定位是死链路：`zhang_heng.py:425-428` 恒传 `file_names: []`，`_model_scope_keyword` 又丢弃 `files`。
- **A-order** 要求文件顺序无角色优先级（`ORDER BY id ASC`）：把旧素材改成 `spec` 会插队到最前，末尾评分表最可能被 digest 截掉。
- **A-deadpath** `main.py:93-98` 的「相对工程根」兜底永远不可能命中（`data_dir.parent / uploads/...`）。
- **A-task** `materials.py:155-163` 的后台解析 task 未保引用（可能被 GC，`parse_status` 永久停在 parsing）。
- **A-doc-title** 多文件时 `doc_title="、".join(...)` 语义含混。
- **`MIN_TOP_LEVEL_NODES = 3` 是硬门槛**：合法的小目录（按评分表只拆 2 个板块）会被判 failed，文案没说怎么办。

## 3.1 子代理提出但**我实测不成立/被高估**的一条

**[子代理称 高] PDF 10% 页边距带排除每页正文首行 → 章节标题丢失**
机制成立（`pdf.py:513-515` 在标题判据前 `if line.get("in_margin"): continue`），但**结论被高估**。我在真实要求文件（`uploads/18/…专项采购…2.pdf`，4 页、有文字层）上实测：
- 全文档 198 行，落在边距带的只有 **4 行**；
- 其中「本可判为标题」的 2 行是 `（1）投标人承诺的交付成果…` / `（2）基本符合数字化转型顶层设计…`，**字号 10.5 = 正文基线**，本来就是评分条目、不该成为章节 —— 当前跳过反而是对的。
- 子代理假设「A4 + 上边距 2.54cm」，但这与具体文档的页边距有关；本文件实测多数内容起点在 84pt 带之外。

**降级为低/中，且修法要保留"边距带里不产生章节"这层保护**（例如只对"被判定为页眉/页脚/页码"的行跳过，或要求边距带内的行同时满足字号证据才认标题）。

---

## 4. 本轮审查中我已修（5 处）

| # | 问题 | 修法 |
|---|---|---|
| 1 | `onError` 把**局部失败**（重出目录失败，上一版目录仍有效）也标成 failed 终态，与刷新后的表现不一致 | `onError` 改 async：读 `/state`，若 stage 仍是闸门 → `restoreSettledThread(tid, stage, {note: 上次操作失败：…})` |
| 2 | `SETTLED_STAGES` 漏 `failed` | 补上（否则失败线程上点主按钮要等 3 秒轮询才恢复） |
| 3 | 结构增删后右上角「N/M 模块」**计数失同步**（`statusPct` 只在 setStatusBar 里写） | 新增 `refreshStatusCounters()`，`applyStructureNodes` 调用 |
| 4 | **竞态**：结构保存未落地时点「确认目录」→ resume 先到，extract 读到旧 `spec.toc` | `confirmOutlineGate` 在 resume 前 `await structureSaveChain` |
| 5 | 新增章节的 toc 条目缺 `raw_content`/`special_marks` 键（形状与 parse 产出一致性） | `runner.update_outline_structure` 显式补空值 |

验证：后端 529 绿、前端 43 绿、`node --check` 通过。

## 5. 建议修复顺序

1. **F7 重复 block_id**（数据丢失风险，纯函数最小改动 + 单测）
2. **A-pptx 白名单/role 校验**（一条上传即打挂工作流的陷阱）
3. **F2 跨文件切段**（正对"按标包拆"的核心诉求，改动集中在 `segment.py` + `parse` 传 `file_index`）
4. **F1 要求未生效的提示**（闸门 1 加"要求已修改，需重新拆分"的脏标记）＋ **F3 范围未命中写进 `outline_error`**
5. **F6 parent 下标语义** + **level 编号判据**（同一族问题：层级可信度）
6. **F5 截断标记**、**A-parse 单文件隔离**、**A-ocr 共锁**、**A-filename 净化**
7. 导出与"删除但保留正文"的一致性（要么删正文、要么导出时跳过）、用户新增章节的提炼策略（跳过 or 让用户贴原文）
8. 清理项 F9/F4/A-order/A-deadpath/A-task/A-doc-title
