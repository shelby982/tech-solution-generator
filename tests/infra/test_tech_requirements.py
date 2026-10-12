"""技术要求矩阵抽取的用例。

背景（产品流程设计，2026-10-12）：
    招标文件与技术规范书回答两个不同问题 —— 前者规定「如何评标」，后者规定「具体做什么」。
    技术规范书必须在**解析阶段**就结构化成要求条目，后续环节只引用条目 ID 与原文，
    不再重解析整份文档（省 token，且避免不同 agent 对同一条款理解不一致）。

实测来源：`4_高可靠技术专题研究与验证-技术招标文件.docx`（该标包 2 的技术规范书）。
"""

import pytest

from infra.parser.tech_requirements import (
    INFORMATIONAL,
    MANDATORY,
    REQUIRED,
    TYPE_DELIVERABLE,
    TYPE_OBJECTIVE,
    TYPE_RESEARCH,
    TYPE_STANDARD,
    TYPE_VERIFY,
    classify_heading,
    classify_mandatory,
    extract_indicators,
    extract_tech_requirements,
)


# ─────────────────────────────────────────────
# 纯函数
# ─────────────────────────────────────────────

def test_classify_mandatory_by_obligation_words():
    """强制级别按义务词判定：必须/须/不得 > 应/需 > 说明性。"""
    assert classify_mandatory("中标人必须向招标人保证人员组织稳定性") == MANDATORY
    assert classify_mandatory("项目开展过程中不得随意更换档案人员") == MANDATORY
    assert classify_mandatory("投标人应提供完整可行的实施方案") == REQUIRED
    assert classify_mandatory("本技术招标文件适用于广东电网有限责任公司") == INFORMATIONAL


def test_extract_indicators_picks_explicit_numbers():
    """量化指标只抓显式数值，不臆造。"""
    got = extract_indicators("配置 1 名现场专职项目管理人员及至少35名项目组成员，确保12小时内响应")
    assert "1名" in got
    assert "至少35名" in got      # 阈值词与量词一并保留，便于核验时看清口径
    assert "12小时" in got
    # 无数字 → 空
    assert extract_indicators("按招标方要求完成归档工作") == []


def test_classify_heading_maps_to_requirement_types():
    """章节标题 → 要求类型。"""
    assert classify_heading("工作项4：验证技术方案") == TYPE_VERIFY
    assert classify_heading("项目交付项") == TYPE_DELIVERABLE
    assert classify_heading("遵循标准") == TYPE_STANDARD
    assert classify_heading("项目背景") == TYPE_OBJECTIVE
    # 「研究及验证」这类专题名同时含两个语义 —— **验证优先**（该专题的产出是验证方案/报告）
    assert classify_heading("信创适配研究及验证") == TYPE_VERIFY
    assert classify_heading("工作项1：梳理分析现状") == TYPE_RESEARCH


# ─────────────────────────────────────────────
# 三种载体的抽取
# ─────────────────────────────────────────────

def _sec(title, content="", level=3):
    return {"title": title, "raw_content": content, "level": level}


def test_numbered_items_become_separate_requirements():
    """编号列表项要**逐条**成条目 —— 投标人要逐条响应，颗粒度必须到条。"""
    body = "\n".join([
        "1. 系统范围与安全对象确认：确认纳入本次专项的系统模块、部署单元与安全边界。",
        "2. 架构与边界安全现状梳理：梳理网络边界、访问路径与外部接入关系。",
        "3. 账号权限与操作留痕核查：核查账号体系、角色权限与审计追溯现状。",
    ])
    reqs = extract_tech_requirements([_sec("工作项1：梳理分析营销管理系统安全现状", body, 6)],
                                     package_id="标包2")

    assert [r.requirement_type for r in reqs] == [TYPE_RESEARCH] * 3, \
        "「梳理分析…现状」是研究任务，不应被 '验证' 规则抢走"
    assert [r.requirement_id for r in reqs] == ["R01", "R02", "R03"]
    assert "系统范围与安全对象确认" in reqs[0].requirement_text
    assert reqs[0].package_id == "标包2"
    assert "第 1 条" in reqs[0].source_location


def test_whole_section_becomes_one_requirement_when_no_list():
    """无编号列表的节（人员要求/知识产权要求）整段作为一条要求。"""
    reqs = extract_tech_requirements([
        _sec("人员要求", "中标人须配置 1 名项目负责人及至少35名项目组成员，确保12小时内响应。"),
    ])
    assert len(reqs) == 1
    assert reqs[0].mandatory_level == MANDATORY
    assert "至少35名" in reqs[0].indicators


def test_deliverable_table_rows_carry_task_and_artifacts():
    """交付物表：每条行要求带上「任务域 / 任务项 → 预期成果」。"""
    table = "\n".join([
        "| 序号 | 任务域 | 任务项 | 预期项目成果和交付物 |",
        "| --- | --- | --- | --- |",
        "| 1 | 安全与保障体系研究及验证（含数据安全） | 工作项1：梳理分析营销管理系统安全现状 | 《营销管理系统安全现状分析报告》 |",
        "| 2 | 安全与保障体系研究及验证（含数据安全） | 工作项3：研究营销管理系统专项安全方案 | 《安全架构说明书》<br>《数据安全专项方案》 |",
    ])
    reqs = extract_tech_requirements([
        _sec("安全与保障体系研究及验证（含数据安全）", "", 5),   # 任务域白名单来源
        _sec("项目交付项", table, 3),
    ])
    deliverable = [r for r in reqs if r.requirement_type == TYPE_DELIVERABLE]
    assert len(deliverable) == 2
    assert "预期成果：《营销管理系统安全现状分析报告》" in deliverable[0].requirement_text
    assert "数据安全专项方案" in deliverable[1].requirement_text


def test_deliverable_table_filters_other_packages_domains():
    """交付物表会**跨标包**列全部任务域 —— 属别的标包的行必须筛掉。

    实测该表混进了「统一数据模型梳理研究」「数据迁移研究及验证」等属其他标包的任务域；
    不筛会让本包矩阵凭空多出别人的要求。
    """
    table = "\n".join([
        "| 序号 | 任务域 | 任务项 | 预期项目成果和交付物 |",
        "| --- | --- | --- | --- |",
        "| 1 | 信创适配研究及验证 | 工作项1：梳理分析现状 | 《信创组件现状分析报告》 |",
        "| 2 | 统一数据模型梳理研究 | 工作项1：梳理分析现状 | 《数据模型痛点清单》 |",
        "| 3 | 数据迁移研究及验证 | 工作项2：归集在运数据模型 | 《全量数据模型PDM》 |",
    ])
    reqs = extract_tech_requirements([
        _sec("信创适配研究及验证", "", 5),          # 本包自己的专题
        _sec("项目交付项", table, 3),
    ])
    deliverable = [r for r in reqs if r.requirement_type == TYPE_DELIVERABLE]
    assert len(deliverable) == 1
    assert "信创组件现状分析报告" in deliverable[0].requirement_text


def test_empty_container_sections_yield_no_requirements():
    """纯容器节（无正文，只用来分组）不产出条目，否则矩阵充满空行。"""
    reqs = extract_tech_requirements([
        _sec("项目内容与要求", "", 2),
        _sec("项目内容", "", 3),
        _sec("高可靠技术专题研究与验证", "", 4),
    ])
    assert reqs == []


def test_duplicate_rows_are_deduplicated():
    """表格里的重复行只保留一次。"""
    table = "\n".join([
        "| 序号 | 任务域 | 任务项 | 预期项目成果和交付物 |",
        "| --- | --- | --- | --- |",
        "| 1 | 信创适配研究及验证 | 工作项4：验证技术方案 | 《信创组件适配研究验证报告》 |",
        "| 2 | 信创适配研究及验证 | 工作项4：验证技术方案 | 《信创组件适配研究验证报告》 |",
    ])
    reqs = extract_tech_requirements([
        _sec("信创适配研究及验证", "", 5),
        _sec("项目交付项", table, 3),
    ])
    assert len([r for r in reqs if r.requirement_type == TYPE_DELIVERABLE]) == 1


def test_verification_and_scoring_links_are_left_empty():
    """规范书不写"如何验证"与"关联评分点" —— 必须留空，不许编。

    编出来的关联会污染下游覆盖度统计（这正是关联节点要解决的问题）。
    """
    reqs = extract_tech_requirements([_sec("工作项4：验证技术方案", "1. 明确验证目标：核查方案的合理性。", 6)])
    r = reqs[0]
    assert r.verification_method is None
    assert r.related_scoring_ids == []
    assert r.requirement_type == TYPE_VERIFY


# ─────────────────────────────────────────────
# 真实技术规范书回归
# ─────────────────────────────────────────────

_REAL_SPEC = (
    "/Users/jianghe/work/软研工作/项目支撑/广东电网/营销系统升级/一期招投标/"
    "广东电网营销一体化(1)/广东电网营销一体化招标文件/招标文件下载2026-06-23-083630/"
    "数智营销一体化平台关键业务场景与高可靠技术研究及验证标的/"
    "高可靠技术专题研究与验证标包/4_高可靠技术专题研究与验证-技术招标文件.docx"
)


@pytest.mark.skipif(
    not __import__("os").path.exists(_REAL_SPEC),
    reason="真实技术规范书不在本机（夹具为本机专有）",
)
def test_real_spec_book_yields_requirements_for_all_five_topics():
    """真实技术规范书：5 个技术专题都有要求条目，且交付物表按本包专题收齐。

    这是「技术规范书在解析阶段结构化」的验收判据 —— 后续关联节点与目录生成
    都建立在这张矩阵上。
    """
    from infra.parser import parse_document

    doc = parse_document(_REAL_SPEC)
    sections = [{"title": s.title, "raw_content": s.raw_content or "", "level": s.level}
                for s in doc.sections]
    reqs = extract_tech_requirements(sections, package_id="标包2")

    topics = {r.topic for r in reqs}
    for must in ("安全与保障体系研究及验证（含数据安全）",
                 "埋点与低延迟系统技术研究及验证",
                 "云原生架构研究及验证",
                 "高可靠性容灾研究及验证",
                 "信创适配研究及验证"):
        assert must in topics, f"缺技术专题：{must}"

    # 交付物：5 个专题各有工作项交付物
    deliverable_topics = {r.topic for r in reqs if r.requirement_type == TYPE_DELIVERABLE}
    assert len(deliverable_topics & topics) >= 5

    # 别的标包的任务域不得混入
    joined = "\n".join(r.requirement_text for r in reqs)
    for foreign in ("统一数据模型梳理研究", "数据迁移研究及验证"):
        assert foreign not in joined, f"混入其他标包任务域：{foreign}"

    # 条目可追溯：ID 连续、出处非空
    assert reqs[0].requirement_id == "R01"
    assert all(r.source_location for r in reqs)
