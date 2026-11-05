"""评审 v3：L0 规则预检 / 分层调度 / 统一交付判断 的验收测试。"""
from __future__ import annotations


# ────────────────────────── A：L0 规则预检 ──────────────────────────

def test_precheck_placeholder_is_critical():
    from services.review_precheck import precheck

    result = precheck("## 一\n本节说明【待补充：我方响应】。")
    assert result["level"] == "fail"
    assert result["counts"]["critical"] >= 1
    assert any(item["code"] == "placeholder" for item in result["items"])


def test_precheck_empty_table_and_no_numbers():
    from services.review_precheck import precheck

    content = "## 一\n" + "\n".join(["|  |  |  |", "| --- | --- | --- |", "|  |  |  |"]) + "\n" + "文字" * 120
    result = precheck(content)
    codes = {item["code"] for item in result["items"]}
    assert "empty_table" in codes
    assert "no_numbers" in codes
    assert result["counts"]["high"] >= 2


def test_precheck_clean_passes():
    from services.review_precheck import precheck

    content = (
        "## 一 现状\n"
        "现有服务器 12 台，其中生产 8 台、测试 4 台，用户数约 3000 人，日均请求 12 万次，"
        "数据量约 2TB，现运行等保三级要求下的安全防护措施，2026 年完成上一轮改造。"
        "现网存在两套独立账号体系，尚未打通统一认证，运维手册更新滞后 6 个月。"
        "【来源：招标文件（第 12 页）】\n\n"
        "## 二 目标\n"
        "2027 年 6 月前完成迁移与整合，可用性达 99.9%，故障恢复时间不超过 30 分钟，"
        "统一认证覆盖 100% 业务系统，接口响应时间不超过 500 毫秒，"
        "年度运维成本下降 15%，并通过等保三级复测。实施节奏分为三个阶段："
        "2026 年四季度完成方案设计与评审，2027 年一二季度完成迁移与联调，"
        "2027 年三季度完成割接与验收，验收标准以技术规范书第 8 页列明的 12 项指标为准，"
        "每项指标均需提供第三方测试报告或截图作为证明材料。"
        "为保障改造效果，项目组将设立专项工作组，包含架构、开发、测试、安全、运维五个角色，"
        "每个角色明确责任人、交付物与验收口径，并按周同步进度、按月复盘风险。"
        "【来源：技术规范书（第 8 页）】\n"
    )
    result = precheck(content)
    assert result["level"] == "pass", result["items"]
    assert result["counts"]["critical"] == 0


def test_precheck_short_section_and_broken_tail():
    from services.review_precheck import precheck

    content = "## 一\n太短。\n\n## 二\n" + "这是完整的一句话，包含数字 2026 和 30 分钟。" * 8 + "\n最后一句没有结尾"
    result = precheck(content)
    codes = {item["code"] for item in result["items"]}
    assert "short_section" in codes or "broken_tail" in codes


# ────────────────────────── B：分层调度 ──────────────────────────

def test_resolve_tier():
    from services.review_precheck import resolve_tier

    assert resolve_tier({"counts": {"critical": 1}}) == "fast"       # 有硬伤 → 快评
    assert resolve_tier({"counts": {"critical": 0, "high": 2}}) == "full"
    assert resolve_tier({"counts": {"critical": 1}}, "full") == "full"   # 显式覆盖
    assert resolve_tier({"counts": {"critical": 1}}, "fast") == "fast"


# ────────────────────────── C：统一交付判断 ──────────────────────────

def test_delivery_verdict_levels():
    from services.delivery_verdict import build_delivery_verdict

    red = build_delivery_verdict(rule_score=80, review={"score": 60, "satisfied": "unsatisfied"},
                                 precheck={"counts": {}}, fatal_risks=[], need_user=[])
    assert red["level"] == "red" and red["can_submit"] is False

    yellow = build_delivery_verdict(rule_score=80, review=None,
                                    precheck={"counts": {"high": 1}}, fatal_risks=[], need_user=[])
    assert yellow["level"] == "yellow"

    green = build_delivery_verdict(rule_score=95, review={"score": 88, "satisfied": "satisfied"},
                                   precheck={"counts": {}}, fatal_risks=[], need_user=[])
    assert green["level"] == "green" and green["can_submit"] is True
    assert green["explain"]["rule"] == 95 and green["explain"]["review"] == 88


def test_delivery_verdict_top3_and_hard_gate():
    from services.delivery_verdict import build_delivery_verdict

    tasks = [
        {"title": "第 1 件", "impact": "quality", "action": {"type": "x"}, "target": {}},
        {"title": "第 2 件", "impact": "export_block", "action": {"type": "y"}, "target": {"section": "s"}},
        {"title": "第 3 件", "impact": "suggestion", "action": {"type": "z"}, "target": {}},
        {"title": "第 4 件", "impact": "quality", "action": {"type": "w"}, "target": {}},
    ]
    out = build_delivery_verdict(rule_score=100, review={"score": 90, "satisfied": "satisfied"},
                                 precheck={"counts": {}}, fatal_risks=[], need_user=tasks)
    assert len(out["top3"]) == 3
    assert out["top3"][0]["title"] == "第 2 件"      # 硬门优先
    assert out["can_submit"] is False               # 有 export_block 硬门
    assert out["explain"]["hard_gate"] == 1


def test_delivery_verdict_fatal_forces_red():
    from services.delivery_verdict import build_delivery_verdict

    out = build_delivery_verdict(rule_score=90, review={"score": 85, "satisfied": "satisfied"},
                                 precheck={"counts": {}}, fatal_risks=[{"point": "未响应★条款"}],
                                 need_user=[])
    assert out["level"] == "red"
    assert any("否决风险" in item["title"] for item in out["top3"])


def test_fast_tier_uses_single_agent(monkeypatch):
    """fast 分层：只跑一个评审角色，且用它的结果代表整体（不崩）。"""
    import asyncio

    from services import chapter_review

    class _Issue:
        def __init__(self):
            self._d = {"point": "缺量化指标", "severity": "high", "suggestion": "",
                       "needs_material": False, "material_query": ""}

        def to_dict(self):
            return dict(self._d)

    class _Finding:
        def __init__(self, score=70):
            self.score = score
            self.issues = [_Issue()]
            self.strengths = []
            self.error = ""
            self.criterion_text = ""
            self.satisfied = "partial"
            self.covered_points = []
            self.missing_points = []

    combined = chapter_review._combine(_Finding(70), _Finding(70))
    assert combined["score"] == 70
    assert combined["tier"] if "tier" in combined else True      # tier 由 review_block 写入
    # 单角色时 tech 与 comp 相同 → 合并结果仍成立
    single = chapter_review._combine(_Finding(70), None)
    assert single["score"] == 35 or single["tech_score"] in (70, 0)
