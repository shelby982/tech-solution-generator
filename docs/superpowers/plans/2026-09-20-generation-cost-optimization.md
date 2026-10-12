# 生成费用优化方案（针对费用大头）

- 日期：2026-09-20
- 作者：江河（与 DSH 协作）
- 状态：**决策已确认**（2026-09-20 二次确认）｜**M6 + M2 + M5 已实施**，M1（保留展示路线）/M3 待施工
- 依据：`docs/superpowers/notes/2026-09-20-generation-cost-analysis.md`（基于 `llm_usage` 表 183 条真实调用）+ 本轮代码复核

---

## 1. 基线与目标

### 1.1 实测基线（208-block 规模的单轮模型）

| 环节 | 调用数 | 输入合计 | 输出合计 | 占加权成本* |
|---|---|---|---|---|
| `outline_extract` | 208 | 177K | **874K** | 29% |
| `stream_generate` | 208 | 175K | 686K | 23% |
| `section_outline` | 208 | 223K | 520K | 18%（**当前近乎全废**） |
| `rerank`（估） | 208 | 894K | 125K | 11% |
| 双评审（估） | 416 | 499K | 416K | 17% |
| `outline_draft` | 1 | 1.7K | 7.4K | 0.03% |
| **合计** | **~1,249** | **≈1.97M** | **≈2.63M** | — |

\* 加权 = 输入×1 + 输出×4；**输出占 84%**。

### 1.2 目标

- **主目标**：单位可用正文字数成本下降 **≥50%**
- **硬约束**（不许牺牲的）：
  - 8 字段提炼的字段级准确率不下降（10 章人工标注基线）
  - 正文可用性不下降（评审 score 分布不劣化）
  - 截断率下降（当前 `stream_generate` 有顶到 `max_tokens=4096` 的硬截断）
  - 失败率不上升

### 1.3 验收口径

**主指标：元 / 千字可用正文** = 该次运行总费用 ÷ 该次运行导出正文字数

- 费用按 `thread_id` 从 `llm_usage` 聚合（归集列本轮已落地），单价按你的实际账单换算
- 辅指标：`reasoning_tokens / output_tokens` 占比、缓存命中率、`finish_reason=length` 占比、字段抽取准确率

> ⚠️ **前置依赖**：账户余额为 0（实测 53 次 402），**没有可用 key 就无法做真实基线与验收**。阶段 0 必须先充值或更换 key。

---

## 2. 措施总览

| 编号 | 措施 | 机制 | 预期节省 | 改动量 | 风险 | 需你决策 |
|---|---|---|---|---|---|---|
| **M1** | `section_outline` 处置 | 按需生成 + 非推理模型（或去掉，规则派生三信号） | **12–18%** | 中 | 中（影响大纲展示与补料来源） | ✅ 选哪个选项 |
| **M2** | 输出长度区间化 | prompt 从「不少于 X 字」改为「X–Y 字，超了必须收束」；`max_tokens` 只当安全网 | **9%** | 小 | 低（章节字数预期变化） | ✅ 区间取值 |
| **M3** | 判断类模型分级路由 | `call_site` → 模型档位（判断类用非推理/便宜档） | **~26%** | 中 | 中（需验证抽取准确率） | ✅ 判断档用哪个模型 |
| **M4** | `outline_extract` 批量合并 | 每批 5 节一次调用，固定前缀重复 ↓5× | 2–4% | 中 | 中（失败降级 + UI 时序） | ✅ 是否接受 UI 变化 |
| **M5** | 缓存前缀重排 | 稳定长指令前置、动态内容全部后置 | 3–5% | **小** | **低** | 无（建议随 M2 做） |
| **M6** | 修 `original_content=""` | 当前 prompt 要求「按原文逐段扩写」但原文是空的 | 质量 + 间接降本 | 小 | 低 | ✅ 是否本轮修 |

**合计预期：-50% ~ -60%**（M1/M2/M3 是主力）。

---

## 3. 措施详述

### M1 `section_outline`：先看清它的产物，再决定去留

**实测现状**：27 次调用 23 次失败（v4-pro **14/14 全失败**），失败形态是 `finish_reason=length` + `content_len=0` + `reasoning_len≈4000` —— 2500 的 `max_tokens` 被思考全部吃光，正文 0 字；**成功的 4 次输出恰好 2499–2500，同样是截断**。208-block 场景下 ≈18% 成本，当前产出为空。

**代码复核（关键）**：它的产物**没有进入正文生成的 prompt**。`_generate_tech` 生成 outline 后调 `dispatch_block_write(...)`，实参只有 `title / requirement / chunks / target_words / feedback_text` —— **outline 没传进去**（尽管 docstring 写的是「用于约束后续正文生成」）。

outline 的真实消费者只有三个：

| 产物 | 消费点 | 作用 |
|---|---|---|
| `needs_diagram` | `_DIAGRAM_PLACEHOLDER_RE` 正则扫描 outline | 标记该 block 需插图 |
| `BlockOutput.outline` | `nodes.py:611` → 工作台展示 | **唯一需要 LLM 的产物** |
| `material_requests` | `_collect_material_requests` 扫 `【待补充：xx】` | 补料闭环的一个来源 |

**四个选项：**

| 选项 | 做法 | 成本影响 | 代价 |
|---|---|---|---|
| A 修好 | `max_tokens` 2500 → 6000+ 或改非推理模型 | 从「纯浪费」变「有效支出」，**总额不降** | 无（保住大纲展示） |
| B 接进正文 | 把 outline 注入 `dispatch_block_write` 的 extra_prompt（补上原意图） | **上升** | 无 |
| C 去掉 | 三个信号改规则派生：`needs_diagram` 从 requirement 正则判；`material_requests` 从评审 issues（**已有机制**）+ `evidence_required` 派生 | **-18%** | 失去自动写作大纲展示 |
| D 按需（**推荐**） | 只对以下 block 生成：`constraint_level=mandatory` 或 requirement 较长 或 命中图纸/表格关键词；其余跳过，且改用非推理模型 | **-12~15%** | 部分 block 无大纲展示 |

**✅ 决策：选 C（去掉该步）** —— 你确认「写作大纲可以不展示」。

**落地清单**（施工时按此做，三个消费者各自替代）：

| 消费者 | 替代实现 | 风险 |
|---|---|---|
| `needs_diagram` | 用正则扫 `requirement + key_points + indicators` 是否含「架构图/拓扑图/流程图/部署图/时序图/网络图/示意图」等词 | 漏判（要求里没写但确实需要图）；建议宁可多判 |
| `material_requests` | 复用**已有的评审驱动机制**（`workspace_action` 从 review issues 的 `needs_material` 派生）；另可从 `evidence_required` / `veto_items` 派生「需提供证明材料」类请求 | 与旧来源不完全等价，补料请求可能变少 |
| `BlockOutput.outline` | 置空字符串（不展示）；`nodes.py:611` 的 `outline_text` 输出空值，前端相应位置不渲染 | 前端需确认空值渲染（应已是「无内容」态） |

- 删除/停用：`generate_section_outline` 调用点（`zhuge_liang._generate_tech`）、`_DIAGRAM_PLACEHOLDER_RE` 的 outline 依赖、`_collect_material_requests(outline)` 调用
- 保留 `generate_section_outline` 函数与 prompt 资产（可被 `D` 方案复用），只断开调用 —— 用 `ENABLE_SECTION_OUTLINE=False` 开关控制，回滚即改回 True
- 预期：调用数 −208/轮，成本 **−18%**

---

### M2 输出长度区间化（输出是 84% 的成本）

**实测现状**：`_stream_generate` 的提示词是「扩写后内容字数**不少于** {target_words} 字」——只有下限无上限；`target_words=600`，实测输出 3,300 token ≈ 2,200 汉字（**3.7 倍**），且 `max=4,096` 正好顶到客户端硬编码的 `max_tokens` → **正文被硬截断**（可能断在半句话，这是质量事故而不只是费用问题）。

**方案**：

1. **prompt 改为区间 + 收束指令**：
   ```
   正文控制在 {min}–{max} 字。
   达到 {max} 字时必须收束：补齐当前段落并结束，不得再展开新的小节。
   ```
2. **分清 `max_tokens` 与长度预算的关系**：只有**实际输出**计费，`max_tokens` 只是安全网 → 建议 `4096 → 6144`，**消除硬截断**（不增加成本，除非真的用满），真正的长度控制交给 prompt 区间。
3. **分档**（参数语义从「下限」改为「目标中值」，兼容现有调用方）：

| block 类型 | 建议区间 |
|---|---|
| 概述 / 承诺类 | 400–600 |
| 一般技术方案 | 600–900 |
| 核心方案 / 评分项覆盖 | 900–1,500 |
| 公文（承诺书/保证函，走 letter 路径） | 不适用（结构固定） |

**预期**：正文输出 686K → ~410K（-40%），总成本 **-9%**。
**风险**：你要的可能是"越长越好"的应标文档 —— 需要你定区间；分档 + 环境变量可调。
**验证**：同 10 章对比改造前后的字数分布、截断率、评审 score 分布。

---

### M3 判断类模型分级路由（最大单点）

**实测现状**：`outline_extract` 的活是「按 8 个字段抽取」，合理输出 300–500 token，**实测 4,296**；差额指向 reasoning（失败样本 `reasoning_len≈4000`）。而 3 条配置平铺轮询，**判断类与生成类落进同一个池**，便宜的活会随机落到 pro 档。

**先量后改**：`reasoning_tokens` 列本轮刚落地 —— 建议先跑一次真实运行，量出各环节 reasoning 占比；若判断类 >50%，本条是本方案 ROI 最高的一项。

**方案**：配置增加档位字段，按 `call_site` 取池：

```
JUDGMENT_CALL_SITES   = {outline_extract, section_outline, rerank,
                         scope_select, review_tech, review_compliance, doc_summary}
GENERATION_CALL_SITES = {stream_generate, outline_draft, letter}
```

- `.api_config.json` 增加可选字段 `"tier": "judgment" | "generation" | "both"`（**缺省 = both，老配置行为不变**）
- `config_store` 新增 `get_configs_for(call_site)`；无匹配档位时**退回全池**（不破坏现状）
- UI 配置区加一个档位选择（可选，先改 JSON 也能用）

**预期**：`outline_extract` 输出 874K → ~110K（**-88%**），评审与重排同步下降；总成本 **-26%**。
**风险**：非推理模型做字段抽取可能漏项 → **必须**用 10 章人工标注做字段级对比，不达标就只对 `rerank`/`scope_select` 这类弱判断任务降档（那里对准确率不敏感）。
**决策点**：判断档用哪个模型？→ 见下方实测与建议。

#### M3 附：非推理模型建议（2026-09-20 实测 provider）

**先说实测到的事实**：直接查 provider 的 `/models` 端点，**当前账户只有两个模型**：

```
deepseek-flash      ← 便宜档，但实测失败样本同样带 reasoning_len≈3,900（也是思考型）
deepseek-v4-pro     ← 贵档，思考型（固定到 max_tokens 被吃光的那种）
```

⚠️ 两个推论：
1. 配置里第三条 **`deepseek-v4-flash` 不是该 provider 暴露的模型 ID**（疑为笔误/失效），它会白占一个轮询槽位 → **建议从 `.api_config.json` 删掉或改正**。
2. **当前 provider 内没有真正的非推理模型可选** → 「判断类改非推理」这一项**无法在现有 key 内完成**，必须引入第三方，或用 TypeSafe 承担判断。

**建议（按接入成本排序）**：

| 方案 | 具体模型 | 为什么适合判断类 | 接入方式 |
|---|---|---|---|
| **A. TypeSafe System One（推荐，已装好）** | `jev-latest` | 就是为「结构化判断」设计的：Noul/Choice/Score 返回概率而非文本，实测 2.0s、478 入/38 出 token；**输出 token 几乎为零**（不生成正文） | **key 已配好并冒烟通过**，`typesafe_client` 待写（见 TypeSafe 集成设计） |
| B. 通义千问 | `qwen-flash` / `qwen-plus`（`enable_thinking=false`） | 国内直连、价格低、支持显式关闭思考 | `config_store` 用 `openai` provider + 自定义 `base_url`（DashScope 兼容模式） |
| C. 智谱 GLM | `glm-4-flash` / `glm-4-air` | 极低价、非推理、OpenAI 兼容 | 同上（`open.bigmodel.cn/api/paas/v4`） |
| D. Moonshot Kimi | `moonshot-v1-8k` / `moonshot-v1-32k` | 非推理，长上下文友好 | `config_store` 已内置 `kimi` provider，补 key 即可 |
| E. 豆包 Doubao | `doubao-lite-32k` / `doubao-pro-32k` | 非推理，国内低价 | 已内置 `doubao` provider，补 key 即可 |

**我的建议**：
- **判断类**（`outline_extract` / `section_outline` / `rerank` / 双评审 / `scope_select`）：优先 **A（TypeSafe）**——它已在你的账户里、已验证可用，且「判断」正是它的设计目标；不适合的判断（如需要长文本理解的重排）退到 **B/C**。
- **生成类**（`stream_generate` / `outline_draft` / `letter`）：按你「优先低价」的原则用 **`deepseek-flash`**；`deepseek-v4-pro` 只作为失败兜底（或抽检对照）。
- 这样档位配置就是：`judgment → typesafe`、`generation → deepseek-flash`、`fallback → deepseek-v4-pro`。

> **代价说明**：把生成模型从 `deepseek-v4-pro` 换成 `deepseek-flash` 会**直接影响正文质量**（评测分数可能下降）。建议先用 3–5 个章节做 A/B（同 prompt、同素材、两模型各生成一次，用王安石/包拯的评审分数对比），再决定是否全面切换。

> ⚠️ 单价我无法从 API 获取（`/models` 只返回 ID，不返回价格），**具体价差请你在控制台确认**；方案里的节省比例都是 token 层面的，与单价无关。

---

### M4 `outline_extract` 批量合并 —— **❌ 已取消**

**取消原因**：你要求「提炼的 UI 时序不能变化」，而批量合并必然改变前端逐卡片「提炼中 → 已完成」的时序。

**保留的部分**：维持「按次序从上到下一个一个生成」（`dispatch_outline_json` 的 `concurrency=1` 已是当前默认），先不考虑并发。

**替代降本手段**：**把 M5（缓存前缀重排）做彻底**。逐节调用模式下，每一节都重复同一段 ~1,200 字的固定规则 —— 只要把它放到 prompt 最前且逐字不变，命中率可以从 21.5% 提到 60–70%，这是逐节模式下性价比最高的一项。

（以下原方案内容留档，供将来若改变主意时参考）

**原方案**：每批 K=5 节一次调用，输出 `{"items":[{title, 8 字段}, ...]}`：

- 条数/字段缺失 → **该批自动退化为逐节调用**（保底，绝不静默丢数据）
- `EXTRACT_BATCH_SIZE=1` 即回到旧行为（回滚开关）
- 预算充裕：非推理模型下 5 节 × ~400 token = 2,000（上限 16,000）

**预期**：调用数 208 → ~42，固定前缀重复 ↓5×，输入侧省 ~150K。
**副作用（需你确认）**：前端逐卡片「提炼中 → 已完成」的骨架屏时序会变（同批同时完成）。要么接受，要么前端按节补进度动画。

---

### M5 缓存前缀重排 —— ✅ 已实施

**实测命中率（改造前）**：`outline_draft` 39% / `stream_generate` 29% / `section_outline` 28% / `outline_extract` **21.5%**。
**根因**：缓存只认**共同前缀**，而当时唯一稳定的前缀就是 system prompt（338–389 字），user prompt 一律以变量开头（`build_outline_extract_user` 第 10 个字就是章节标题）。

**改法**：把稳定指令块抽成模块常量并前置，动态内容全部后置。

| 调用点 | 改动 | 稳定前缀（改造前 → 后） | 典型请求 | 可命中比例 |
|---|---|---|---|---|
| `outline_extract` | 新增 `OUTLINE_EXTRACT_INSTRUCTIONS`（808 字）置于 user prompt 最前 | 389 → **1,197 字** | 2,436 字 | **21.5% → 49%** |
| `stream_generate` | 任务陈述 + 6 条核心规则（569 字）前置，标题/长度/摘要/要求素材全部后置 | 338 → **907 字** | 2,155 字 | ~16% → **42%** |
| `section_outline` | 首行不再嵌 `{title}`（原首行即变量，前缀立即分叉） | 389 → 594 字 | 1,932 字 | 28% → 31%（**收益最小**） |
| `rerank` | 指令块前置 | 283 字 | 6,510 字 | **仅 4%**（10 条候选各 600 字，占绝对多数；这条基本赚不到） |

**诚实的边界**：
- 真实命中率还受**缓存 TTL** 与调用交错影响，上表是「前缀可命中的上限」，**必须在有余额后跑一次真实运行才能验证**（`cache_read_tokens` 已在记账）。
- 输入只占总成本 16%，所以 M5 的净值约 **3–5%**（与方案预估一致）——它便宜、无行为风险，是"顺手做掉"的项，不是主力。
- **未做**：`build_letter_user`（公文块占比极小，改它不值当）。

**契约测试**：`tests/agents/test_prompt_cache_prefix.py` 6 条 —— 盯住「稳定块前置、动态后置」这个结构。**这类改动功能测试全绿但缓存收益会静默消失（只有账单变贵）**，所以必须有测试盯着；其中一条断言实测共同前缀 ≥500 字且分叉点正好落在 `【章节】` 之后。

**性能前提**：唯一的行为代价是提示词顺序变化可能影响遵循度 → 需在有余额后用同一批章节对比抽取字段准确率与正文评审分数。

---

### M6 顺带修两个既有缺陷

1. **`dispatch_block_write` 传 `original_content=""`，但 prompt 写着「请严格按照提供的原文逐段进行独立扩写，一段原文对应一段扩写内容」** —— 原文是空的，模型只能凭空写，更容易"自由发挥"把篇幅拉长（也间接推高输出成本）。
   修法二选一：(a) 把 `requirement + 素材片段` 作为「原文结构」传入；(b) 改写该分支措辞，不再要求「逐段对应原文」。
   （注：工作台手动生成路径 `routes/blocks.py:147` 传的是真实 `content`，只有自动路径是空 —— 两条路径行为不一致，本身也值得统一。）
2. **`_generate_doc_summary` 预算记账 bug**（标题行不扣预算，实测输入 15,615 vs 预算 8,000）：该步当前停用，**若 M1/M3 之后恢复它，必须先修**（最小改法：直接复用 `build_spec_digest(sections, budget=8000)`）。

---

## 4. 分阶段执行与回滚

| 阶段 | 内容 | 预期 | 前置 |
|---|---|---|---|
| **0 前置** | 充值/换 key；跑一次真实基线，量 reasoning 占比与各项命中率 | — | **必须** |
| **1 低风险** | M5 缓存重排 + M2 输出区间 + M6 original_content | **-15%** | 阶段 0 |
| **2 流程改动** | M1（选 D 或 C）+ M4 批量 | **-20%** | 阶段 1 验证通过 |
| **3 模型决策** | M3 分级路由 | **-26%** | 需要你的模型清单 |

**每阶段独立提交、独立回归**：`LLM_MODE=mock` 全绿（当前 486）+ 10 章离线样本对比 + 一次真实小项目端到端。

**回滚手段**：M1/M4 有开关（`ENABLE_SECTION_OUTLINE` / `EXTRACT_BATCH_SIZE=1`）；M2/M5 是 prompt 文案，回退即恢复；M3 无档位配置时自动退回全池。

---

## 5. 已确认的决策（2026-09-20）与施工状态

| # | 决策项 | 结论 | 施工状态 |
|---|---|---|---|
| 1 | `section_outline` 处置 | **改：保留大纲展示**（用户 2026-09-20 追加）→ 不走 C（去掉），改走 **A/D**：保留该步但让它真能产出 + 按需/低价档 | 待施工（需配合 M3） |
| 2 | 正文长度 | **分档，且应标文档整体偏长** → `brief 600–1000` / `standard 1000–1600` / `core 1600–2400` 汉字 | ✅ **已实施** |
| 3 | 模型选择 | **生成模型优先选价格低的**；判断类同样优先低价档（非推理模型建议见 §3 M3 末） | 待施工 |
| 4 | 提炼 UI 时序 | **不能变化** → **放弃批量合并**，保持「按次序从上到下一个一个生成」，先不考虑并发 | 已确认（不做 M4） |
| 5 | M6 `original_content` | **修** | ✅ **已实施** |
| 6 | M5 缓存前缀 | 用户指示「继续执行 M5」 | ✅ **已实施** |

**⚠️ M1 决策变更的影响**：保留大纲展示意味着 **M1 不能按 C 省下那 18%**。该环节的钱只能靠两条路赚回来：
1. **让它真能产出**（现状是 `max_tokens=2500` 被 reasoning 吃光、v4-pro 14/14 失败 → 花了钱什么都没拿到）。修法：走 M3 的**判断档低价模型** + 按需触发（只对 mandatory / 长要求 / 命中图纸关键词的 block 生成）；
2. 若将来改主意，C 方案的落地清单仍保留在 §3 M1 里可直接启用。

**对方案的影响**：

- **M4 取消**：批量合并会改变前端逐卡片时序，与你的要求冲突 → 提取环节保持逐节调用（`concurrency=1` 已是默认）。代价是省不掉那 2–4%，**替代手段是把 M5（缓存前缀）做得更彻底**：逐节调用时每节都重复同一段固定前缀，缓存命中率的收益在逐节模式下反而更大。
- **M1 走 C**：省 18%，但需要处理三个下游消费者的替代实现（见 §3 M1 的 C 方案落地清单）。
- **M2 已按「偏长」落档**，并且 `max_tokens` 4096 → 8192（`LLM_STREAM_MAX_TOKENS` 可调），消除硬截断；`max_tokens` 只是安全网，不增加成本。

### M2 + M6 实施记录（已完成）

| 改动 | 文件 | 说明 |
|---|---|---|
| 长度分档 | `infra/llm/dispatcher.py` | 新增 `LENGTH_BANDS` + `length_band(requirement, constraint_level)`；判据：mandatory 或要求 ≥400 字 → core，≥150 字 → standard，否则 brief |
| 区间提示词 | `infra/llm/dispatcher.py` | `_stream_generate` / `dispatch_stream_generate` / `dispatch_block_write` 增加 `max_words`；给了上限就写「正文长度要求：X–Y 字 + 达到 Y 必须收束」，**不给则沿用旧的「不少于 N 字」**（工作台手动路径行为不变） |
| M6 无原文分支 | `infra/llm/dispatcher.py` | `original_content` 为空时不再要求「按原文逐段扩写」（改成按【应标要求】+【参考素材】撰写，规则 1/3/4 同步改写）；有原文时逐字保留旧措辞 |
| 调用方 | `agents/zhuge_liang.py` | 按 `row.requirement` + `constraint_level` 选档；`self.target_words` 更大时下限抬高、上限跟着放宽 |
| 输出上限 | `infra/llm/clients.py` | 流式 `max_tokens` 4096 → `STREAM_MAX_TOKENS`（默认 8192） |
| 测试 | `tests/infra/test_llm_dispatcher.py`、`tests/agents/test_zhuge_liang.py` | +5 条：分档判据、无原文分支措辞、区间+收束、有原文保持旧措辞、诸葛亮的档位传递 |

验证：后端 **491 绿**（486 → 491），前端 21 绿。

---

## 6. 待办与后续（施工顺序）

1. ~~M5 缓存前缀重排~~ ✅ 已完成
2. **M1 保留展示路线**：修 `section_outline` 的 `max_tokens`（2500 被 reasoning 吃光）+ 改按需触发 + 走 M3 的判断档 —— 目标从「纯浪费 18%」变成「有效支出但只在必要 block 上花」
3. **M3 模型分级路由**（等确认非推理模型清单，见 §3 M3 末）
4. 阶段 0 的**真实基线测量**（需先充值；`reasoning_tokens` / `cache_read_tokens` 归集列都已就位）

---

## 7. 明确不做（本方案边界）

- 不改 8 字段的字段定义、不改双评审 prompt 语义
- 不引入向量检索、不新增模型供应商（TypeSafe 那条线独立推进）
- 不改闸门顺序与人工确认节点
- 不为省成本牺牲「否决项/评分项」的识别覆盖（M3 降档只针对弱判断环节，抽取准确率不达标就不降）
