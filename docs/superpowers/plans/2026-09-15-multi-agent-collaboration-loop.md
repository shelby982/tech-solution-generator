# 多 Agent 协同闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 5 agent 架构上补齐"审核回灌 + 迭代控制 + 按需补料"三条闭环，让编写/收集/审核三个角色真正形成反馈回路，而不是单向流水线。

**Architecture:** 新增三个不调 LLM 的编排节点（`collect_gaps` / `check_convergence` / `build_feedback`）承担角色间的调度与转译。`collect_gaps` 置于 `zhuge_liang_generate` **之前**，使"编写主动要料"与"审核意见要料"两个来源的请求合流到同一消费点，都在下一轮生成前就位。审核意见通过 `review.feedback` 注入生成 prompt，通过 `Issue.needs_material` 转成补料请求。

**Tech Stack:** Python 3.13 / FastAPI / LangGraph（StateGraph + AsyncSqliteSaver）/ pytest（`LLM_MODE=mock`）

**Spec:** `docs/superpowers/specs/2026-09-15-multi-agent-collaboration-loop-design.md`

---

## 前置条件（开工前必须完成）

工作区当前有 65 个未提交文件。**必须先落盘再开始 Task 1**，否则本计划的回归失败无法与既有未提交改动二分定位。

```bash
git status --short | grep -v pycache | wc -l   # 应显示 65
git add -A && git commit -m "chore: 落盘多 agent 系统既有改动"
git status --short | grep -v pycache | wc -l   # 应显示 0
```

---

## File Structure

**修改的文件（本计划涉及全部 11 个）：**

| 文件 | 职责 | 涉及 Task |
|---|---|---|
| `backend/domain/review/models.py` | `Issue` 增加 `needs_material` / `material_query` | 1 |
| `backend/orchestrator/state.py` | `WorkflowState` 增加闭环字段 | 2 |
| `backend/agents/shen_kuo.py` | 新增 `retrieve_for()` 批量按需检索 | 3 |
| `backend/agents/prompts.py` | 评审 prompt 加补料字段；新增 `format_feedback_block()`；大纲/正文 prompt 支持反馈注入 | 4, 6, 7 |
| `backend/agents/wang_anshi.py` | `_parse_finding` 解析补料字段 | 4 |
| `backend/agents/bao_zheng.py` | 同上 | 4 |
| `backend/domain/proposal/models.py` | `BlockOutput` 增加 `material_requests` | 5 |
| `backend/agents/zhuge_liang.py` | 补料请求抽取 + 修订分支 | 5, 7 |
| `backend/infra/llm/dispatcher.py` | `dispatch_block_write` 支持反馈注入 | 6 |
| `backend/orchestrator/events.py` | 新增 5 个 SSE 事件 | 8 |
| `backend/orchestrator/nodes.py` | 3 个新节点 + 2 处节点改造 | 9 |
| `backend/orchestrator/graph.py` | 边变更 | 10 |
| `frontend/assets/api.js` | 新事件映射 | 11 |
| `frontend/assets/review.js` | 迭代轮次与未收敛展示 | 11 |
| `frontend/review.html` | 承载迭代/未收敛的 DOM 节点 | 11 |

**新增测试：** 全部追加到既有测试文件，不新建文件。

---

### Task 1: `Issue` 模型增加补料字段

**Files:**
- Modify: `backend/domain/review/models.py:6-26`
- Test: `tests/domain/test_review_repository.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/domain/test_review_repository.py` 末尾：

```python
def test_issue_new_material_fields_roundtrip():
    """Issue 的 needs_material / material_query 能完整往返。"""
    from domain.review import Issue

    issue = Issue(
        severity="critical",
        point="未响应否决项『必须提供 3 年内同类项目业绩证明』",
        suggestion="补充业绩证明材料",
        needs_material=True,
        material_query="近三年同类项目业绩证明合同",
    )

    d = issue.to_dict()
    assert d["needs_material"] is True
    assert d["material_query"] == "近三年同类项目业绩证明合同"
    assert Issue.from_dict(d) == issue


def test_issue_from_dict_tolerates_legacy_payload():
    """老 checkpoint / 老 reviews 表数据没有新字段，反序列化必须不炸。"""
    from domain.review import Issue

    legacy = {"severity": "high", "point": "p", "suggestion": "s"}
    issue = Issue.from_dict(legacy)

    assert issue.needs_material is False
    assert issue.material_query == ""


def test_issue_defaults_are_rewrite_problems():
    """新建 Issue 默认不是补料问题——避免误把重写类问题转成检索请求。"""
    from domain.review import Issue

    issue = Issue(severity="medium", point="表述冗余")
    assert issue.needs_material is False
    assert issue.material_query == ""
    assert issue.to_dict()["needs_material"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/domain/test_review_repository.py -k issue -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'needs_material'`

- [ ] **Step 3: 实现**

把 `backend/domain/review/models.py` 的 `Issue` 整个替换为：

```python
@dataclass
class Issue:
    """评审发现的单个问题。

    needs_material / material_query 由评审 agent 在输出 JSON 里自行声明，
    而不是事后用 LLM 分类——包拯的视角本就包含「证明材料是否齐备」，
    它与王安石天然知道某条问题是"没写"还是"没料可写"。
    """
    severity: str            # critical | high | medium | low
    point: str               # 问题描述
    suggestion: str = ""     # 改进建议
    needs_material: bool = False    # 必须补外部素材才能修复，非重写可解决
    material_query: str = ""        # 缺什么，直接当检索 query

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "point": self.point,
            "suggestion": self.suggestion,
            "needs_material": bool(self.needs_material),
            "material_query": self.material_query,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        return cls(
            severity=d.get("severity", "medium"),
            point=d.get("point", ""),
            suggestion=d.get("suggestion", ""),
            needs_material=bool(d.get("needs_material", False)),
            material_query=d.get("material_query", ""),
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/domain/test_review_repository.py -k issue -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 跑全量 domain 测试确认无回归**

Run: `PYTHONPATH=backend pytest tests/domain/ -v`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add backend/domain/review/models.py tests/domain/test_review_repository.py
git commit -m "feat(domain): Issue 增加 needs_material / material_query 字段"
```

---

### Task 2: `WorkflowState` 增加闭环字段

**Files:**
- Modify: `backend/orchestrator/state.py:41-52`（ProposalState）、`:47-52`（ReviewState）、`:109-143`（WorkflowState）
- Test: `tests/orchestrator/test_state.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/orchestrator/test_state.py` 末尾：

```python
def test_merge_state_merges_new_proposal_fields():
    """material_requests / updated_blocks 走 proposal 的浅 merge，不冲掉 blocks。"""
    from orchestrator.state import merge_state

    base = {"proposal": {"blocks": {"s1": {"content": "x"}}, "regenerate_targets": []}}
    patch = {
        "proposal": {
            "material_requests": {"s1": [{"query": "业绩证明", "reason": "缺证据"}]},
            "updated_blocks": ["s1"],
        }
    }

    merged = merge_state(base, patch)

    assert merged["proposal"]["blocks"] == {"s1": {"content": "x"}}
    assert merged["proposal"]["material_requests"] == {
        "s1": [{"query": "业绩证明", "reason": "缺证据"}]
    }
    assert merged["proposal"]["updated_blocks"] == ["s1"]


def test_merge_state_merges_new_review_fields():
    """feedback / convergence 走 review 的浅 merge，不冲掉 findings。"""
    from orchestrator.state import merge_state

    base = {"review": {"tech_findings": {"s1": {"score": 50}}}}
    patch = {
        "review": {
            "feedback": {"s1": {"issues": [], "scores": {"tech": 50, "comp": 40}}},
            "convergence": {"status": "refine", "unconverged_blocks": ["s1"]},
        }
    }

    merged = merge_state(base, patch)

    assert merged["review"]["tech_findings"] == {"s1": {"score": 50}}
    assert merged["review"]["feedback"]["s1"]["scores"]["tech"] == 50
    assert merged["review"]["convergence"]["status"] == "refine"


def test_iteration_is_scalar_and_overwrites():
    """iteration 是标量，patch 直接覆盖而非合并。"""
    from orchestrator.state import merge_state

    assert merge_state({"iteration": 1}, {"iteration": 2})["iteration"] == 2
    assert merge_state({}, {"iteration": 1})["iteration"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_state.py -k "new_proposal_fields or new_review_fields or scalar" -v`
Expected: **实际上是 3 passed，不会失败。**

⚠️ 执行时发现的计划缺陷：`merge_state()` 只按字面 key 做浅 union，不认识 TypedDict
声明；而 TypedDict 的注解在运行时被擦除 —— 新字段加不加，`merge_state` 都照常合并
成功。上面三个用例测的是 merge_state 的既有通用行为，**在改动前也是绿的**，给不出
回归保护。

因此 Step 1 追加的测试中必须再补一个真正锁住声明的用例
（`test_closed_loop_fields_are_declared`，断言 `__annotations__` 含新字段）：
改动前 FAIL、改动后 PASS，这才是 Task 2 的红灯。

- [ ] **Step 3: 实现**

在 `backend/orchestrator/state.py` 中，把 `ProposalState` 替换为：

```python
class ProposalState(TypedDict, total=False):
    """诸葛亮产出。"""
    blocks: dict[str, dict]            # block_id → BlockOutput.to_dict()
    regenerate_targets: list[str]
    updated_blocks: list[str]          # 本轮实际生成的 block（评审范围）
    material_requests: dict[str, list[dict]]   # block_id → [{query, reason}]
```

把 `ReviewState` 替换为：

```python
class ReviewState(TypedDict, total=False):
    """两位评审 agent + aggregate/判定/转译节点产出。"""
    tech_findings: dict[str, dict]         # block_id → Finding.to_dict()
    compliance_findings: dict[str, dict]
    report: dict                            # GlobalReport.to_dict()
    feedback: dict[str, dict]               # block_id → {issues, scores}
    convergence: dict                       # {status, unconverged_blocks, reason}
```

在 `WorkflowState` 的元信息区（`cancel_requested: bool` 之后）加一行：

```python
    iteration: int                     # 协同闭环迭代轮次，首轮为 0
```

同时在模块 docstring 的"字段写入约定"里补三行（追加到现有列表末尾）：

```
- 编排闭环节点：collect_gaps 写 materials.matches / proposal.material_requests
-               check_convergence 写 review.convergence
-               build_feedback 写 review.feedback / proposal.material_requests /
-                               proposal.regenerate_targets / iteration
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_state.py -v`
Expected: PASS（全绿）

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/state.py tests/orchestrator/test_state.py
git commit -m "feat(orchestrator): WorkflowState 增加协同闭环字段"
```

---

### Task 3: `ShenKuoAgent.retrieve_for()` 批量按需检索

**Files:**
- Modify: `backend/agents/shen_kuo.py`（在 `match()` 之后新增方法）
- Test: `tests/agents/test_shen_kuo.py`

**接口说明（与 spec §4.3 的差异）：** spec 写的是 `retrieve_for(requests, chunks)` 逐 block 调用，本计划改为**批量入参** `retrieve_for(requests_by_block, chunks)`。理由：`match()` 的 docstring 明确"全 corpus BM25 索引一次性构建，避免每 block 重建"，逐 block 调用会导致 N 次重建索引。批量签名在收集角色内部消化这个优化，节点层无需感知。

- [ ] **Step 1: 写失败测试**

追加到 `tests/agents/test_shen_kuo.py` 末尾：

```python
async def test_retrieve_for_empty_inputs_returns_empty():
    """空请求 / 空语料都返回 {}，不抛错。"""
    agent = ShenKuoAgent(configs_provider=lambda: ([], 0))

    assert await agent.retrieve_for({}, [{"id": 1, "content": "x"}]) == {}
    assert await agent.retrieve_for({"s1": [{"query": "q"}]}, []) == {}


async def test_retrieve_for_without_llm_falls_back_to_keyword():
    """无 LLM 配置时降级为纯关键词截断，与 match() 的降级行为一致。"""
    agent = ShenKuoAgent(configs_provider=lambda: ([], 0), rerank_top_n=2)
    chunks = [
        {"id": 1, "content": "配电柜温升试验报告 型式试验"},
        {"id": 2, "content": "无关内容 园林绿化"},
        {"id": 3, "content": "配电柜温升 试验数据"},
    ]

    result = await agent.retrieve_for({"s1": [{"query": "配电柜温升试验"}]}, chunks)

    assert "s1" in result
    assert len(result["s1"]) <= 2
    assert all(m.score == 0.0 for m in result["s1"])
    assert all("未配置 LLM" in m.reason for m in result["s1"])


async def test_retrieve_for_batches_all_requests():
    """多个 block 的请求在一次调用里处理，每块各得结果。"""
    agent = ShenKuoAgent(configs_provider=lambda: ([], 0), rerank_top_n=2)
    chunks = [
        {"id": 1, "content": "配电柜温升试验报告"},
        {"id": 2, "content": "安全生产许可证 复印件"},
    ]

    result = await agent.retrieve_for(
        {
            "s1": [{"query": "配电柜温升试验"}],
            "s2": [{"query": "安全生产许可证"}],
        },
        chunks,
    )

    assert set(result.keys()) == {"s1", "s2"}


async def test_retrieve_for_skips_blank_queries():
    """空 query 的请求被跳过，不产生检索。"""
    agent = ShenKuoAgent(configs_provider=lambda: ([], 0))
    chunks = [{"id": 1, "content": "配电柜温升试验报告"}]

    result = await agent.retrieve_for({"s1": [{"query": "   "}, {}]}, chunks)

    assert result == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/agents/test_shen_kuo.py -k retrieve_for -v`
Expected: FAIL — `AttributeError: 'ShenKuoAgent' object has no attribute 'retrieve_for'`

- [ ] **Step 3: 实现**

在 `backend/agents/shen_kuo.py` 的 `match()` 方法之后（`__all__` 之前）插入：

```python
    # ── retrieve_for ───────────────────────────

    async def retrieve_for(
        self,
        requests_by_block: dict[str, list[dict]],
        chunks: list[dict],
    ) -> dict[str, list[Match]]:
        """按补料请求定向检索素材 —— 收集角色的按需服务入口。

        requests_by_block: {block_id: [{"query": str, "reason": str}, ...]}
        chunks:            全语料切片

        返回 {block_id: list[Match]}，只为真正检索到结果的 block 建键。
        空入参 → 返回 {}。BM25 索引对本批次只建一次。

        单条请求检索/重排失败只跳过该请求，不影响其它请求与其它 block。
        """
        if not requests_by_block or not chunks:
            return {}

        configs, rr_start = self._configs_provider()
        bm25_index, _ = build_bm25_index(chunks)

        result: dict[str, list[Match]] = {}
        idx = 0

        for block_id, requests in requests_by_block.items():
            merged: dict[object, Match] = {}

            for req in (requests or []):
                query = (req.get("query") or "").strip()
                if not query:
                    continue

                candidates = keyword_search(
                    chunks, query=query, top_k=self.keyword_top_k,
                    bm25_index=bm25_index,
                )
                rr_index = (rr_start + idx) % max(len(configs), 1)
                idx += 1

                if not candidates:
                    continue

                if not configs:
                    # 与 match() 的降级一致：关键词 top_n 截断，score=0 标识未重排
                    for c in candidates[: self.rerank_top_n]:
                        cid = c.get("id") or c.get("chunk_id")
                        if cid not in merged:
                            merged[cid] = Match(
                                chunk_id=cid,
                                score=0.0,
                                reason="(未配置 LLM，仅关键词检索结果)",
                                hit_points=[],
                            )
                    continue

                try:
                    matches = await llm_rerank(
                        candidates,
                        query=query,
                        requirement=(req.get("reason") or query),
                        top_n=self.rerank_top_n,
                        configs=configs,
                        rr_start_index=rr_index,
                    )
                except Exception as e:
                    logger.warning(
                        f"沈括：补料请求 {query!r}（{block_id}）重排异常，跳过：{e}"
                    )
                    continue

                # 同一 chunk 被多条请求命中时保留高分
                for m in matches:
                    existing = merged.get(m.chunk_id)
                    if existing is None or m.score > existing.score:
                        merged[m.chunk_id] = m

            if merged:
                result[block_id] = sorted(
                    merged.values(), key=lambda x: x.score, reverse=True,
                )

        return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/agents/test_shen_kuo.py -v`
Expected: PASS（原有 + 新增 4 个）

- [ ] **Step 5: Commit**

```bash
git add backend/agents/shen_kuo.py tests/agents/test_shen_kuo.py
git commit -m "feat(shen_kuo): 新增 retrieve_for 批量按需检索"
```

---

### Task 4: 两位评审 agent 输出 `needs_material`

**Files:**
- Modify: `backend/agents/prompts.py:275-301`（`build_tech_review_user`）、`:320-345`（`build_compliance_review_user`）
- Modify: `backend/agents/wang_anshi.py:179-189`（`_parse_finding` 的 issues 解析）
- Modify: `backend/agents/bao_zheng.py`（同结构的 `_parse_finding`）
- Test: `tests/agents/test_prompts.py`、`tests/agents/test_wang_anshi.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/agents/test_prompts.py` 末尾：

```python
def test_review_prompts_request_material_fields():
    """两位评审的 user prompt 都必须要求输出 needs_material / material_query。"""
    from agents.prompts import build_compliance_review_user, build_tech_review_user

    block = {"block_id": "s1", "title": "配电系统", "content": "正文", "kind": "tech"}
    row = {"requirement": "提供业绩证明", "veto_items": "★必须提供 3 年内业绩"}

    for prompt in (
        build_tech_review_user(block, row),
        build_compliance_review_user(block, row),
    ):
        assert "needs_material" in prompt
        assert "material_query" in prompt
```

追加到 `tests/agents/test_wang_anshi.py` 末尾：

```python
def test_parse_finding_reads_material_fields():
    """_parse_finding 解析 needs_material / material_query。"""
    agent = WangAnshiAgent(configs_provider=lambda: ([], 0))
    raw = json.dumps({
        "score": 60,
        "issues": [
            {
                "severity": "critical",
                "point": "未提供型式试验数据",
                "suggestion": "补充试验报告",
                "needs_material": True,
                "material_query": "配电柜型式试验报告",
            },
            {
                "severity": "low",
                "point": "表述冗长",
                "suggestion": "精简",
            },
        ],
        "strengths": [],
    })

    finding = agent._parse_finding("s1", raw)

    assert finding.issues[0].needs_material is True
    assert finding.issues[0].material_query == "配电柜型式试验报告"
    # 缺省字段必须退化为"非补料问题"，否则会把重写类问题误转成检索请求
    assert finding.issues[1].needs_material is False
    assert finding.issues[1].material_query == ""
```

（`test_wang_anshi.py` 若未 `import json` 需在文件头补上。）

⚠️ 执行时发现的计划缺陷：还要给 `tests/agents/test_bao_zheng.py` 追加**镜像的**
同款测试（断言 `needs_material` / `material_query` 的解析与缺省退化）。原计划只
覆盖了王安石一侧 —— 而两者的 `_parse_finding` 实现逐字节相同，把包拯那侧改坏
不会有任何测试报警。变异实测：将 bao_zheng 的解析改为恒返回 False/""，全量
`209 passed` 仍全绿。合规视角恰是最常提出补料需求的一方（缺资质证书、业绩证明、
检测报告），漏测方向是反的。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/agents/test_prompts.py -k material_fields tests/agents/test_wang_anshi.py -k material_fields -v`
Expected: FAIL — 断言 `"needs_material" in prompt` 为 False

- [ ] **Step 3: 实现 —— 改两处 prompt**

`backend/agents/prompts.py` 的 `build_tech_review_user`，把 `"- issues: ..."` 那一行替换为：

```python
        "- issues: 问题清单 [{severity, point, suggestion, needs_material, material_query}]，"
        "severity 取 critical/high/medium/low\n"
        "  - needs_material：该问题是否**必须依靠补充外部素材**（证书/合同/检测报告/业绩证明/"
        "参数表）才能修复。若属于「正文没写」「写法不佳」「表述不准确」这类重写即可解决的问题，"
        "**必须为 false**。\n"
        "  - material_query：当 needs_material=true 时，填入用于检索素材库的查询词"
        "（15 字以内，具体到材料名称，如「配电柜型式试验报告」）；否则留空字符串。\n"
```

`build_compliance_review_user` 同样替换其 `"- issues: ..."` 行，并在"否决项任一未响应必须列为 critical"之后追加完全相同的两个子项说明（`needs_material` / `material_query` 的措辞与上面逐字一致，不要改写——两边口径必须一致）。

- [ ] **Step 4: 实现 —— 改两处解析**

`backend/agents/wang_anshi.py` 的 `_parse_finding`，把构造 `Issue` 的片段替换为：

```python
                issues.append(Issue(
                    severity=str(item.get("severity", "medium")),
                    point=str(item.get("point", "")),
                    suggestion=str(item.get("suggestion", "")),
                    needs_material=bool(item.get("needs_material", False)),
                    material_query=str(item.get("material_query", "") or ""),
                ))
```

`backend/agents/bao_zheng.py` 的 `_parse_finding` 做**完全相同**的替换。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/agents/ -v`
Expected: PASS（全绿）

- [ ] **Step 6: Commit**

```bash
git add backend/agents/prompts.py backend/agents/wang_anshi.py backend/agents/bao_zheng.py tests/agents/test_prompts.py tests/agents/test_wang_anshi.py
git commit -m "feat(agents): 评审 agent 声明 needs_material / material_query"
```

---

### Task 5: `BlockOutput.material_requests` 与大纲占位符抽取

**Files:**
- Modify: `backend/domain/proposal/models.py:45-79`（`BlockOutput`）
- Modify: `backend/agents/zhuge_liang.py`（新增正则与 `_collect_material_requests`）
- Test: `tests/agents/test_zhuge_liang.py`

**为什么从大纲占位符抽取而不是再调一次 LLM：** `build_section_outline_user`（`prompts.py:203-204`）第 4 条已经定义了 `【待补充：xx】` 占位符的语义——"暂时无法直接生成的具体数据/案例/品牌型号/数值"。这正是"写的时候发现素材不够"的标记。直接抽取可省掉每 block 每轮一次 LLM 调用。局限是非素材类占位符（如"【待补充：项目名称】"）会产生无效请求，但 `retrieve_for` 检索不到就返回空，代价仅为一次 BM25 查询。

- [ ] **Step 1: 写失败测试**

追加到 `tests/agents/test_zhuge_liang.py` 末尾：

```python
def test_collect_material_requests_extracts_placeholders():
    """从写作大纲的 【待补充：xx】 占位符抽取补料请求。"""
    from agents.zhuge_liang import _collect_material_requests

    outline = (
        "## 配电系统\n"
        "- 引用【待补充：配电柜型式试验报告】证明绝缘性能\n"
        "- 参见【待补充：近三年同类项目业绩证明合同】\n"
        "- 交付周期【待补充：配电柜型式试验报告】\n"
    )

    reqs = _collect_material_requests(outline)

    assert [r["query"] for r in reqs] == [
        "配电柜型式试验报告",
        "近三年同类项目业绩证明合同",
    ]
    assert all(r["reason"] for r in reqs)


def test_collect_material_requests_handles_empty_and_none():
    """无大纲 / 无占位符都返回空列表，不抛错。"""
    from agents.zhuge_liang import _collect_material_requests

    assert _collect_material_requests("") == []
    assert _collect_material_requests("## 纯文字大纲，没有占位符") == []


def test_block_output_carries_material_requests():
    """BlockOutput 的 material_requests 能完整往返。"""
    from domain.proposal import BlockOutput

    out = BlockOutput(
        block_id="s1",
        kind="tech",
        content="正文",
        material_requests=[{"query": "业绩证明", "reason": "缺证据"}],
    )

    d = out.to_dict()
    assert d["material_requests"] == [{"query": "业绩证明", "reason": "缺证据"}]
    assert BlockOutput.from_dict(d).material_requests == d["material_requests"]


def test_block_output_from_dict_tolerates_legacy_payload():
    """老 checkpoint 里的 BlockOutput 没有 material_requests，反序列化不炸。"""
    from domain.proposal import BlockOutput

    legacy = {"block_id": "s1", "kind": "tech", "content": "正文", "sources": []}
    assert BlockOutput.from_dict(legacy).material_requests == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/agents/test_zhuge_liang.py -k material -v`
Expected: FAIL — `ImportError: cannot import name '_collect_material_requests'`

- [ ] **Step 3: 实现 —— BlockOutput**

`backend/domain/proposal/models.py` 的 `BlockOutput`，在 `sources` 字段之后加：

```python
    material_requests: list[dict] = field(default_factory=list)  # [{query, reason}]，工作流中间态
```

`to_dict` 里加一行（`"sources"` 之后）：

```python
            "material_requests": [dict(r) for r in self.material_requests],
```

`from_dict` 里加一行：

```python
            material_requests=[
                dict(r) for r in d.get("material_requests", []) if isinstance(r, dict)
            ],
```

- [ ] **Step 4: 实现 —— 占位符抽取**

`backend/agents/zhuge_liang.py`，在 `_DIAGRAM_PLACEHOLDER_RE` 之后加：

```python
# 大纲里的"待补充"占位符：写作阶段发现欠缺的外部素材/数据，
# 直接转成补料请求（见 spec §4.6 的补料请求抽取说明），省掉一次 LLM 调用。
_MATERIAL_PLACEHOLDER_RE = re.compile(r"【待补充[：:]([^】]+)】")


def _collect_material_requests(outline: str) -> list[dict]:
    """把写作大纲中的 【待补充：xx】 占位符转成补料请求（去重，保序）。

    返回 [{"query": xx, "reason": "..."}]；无占位符或大纲为空时返回 []。
    """
    if not outline:
        return []
    seen: set[str] = set()
    requests: list[dict] = []
    for m in _MATERIAL_PLACEHOLDER_RE.finditer(outline):
        query = m.group(1).strip()
        if not query or query in seen:
            continue
        seen.add(query)
        requests.append({
            "query": query,
            "reason": "写作大纲中的待补充占位符",
        })
    return requests
```

- [ ] **Step 5: 实现 —— 在 `_generate_tech` 中挂上抽取**

`backend/agents/zhuge_liang.py` 的 `_generate_tech`，把返回 `BlockOutput(...)` 的构造替换为（在已有字段基础上新增最后一行）：

```python
        return BlockOutput(
            block_id=block_id,
            kind="tech",
            content=content,
            outline=outline or "",
            sources=sources,
            needs_diagram=needs_diagram,
            material_requests=_collect_material_requests(outline or ""),
        )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/agents/test_zhuge_liang.py tests/domain/ -v`
Expected: PASS（全绿）

- [ ] **Step 7: Commit**

```bash
git add backend/domain/proposal/models.py backend/agents/zhuge_liang.py tests/agents/test_zhuge_liang.py
git commit -m "feat(zhuge_liang): 从大纲占位符抽取补料请求，BlockOutput 承载"
```

---

### Task 6: `dispatch_block_write` 支持评审意见注入

**Files:**
- Modify: `backend/infra/llm/dispatcher.py:302-331`
- Test: `tests/infra/test_llm_dispatcher.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/infra/test_llm_dispatcher.py` 末尾：

```python
async def test_dispatch_block_write_injects_feedback(monkeypatch):
    """feedback_text 非空时注入 extra_prompt，内容出现在实际发出的 prompt 里。"""
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield "ok"

    import infra.llm.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "dispatch_stream_generate", _fake_stream)

    tokens = [
        t async for t in dispatcher.dispatch_block_write(
            configs=[], rr_start_index=0, title="配电系统",
            requirement="提供业绩证明", chunks=[],
            feedback_text="【上轮评审意见】\n1. [CRITICAL] 缺业绩证明",
        )
    ]

    assert tokens == ["ok"]
    assert "上轮评审意见" in captured["extra_prompt"]
    assert "提供业绩证明" in captured["extra_prompt"]


async def test_dispatch_block_write_without_feedback_unchanged(monkeypatch):
    """不传 feedback_text 时，prompt 内容与改造前一致。"""
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield "ok"

    import infra.llm.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "dispatch_stream_generate", _fake_stream)

    async for _ in dispatcher.dispatch_block_write(
        configs=[], rr_start_index=0, title="配电系统",
        requirement="提供业绩证明", chunks=[],
    ):
        pass

    assert "上轮评审意见" not in captured["extra_prompt"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/infra/test_llm_dispatcher.py -k feedback -v`
Expected: FAIL — `TypeError: dispatch_block_write() got an unexpected keyword argument 'feedback_text'`

- [ ] **Step 3: 实现**

`backend/infra/llm/dispatcher.py` 的 `dispatch_block_write` 整个替换为：

```python
async def dispatch_block_write(
    configs: list[LLMConfig],
    rr_start_index: int,
    title: str,
    requirement: str,
    chunks: list[dict],
    target_words: int = 600,
    feedback_text: str = "",
):
    """
    为单个 block 流式生成正文内容。
    将 requirement + chunks 拼入 extra_prompt，复用 dispatch_stream_generate。

    feedback_text：协同闭环回灌的上一轮评审意见（已由
    ``agents.prompts.format_feedback_block`` 格式化）。为空时行为与改造前一致。
    """
    snippets = "\n".join(
        f"- {c['content'][:200]}" for c in chunks
    )
    extra_prompt = ""
    if requirement:
        extra_prompt += f"【应标要求】\n{requirement}\n\n"
    if snippets:
        extra_prompt += f"【参考素材】\n{snippets}"
    if feedback_text:
        extra_prompt += f"\n\n{feedback_text}"

    async for token in dispatch_stream_generate(
        configs=configs,
        rr_start_index=rr_start_index,
        section_title=title,
        original_content="",
        target_words=target_words,
        extra_prompt=extra_prompt,
    ):
        yield token
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/infra/test_llm_dispatcher.py -v`
Expected: PASS（全绿）

- [ ] **Step 5: Commit**

```bash
git add backend/infra/llm/dispatcher.py tests/infra/test_llm_dispatcher.py
git commit -m "feat(infra): dispatch_block_write 支持注入评审意见"
```

---

### Task 7: 诸葛亮消费评审意见（修订分支）

**Files:**
- Modify: `backend/agents/prompts.py`（新增 `format_feedback_block`，导出）
- Modify: `backend/agents/zhuge_liang.py`（`generate()` 与 `_generate_tech()` 增加 feedback 参数）
- Test: `tests/agents/test_prompts.py`、`tests/agents/test_zhuge_liang.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/agents/test_prompts.py` 末尾：

```python
def test_format_feedback_block_renders_issues():
    """评审意见被格式化为带严重度的逐条清单。"""
    from agents.prompts import format_feedback_block

    issues = [
        {"severity": "critical", "point": "缺业绩证明", "suggestion": "补充合同"},
        {"severity": "low", "point": "表述冗余", "suggestion": "精简"},
    ]

    text = format_feedback_block(issues)

    assert "上轮评审意见" in text
    assert "CRITICAL" in text
    assert "缺业绩证明" in text
    assert "补充合同" in text


def test_format_feedback_block_flags_material_issues():
    """needs_material 的问题必须提示用占位符而不是编造数据。"""
    from agents.prompts import format_feedback_block

    issues = [{
        "severity": "critical", "point": "缺型式试验报告",
        "suggestion": "补充", "needs_material": True,
    }]

    text = format_feedback_block(issues)

    assert "待补充" in text
    assert "不要编造" in text


def test_format_feedback_block_empty_returns_blank():
    """空意见返回空串——调用方靠它判断是否走修订分支。"""
    from agents.prompts import format_feedback_block

    assert format_feedback_block([]) == ""
    assert format_feedback_block(None) == ""
```

追加到 `tests/agents/test_zhuge_liang.py` 末尾：

```python
async def test_generate_passes_feedback_into_write_prompt(monkeypatch):
    """feedback 中该 block 的意见被注入到正文生成的 prompt。"""
    captured = {}

    async def _fake_write(**kwargs):
        captured.update(kwargs)
        yield "正文"
        return

    async def _fake_outline(*a, **kw):
        return "## 大纲"

    class _Cfg:
        provider = "openai"
        model = "m"

    import infra.llm
    monkeypatch.setattr(infra.llm, "generate_section_outline", _fake_outline)
    monkeypatch.setattr(infra.llm, "dispatch_block_write", _fake_write)

    agent = ZhugeLiangAgent(configs_provider=lambda: ([_Cfg()], 0))

    async def _drain(gen):
        async for _ in gen:
            pass

    await agent.generate(
        outline_matrix={"s1": OutlineMatrixRow(title="配电系统", requirement="要求")},
        materials={"s1": []},
        feedback={"s1": [{
            "severity": "critical", "point": "缺业绩证明", "suggestion": "补充",
        }]},
    )

    assert "上轮评审意见" in captured.get("feedback_text", "")
    assert "缺业绩证明" in captured.get("feedback_text", "")
```

（若 `_fake_write` 的 `async def ... yield` 写法导致 `infra.llm.dispatch_block_write` 被当成协程，改为普通生成器函数：去掉 `async`，用 `yield` 即可——`zhuge_liang._generate_tech` 用 `async for` 消费，普通生成器不兼容，因此这里必须保留 `async def` + `yield`。若 monkeypatch 报错，改用 `pytest.mark.asyncio` 的 async generator helper。）

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/agents/ -k "feedback" -v`
Expected: FAIL — `ImportError: cannot import name 'format_feedback_block'`

- [ ] **Step 3: 实现 —— `format_feedback_block`**

`backend/agents/prompts.py` 末尾（`__all__` 之前）插入：

```python
# ─────────────────────────────────────────────
# 协同闭环：评审意见 → 生成 prompt 的转译
# ─────────────────────────────────────────────

def format_feedback_block(issues: list[dict] | None) -> str:
    """把上一轮评审意见格式化为可注入生成 prompt 的文本块。

    issues: [{"severity","point","suggestion","needs_material","material_query"}]
    空列表 / None 返回 "" —— 调用方靠空串判断是否走修订分支。
    """
    if not issues:
        return ""

    lines = ["【上轮评审意见（必须逐条修复）】"]
    for i, it in enumerate(issues, 1):
        severity = str(it.get("severity") or "medium").upper()
        lines.append(f"{i}. [{severity}] {it.get('point') or ''}")
        if it.get("suggestion"):
            lines.append(f"   修改建议：{it['suggestion']}")
        if it.get("needs_material"):
            lines.append(
                "   本条需外部材料支撑：【参考素材】中若仍无对应内容，"
                "请用占位符 `【待补充：材料名称】` 标注，不要编造具体数据、证书编号或业绩。"
            )

    lines.append("")
    lines.append("要求：逐条消除上述问题；已满足的项不要改动。")
    return "\n".join(lines)
```

并在 `__all__` 列表中加 `"format_feedback_block",`。

- [ ] **Step 4: 实现 —— 诸葛亮 `generate()` 增加 feedback**

`backend/agents/zhuge_liang.py` 的 `generate()` 签名，在 `materials` 之后加参数：

```python
        materials: dict[str, list[Match]],
        feedback: Optional[dict[str, list[dict]]] = None,
        regenerate_targets: Optional[list[str]] = None,
```

在 `_run_one` 内，`kind == "letter"` 分支保持不变，`else` 分支改为把 feedback 传下去：

```python
                    else:
                        matches = materials.get(block_id, []) or []
                        output = await self._generate_tech(
                            block_id, row, matches,
                            configs, (rr_start + idx) % max(len(configs), 1),
                            emitter,
                            feedback=(feedback or {}).get(block_id) or [],
                        )
```

- [ ] **Step 5: 实现 —— `_generate_tech` 注入 feedback**

`backend/agents/zhuge_liang.py` 的 `_generate_tech` 签名加参数：

```python
        emitter: Optional[EventCallback],
        feedback: Optional[list[dict]] = None,
    ) -> BlockOutput:
```

在函数体内、构造 `chunks_for_write` 之前加：

```python
        # 上一轮评审意见：转成可注入文本；为空则走首次生成路径（行为不变）
        from agents.prompts import format_feedback_block
        feedback_text = format_feedback_block(feedback)
```

把 `infra.llm.dispatch_block_write(...)` 调用改为传入 `feedback_text`：

```python
        async for token in infra.llm.dispatch_block_write(
            configs=configs,
            rr_start_index=rr_start,
            title=row.title,
            requirement=row.requirement or "",
            chunks=chunks_for_write,
            target_words=self.target_words,
            feedback_text=feedback_text,
        ):
```

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/agents/ tests/infra/ -v`
Expected: PASS（全绿）

- [ ] **Step 7: Commit**

```bash
git add backend/agents/prompts.py backend/agents/zhuge_liang.py tests/agents/test_prompts.py tests/agents/test_zhuge_liang.py
git commit -m "feat(zhuge_liang): 支持按评审意见修订生成"
```

---

### Task 8: SSE 新增五个事件

**Files:**
- Modify: `backend/orchestrator/events.py`
- Test: `tests/orchestrator/test_events.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/orchestrator/test_events.py` 末尾：

```python
def test_iteration_and_gap_events():
    """新增的迭代/补料事件帧格式合法。"""
    from orchestrator import events

    for frame in [
        events.iteration_start(1),
        events.gaps_collecting("s1", "配电柜型式试验报告"),
        events.gaps_done("s1", 3, 5),
        events.feedback_ready({"targets": ["s1"], "material_request_count": 2, "iteration": 1}),
    ]:
        assert frame.startswith("event: ")
        assert frame.endswith("\n\n")


def test_convergence_event_payload():
    """convergence 事件携带判定结果。"""
    import json
    from orchestrator import events

    frame = events.convergence({
        "status": "refine",
        "unconverged_blocks": ["s1"],
        "reason": "存在未达标 block",
        "iteration": 1,
    })

    assert "event: convergence" in frame
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["status"] == "refine"
    assert payload["unconverged_blocks"] == ["s1"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_events.py -k "iteration or convergence" -v`
Expected: FAIL — `AttributeError: module 'orchestrator.events' has no attribute 'iteration_start'`

- [ ] **Step 3: 实现**

`backend/orchestrator/events.py`，在 `aborted` 函数之后插入（帧构造器复用既有的 `format_sse_event`，见 `events.py` 顶部已 import）：

```python
# ─────────────────────────────────────────────
# 协同闭环事件（spec §10）
# ─────────────────────────────────────────────

def iteration_start(iteration: int) -> str:
    """进入第 N 轮迭代（collect_gaps 发出）。"""
    return format_sse_event("iteration_start", {"iteration": int(iteration)})


def gaps_collecting(block_id: str, query: str) -> str:
    """正在为某 block 检索补充素材。"""
    return format_sse_event("gaps_collecting", {
        "block_id": block_id,
        "query": query,
    })


def gaps_done(block_id: str, new_matches: int, total_matches: int) -> str:
    """某 block 补料完成。"""
    return format_sse_event("gaps_done", {
        "block_id": block_id,
        "new_matches": int(new_matches),
        "total_matches": int(total_matches),
    })


def convergence(payload: dict[str, Any]) -> str:
    """收敛判定结果。"""
    return format_sse_event("convergence", dict(payload))


def feedback_ready(payload: dict[str, Any]) -> str:
    """修订指令与补料请求已生成。"""
    return format_sse_event("feedback_ready", dict(payload))
```

同时把 `events.py` 底部 `__all__` 的 `"aborted",` 之后加上：

```python
    "iteration_start",
    "gaps_collecting",
    "gaps_done",
    "convergence",
    "feedback_ready",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_events.py -v`
Expected: PASS（全绿）

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/events.py tests/orchestrator/test_events.py
git commit -m "feat(orchestrator): 新增协同闭环 SSE 事件"
```

---

### Task 9: 三个新编排节点 + 两处节点改造

**Files:**
- Modify: `backend/orchestrator/nodes.py`
- Test: `tests/orchestrator/test_nodes.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/orchestrator/test_nodes.py` 末尾：

```python
# ─────────────────────────────────────────────
# 协同闭环：check_convergence
# ─────────────────────────────────────────────

def _finding(score: int, issues=None, error: str = "") -> dict:
    return {
        "block_id": "s1", "agent": "x", "score": score,
        "issues": issues or [], "strengths": [], "error": error,
    }


async def test_check_convergence_all_pass():
    state = {
        "review": {
            "tech_findings": {"s1": _finding(90)},
            "compliance_findings": {"s1": _finding(85)},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "converged"
    assert patch["review"]["convergence"]["unconverged_blocks"] == []


async def test_check_convergence_low_score_triggers_refine():
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50)},
            "compliance_findings": {"s1": _finding(85)},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "refine"
    assert patch["review"]["convergence"]["unconverged_blocks"] == ["s1"]


async def test_check_convergence_critical_triggers_refine_even_with_high_score():
    """双分都高，但含 critical issue —— 仍判未达标。"""
    issue = {"severity": "critical", "point": "否决项未响应"}
    state = {
        "review": {
            "tech_findings": {"s1": _finding(95, [issue])},
            "compliance_findings": {"s1": _finding(95)},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "refine"


async def test_check_convergence_stops_at_max_iterations():
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50)},
            "compliance_findings": {"s1": _finding(50)},
        },
        "iteration": nodes.MAX_ITERATIONS,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "max_iterations"


async def test_check_convergence_ignores_review_failures():
    """评审本身报错（基础设施故障）不触发回炉。"""
    state = {
        "review": {
            "tech_findings": {"s1": _finding(0, error="全部 API 失败")},
            "compliance_findings": {"s1": _finding(0, error="全部 API 失败")},
        },
        "iteration": 0,
    }

    patch = await nodes.check_convergence_node(state)

    assert patch["review"]["convergence"]["status"] == "converged"
    assert patch["review"]["convergence"]["review_failed"] is True


# ─────────────────────────────────────────────
# 协同闭环：build_feedback
# ─────────────────────────────────────────────

async def test_build_feedback_splits_issues_into_two_channels():
    issues = [
        {"severity": "critical", "point": "缺业绩证明", "suggestion": "补充",
         "needs_material": True, "material_query": "近三年业绩证明合同"},
        {"severity": "low", "point": "表述冗余", "suggestion": "精简",
         "needs_material": False, "material_query": ""},
    ]
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50, issues)},
            "compliance_findings": {"s1": _finding(40)},
            "convergence": {"status": "refine", "unconverged_blocks": ["s1"]},
        },
        "proposal": {"blocks": {"s1": {}}},
        "iteration": 0,
    }

    patch = await nodes.build_feedback_node(state)

    assert len(patch["review"]["feedback"]["s1"]["issues"]) == 2
    assert patch["review"]["feedback"]["s1"]["scores"] == {"tech": 50, "comp": 40}
    assert patch["proposal"]["material_requests"]["s1"] == [
        {"query": "近三年业绩证明合同", "reason": "缺业绩证明"}
    ]
    assert patch["proposal"]["regenerate_targets"] == ["s1"]
    assert patch["iteration"] == 1


async def test_build_feedback_max_iterations_mode_writes_no_regen():
    """达上限模式只写反馈与补料请求，不回炉、不递增。"""
    issues = [{"severity": "high", "point": "p", "suggestion": "s",
               "needs_material": True, "material_query": "q"}]
    state = {
        "review": {
            "tech_findings": {"s1": _finding(50, issues)},
            "compliance_findings": {"s1": _finding(40)},
            "convergence": {"status": "max_iterations", "unconverged_blocks": ["s1"]},
        },
        "proposal": {"blocks": {"s1": {}}},
        "iteration": nodes.MAX_ITERATIONS,
    }

    patch = await nodes.build_feedback_node(state)

    assert "feedback" in patch["review"]
    assert "material_requests" in patch["proposal"]
    assert "regenerate_targets" not in patch["proposal"]
    assert "iteration" not in patch


# ─────────────────────────────────────────────
# 协同闭环：collect_gaps
# ─────────────────────────────────────────────

class _ShenKuoStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def retrieve_for(self, requests_by_block, chunks):
        self.calls.append({"requests": requests_by_block, "chunks": chunks})
        return self.result


async def test_collect_gaps_no_requests_is_noop():
    """无补料请求时是空操作，不碰 materials.matches。"""
    stub = _ShenKuoStub({})
    state = {"proposal": {}, "materials": {"chunks": [{"id": 1}]}}

    patch = await nodes.collect_gaps_node(state, agent=stub)

    assert patch == {}
    assert stub.calls == []


async def test_collect_gaps_appends_without_overwriting():
    """新素材追加到已有 matches，同 chunk_id 去重，旧的不被冲掉。"""
    from infra.retrieval import Match

    stub = _ShenKuoStub({"s1": [
        Match(chunk_id=2, score=8.0, reason="新", hit_points=[]),
        Match(chunk_id=1, score=9.0, reason="重复", hit_points=[]),
    ]})
    state = {
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {
            "chunks": [{"id": 1}],
            "matches": {"s1": [{"chunk_id": 1, "score": 5.0, "reason": "旧"}]},
        },
    }

    patch = await nodes.collect_gaps_node(state, agent=stub)

    matches = patch["materials"]["matches"]["s1"]
    assert [m["chunk_id"] for m in matches] == [1, 2]
    assert matches[0]["reason"] == "旧"          # 原有匹配保留，不被覆盖
    assert patch["proposal"]["material_requests"] == {}


async def test_collect_gaps_does_not_wipe_matches_when_nothing_found():
    """补料返回空时不得写 materials.matches，否则会把原匹配清空。"""
    stub = _ShenKuoStub({})
    state = {
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {"chunks": [{"id": 1}], "matches": {"s1": [{"chunk_id": 1}]}},
    }

    patch = await nodes.collect_gaps_node(state, agent=stub)

    assert "materials" not in patch
    assert patch["proposal"]["material_requests"] == {}


async def test_collect_gaps_retrieval_failure_does_not_block():
    """检索抛错时记 errors 并放行，不改 matches。"""
    class _Boom:
        async def retrieve_for(self, *a, **kw):
            raise RuntimeError("bm25 挂了")

    state = {
        "proposal": {"material_requests": {"s1": [{"query": "q"}]}},
        "materials": {"chunks": [{"id": 1}], "matches": {"s1": [{"chunk_id": 1}]}},
    }

    patch = await nodes.collect_gaps_node(state, agent=_Boom())

    assert "materials" not in patch
    assert patch["errors"][0]["agent"] == "collect_gaps"


# ─────────────────────────────────────────────
# 协同闭环：_run_review 复审范围与 finding 合并
# ─────────────────────────────────────────────

class _RecordingReviewer:
    """记录每次复审到的 block_id 列表，返回固定 finding。"""

    def __init__(self, score: int = 80):
        self.score = score
        self.scopes: list[list[str]] = []

    async def review(self, *, blocks, outline_matrix, emitter=None):
        from domain.review import Finding
        self.scopes.append(sorted(blocks.keys()))
        return {
            bid: Finding(block_id=bid, agent="wang_anshi", score=self.score)
            for bid in blocks
        }


def _block(block_id: str) -> dict:
    from domain.proposal import BlockOutput
    return BlockOutput(
        block_id=block_id, kind="tech", content="正文", sources=[],
    ).to_dict()


async def test_run_review_narrows_scope_to_updated_blocks():
    """有 updated_blocks 时只复审本轮重跑过的 block。"""
    agent = _RecordingReviewer()
    state = {
        "proposal": {
            "blocks": {"s1": _block("s1"), "s2": _block("s2")},
            "updated_blocks": ["s2"],
        },
    }

    await nodes._run_review(
        state, agent=agent, emitter=None,
        finding_field="tech_findings", agent_name="wang_anshi",
    )

    assert agent.scopes == [["s2"]]


async def test_run_review_without_updated_blocks_reviews_all():
    """老 checkpoint 无 updated_blocks 字段 → 复审全部（不能退化成零 block）。"""
    agent = _RecordingReviewer()
    state = {"proposal": {"blocks": {"s1": _block("s1"), "s2": _block("s2")}}}

    await nodes._run_review(
        state, agent=agent, emitter=None,
        finding_field="tech_findings", agent_name="wang_anshi",
    )

    assert agent.scopes == [["s1", "s2"]]


async def test_run_review_merges_into_existing_findings():
    """部分复审时必须并入已有 findings —— 整体替换会抹掉未复审 block 的历史分数。

    若被抹掉，check_convergence 会把缺失 block 的 score 读成 0 判为未达标，
    反复回炉且两个 block 交替被清空，收敛判定永远无法稳定。
    """
    agent = _RecordingReviewer(score=60)
    state = {
        "review": {
            "tech_findings": {
                "s1": _finding(95),
                "s2": _finding(50),
            },
        },
        "proposal": {
            "blocks": {"s1": _block("s1"), "s2": _block("s2")},
            "updated_blocks": ["s2"],
        },
    }

    patch = await nodes._run_review(
        state, agent=agent, emitter=None,
        finding_field="tech_findings", agent_name="wang_anshi",
    )

    findings = patch["review"]["tech_findings"]
    assert findings["s1"]["score"] == 95     # 未复审，历史分数保留
    assert findings["s2"]["score"] == 60     # 已复审，被本轮结果覆盖
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_nodes.py -k "convergence or feedback or gaps" -v`
Expected: FAIL — `AttributeError: module 'orchestrator.nodes' has no attribute 'check_convergence_node'`

- [ ] **Step 3: 实现 —— 阈值常量**

`backend/orchestrator/nodes.py` 顶部 `import logging` 之后补 `import os`，并在 `_check_cancel` 之后插入：

```python
# ─────────────────────────────────────────────
# 协同闭环：收敛阈值与迭代上限（spec §5.1）
# ─────────────────────────────────────────────

REVIEW_SCORE_THRESHOLD = int(os.getenv("REVIEW_SCORE_THRESHOLD", "80"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: 实现 —— 三个新节点**

在 `backend/orchestrator/nodes.py` 的 `aggregate_review_node` 之后插入：

```python
# ─────────────────────────────────────────────
# collect_gaps 节点：调度收集角色补料（spec §4.3）
# ─────────────────────────────────────────────

async def collect_gaps_node(
    state: WorkflowState,
    *,
    agent: ShenKuoAgent,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """把补料请求交给沈括，新素材并回 materials.matches。

    位于 zhuge_liang_generate **之前**，因此两个来源的请求
    （编写主动要料 + build_feedback 的审核要料）都在下一轮生成前就位。

    无请求时空操作，不写任何字段。
    """
    _check_cancel(state)

    requests_by_block = (state.get("proposal") or {}).get("material_requests") or {}
    if not requests_by_block:
        return {}

    materials = state.get("materials") or {}
    chunks = materials.get("chunks") or []

    # 无语料：清空请求直接放行（否则请求会累积到下一轮重复触发）
    if not chunks:
        return {"proposal": {"material_requests": {}}}

    await _emit_frame(emitter, events.iteration_start(int(state.get("iteration") or 0)))
    for block_id, reqs in requests_by_block.items():
        for req in (reqs or []):
            await _emit_frame(emitter, events.gaps_collecting(
                block_id, req.get("query", ""),
            ))

    errors: list[dict] = []
    try:
        new_matches = await agent.retrieve_for(requests_by_block, chunks)
    except Exception as e:
        logger.warning(f"collect_gaps：补料检索失败，保留原 matches 继续：{e}")
        new_matches = {}
        errors.append({
            "agent": "collect_gaps",
            "message": f"补料检索失败：{e}",
            "timestamp": _now_iso(),
            "retryable": True,
        })

    existing = dict(materials.get("matches") or {})
    merged: dict[str, list[dict]] = {}
    for block_id, matches in new_matches.items():
        old = list(existing.get(block_id) or [])
        seen = {m.get("chunk_id") for m in old}
        added = [m for m in matches if m.chunk_id not in seen]
        if not added:
            continue
        merged[block_id] = old + [_match_to_dict(m) for m in added]
        await _emit_frame(emitter, events.gaps_done(
            block_id, len(added), len(merged[block_id]),
        ))

    patch: dict = {"proposal": {"material_requests": {}}}
    # 关键：new_matches 为空时绝不能写 materials.matches —— _merge_dict 是浅合并，
    # 写 {"matches": {}} 会把已有匹配全部清空。
    if merged:
        patch["materials"] = {"matches": merged}
    if errors:
        patch["errors"] = errors
    return patch


# ─────────────────────────────────────────────
# check_convergence 节点：逐 block 收敛判定（spec §5.1）
# ─────────────────────────────────────────────

async def check_convergence_node(
    state: WorkflowState,
    *,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """纯判定：不调 LLM、不改业务数据，只写 review.convergence。"""
    _check_cancel(state)

    review = state.get("review") or {}
    tech = review.get("tech_findings") or {}
    comp = review.get("compliance_findings") or {}
    iteration = int(state.get("iteration") or 0)

    unconverged: list[str] = []
    review_failed = False

    for block_id in sorted(set(tech) | set(comp)):
        t = tech.get(block_id) or {}
        c = comp.get(block_id) or {}

        # 评审本身失败是基础设施故障，不是内容问题 —— 回炉解决不了，
        # 反而会烧光迭代次数。排除在未达标之外，直接交人工终审。
        if t.get("error") or c.get("error"):
            review_failed = True
            continue

        tech_score = int(t.get("score") or 0)
        comp_score = int(c.get("score") or 0)
        has_critical = any(
            str(i.get("severity") or "").lower() == "critical"
            for i in list(t.get("issues") or []) + list(c.get("issues") or [])
        )

        if (
            tech_score < REVIEW_SCORE_THRESHOLD
            or comp_score < REVIEW_SCORE_THRESHOLD
            or has_critical
        ):
            unconverged.append(block_id)

    if not unconverged:
        status = "converged"
        reason = "review_failed_ignored" if review_failed else ""
    elif iteration >= MAX_ITERATIONS:
        status = "max_iterations"
        reason = f"已达迭代上限 {MAX_ITERATIONS} 轮"
    else:
        status = "refine"
        reason = "存在未达标 block"

    convergence = {
        "status": status,
        "unconverged_blocks": unconverged,
        "reason": reason,
        "iteration": iteration,
        "review_failed": review_failed,
    }
    await _emit_frame(emitter, events.convergence(convergence))
    return {"review": {"convergence": convergence}}


# ─────────────────────────────────────────────
# build_feedback 节点：把审核意见转译成两路（spec §4.4）
# ─────────────────────────────────────────────

async def build_feedback_node(
    state: WorkflowState,
    *,
    emitter: EmitterArg = None,
) -> WorkflowState:
    """转译：给编写的修订指令 + 给收集的补料请求。纯转译，不调 LLM。

    refine 模式：写全部四个字段（feedback / material_requests /
                 regenerate_targets / iteration）。
    max_iterations 模式：只写前两个，不回炉、不递增，
                 由 graph 的条件边直连闸门 3（spec §5.3）。
    """
    _check_cancel(state)

    review = state.get("review") or {}
    tech = review.get("tech_findings") or {}
    comp = review.get("compliance_findings") or {}
    convergence = review.get("convergence") or {}
    status = convergence.get("status", "")
    targets = list(convergence.get("unconverged_blocks") or [])
    iteration = int(state.get("iteration") or 0)

    feedback: dict[str, dict] = {}
    material_requests: dict[str, list[dict]] = {}

    for block_id in targets:
        t = tech.get(block_id) or {}
        c = comp.get(block_id) or {}
        issues = list(t.get("issues") or []) + list(c.get("issues") or [])

        feedback[block_id] = {
            "issues": issues,
            "scores": {
                "tech": int(t.get("score") or 0),
                "comp": int(c.get("score") or 0),
            },
        }

        reqs: list[dict] = []
        for issue in issues:
            if not issue.get("needs_material"):
                continue
            query = (issue.get("material_query") or "").strip() or \
                    (issue.get("point") or "").strip()
            if not query:
                continue
            reqs.append({"query": query, "reason": issue.get("point") or ""})
        if reqs:
            material_requests[block_id] = reqs

    patch: dict = {
        "review": {"feedback": feedback},
        "proposal": {"material_requests": material_requests},
        "errors": [],
    }

    if status == "refine":
        if iteration >= MAX_ITERATIONS:
            # 断言式兜底：check_convergence 的判定若被改坏，最坏也只是重复一轮，
            # 不会无限回环。改写 status 让 graph 的 _route_feedback 直连闸门。
            patch["review"]["convergence"] = {
                **convergence,
                "status": "max_iterations",
                "reason": f"迭代已达上限 {MAX_ITERATIONS} 仍收到 refine，强制收敛",
            }
            patch["errors"].append({
                "agent": "build_feedback",
                "message": f"迭代已达上限 {MAX_ITERATIONS} 仍收到 refine，拒绝回炉",
                "timestamp": _now_iso(),
                "retryable": False,
            })
        else:
            patch["proposal"]["regenerate_targets"] = targets
            patch["iteration"] = iteration + 1

    await _emit_frame(emitter, events.feedback_ready({
        "targets": targets,
        "material_request_count": sum(len(v) for v in material_requests.values()),
        "iteration": iteration,
    }))
    return patch
```

- [ ] **Step 5: 实现 —— `zhuge_liang_generate_node` 写 `updated_blocks` 并传 feedback**

`backend/orchestrator/nodes.py` 的 `zhuge_liang_generate_node`：

(a) 在 `regen_targets = ...` 之后读取 feedback：

```python
    feedback_state = (state.get("review") or {}).get("feedback") or {}
    feedback: dict[str, list[dict]] = {
        bid: list((f or {}).get("issues") or [])
        for bid, f in feedback_state.items()
    }
```

(b) 把签名探测从单参数扩展为两参数：

```python
    import inspect as _inspect
    try:
        sig = _inspect.signature(agent.generate)
        supports_should_cancel = "should_cancel" in sig.parameters
        supports_feedback = "feedback" in sig.parameters
    except (TypeError, ValueError):
        supports_should_cancel = False
        supports_feedback = False
```

(c) 把两个 `agent.generate(...)` 调用改为按能力传参（用一层 kwargs 组装，避免四个分支）：

```python
    call_kwargs: dict = {
        "outline_matrix": sub_matrix,
        "materials": matches,
        "regenerate_targets": target_for_agent if regen_targets else None,
        "emitter": bridge,
    }
    if supports_feedback:
        call_kwargs["feedback"] = feedback
    if supports_should_cancel:
        call_kwargs["should_cancel"] = should_cancel

    new_blocks, _consumed = await agent.generate(**call_kwargs)
```

(d) 早退分支补 `updated_blocks`。把

```python
    if not target_for_agent:
        # 全部已完成（resume 后无新 block）
        return {"proposal": {
            "blocks": existing_blocks,
            "regenerate_targets": [],
        }}
```

改为

```python
    if not target_for_agent:
        # 全部已完成（resume 后无新 block）
        return {"proposal": {
            "blocks": existing_blocks,
            "regenerate_targets": [],
            "updated_blocks": [],
        }}
```

(e) 函数末尾返回的 patch 里加 `updated_blocks`（取本轮实际产出的 block）：

```python
        "updated_blocks": list(new_blocks.keys()),
```

- [ ] **Step 6: 实现 —— `_run_review` 只复审本轮更新的 block，且合并而非替换已有 finding**

`backend/orchestrator/nodes.py` 的 `_run_review`，在 `blocks` 构造之后插入（范围收窄）：

```python
    # 只复审本轮真正重跑过的 block：不只是省 token —— 同一段未改动的正文
    # 重复评审会因 LLM 采样随机性给出不同分数，低分会被误判为未达标并触发
    # 无意义的回炉，甚至在两轮之间振荡。老 checkpoint 无该字段时复审全部。
    updated = proposal.get("updated_blocks")
    if updated:
        updated_set = set(updated)
        blocks = {bid: blk for bid, blk in blocks.items() if bid in updated_set}
```

再把同函数末尾的 return 改为合并写入（**这一步不能省**）：

```python
    # 必须并入已有 findings，不能整体替换：review 的 reducer 是浅 merge，
    # 返回 {"tech_findings": findings_dict} 会把未复审 block 的历史 finding 一并
    # 抹掉。那样 check_convergence 读到缺失 block 的 score=0，误判未达标并再次
    # 回炉，两个 block 交替被清空 —— 收敛判定来回振荡直到迭代上限。
    existing = dict((state.get("review") or {}).get(finding_field) or {})
    existing.update(findings_dict)

    return {"review": {finding_field: existing}}
```

（把原来那行 `return {"review": {finding_field: findings_dict}}` 删掉。）

- [ ] **Step 7: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_nodes.py -v`
Expected: PASS（全绿）

- [ ] **Step 8: Commit**

```bash
git add backend/orchestrator/nodes.py tests/orchestrator/test_nodes.py
git commit -m "feat(orchestrator): 新增 collect_gaps/check_convergence/build_feedback 节点"
```

---

### Task 10: `graph.py` 边变更与集成测试

**Files:**
- Modify: `backend/orchestrator/graph.py`
- Modify: `backend/orchestrator/runner.py`（`GraphDeps` 装配处传沈括给新节点）
- Test: `tests/orchestrator/test_graph.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/orchestrator/test_graph.py` 末尾（复用本文件既有的 `_build_deps` 与 stub 模式）：

```python
# ─────────────────────────────────────────────
# 协同闭环：收敛判定 → 回边 → 闸门
# ─────────────────────────────────────────────

class _ScoreReviewer:
    """恒定打分的评审 stub —— 用来驱动收敛判定的两个分支。"""

    def __init__(self, name: str, score: int):
        self.name = name
        self.score = score
        self.reviewed: list[list[str]] = []

    async def review(self, *, blocks, outline_matrix, emitter=None):
        self.reviewed.append(sorted(blocks.keys()))
        return {
            bid: Finding(block_id=bid, agent=self.name, score=self.score)
            for bid in blocks
        }


class _RetrieveStubShenKuo(_StubShenKuo):
    """补上闭环所需的按需检索入口。"""

    def __init__(self):
        self.retrieve_calls: list[dict] = []

    async def retrieve_for(self, requests_by_block, chunks):
        self.retrieve_calls.append(dict(requests_by_block))
        return {}


async def test_converged_goes_straight_to_gate_report(tmp_path):
    """全部达标 → check_convergence 判 converged → 直达闸门 3，不回炉。"""
    from orchestrator.nodes import MAX_ITERATIONS  # noqa: F401  (确认常量存在)

    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-converged"}}
    shen = _RetrieveStubShenKuo()
    wang = _ScoreReviewer("wang_anshi", score=95)
    bao = _ScoreReviewer("bao_zheng", score=95)

    deps = _build_deps(wang=wang, bao=bao)
    deps.shen_kuo = shen

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(deps, checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # generate+review+判定

        snap = await graph.aget_state(config)

    assert snap.values["stage"] == "report_review"
    assert snap.values["review"]["convergence"]["status"] == "converged"
    assert snap.values.get("iteration", 0) == 0
    # 达标就不该转译任何修订指令
    assert not snap.values["review"].get("feedback")
    # 两位评审各只跑一次，没有重复评审
    assert wang.reviewed == [["s1", "s2"]]
    assert bao.reviewed == [["s1", "s2"]]


async def test_unconverged_loops_until_max_iterations_then_gate_report(tmp_path):
    """持续未达标 → 回炉直到迭代上限 → 停在闸门 3 等人工终审。"""
    from orchestrator.nodes import MAX_ITERATIONS

    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-unconverged"}}
    zhuge = _StubZhugeLiang()
    shen = _RetrieveStubShenKuo()
    wang = _ScoreReviewer("wang_anshi", score=50)
    bao = _ScoreReviewer("bao_zheng", score=50)

    deps = _build_deps(zhuge=zhuge, wang=wang, bao=bao)
    deps.shen_kuo = shen

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(deps, checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # 回炉直到上限

        snap = await graph.aget_state(config)

    assert snap.values["stage"] == "report_review"
    assert snap.values["iteration"] == MAX_ITERATIONS
    assert snap.values["review"]["convergence"]["status"] == "max_iterations"
    # 达上限后仍补跑一次 build_feedback，把意见留给人工终审参考
    assert snap.values["review"]["feedback"]
    # 首轮全量 + MAX_ITERATIONS 轮回炉
    assert len(zhuge.calls) == MAX_ITERATIONS + 1
    assert zhuge.calls[0] == ["s1", "s2"]
    assert zhuge.calls[1] == ["s1", "s2"]   # 两个 block 都未达标


class _PerBlockReviewer:
    """按 block 给不同分数的评审 stub —— 用来制造"部分收敛"。"""

    def __init__(self, name: str, scores: dict[str, int]):
        self.name = name
        self.scores = scores
        self.reviewed: list[list[str]] = []

    async def review(self, *, blocks, outline_matrix, emitter=None):
        self.reviewed.append(sorted(blocks.keys()))
        return {
            bid: Finding(
                block_id=bid, agent=self.name,
                score=self.scores.get(bid, 50),
            )
            for bid in blocks
        }


async def test_partially_converged_narrows_review_scope(tmp_path):
    """s1 达标 / s2 不达标 → 只回炉 s2 → 后续复审范围也应只剩 s2。

    若复审范围未收窄，每轮都会全量重评；未改动的 s1 会因 LLM 采样随机性拿到
    不同分数，可能被误判为未达标而额外回炉，甚至在两轮之间振荡。
    """
    db = str(tmp_path / "wf.db")
    config = {"configurable": {"thread_id": "tid-scope"}}
    wang = _PerBlockReviewer("wang_anshi", {"s1": 95, "s2": 50})
    bao = _PerBlockReviewer("bao_zheng", {"s1": 95, "s2": 50})

    deps = _build_deps(wang=wang, bao=bao)

    async with checkpointer_from_path(db) as saver:
        graph = build_graph(deps, checkpointer=saver)
        await graph.ainvoke({"project_id": 1}, config=config)  # gate1
        await graph.ainvoke(None, config=config)               # gate2
        await graph.ainvoke(None, config=config)               # 回炉直到上限

        snap = await graph.aget_state(config)

    assert snap.values["review"]["convergence"]["status"] == "max_iterations"
    assert snap.values["review"]["convergence"]["unconverged_blocks"] == ["s2"]

    # 首轮全量，此后每轮只复审仍在回炉的 s2
    assert wang.reviewed[0] == ["s1", "s2"]
    assert all(scope == ["s2"] for scope in wang.reviewed[1:]), (
        f"复审范围应收窄到本轮更新的 block，实际 {wang.reviewed}"
    )

    # s1 的历史高分必须保留 —— 被抹掉的话下一轮判定会把它读成 0 分，
    # 于是 s1 又被拖回回炉，两个 block 交替清空、永久振荡。
    assert snap.values["review"]["tech_findings"]["s1"]["score"] == 95
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend pytest tests/orchestrator/test_graph.py -v`
Expected: FAIL — 新测试断言不成立（图仍是旧结构）

- [ ] **Step 3: 实现 —— 新增节点常量**

`backend/orchestrator/graph.py` 的常量区加：

```python
NODE_COLLECT_GAPS = "collect_gaps"
NODE_CHECK_CONVERGENCE = "check_convergence"
NODE_BUILD_FEEDBACK = "build_feedback"
```

- [ ] **Step 4: 实现 —— 注册节点**

在 `build_graph` 内，`aggregate_node` 定义之后加：

```python
    async def collect_gaps_node(state: WorkflowState) -> WorkflowState:
        return await nodes.collect_gaps_node(
            state, agent=deps.shen_kuo, emitter=emitter,
        )

    async def check_convergence_node(state: WorkflowState) -> WorkflowState:
        return await nodes.check_convergence_node(state, emitter=emitter)

    async def build_feedback_node(state: WorkflowState) -> WorkflowState:
        return await nodes.build_feedback_node(state, emitter=emitter)
```

并在 `builder.add_node(NODE_ABORT, ...)` 之后加：

```python
    builder.add_node(NODE_COLLECT_GAPS, collect_gaps_node)
    builder.add_node(NODE_CHECK_CONVERGENCE, check_convergence_node)
    builder.add_node(NODE_BUILD_FEEDBACK, build_feedback_node)
```

- [ ] **Step 5: 实现 —— 改边**

把 `builder.add_edge(GATE_MATERIALS, NODE_GENERATE)` 改为：

```python
    builder.add_edge(GATE_MATERIALS, NODE_COLLECT_GAPS)
    builder.add_edge(NODE_COLLECT_GAPS, NODE_GENERATE)
```

把 `builder.add_edge(NODE_AGGREGATE, GATE_REPORT)` 改为：

```python
    builder.add_edge(NODE_AGGREGATE, NODE_CHECK_CONVERGENCE)

    def _route_convergence(state: WorkflowState) -> str:
        """refine 与 max_iterations 都先去 build_feedback，由其出边再区分。"""
        status = (
            (state.get("review") or {}).get("convergence") or {}
        ).get("status", "")
        return NODE_BUILD_FEEDBACK if status in ("refine", "max_iterations") \
            else GATE_REPORT

    builder.add_conditional_edges(
        NODE_CHECK_CONVERGENCE,
        _route_convergence,
        {
            NODE_BUILD_FEEDBACK: NODE_BUILD_FEEDBACK,
            GATE_REPORT: GATE_REPORT,
        },
    )

    def _route_feedback(state: WorkflowState) -> str:
        """refine 回环补料；max_iterations 直连闸门 3。"""
        status = (
            (state.get("review") or {}).get("convergence") or {}
        ).get("status", "")
        return NODE_COLLECT_GAPS if status == "refine" else GATE_REPORT

    builder.add_conditional_edges(
        NODE_BUILD_FEEDBACK,
        _route_feedback,
        {
            NODE_COLLECT_GAPS: NODE_COLLECT_GAPS,
            GATE_REPORT: GATE_REPORT,
        },
    )
```

**不要改动** `_generate_fanout`、`_route_after_pause`、`_route_after_report`，也不要改 `interrupt_after` 列表——`collect_gaps` 前置后，这些全部保持原语义正确。

- [ ] **Step 6: 更新 `__all__`**

`backend/orchestrator/graph.py` 的 `__all__` 加入三个新常量：

```python
    "NODE_COLLECT_GAPS",
    "NODE_CHECK_CONVERGENCE",
    "NODE_BUILD_FEEDBACK",
```

- [ ] **Step 7: 跑测试确认通过**

Run: `PYTHONPATH=backend pytest tests/orchestrator/ -v`
Expected: PASS（全绿）

- [ ] **Step 8: 跑全量回归**

Run: `LLM_MODE=mock PYTHONPATH=backend pytest tests/ --ignore=tests/e2e -v`
Expected: 全绿

- [ ] **Step 9: Commit**

```bash
git add backend/orchestrator/graph.py tests/orchestrator/test_graph.py
git commit -m "feat(orchestrator): 接入协同闭环回边"
```

---

### Task 11: 前端展示迭代轮次与未收敛标注

**Files:**
- Modify: `frontend/assets/api.js:183-190`（事件映射）
- Modify: `frontend/assets/review.js`（`renderBanner` + `openStream`）
- Modify: `frontend/review.html`（DOM 容器）

- [ ] **Step 1: 加事件映射**

`frontend/assets/api.js` 的 `stream` 内，在 `wire('report_ready', ...)` 之后加：

```javascript
      wire('iteration_start',  handlers.onIterationStart);
      wire('gaps_collecting',  handlers.onGapsCollecting);
      wire('gaps_done',        handlers.onGapsDone);
      wire('convergence',      handlers.onConvergence);
      wire('feedback_ready',   handlers.onFeedbackReady);
```

- [ ] **Step 2: 加 DOM 容器**

`frontend/review.html` 中，`banner` 元素之后加：

```html
      <div id="convergence-bar" class="convergence-bar" hidden>
        <span class="cv-iteration">第 <b id="cv-round">1</b> 轮</span>
        <span id="cv-status" class="cv-status"></span>
        <span id="cv-unconverged" class="cv-unconverged"></span>
      </div>
```

并在 `frontend/assets/review.css` 末尾追加：

```css
.convergence-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  margin-bottom: 8px;
  border-radius: 6px;
  background: var(--surface-2, #f5f5f5);
  font-size: 13px;
}
.convergence-bar.unconverged { background: #fff4e5; color: #8a5300; }
.convergence-bar .cv-unconverged { color: #b45309; }
```

- [ ] **Step 3: 加渲染逻辑**

`frontend/assets/review.js`，在 `renderBanner` 之后插入：

```javascript
// ── 渲染：协同闭环迭代条 ──────────────────────────
function renderConvergence({ iteration, status, unconverged }) {
  const bar = document.getElementById('convergence-bar');
  if (!bar) return;
  bar.hidden = false;

  const round = document.getElementById('cv-round');
  if (round && iteration != null) round.textContent = String(Number(iteration) + 1);

  const statusEl = document.getElementById('cv-status');
  const listEl = document.getElementById('cv-unconverged');
  const unconvergedList = Array.isArray(unconverged) ? unconverged : [];

  if (status === 'converged') {
    bar.classList.remove('unconverged');
    if (statusEl) statusEl.textContent = '已收敛';
    if (listEl) listEl.textContent = '';
    return;
  }
  if (status === 'max_iterations') {
    bar.classList.add('unconverged');
    if (statusEl) statusEl.textContent = '已达迭代上限，未收敛：';
    if (listEl) listEl.textContent = unconvergedList.join('、') || '—';
    return;
  }
  // refine / 进行中
  bar.classList.remove('unconverged');
  if (statusEl) statusEl.textContent = '修订中';
  if (listEl) listEl.textContent = unconvergedList.length ? `待修订 ${unconvergedList.join('、')}` : '';
}
```

- [ ] **Step 4: 接上事件**

`frontend/assets/review.js` 的 `openStream()` 内，在 `onReportReady` 之后加：

```javascript
    onIterationStart: (data) => {
      renderConvergence({ iteration: data?.iteration, status: 'refine', unconverged: [] });
    },
    onGapsCollecting: (data) => {
      const bar = document.getElementById('convergence-bar');
      const statusEl = document.getElementById('cv-status');
      if (bar) bar.hidden = false;
      if (statusEl) statusEl.textContent = `补充素材中：${data?.query || ''}`;
    },
    onConvergence: (data) => {
      renderConvergence({
        iteration: data?.iteration,
        status: data?.status,
        unconverged: data?.unconverged_blocks,
      });
    },
```

- [ ] **Step 5: 起服务验证**

Run: `bash start.sh`（另开一个终端）

Expected: 打开 `http://localhost:8000/review?threadId=<某任务id>`，页面上出现迭代条；跑一个会触发回炉的任务，能看到"修订中 → 补充素材中 → 已收敛/已达迭代上限"的状态流转。

</br>若没有可触发回炉的真实任务，退而验证：在浏览器控制台执行 `renderConvergence({iteration: 2, status: 'max_iterations', unconverged: ['s1','s3']})`，确认迭代条显示"第 3 轮 / 已达迭代上限，未收敛：s1、s3"且背景变橙。

- [ ] **Step 6: Commit**

```bash
git add frontend/assets/api.js frontend/assets/review.js frontend/assets/review.css frontend/review.html
git commit -m "feat(frontend): 评审页展示迭代轮次与未收敛标注"
```

---

## 收尾

- [ ] **全量回归**

```bash
LLM_MODE=mock PYTHONPATH=backend pytest tests/ --ignore=tests/e2e -v
```

- [ ] **黄金样本（需真实 API）**

```bash
LLM_MODE=real PYTHONPATH=backend pytest tests/e2e/test_golden_sample.py -v
```

需 `tests/fixtures/sample_*.docx` 存在。这一步验证闭环在真实 LLM 下不会把迭代跑飞。

- [ ] **更新 `.planning/2026-09-15-multi-agent-collaboration-loop/progress.md`**

- [ ] **知识提炼**：`/knowledge` 将本次设计决策沉淀到 `~/Claude/wiki/Projects/ge-solution/`
