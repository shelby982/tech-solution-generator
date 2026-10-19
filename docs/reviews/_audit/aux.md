# 仓库资产审计（backend/、frontend/ 之外）

> 审计员：aux-auditor（task-3）
> 仓库：`/Users/jianghe/projects/ge-solution`，分支 `feature/multi-agent-bid-system`（HEAD `4a6f627`，2026-10-17）
> 方式：只读。`git ls-files` / `git log` / `du` / `grep` / `md5` 取证；未启动服务、未构建、未 checkout/stash/reset/rm 任何项目文件。
> 时点：2026-10-19。文中日期均为 **2026 年**（仓库历史 2026-05-07 ~ 2026-10-17，共 447 commits）。
> 唯一写入：本文件。

---

## 0. 结论摘要

### 0.1 仓库体量基线

| 指标 | 数值 | 取证 |
|---|---|---|
| git 跟踪文件 | **541 个** | `git ls-files \| wc -l` |
| 跟踪文件总字节 | **35.88 MB** | 逐文件 `stat -f %z` 求和 |
| 工作区总体积 | **1.2 GB** | `du -sh .` |
| `.git` | **47 MB**（pack 36.23 MiB / 6,373 objects / 3 packs） | `du -sh .git`；`git count-objects -vH` |
| 工作区中最大两块（均被 `.gitignore` 排除） | `.venv/` 490 MB、`backend/data/` 599 MB | `du -sh` |

**跟踪字节里 77.6% 是截图**：`docs/screenshots/` 29,208,125 B = 27.86 MB / 172 个 png；其中 **164 个（26.46 MB）没有被任何 md 引用**。

### 0.2 可删除/可归档体积估算（只算 HEAD 工作区，不含历史重写）

| 层级 | 体积 | 说明 |
|---|---|---|
| **可直接回收（引用为零、四分类内）** | **≈ 28.6 MB** | 26.46 MB 未引用截图 + 1.37 MB 冗余原型 + 0.58 MB ge-solution 冗余资产 + 0.24 MB skill/ + 少量配置 |
| 其中「删了会丢证据、需产品确认」 | 26.46 MB（截图）+ 约 0.6 MB（ge-solution 早期原型） | 见 §4 |
| **本地垃圾（未跟踪，不影响 clone）** | **≈ 0.28 MB** | `.pytest_cache` 87 KB、`.DS_Store` ×3 共 22.5 KB、未跟踪截图 178 KB、`.planning/.superpowers/.gstack` 644 KB（本地工作目录，建议保留） |
| **`.git` 能回收的** | **0（不重写历史时）** | 截图历史 blob 已在 pack 中；`git rm` 只影响未来 checkout，clone 体积仍是 ~47 MB。真要回收需 `git filter-repo`，见 §4.6 |

> 结论：**不重写历史的前提下，删除全部候选可从工作区回收约 28.6 MB（其中 26.5 MB 是截图证据）；clone 体积仍为 ~47 MB。**

### 0.3 风险最高的 5 项（按修复优先级）

1. **`.dockerignore` 未排除 `backend/data/`（最危险，非删除类）**：Docker build context 会包含 **~630 MB**（模拟估算：`backend/data` 599 MB + `docs/` 32 MB），且 `Dockerfile` 的 `COPY backend/ ./backend/` 会把 **`ge_solution.db`（180.9 MB）+ 两个 DB 备份（397.7 MB、22.2 MB）+ 22 MB 真实投标 PDF** 打进镜像。这些文件被 `.gitignore` 排除（不入库），但 Docker 只看 `.dockerignore`。
2. **`docs/screenshots/` 172 个 png、27.86 MB、占跟踪字节 77.6%，其中 164 个（26.46 MB）零引用**；且 10 个文件构成 5 对完全重复。截图由 115 个提交逐个加进来（149 个提交触达），是最主要的仓库膨胀源。
3. **`.claude/worktrees/` 两个损坏 gitlink**：mode `160000` 指向 commit `ffeda42`，但该对象在本仓库**不存在**、也没有 `.gitmodules`；`git submodule status` 直接 fatal。全新 clone 会留下两个空目录。
4. **`skill/` 236 KB / 39 文件 / 17 个 SKILL.md 与 13 个 py 脚本，零运行时引用**；提交信息自述「后端 Python 代码不引用本目录」，且 SKILL.md 内引用的是不存在的 `.claude/skill/...` 路径（仓库只有 `skill/`）——**as-committed 没有任何加载入口**。
5. **真实客户/招标信息在跟踪文件中**：`docs/design/mockups/*.html` 出现「南方电网」850 次，`docs/tender/` 是真实标包 2 的评分点拆解；remote 指向 `github.com/shelby982/tech-solution-generator`（可见性未核实）。是否允许公开待产品/安全确认（§4.7）。

### 0.4 总评

- **不是"一堆误提交垃圾"**：`ge-solution/`（2026-09-15 `591a841`「docs: 归档 ge-solution 原型设计稿」）与 `skill/`（2026-09-15 `a68b883`「feat(skill): 新增应标流程 skill 库」）都是**有意入库**的归档/资产，只归 LEGACY，不归 JUNK；真正无争议的垃圾只有 2 个损坏 gitlink、5 张 stray png、本地 `.DS_Store`/`.pytest_cache`、1 张未跟踪截图。
- **最该优先处理的是根配置而不是删目录**：`.dockerignore` 的漏洞（体积 + 客户数据外泄）、`.gitignore` 缺少 `.claude/worktrees/` 与 `.pytest_cache/`、README 架构章节停留在 5 月。
- **截图策略需要一次产品决策**：172 张里只有 8 张被 3 份设计文档引用；要么改存 wiki/对象存储，要么给后续提交定规则（本次不擅自建议"全删"，因为它们是 QA 证据且已在历史里）。

---

## 1. 逐项清单

> 类别：**JUNK** 无价值/误入；**LEGACY** 历史遗留但可能留档；**DUPLICATE** 重复实现/重复内容；**DEAD** 无任何引用、无加载路径。
> 体积均为文件真实字节（`du -h` 因块对齐会略大）。"最后提交"取 `git log -1 --format=%ad --date=short -- <path>`。

| ID | 路径 | 类别 | 体积 / 规模 | 证据（跟踪 / 引用 / 日期） | 风险 | 建议动作 | 验证方式 |
|---|---|---|---|---|---|---|---|
| G1 | `ge-solution/B - 应标流程.html` | LEGACY | 82,128 B / 1,888 行 | 跟踪；引用 0（仅 `docs/superpowers/...loop.md:2294` 提到 `~/Claude/wiki/Projects/ge-solution/`，非本路径）；加入于 `591a841` 2026-09-15，文件 mtime 2026-05-25 | product-decision | 归档到 `docs/design/legacy/` 或保留现状；若留档建议加 README 注明「早期原型，非当前产品」 | `git log --follow -- "ge-solution/B - 应标流程.html"`；`grep -rn "B - 应标流程" backend frontend tests scripts docs` |
| G2 | `ge-solution/sidebar.html` | LEGACY | 22,064 B / 549 行 | 跟踪；代码/文档引用 0；同提交 `591a841`；内容为"左侧栏 · 应标流程"早期版 | product-decision | 同 G1；可与 G1 合并为一份归档说明 | 同 G1 换路径 |
| G3 | `ge-solution/design-canvas.jsx` | JUNK | 49,676 B / 966 行 | 跟踪；引用 0；**通用 Anthropic artifact 设计画布工具**（host bridge / postMessage 协议），产品词（应标/标包/评分点/方案/章节）出现 **0 次** | safe | 删除（它不承载本项目设计决策，只是当时用的工具壳） | `grep -c "应标\|标包" ge-solution/design-canvas.jsx` = 0；`grep -rn design-canvas backend frontend tests` |
| G4 | `ge-solution/tweaks-panel.jsx` | JUNK | 25,802 B / 568 行 | 跟踪；引用 0；通用 tweaks 面板（`__activate_edit_mode` / `EDITMODE` 协议），产品词 0 次 | safe | 删除 | 同 G3 |
| G5 | `ge-solution/uploads/pasted-*.png` ×5 | JUNK | 520,028 B（109.5K+57.5K+55.2K+249.5K+48.3K） | 跟踪；**原型 HTML 未引用**（`grep "uploads/\|pasted-" B html sidebar.html` 无命中）；文件名是 Claude 粘贴截图（epoch 1779173595073 ≈ 2026-05-19 ~ 05-20）；内容是早期大纲编辑界面 | safe | 删除（如需留证只留最大 1 张） | `grep -rn "pasted-1779" ge-solution/ docs/` = 0 |
| C1 | `.claude/worktrees/groovy-shimmying-elephant`、`.claude/worktrees/security-hardening` | JUNK | 0 B / 2 个 gitlink | 跟踪（mode `160000`）；无 `.gitmodules`；指向 `ffeda42` 但 `git cat-file -t` 失败（对象不存在）；加入于 `59551ed` 2026-05-13；对应本地分支 `worktree-*` 仍存在 | safe | `git rm --cached` 两个条目（不删本地文件，目录本就是空的）；并把 `.claude/worktrees/` 加入 `.gitignore` | `git ls-files -s .claude` = 2×160000；`git submodule status` → fatal；`git clone` 测试见 §2.2 |
| K1 | `skill/`（18 个目录 / 39 文件） | DEAD | 242,059 B（SKILL.md 4,784 行 + py 2,214 行 + 5 份 references） | 跟踪；**backend/frontend/tests/scripts/根 py/sh/md 引用 0**；docs 引用 0（`docs/superpowers/...` 里的 skill 指 typesafe-ai 与 superpowers 插件，非本目录）；提交 `a68b883` 2026-09-15 自述「后端 Python 代码不引用本目录」；2026-09-16 `e5ea0c0` 还修过 `export_docx.py` 语法；文件 mtime 2026-03-01~03（3 月拷贝进来补提交） | product-decision | 二选一：①若 agent 工作流仍要用 → 迁到 `.claude/skills/` 并修正 SKILL.md 内 `.claude/skill/` 路径；②否则移出产品仓库（独立 repo / wiki 存档）| `grep -rn "skill/" backend frontend tests scripts *.py` = 0；`ls .claude/skill*` 均不存在 |
| K2 | `skill/*/references/应标语言规范.md` ×5 副本 | DUPLICATE | 5 个文件 md5 全为 `4ed14f0…`；`_shared_references/应标语言规范.md` 是**另一个** md5 `51b745b…` | 同一份规范被复制 5 份，且与"共享版"内容分叉 | safe（若保留 K1） | 收敛为 `.claude/skills/_shared/` 单一副本 | `find skill -name '应标语言规范.md' -exec md5 -r {} +` |
| D1 | `docs/screenshots/*.png` 中 164 个未引用 | JUNK | 27,740,419 B = **26.46 MB** / 164 个 | 172 跟踪；全仓 md 全文搜索仅 **8 个**被引用（1.40 MB）；引用集中在 3 份文档（见 §3）；`docs/screenshots` 首提交 2026-10-13、末 2026-10-17，115 个提交加图 | product-decision | 保留 8 张被引用的；其余移出仓库（wiki/对象存储）或删除；长期给截图提交定规则 | §2.4 统计脚本；`grep -rIn "docs/screenshots" docs/*.md` |
| D2 | `docs/screenshots/` 内 5 对完全重复 | DUPLICATE | 10 文件共 2,003,132 B，冗余 5 个 | md5 相同对：`strip-simplified`=`strip-expanded`、`live-budget-confirmed`=`live-budget-height`、`entry-from-project-list`=`entry-lands-on-panel`、`package-cards`=`material-integrity`、`ia-no-extract-term`=`panel-head-clean`；**注意 `ia-no-extract-term.png` 被 `UI一致性审计.md:520` 引用，必须保留被引用的那一份** | safe | 只删每对中未被引用的副本 | `find docs/screenshots -name '*.png' -exec md5 -r {} + \| sort` |
| D3 | `docs/screenshots/live-kind-row.png` | JUNK | 182,657 B | **未跟踪**（`??`）；mtime 2026-10-17 22:29；全仓引用 0；未随任何提交入库 | safe | 删除本地文件（或确认后补提交——但无人引用，倾向删） | `git status --porcelain docs/screenshots`；`grep -rn "live-kind-row" .` = 0 |
| M1 | `docs/design/mockups/prod-plan-budget.html` | DUPLICATE | 1,108,667 B / 15,214 行 | 跟踪；引用 **0**；与 `prod-prototype.html` 去空白后 diff 仅 679 行差异（约 95%+ 内容相同）；最后提交 2026-10-16；自包含静态 HTML（无 CDN、无外链资源） | product-decision | 删除或并入 prod-prototype 的回滚/变体说明；如保留，改名 `prod-plan-budget-variant` 并注明差异 | `diff <(sed 's/[[:space:]]//g' a) <(sed 's/[[:space:]]//g' b) \| grep -c '^[<>]'` |
| M2 | `docs/design/mockups/` 其余 13 个 html | LEGACY | 309,888 B，13 文件（five-stage-panel*.html、plan-1/2/3、confirm-two-step、stage-rail-*、writing-budget-*、product-shell-*、response-plan-*、catalog-budget-merge） | 跟踪；**逐个检查引用均为 0**；日期 2026-10-13 ~ 10-17；均为自包含设计原型 | product-decision | 移入 `docs/design/mockups/archive/`（保留目录但降低"当前有效原型"的误导）或删除 | `grep -rIl -F <basename> docs backend frontend tests scripts` 每个=0 |
| N1 | `docs/design/*.md` 24 份 | LEGACY | 307,890 B / 5,423 行 | 跟踪；2026-10-13 ~ 10-17（最近一周产物）；其中 5 份被代码/文档反向引用（见 §3），其余为一次性规划/分析稿（如「面板…-规划v2/v3」「标包驱动主流程-三个方案/诊断」「标包优先流程-三套产品优化方案」） | product-decision | 保留被引用的 5 份 + 实施记录；把已被替代的"规划 v1/v2/v3、三方案"移入 `archive/` | `grep -rIl -F "<文件名去扩展名>" backend frontend tests docs` |
| N2 | `docs/reviews/2026-10-15-code-review-{plan,report}.md` | LEGACY | 13,731 B / 278 行 | 跟踪；外部引用 0；报告结论「P1×6、发布门禁不通过」是 2026-10-15 快照，之后本分支已有大量修复提交；`scripts/clear_data.py` 的 P1-6 已在 `.planning` 记为修复 | product-decision | 保留作为审计痕迹；建议在文首加「已被后续提交部分关闭，勿据此判断当前门禁」 | 读文件头；`grep -rn "code-review-report" .` = 0 |
| F1 | `railway.toml` + `render.yaml` | DEAD / DUPLICATE | 286 B + 310 B | 均跟踪；**全仓引用 0**；同为 2026-05-07 初始提交（`61a18ed`）后**再未改动**；两者都与 `Dockerfile` 的 `CMD`/`HEALTHCHECK` 重复声明同一启动命令与 `/health`；仓库无 CI、无部署文档 | needs-check | 确认实际部署平台后只留一套（推荐留 `Dockerfile`，删两个 PaaS 清单；若确实用 Railway/Render 则删另一个） | `git log --oneline -- railway.toml render.yaml`；`grep -rn "railway\|render.yaml" .` = 0 |
| F2 | `.dockerignore`（缺 `backend/data/` 等） | DUPLICATE / DEAD（配置失效） | 387 B | 排除项含 `.venv`、`tests`、`.api_config.json`、`.git`，**未排除 `backend/data/`、`docs/`、`skill/`、`ge-solution/`、`.planning/`、`.superpowers/`、`.claude/`、`.pytest_cache/`**；`Dockerfile:31` 是 `COPY backend/ ./backend/`；本地 `backend/data` 599 MB 且含真实客户 PDF | safe（只改配置） | 追加 `backend/data/`、`docs/`、`skill/`、`ge-solution/`、`.planning/`、`.superpowers/`、`.claude/`、`.pytest_cache/`、`*.db*` | §2.5 的 context 模拟脚本；`docker build` 前 `du -sh` 对比 |
| F3 | `clear_data.sh` | DUPLICATE（表层） | 152 B | 跟踪；内容仅 `exec python3 scripts/clear_data.py "$@"`——**不是重复实现，是薄包装**；start.sh 未调用它；全仓对 `clear_data.sh` 的引用 0；`scripts/clear_data.py` 活着（`docs/reviews/...report.md:55` 与 `.planning/.../progress.md:55` 均引用） | safe | 保留 wrapper（对用户友好）或删除；不要改 Python 实现 | `cat clear_data.sh`；`grep -rn "clear_data" .` |
| F5 | `README.md` | LEGACY（部分过时） | 1,632 B / 41 行 | 跟踪；2026-10-16 仅更新了「前端主路径/旧入口」两行；架构章节仍是 v1.0 口径：5 个 agent（实际 `backend/agents/` 9 个 py、8 个 agent 文件）、`/api/workflow/*` 7 端点 + `/api/review/{tid}`（实际 `backend/routes/` 14 个路由模块）；启动说明 `bash start.sh` 有效 | needs-check | 更新架构/端点/agent 数；启动与测试说明保留 | `ls backend/agents/*.py \| wc -l`=9；`ls backend/routes/*.py \| wc -l`=14；`git show 74511ef -- README.md` |
| F6 | `.gitignore` 缺口 | DEAD（配置缺口） | 525 B | 已覆盖 `.venv/`、`__pycache__/`、`backend/data/`、`.api_config.json`、`.gstack/`、`.superpowers/`、`.planning/`、`.DS_Store`；**缺 `.claude/worktrees/`、`.pytest_cache/`**（C1 事故根因；`.pytest_cache` 靠 pytest 自带 `.gitignore` 才没被误加） | safe | 追加两行 | `grep -nE "worktrees\|pytest_cache" .gitignore` = 0 |
| J1 | `.DS_Store` ×3（根、`frontend/`、`ge-solution/`） | JUNK | 10,244 + 6,148 + 6,148 = 22,540 B | 均**未跟踪**；被 `.gitignore:16` 覆盖；根 mtime 2026-09-05 | safe | 本地删除（不入库） | `git check-ignore -v .DS_Store` |
| J2 | `.pytest_cache/` | JUNK | 87 KB（89,324 B）/ 6 文件 | **未跟踪**；mtime 2026-05-18（4 个月未更新）；未在 `.gitignore`，但 pytest 自己在目录内写了 `.gitignore`（`*`）所以 `git status` 不可见 | safe | 本地删除 + `.gitignore` 加一行（F6） | `ls -la .pytest_cache`；`git check-ignore -v .pytest_cache/v/cache/nodeids` |
| J3 | `.planning/`、`.superpowers/`、`.gstack/` | JUNK（本地工具目录，非仓库资产） | 104 KB + 120 KB + 420 KB | 均**未跟踪**且被 `.gitignore` 覆盖；`.planning` 是本项目多轮规划记录（含对 `skill/`、`clear_data.py` 的决策痕迹）、`.superpowers` 是插件 brainstorm 缓存、`.gstack` 是 browse 日志 | safe | 保留本地（`.planning` 有决策价值）；若团队要共享历史，另挑文件转正入库，勿整体提交 | `git status --ignored`；`find .planning -name '*.md'` |

**统计：23 项** —— JUNK 9（G3/G4/G5/C1/D1/D3/J1/J2/J3）、LEGACY 6（G1/G2/M2/N1/N2/F5）、DUPLICATE 5（K2/D2/M1/F3/F2）、DEAD 3（K1/F1/F6）。
按风险：**safe 13 项 / needs-check 2 项（F1、F5）/ product-decision 8 项（G1、G2、K1、D1、M1、M2、N1、N2）**。

---

## 2. 逐项详述

### 2.1 `ge-solution/`：不是"误提交"，是 9 月有意归档的早期原型

**它是什么**：2026-05-19~05-25 用 React 18 + Babel standalone（unpkg CDN）做的静态原型，「应标流程 · B 方案」+ 左侧栏 + 设计画布/调参面板工具；2026-09-15 由提交 `591a841` 一次性加入仓库，提交信息原文：

> 应标流程的早期原型探索：页面结构（B - 应标流程.html、sidebar.html）、画布与调参面板草案（design-canvas.jsx、tweaks-panel.jsx）及过程截图。**保留作设计决策的追溯依据，非运行代码。**

**为什么说与当前产品无关**：

- 原型词汇是「应标要求 / 原始素材 / 大纲 / 步骤」（`应标` 17 次、`素材` 12 次、`大纲` 12 次），**没有** `标包`、`评分点`、`工作台`（各 0 次）——这是当前"标包驱动 + 评分点主线"之前的交互模型。
- `B - 应标流程.html` 内**没有任何 `fetch(` 或 `/api/` 调用**（grep 0 命中），纯静态。
- 全仓仅 1 条无关引用（`docs/superpowers/plans/2026-09-15-multi-agent-collaboration-loop.md:2294` 的 `~/Claude/wiki/Projects/ge-solution/`，是 wiki 路径不是仓库路径）。
- `frontend/` 无任何 import/引用。

**拆分结论**（若产品决定"留档追溯"，也不该整目录照单全收）：

| 子项 | 判断 | 理由 |
|---|---|---|
| `B - 应标流程.html` + `sidebar.html`（102 KB / 2,437 行） | LEGACY，建议留档 | 确实记录早期页面结构，是唯一有追溯价值的部分 |
| `design-canvas.jsx` + `tweaks-panel.jsx`（74 KB） | JUNK | 通用 Anthropic artifact 工具壳（host bridge 协议、`EDITMODE` 块），**0 处产品词**，不是本项目设计决策 |
| `uploads/pasted-*.png` ×5（508 KB） | JUNK | 原型 HTML 不引用；Claude 粘贴临时文件名；内容只是早期大纲编辑器截图 |

**做法建议**：把 G1/G2 移到 `docs/design/legacy/ge-solution-prototype/` 并写一行 README；G3/G4/G5 直接删。若整目录保留，至少删 `design-canvas.jsx`/`tweaks-panel.jsx`/`uploads/`（能回收 582 KB）。

**验证**：
```bash
git log --follow --format='%h %ad %s' --date=short -- "ge-solution/B - 应标流程.html"   # 仅 591a841
grep -rIn --exclude-dir=__pycache__ -E "design-canvas|tweaks-panel|pasted-1779|B - 应标流程" backend frontend tests scripts docs skill
grep -c "标包\|评分点" "ge-solution/B - 应标流程.html"                                   # 0
```

### 2.2 `.claude/worktrees/`：两个损坏 gitlink（真实垃圾，应删）

**证据链**：

- `git ls-files -s .claude` → `160000 ffeda42… .claude/worktrees/groovy-shimmying-elephant` / 同 `security-hardening`（gitlink，等同 submodule 指针）。
- 无 `.gitmodules` 文件；`git submodule status` → `fatal: no submodule mapping found in .gitmodules for path ...`。
- `git cat-file -t ffeda42…` → `could not get object info`——**指向的 commit 根本不存在**。
- 磁盘上两个目录为空；`.git/worktrees/` 无残留。
- 加入于 `59551ed` 2026-05-13（"优化整体安全设计…"），之后未再变。
- 对应本地分支仍在：`worktree-groovy-shimmying-elephant` → `fa1e106`（2026-05-11，已并入 main/feature），`worktree-security-hardening` → `89f29fb`（2026-05-14，**未并入** main/feature）。

**全新 clone 实测**（在 `/tmp` 做临时 clone 后已删除）：`git clone` 成功（exit 0），但会留下两个空目录；`git submodule status` fatal。即：不阻断 clone，但会打断任何 `git submodule update --init` 的 CI/工具。

**做法**：`git rm --cached .claude/worktrees/<两个名字>` 后提交；`.gitignore` 加 `.claude/worktrees/`。**不要**顺手删 `worktree-security-hardening` 分支——它未合并，可能含未回收的安全改造，需产品确认（见 §3）。

### 2.3 `skill/`：有意入库但"无加载入口"的 agent 资产

**它是什么**：18 个目录（17 个 skill + `_shared_references`），覆盖应标全链路：`bid-setup / bid-doc-parser / compliance-extractor / compliance-checker / chapter-indexer / progressive-retriever / outline-builder / section-writer / content-expander / content-polisher / draft-polisher / hint-generator / content-exporter / docx-export-tool / bid-pipeline / bid-role-analyzer / bid-session-manager`。每个 SKILL.md 有 Claude Code 的 `name/description` frontmatter；13 个独立 Python 脚本（`uv run` 方式调用）。

**问题 1：零引用（8 个必须核实的命令全部为 0）**

```bash
grep -rn "skill/" backend/ frontend/ tests/ scripts/ *.py        # 0
grep -rIn -w "skill" backend frontend tests scripts *.py *.sh *.md # 0
grep -rIn -w "skill" docs/                                        # 12 处，全部指 typesafe-ai skill / superpowers 插件，非本目录
```

**问题 2：自指路径不存在**。两份 SKILL.md 让 agent 执行 `.claude/skill/...`：

- `skill/content-expander/SKILL.md:66` → `uv run .claude/skill/progressive-retriever/scripts/search.py`
- `skill/docx-export-tool/SKILL.md:33` → `uv run .claude/skill/docx-export-tool/scripts/export_docx.py`

但仓库里 `.claude/skill` 与 `.claude/skills` **都不存在**（`ls .claude/skill*` 报 No such file）。Claude Code 的项目技能目录是 `.claude/skills/`，因此**as-committed 没有任何机制会加载这 17 个 skill**。

**问题 3：与 backend 能力重叠（平行实现）**。语义映射明显：`compliance-extractor←backend/infra/parser/{tech_requirements,review_points}.py`、`chapter-indexer←infra/parser/outline_builder.py`、`progressive-retriever←infra/retrieval/*`、`content-exporter/docx-export-tool←infra/docx/* + routes/export.py`、`section-writer/outline-builder/polisher←agents/prompts.py + agents/*`、`bid-pipeline←orchestrator/*`。它是"给 agent 手动跑标书"的提示词/脚本资产，与产品运行时是**两套并行实现**。

**问题 4：体积里有 5 份重复规范**（K2）：`find skill -name '应标语言规范.md' -exec md5 -r {} +` → content-expander / content-polisher / draft-polisher / hint-generator / section-writer 五处 md5 完全相同（`4ed14f0…`），而 `_shared_references/应标语言规范.md` 是另一份（`51b745b…`）。共享引用机制被复制粘贴打败。

**关键背景（决定风险等级）**：

- 提交 `a68b883`（2026-09-15）信息明说「后端 Python 代码不引用本目录，**属独立的编排资产**」——**不是误提交**。
- 2026-09-16 `e5ea0c0` 还专门修了 `export_docx.py` 两处语法错误（git 历史只有 2 个提交，但文件 mtime 是 2026-03-01~03，说明是 3 月旧资产在 9 月补提交）。
- 未跟踪的 `.planning/2026-09-15-.../findings.md:56` 写有「削弱现有 `domain/` 与 `skill/` 资产复用」——团队确实把它当资产看待。

**结论**：不能算"垃圾"，但**当前形态是 DEAD**（没有加载路径、零引用）。建议产品决策二选一：

1. 继续作为 agent 资产 → 迁到 `.claude/skills/<name>/`（保留 frontmatter），修正 SKILL.md 内 `.claude/skill/` 路径，去重 references；
2. 不再需要 → 移到独立 repo / wiki 存档，从产品仓库删除（回收 236 KB），历史仍在 git 里可查。

**移除前必做**：`grep -rn "skill/" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__` 确认为 0（现为 0，除自身文档外）。

### 2.4 `docs/`：截图是真正的膨胀源；superpowers/tender 必须留

#### 2.4.1 `docs/screenshots/`（172 个跟踪 png，27.86 MB，占跟踪字节 77.6%）

统计口径（脚本见 §5.0）：对 172 个文件逐一用 basename 在全部 md 中做全文匹配，结果：

- **被引用 8 个，共 1,467,706 B（1.40 MB）**，全部集中在 3 份文档：
  - `docs/design/产品设计分析-待确认模块.md:155` → `confirm-redesigned.png`、`confirm-edit-structure.png`
  - 同文件 `:248` → `batch-operations.png`
  - `docs/design/UI一致性审计.md:441` → `statusbar-removed.png`
  - 同文件 `:520` → `ia-no-extract-term.png`
  - 同文件 `:766` → `confirm-shows-result.png`
  - `docs/design/上传模块重设计-三方向.md:201` → `desk-a.png`；`:286` → `card-menu.png`
- **未被引用 164 个 = 27,740,419 B = 26.46 MB（94.8% 的体积）**。
- 代码（py/js/mjs/html）对 `screenshots/` 引用 0。
- 5 对完全重复（D2）；注意 `ia-no-extract-term.png` 是**被引用**的那一份，删除时只能删它的重复副本 `panel-head-clean.png`。
- 历史：首提交 2026-10-13、末 2026-10-17；**115 个提交**各加若干截图（149 个提交触达）——这是 agent 开发流程"每个 feature 都截屏落盘"的产物，属于入库策略问题，不是单次事故。

**建议**：保留被引用的 8 张；其余 164 张迁出（wiki/对象存储）或删；后续截图默认不入库（或只入被引用图）。**不要**为了数字好看而删被引用的那 8 张。

**注意（诚实声明）**：删 HEAD 文件只能回收工作区 26.46 MB；`.git` 的 pack 仍含这些 blob（`size-pack 36.23 MiB`），clone 体积不变。要真正瘦身 clone 需 `git filter-repo` 重写历史（§4.6，高风险，需全组协调）。

#### 2.4.2 `docs/design/mockups/`（15 个 html，35,751 行，2.48 MB）

| 文件 | 字节 | 行数 | 最后提交 | 文档/代码引用 |
|---|---|---|---|---|
| prod-prototype.html | 1,184,963 | 15,733 | 2026-10-16 | **2**（`docs/design/原型到代码-落点对照.md:3`；`tests/services/test_page_budget.py:3`） |
| prod-plan-budget.html | 1,108,667 | 15,214 | 2026-10-16 | 0 |
| product-shell-plan-and-write.html | 51,693 | 717 | 2026-10-16 | 0 |
| response-plan-and-chapter-editor.html | 38,096 | 537 | 2026-10-16 | 0 |
| five-stage-panel-v2.html | 35,900 | 533 | 2026-10-14 | 0 |
| writing-budget-in-chapter-studio.html | 34,160 | 490 | 2026-10-16 | 0 |
| writing-budget-and-evidence.html | 26,849 | 419 | 2026-10-16 | 0 |
| five-stage-panel.html | 26,433 | 423 | 2026-10-14 | 0 |
| confirm-two-step.html | 21,192 | 337 | 2026-10-14 | 0 |
| stage-rail-accordion.html | 17,174 | 320 | 2026-10-13 | 0 |
| stage-rail-switch.html | 15,248 | 287 | 2026-10-14 | 0 |
| catalog-budget-merge.html | 11,478 | 164 | 2026-10-17 | 0 |
| plan-1-package-workspace.html | 10,966 | 201 | 2026-10-15 | 0 |
| plan-3-document-derived.html | 10,628 | 197 | 2026-10-15 | 0 |
| plan-2-intake-matrix.html | 10,071 | 179 | 2026-10-15 | 0 |

**它们是什么**：自包含静态 HTML（无 CDN、无外链、无 `screenshots/` 引用），是把当期生产页面（`frontend/project-init.html`、`three-mode.html` 的 CSS/结构）拼进单文件 + mock 数据的设计原型。`prod-prototype.html` 被文档明确称为「真实生产页面 + 增量」。

**问题**：只有 `prod-prototype.html` 被引用；`prod-plan-budget.html` 与它去空白后差异仅 679 行（约 95%+ 相同）——两个 1.1 MB 的近似副本。其余 13 个为迭代过程稿（`five-stage-panel` v1/v2、三段式规划、stage-rail 三个变体），**全部 0 引用**。这些是 10 月 13-17 的新产物（不是"5 月遗留"），删/留是产品决定，不是技术判断。

**建议**：保留 `prod-prototype.html`（唯一有引用链的"真原型"）；`prod-plan-budget.html` 与其余 13 个移入 `docs/design/mockups/archive/` 或删除（回收 1.37 MB）。删除前确认 `tests/services/test_page_budget.py` 不需要打开文件（它只在 docstring 里引用路径，不需要文件存在）。

#### 2.4.3 `docs/superpowers/`（15 文件，326.7 KB，6,841 行）——**必须保留**

被产品代码直接引用：

- `frontend/project-init.html:2159`、`frontend/assets/mode-outline.js:1310` → `plans/2026-09-21-chapter-centric-workspace-design.md`
- `backend/infra/llm/dispatcher.py:691`、`backend/domain/material/models.py:89`、`backend/domain/spec/response_items.py:4` → `plans/2026-09-21-response-coverage-and-matching-plan.md`
- 文档间有 spec↔plan 依赖链（2026-06-01/06-03/06-16/09-15）。日期 2026-06-01 ~ 09-21，是设计依据不是垃圾。

**做法**：原样保留，不要因目录名带 "superpowers" 就误判（根目录还有一个被 ignore 的 `.superpowers/`，那是插件缓存，二者不同）。

#### 2.4.4 `docs/tender/`（3 文件，65.1 KB，677 行）——**必须保留**

`tests/infra/test_review_points.py:173` 明确写「期望值直接来自人工拆解结果（见 docs/tender/标包2-技术评分点拆分.md）」——是测试期望值的人工依据。2026-10-12 随解析管线功能提交（`d67a19a`）。**但内容含真实招标方信息（南方电网/广东电网），见 §4.7。**

#### 2.4.5 `docs/design/*.md`（24 份，300.7 KB，5,423 行）

2026-10-13 ~ 10-17 的设计/规划/实施记录，被代码反向引用的 5 份：

- `UI一致性审计.md`（3 处截图引用）
- `Word交付格式与导出链路.md`（引用 `scripts/build_response_templates.py`）
- `原型到代码-落点对照.md`（引用 mockups）
- `应答策划-商务技术独立编制.md`
- `标包驱动主流程-诊断.md`

其余 19 份外部引用 0，其中「面板三段式重组-规划v2 / 面板三阶段平级布局-规划v3 / 面板按标包类别重组-规划 / 标包驱动主流程-三个方案 / 标包优先流程-三套产品优化方案」是已被实现取代的方案比较稿。建议保留被引用 + 实施记录，迭代规划稿移 `archive/`（体积不重要，主要是降低"哪份是当前方案"的噪音）。

#### 2.4.6 `docs/reviews/`（2 文件，13.4 KB）

2026-10-15 的代码审查计划+报告，外部引用 0。报告结论是「发布门禁：不通过（P1×6）」，属于当时快照；其中 P1-6（clear_data.py 只清六张表）已在 `.planning/2026-10-15-package-workspace/progress.md:55` 记为修复。**保留作审计痕迹，但建议标注"部分已关闭，勿据此判断当前门禁"**；另本审计文件也放在此目录下。

### 2.5 根配置与脚本

#### 2.5.1 `.dockerignore` 是本次审计最该先修的一项（不删文件）

- `.dockerignore`（387 B，2026-05-07 后未改）排除了 `.venv/`、`tests/`、`requirements-dev.txt`、`.git/`、`.api_config.json` 等，**但没有排除 `backend/data/`**。
- `Dockerfile:31` 是 `COPY backend/ ./backend/`——Docker 只认 `.dockerignore`，不认 `.gitignore`。
- 本地 `backend/data/` 599 MB：`ge_solution.db` 180.9 MB + `ge_solution.db.bak-20261013-1742` 397.7 MB + `bak-…1751` 22.2 MB + `uploads/` 22 MB（**真实投标 PDF，含客户/项目名**）。
- 按 `.dockerignore` 规则模拟 walk 文件系统：构建上下文约 **630 MB**（其中 `backend/data` ≈ 599 MB、`docs` ≈ 32 MB），而镜像实际只用到 `backend/`（无 data）+ `frontend/`。后果：构建慢/失败风险、镜像膨胀、**把客户标书与本地 DB 打进镜像**。

建议追加（按现有文件风格分组）：
```
# 运行期本地数据（含客户上传与 SQLite，绝不入镜像）
backend/data/
*.db
*.db.bak*
backend/.tmp_uploads/

# 文档 / 归档 / 本地工具目录（镜像不需要）
docs/
skill/
ge-solution/
.planning/
.superpowers/
.claude/
.gstack/
.pytest_cache/
```
验证：修改后 `docker build --no-cache` 观察 "transferring context" 体积应从 ~630 MB 降到 <15 MB；或本地用 `tar --exclude-from` 粗估。

#### 2.5.2 `railway.toml` + `render.yaml`：初始提交后再未维护的重复部署清单

- 均为 2026-05-07 `61a18ed`（v1.0）引入，之后 5 个月零改动；`Dockerfile` 在 2026-09-15 还改过（补 libgomp1）。
- 内容互相重复且与 Dockerfile 重复：`railway.toml:6` 的 `startCommand` 与 `Dockerfile:46 CMD` 完全相同（`uvicorn main:app … --app-dir /app/backend`）；`railway.toml:7` 与 `render.yaml:6` 都声明 `/health`；`Dockerfile:43-44` 也有 HEALTHCHECK。
- 全仓（含 README/docs）对 railway/render 的引用 **0**；git remote 是 GitHub，仓库中**没有 CI 配置**（无 `.github/`）。无法从仓库判断实际部署平台 → `needs-check`。
- 建议：确认平台后只保留一套。若当前就是 `docker build` 手动部署，两个 PaaS 清单都删（回收 596 B，主要价值是消除"启动命令有 3 份、改一处漏两处"的漂移风险）。

#### 2.5.3 `clear_data.sh` vs `scripts/clear_data.py`：不是重复实现

- `clear_data.sh`（152 B）：`exec python3 "$SCRIPT_DIR/scripts/clear_data.py" "$@"`——**薄包装，没有任何清理逻辑**。
- `scripts/clear_data.py`（3,773 B，最后提交 2026-10-16）：真正的实现，且在 `docs/reviews/2026-10-15-code-review-report.md:55` 与 `.planning/.../progress.md:55` 被讨论/修复（已补齐 packages、运行、评审、LLM 用量、LangGraph 表）。
- `start.sh` 不调用它；全仓只有它自己定义 `clear_data.sh` 字符串。**结论：不存在重复实现，不要删 Python 脚本**；wrapper 可留（用户入口）可删（几乎没人用，引用 0）。

#### 2.5.4 `requirements.txt` vs `requirements-dev.txt`：无冲突，分层正确

- `requirements-dev.txt`（172 B）：`-r requirements.txt` + `pytest==8.3.2` + `pytest-asyncio==0.24.0` + `httpx==0.27.2`。
- `requirements.txt` 约束 `httpx>=0.23.0,<0.28.0`——dev 里的 0.27.2 落在区间内，**无版本冲突**，不是"两份互相打架的依赖清单"。
- `Dockerfile` 只 `pip install -r requirements.txt`，**不安装 dev 依赖**（正确）；`.dockerignore` 也排除了 `requirements-dev.txt` 和 `tests/`。
- 唯一缺口：README 的启动说明只有 `bash start.sh` 与 `LLM_MODE=mock pytest tests/`，没写「跑测试需先 `pip install -r requirements-dev.txt`」。属文档小缺口（needs-check，非删除项）。

#### 2.5.5 `start.sh`、`scripts/build_response_templates.py`、`pytest.ini`：活的

- `start.sh`（4,863 B，2026-09-15）：README:31 与 `docs/superpowers/plans/2026-09-15-...loop.md:2261` 引用；本地开发入口；Docker 不走它（Dockerfile 直接 uvicorn）。**保留**。注意 fs 权限是 `-rwx--x--x`（711，仅 owner 可读），git index 是 100755，换用户 clone 不受影响（git 只存 755）。
- `scripts/build_response_templates.py`（2,787 B，2026-10-16）：被 `docs/design/Word交付格式与导出链路.md:33` 引用，且 `sys.path` 后 `from services.chapter_studio import TEMPLATES` 指向真实模块。**保留**。
- `pytest.ini`（102 B）：`asyncio_mode = auto` + `slow` marker + 默认 `-m "not slow"`。配套 dev 依赖；**保留**。

#### 2.5.6 `README.md`：部分过时（needs-check，非删除项）

- 2026-10-16 的提交 `74511ef` 只改了 2 行（前端主路径 + 旧入口重定向），架构/工作流段落仍是 2026-05-07 v1.0 的 5-agent 流程图。
- 实际：`backend/agents/` 9 个 py（8 个 agent 实现 + `__init__`），`backend/routes/` 14 个模块；README 写「`/api/workflow/*` 7 端点 + `/api/review/{tid}`」。
- README 里仍然有效的部分：`bash start.sh`、入口/工作流/黄金样本路径（文件都存在）、前端主路径（5 个 html 都存在）。
- 建议：更新架构表与端点/agent 描述；不要删 README（它是唯一的启动说明）。

### 2.6 本地垃圾（未跟踪，不影响仓库）

| 项 | 体积 | 状态 | 备注 |
|---|---|---|---|
| `.DS_Store`（根 / frontend / ge-solution） | 22,540 B | 未跟踪，`.gitignore:16` 覆盖 | 2026-09-05 等，纯 macOS 元数据 |
| `.pytest_cache/` | 89,324 B | 未跟踪；pytest 自带 `.gitignore` 使其不可见 | 2026-05-18 后未更新，4 个月前的运行缓存 |
| `docs/screenshots/live-kind-row.png` | 182,657 B | **未跟踪** | 2026-10-17 22:29 留下的单张截图，全仓引用 0 |
| `.planning/` / `.superpowers/` / `.gstack/` | 104 / 120 / 420 KB | 未跟踪且被 ignore | 本地工作目录；`.planning` 有决策历史价值，不建议整体入库也不建议删 |

---

## 3. 已核实「看起来是垃圾但其实有用」的排除项

| 项 | 看起来像 | 实际情况（引用证据） |
|---|---|---|
| `docs/tender/` 3 份 md | 一次性客户文档 | `tests/infra/test_review_points.py:173` 的测试期望值直接来自它；2026-10-12 随解析功能提交 |
| `docs/superpowers/` 15 份 plan/spec | 方法论噪音 | 被 `frontend/project-init.html:2159`、`frontend/assets/mode-outline.js:1310`、`backend/infra/llm/dispatcher.py:691`、`backend/domain/material/models.py:89`、`backend/domain/spec/response_items.py:4` 直接引用 |
| `docs/design/Word交付格式与导出链路.md` | 普通设计稿 | 是 `scripts/build_response_templates.py` 的唯一文档入口（:33） |
| `docs/design/mockups/prod-prototype.html`（1.13 MB） | 与 prod-plan-budget 重复的巨型文件 | 被 `docs/design/原型到代码-落点对照.md:3` 与 `tests/services/test_page_budget.py:3` 引用，是唯一"原型→代码"依据；**建议保留** |
| `docs/screenshots/` 里的 8 张 | 172 张都是垃圾 | 这 8 张被 3 份设计文档引用（见 §2.4.1），删截图时必须保留 |
| `docs/screenshots/ia-no-extract-term.png` | 与 `panel-head-clean.png` 重复 | 它是被 `UI一致性审计.md:520` 引用的那一份；重复对里要删的是未引用的那份 |
| `scripts/clear_data.py` | 与 `clear_data.sh` 重复 | 它是唯一实现；wrapper 才是可删的薄壳 |
| `scripts/build_response_templates.py` | 一次性生成脚本 | 有文档引用且 import 活的 `services.chapter_studio.TEMPLATES`；改模板样式的唯一重建入口 |
| `requirements-dev.txt` | 与 requirements 重复的清单 | 是增量分层（`-r requirements.txt` + pytest），httpx 0.27.2 满足主清单 `<0.28`；Docker 不装它 |
| `start.sh` | 被 Dockerfile 取代的旧启动脚本 | README:31 与 superpowers plan:2261 引用；Docker 镜像不用它，但本地开发入口仍是它 |
| `.api_config.json`（943 B） | 泄露的 key | 未跟踪；`.gitignore` 与 `.dockerignore` 双重排除；**正确排除项，不要入库** |
| `.claude/settings.local.json`（1,039 B） | 应入库的项目配置 | 未跟踪；全局 `~/.gitignore_global` 有 `**/.claude/settings.local.json`；含本机 curl/pytest 白名单，属个人配置 |
| `ge-solution/B - 应标流程.html` + `sidebar.html` | 误提交旧原型 | 2026-09-15 提交信息明确「保留作设计决策的追溯依据，非运行代码」；可留档（但同目录的 jsx/png 是工具壳和过程图，可删） |
| `.planning/`（未跟踪） | 垃圾 | 含 `skill/`、`clear_data.py`、`prod-prototype` 等决策痕迹；既然是本地目录且被 ignore，建议保留而非删除 |
| `backend/data/*.bak-*`（未跟踪） | 可直接删的备份 | **不在本审计范围**（backend/ 内），但它是 `.dockerignore` 风险的主要来源（397.7 MB + 22.2 MB）；由 backend 侧确认后清理 |
| 本地分支 `worktree-security-hardening` | 与损坏 gitlink 一起删 | **未并入** main/feature（`git merge-base --is-ancestor` 失败），可能含未回收的安全改造；删分支前必须人工看 `89f29fb` diff |

---

## 4. 需要产品决策的项

### 4.1 截图留存策略（最大体积项，26.46 MB）
172 张里 164 张零引用。选项：①移出仓库（wiki/对象存储/测试报告附件）；②保留但改 Git LFS；③只保留被引用 8 张 + 后续"只提交被引用截图"规则。**不建议**直接全删——它们是 10-13~10-17 的 QA 证据，且已在历史里。

### 4.2 设计原型（mockups）是否作为长期资产
15 个 html 中只有 `prod-prototype.html` 有引用链；`prod-plan-budget.html` 与其 95%+ 相同。是"保留一份真原型 + 其余归档"，还是"全部保留作为设计过程档案"？若保留，建议加 `README.md` 说明哪个是当前有效版本、哪些是过程稿。

### 4.3 `skill/` 的定位
是"agent 生产工具链"（应迁 `.claude/skills/` 并修正路径）还是"历史资产"（迁出仓库）？当前形态两边都不满足：零引用 + 指向不存在的 `.claude/skill/` 路径 + 5 份重复规范。**注意**：2026-09-16 还在修它的脚本，说明近期有人用，删前必须确认。

### 4.4 `ge-solution/` 是否留档
提交信息说是"设计决策追溯依据"，但同目录 74 KB 通用工具壳 + 508 KB 临时粘贴图与"追溯"无关。建议"留 HTML + sidebar，删 jsx/uploads"。

### 4.5 `docs/reviews/2026-10-15-code-review-report.md` 的状态
报告结论「发布门禁不通过（P1×6）」是 10-15 快照；之后本分支增加了大量修复提交（HEAD 比 origin 领先 249 个提交）。需确认：P1 是否已关闭并复审？建议在文首标注状态，避免后续读者误判当前质量。

### 4.6 是否重写 git 历史以真正缩容 clone
删 HEAD 截图只回收 26.46 MB 工作区；`.git` 36.23 MiB pack 不变。若目标包括"减小 clone 体积"，需要 `git filter-repo --path docs/screenshots --invert-paths` 等历史重写：**会改写全部 commit hash、影响所有协作者与 open PR（本分支领先 origin 249 提交）**，必须全组协调并在镜像/备份后执行。默认建议：**本轮不做**，先阻止未来再增长（.gitignore/流程），等分支合并后再评估。

### 4.7 真实客户/招标信息是否允许入库（安全）
- `docs/design/mockups/*.html` 中「南方电网」850 次、「南网」94 次、「广东电网」7 次；`docs/tender/` 是真实标包 2 的评分点/技术要求拆解；`docs/screenshots/` 与 `backend/data/uploads/` 含真实投标文件与项目名。
- repo 的 remote 是 `https://github.com/shelby982/tech-solution-generator.git`，**可见性未核实**（本次未联网/未 ls-remote）。
- 决策：①repo 是否私有？②mockups/tender/截图里的客户名与招标内容是否需脱敏或移出仓库？`docs/tender` 被测试引用，脱敏需同步改测试期望值。

### 4.8 部署平台确认
`railway.toml` 与 `render.yaml` 二选一还是都不用了？目前 5 个月没改动、无引用、无 CI，无法从仓库判断。若都不用了建议删，让 `Dockerfile` 成为唯一启动事实源。

---

## 5. 建议清理顺序与每步验证命令（**本节命令不要现在执行**，供 owner 评审后按序做）

> 原则：先零风险（配置/本地垃圾）→ 再零引用（gitlink/stray 文件）→ 再证据类（截图/原型/skill，需 §4 决策）→ 最后才考虑历史重写。
> 每步保持一个独立提交，提交信息写清"删除依据 = 引用检查为 0"，便于 revert。

### 步骤 0：基线与快照（只读）
```bash
cd /Users/jianghe/projects/ge-solution
git status --porcelain
git ls-files | wc -l
du -sh .git
git -c core.quotepath=false ls-files -z | xargs -0 stat -f '%z' | awk '{s+=$1} END{print s}'   # 35.88MB
# 先备份工作区（含未跟踪）：tar 排除 .venv/ .git/ backend/data/
```
验收：记录 541 files / 35,881,xxx B / .git 47 MB 三个基线数字，后续每步对比。

### 步骤 1：修 `.dockerignore`（零删除，收益最大）
修改后：
```bash
# 只读模拟：分别按「当前 .dockerignore」和「修复后规则」走一遍文件系统
python3 - <<'PY'
import os
# 当前 .dockerignore 近似规则（Docker 只认 .dockerignore，不读 .gitignore）
current = {'.venv','venv','__pycache__','tests','.git','.pytest_cache','tmp','temp'}
# 建议新增排除后的规则
fixed   = current | {'backend/data','docs','skill','ge-solution','.claude',
                     '.planning','.superpowers','.gstack'}
skip_files = {'.dockerignore','Dockerfile','requirements-dev.txt','.api_config.json',
              '.gitignore','README.md'}
for label, ign in [('current', current), ('after-fix', fixed)]:
    tot = 0
    for dp, dns, fns in os.walk('.'):
        dns[:] = [d for d in dns
                  if os.path.relpath(os.path.join(dp, d), '.') not in ign
                  and not d.endswith('__pycache__')]
        for f in fns:
            if f in skip_files or f.endswith(('.pyc','.swp','.log')):
                continue
            try: tot += os.path.getsize(os.path.join(dp, f))
            except OSError: pass
    print(f'{label} docker build context ≈ {tot/1048576:.0f} MB')
PY
# 预期输出：current ≈ 631 MB（backend/data 599 MB + docs 32 MB）；after-fix ≈ 3 MB
# 真正验证需要构建（本轮禁止）：
# docker build -t ge-solution:audit .  2>&1 | grep "transferring context"
```
验收：context 估算从 ~630 MB → <15 MB；镜像内 `ls /app/backend/data` 应为不存在（COPY 前已忽略，`RUN mkdir -p /data` 仍保留）。

### 步骤 2：本地垃圾（不产生 git 变更）
```bash
rm -f .DS_Store frontend/.DS_Store ge-solution/.DS_Store
rm -rf .pytest_cache
rm -f docs/screenshots/live-kind-row.png        # 未跟踪且零引用
```
验收：`git status --porcelain` 与清理前完全一致（这些文件本就不在 git 里，除 live-kind-row.png 的 `??` 消失）。

### 步骤 3：删除损坏 gitlink + 补 `.gitignore`
```bash
git rm --cached .claude/worktrees/groovy-shimmying-elephant .claude/worktrees/security-hardening
printf '\n# 本地 git worktree 目录（曾误提交为 gitlink）\n.claude/worktrees/\n\n# pytest 缓存\n.pytest_cache/\n' >> .gitignore
git add .gitignore && git commit -m "chore(repo): 移除指向不存在 commit 的 .claude/worktrees gitlink；忽略 worktrees/pytest_cache"
```
验收：
```bash
git ls-files .claude            # 应为空
git submodule status             # 不再 fatal
git ls-files | wc -l             # 541 - 2 = 539
```

### 步骤 4：ge-solution 精简（需 §4.4 决策；若决定全留则跳过）
```bash
# 删除与"设计追溯"无关的工具壳与临时粘贴图（582 KB）
git rm -r ge-solution/design-canvas.jsx ge-solution/tweaks-panel.jsx ge-solution/uploads
# 可选：把两份 HTML 移到 docs/design/legacy/
git mv "ge-solution/B - 应标流程.html" "ge-solution/sidebar.html" docs/design/legacy/ 2>/dev/null || true
```
验收：`grep -rIn --exclude-dir=__pycache__ "ge-solution/" backend frontend tests scripts docs` 应仍只有 wiki 路径或无命中。

### 步骤 5：截图收敛（26.46 MB，需 §4.1 决策）
```bash
# 5.1 生成"未引用清单"（保留 8 张被引用的 + 5 对重复里被引用的那一份）
REF='confirm-redesigned.png confirm-edit-structure.png batch-operations.png statusbar-removed.png ia-no-extract-term.png confirm-shows-result.png desk-a.png card-menu.png'
: > /tmp/ss_unref.txt
while IFS= read -r p; do
  b=$(basename "$p")
  case " $REF " in *" $b "*) ;; *) echo "$p" >> /tmp/ss_unref.txt;; esac
done < <(git -c core.quotepath=false ls-files docs/screenshots | grep '\.png$')
wc -l /tmp/ss_unref.txt                       # 应为 164
# 5.2 人工抽查后批量移出（示例：先放到仓外备份目录）
mkdir -p /tmp/screenshots-archive && while read -r p; do cp "$p" /tmp/screenshots-archive/; done < /tmp/ss_unref.txt
while read -r p; do git rm --cached "$p"; done < /tmp/ss_unref.txt
git commit -m "chore(docs): 移出未被引用的 164 张界面截图（保留 8 张被设计文档引用的）"
```
验收：
```bash
grep -rIn --include='*.md' --include='*.py' "docs/screenshots" docs backend frontend tests | grep -v _audit   # 仍应有 7 行 + 1 行
git ls-files docs/screenshots | wc -l          # 8
```
注意：`git rm --cached` 只移出跟踪；如需同时删工作区才加 `git rm`。历史 blob 仍在，clone 不变（见 §4.6）。

### 步骤 6：mockups 归档（需 §4.2 决策）
```bash
# 保留唯一被引用的 prod-prototype.html；其余移到 archive 子目录（保留追溯，不再误导为当前原型）
mkdir -p docs/design/mockups/archive
for f in docs/design/mockups/*.html; do
  [ "$(basename "$f")" = "prod-prototype.html" ] && continue
  git mv "$f" docs/design/mockups/archive/
done
```
验收：
```bash
grep -rIn "prod-plan-budget\|five-stage-panel\|plan-1-package" docs backend frontend tests scripts | grep -v _audit   # 应为 0
test -f docs/design/mockups/prod-prototype.html && echo keep-ok
```

### 步骤 7：`skill/` 决策落地（需 §4.3 决策）
- 若迁移：`git mv skill/<name> .claude/skills/<name>/...` 并批量修正 SKILL.md 中 `.claude/skill/` → `.claude/skills/`，去重 5 份 `应标语言规范.md`。
- 若删除：`git rm -r skill/` 后确认 `grep -rn "skill/" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__` 仅剩历史/忽略目录。
验收：`ls .claude/skills/*/SKILL.md | wc -l`（迁移后 17）或 `test ! -d skill && echo removed`；跑一次 agent 触发词验证可加载（本轮未启动服务）。

### 步骤 8：部署清单与文档（需 §4.8/§4.5 决策）
```bash
# 二选一（示例：都不使用 PaaS 清单）
git rm railway.toml render.yaml
# README 架构段更新（5 agents → 实际 agent 清单/路由数；补 dev 依赖安装说明）
```
验收：`grep -rn "railway\|render.yaml" . --exclude-dir=.git` = 0；README 中提到的每个路径 `test -f` 通过。

### 步骤 9：历史重写（默认不做，需 §4.6 决策 + 全组协调）
```bash
# 只在镜像/备份后、分支合并前，且所有协作者停手时执行：
# git filter-repo --path docs/screenshots --invert-paths
# git filter-repo --path skill --invert-paths     # 若决定删 skill
# 然后 force-push 所有分支；通知所有协作者重新 clone
```
验收：`git count-objects -vH` 的 size-pack 应显著下降；`git rev-list --count HEAD` 不变；全量 `git fsck` 通过。

---

## 附录 A：关键证据命令与输出（本审计实际执行）

```bash
# 基线
git branch --show-current                         # feature/multi-agent-bid-system
git ls-files | wc -l                              # 541
du -sh . .git .venv backend/data docs screenshots... # 1.2G / 47M / 490M / 599M / 32M ...
git count-objects -vH                             # size-pack: 36.23 MiB, in-pack: 6373, commits: 447

# 引用检查（全部为只读 grep）
grep -rn "skill/" backend/ frontend/ tests/ scripts/ *.py          # 0
grep -rIn -w "skill" backend frontend tests scripts *.py *.sh *.md  # 0
grep -rIn "docs/superpowers" .                                     # 10 行，含 5 处产品代码
grep -rIn -F "prod-prototype" docs backend frontend tests scripts  # 2 行
grep -rIn "docs/tender" docs backend frontend tests scripts        # 1 行（test_review_points.py:173）
grep -rIn "screenshots/" backend frontend tests scripts            # 0
grep -rIn "railway\|render.yaml" . --exclude-dir=.git              # 0
grep -rIn "design-canvas\|tweaks-panel" backend frontend tests scripts docs # 0（除自身）
grep -rIn "clear_data.sh" . --exclude-dir=.git                     # 0
git log -1 --format='%ad %s' --date=short -- <each path>            # 见 §1 每行日期

# 跟踪/日期
git -c core.quotepath=false ls-files ge-solution skill docs        # 9 / 39 / 231
git ls-files -s .claude                                            # 2×160000 gitlink
git cat-file -t ffeda42acc608bfde79ffe54efb3baac43485ac3           # 失败：对象不存在
git submodule status                                               # fatal: no submodule mapping
git clone --no-local . /tmp/aux-clone-test && git -C /tmp/aux-clone-test submodule status  # clone 成功、submodule fatal

# 截图引用统计（对 172 个 basename 在全部 md 中查找）
find docs backend frontend tests -name '*.md' -not -path '*/_audit/*' -print0 | xargs -0 cat > /tmp/allmd.txt
# 结果：8 referenced / 164 unreferenced；unref bytes = 27,740,419 (26.46 MB)

# 重复检测
find docs/screenshots -name '*.png' -exec md5 -r {} + | awk ...    # 5 对完全相同
find skill -name '应标语言规范.md' -exec md5 -r {} +                 # 5 份相同 + 1 份不同
diff <(sed 's/[[:space:]]//g' prod-prototype.html) <(sed 's/[[:space:]]//g' prod-plan-budget.html) | grep -c '^[<>]'  # 679

# 体积/上下文
find backend/data -type f -exec stat -f '%z %N' {} +               # db 180.9MB / bak 397.7MB / bak 22.2MB / uploads 22MB
cat .dockerignore                                                  # 37 行，无 backend/data / docs / skill / ge-solution
grep -n "COPY" Dockerfile                                          # COPY backend/ ./backend/
```

## 附录 B：限制与未做的事

- **未执行**（按要求）：任何删除/移动/修改项目文件；`git checkout/stash/reset/rm`；启动服务或构建镜像；访问网络（含 remote 可见性、Railway/Render 账号）。
- **未验证**：实际部署平台（railway/render 是否在用）；`docs/reviews` 中 P1×6 是否已全部关闭；`.claude/worktrees` 对应的两个本地分支是否可安全删（`worktree-security-hardening` 未合并）；repo 是否公开。
- **一处过程说明（已恢复原状）**：验证 `skill/*/scripts/*.py` 语法时曾用 `python3 -m py_compile`，在 `skill/*/scripts/` 生成了被 `.gitignore` 覆盖的 `__pycache__`；已逐目录确认只含本次生成的 `.pyc` 后删除，`git status skill/` 为空，仓库内容无变化。之后未再运行任何写文件命令（除本报告）。
- 引用统计是"文本 grep"：动态拼接路径/正则运行时引用无法覆盖；但候选均为文档/原型/脚本，静态引用已足以判定。
- 体积为文件字节精确值；`du -h` 的块对齐差异会略大（如 skill 236 KB vs 316 KB）。
