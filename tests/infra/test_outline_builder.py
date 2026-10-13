"""目录骨架的确定性保证。

用户的要求（2026-10-13）：
  「需要稳定地按照评分点和技术规范书的内容结合，输出专业目录。
    请核实如何保障它能够稳定地输出目录结构，不要一会儿变这样，一会儿变那样。」

所以这里的测试**主要不是测功能，而是钉住"稳定"本身**：
同样的输入必须逐字得到同样的目录。目录结构一旦由模型自由生成，就永远做不到这点。
"""
from __future__ import annotations

import pytest

from infra.parser.outline_builder import (
    OutlineNode,
    build_outline,
    build_scoring_skeleton,
    render_outline_text,
    split_criterion,
)
from infra.parser.review_points import ReviewPoint


def _rp(factor, item, criterion, *, category="tech", package_no="2", points=10.0):
    return ReviewPoint(package_no=package_no, category=category, factor=factor,
                       item=item, criterion=criterion, points=points)


# ── 稳定性：这是本模块存在的理由 ──────────────────────────────

def test_same_input_yields_identical_outline():
    """同样输入两次 → 目录逐字一致。

    这是"不要一会儿变这样一会儿变那样"的机器化表述。只要结构来自规则，
    这条断言就永远成立；结构一旦交给模型自由生成，它必然时好时坏。
    """
    pts = [
        _rp("技术方案", "技术方案", "1、方案可行性：（1）遵循数字化转型顶层设计，方案总体架构设计合理，得8-12分。"
                                  "2、方案先进性：（1）对信创适配、云原生、容灾等技术理解准确，得12-18分。"),
        _rp("服务团队", "项目负责人", "(1)拟委派本项目的项目负责人具有 PMP 证书，满足得5分。"),
    ]
    a = render_outline_text(build_outline(pts))
    b = render_outline_text(build_outline(pts))
    assert a == b


def test_outline_is_independent_of_point_order_uniqueness():
    """同名评分因素只出现一个 L1（多行共用同一因素时不能重复开章）。"""
    pts = [
        _rp("服务团队", "项目负责人", "PMP 证书得5分。"),
        _rp("服务团队", "项目团队（项目负责人除外）", "不少于35人得5分。"),
    ]
    nodes = build_scoring_skeleton(pts)
    assert [n.title for n in nodes if n.level == 1] == ["服务团队"]


# ── 结构：评分因素 → 详细评审项 → 分项要素 ────────────────────

def test_two_level_when_item_differs_from_factor():
    pts = [_rp("对项目的理解", "对项目的理解正确、深入、全面", "（1）深刻理解项目背景、现状、目标、建设范围、建设内容、项目重难点等。")]
    nodes = build_scoring_skeleton(pts)
    assert [(n.level, n.title) for n in nodes[:2]] == [
        (1, "对项目的理解"), (2, "对项目的理解正确、深入、全面")]


def test_item_same_as_factor_is_not_duplicated():
    """L2 与 L1 同名时跳过 L2。

    实测模型就是这样产出 `L1 技术方案 / L2 技术方案` 的重复节点 ——
    确定性骨架不能重复这个错误。
    """
    pts = [_rp("技术方案", "技术方案", "1、方案可行性：（1）…得8-12分。2、方案先进性：（1）…得12-18分。")]
    nodes = build_scoring_skeleton(pts)
    titles = [n.title for n in nodes]
    assert titles.count("技术方案") == 1
    assert "方案可行性" in titles and "方案先进性" in titles


def test_criterion_labels_become_subchapters():
    """分项要素里的「1、方案可行性：…」要成为 L3 章节名。"""
    subs = split_criterion("1、方案可行性：（1）遵循数字化转型顶层设计，得8-12分。"
                           "2、方案先进性：（1）对信创适配、云原生理解准确，得12-18分。")
    assert [t for t, _ in subs] == ["方案可行性", "方案先进性"]


def test_criterion_list_becomes_subchapters():
    """顿号清单（项目背景、现状、目标…）要逐项成章 —— 这就是用户要的"具体评分点"。"""
    subs = split_criterion("（1）深刻理解项目背景、现状、目标、建设范围、建设内容、项目重难点等。")
    titles = [t for t, _ in subs]
    assert "项目背景" in titles[0] and "建设内容" in titles and len(titles) >= 5


def test_band_criterion_gets_standard_landing_points():
    """档位式条款（优良/中等/一般）没有细分标签，但要给标准落点，不留空壳。"""
    subs = split_criterion("优良（7-10分）：投标响应度较高，有部分条款优于招标技术要求。"
                           "中等（6分）：响应度良好。一般（1-5分）：存在非实质性负偏差或未作响应。")
    titles = [t for t, _ in subs]
    assert any("优于" in t for t in titles)
    assert any("负偏差" in t for t in titles)


def test_band_rule_not_triggered_by_ordinary_clause():
    """普通条款不能被误判成档位式。

    实测 `交付成果满足要求` 的原文含"满足项目要求"，旧规则据此编出两个假落点。
    """
    subs = split_criterion("（1）投标人承诺的交付成果和成果质量满足项目要求(3分)。")
    titles = [t for t, _ in subs]
    assert not any("优于招标技术要求" in t for t in titles)


# ── 规范书结合点 ──────────────────────────────────────────────

def _spec(id_, level, title, content=""):
    return {"id": id_, "level": level, "title": title, "raw_content": content}


def test_spec_topics_are_merged_into_matching_scoring_chapter():
    """规范书主题要**并进对应评分章节**，而不是只丢进独立板块。

    实测依据：`方案先进性` 的分项要素原文直接写着"信创适配、云原生、容灾、埋点、低延迟"，
    而规范书里正有这五个研究专题 —— 这就是用户说的"结合点"。
    """
    pts = [_rp("技术方案", "技术方案",
               "1、方案可行性：（1）得8-12分。2、方案先进性：（1）对信创适配、云原生、容灾、"
               "埋点、低延迟等技术理解准确、深入，技术方案先进，得12-18分。")]
    spec = [
        _spec("c1", 4, "项目内容与要求"),
        _spec("c2", 5, "高可靠技术专题研究与验证"),
        _spec("c3", 6, "信创适配研究及验证"),
        _spec("c4", 7, "工作项1：梳理分析现状"),
    ]
    nodes = build_outline(pts, spec_sections=spec)
    adv = next(n for n in nodes if n.title == "方案先进性")
    assert "信创适配研究及验证" in adv.spec_refs
    # 工作项要跟着专题一起进来（研究/验证任务必须逐条能应答）
    assert any(n.title.startswith("工作项1") for n in nodes)


def test_unmatched_spec_sections_get_their_own_block():
    """没匹配上的规范书章节也要有落点（不能凭空消失）。"""
    pts = [_rp("技术方案", "技术方案", "1、方案可行性：（1）得8-12分。2、方案先进性：（1）得12-18分。")]
    spec = [_spec("c1", 3, "保密要求"), _spec("c2", 4, "人员要求")]
    nodes = build_outline(pts, spec_sections=spec)
    assert any(n.title == "技术规范书要求应答" for n in nodes)
    assert any(n.title == "保密要求" for n in nodes)


def test_merged_spec_topics_do_not_reappear_in_leftover_block():
    """已并入的专题不能在残留板块里再出现一次（实测出现过"两遍"）。"""
    pts = [_rp("技术方案", "技术方案",
               "1、方案可行性：（1）得8-12分。2、方案先进性：（1）信创适配、云原生，得12-18分。")]
    spec = [
        _spec("c1", 4, "项目内容与要求"),
        _spec("c2", 5, "高可靠技术专题研究与验证"),
        _spec("c3", 6, "信创适配研究及验证"),
    ]
    nodes = build_outline(pts, spec_sections=spec)
    titles = [n.title for n in nodes]
    assert titles.count("信创适配研究及验证") == 1


def test_children_follow_their_parent_in_flat_order():
    """扁平列表 + level 缩进：子节点必须紧跟父节点，否则视觉上会挂到别人下面。

    实测：方案先进性的规范书子树被追加到列表末尾，渲染出来挂在了「服务团队」下。
    """
    pts = [_rp("技术方案", "技术方案",
               "1、方案可行性：（1）得8-12分。2、方案先进性：（1）信创适配，得12-18分。")]
    spec = [_spec("c1", 4, "项目内容与要求"), _spec("c2", 5, "高可靠技术专题研究与验证"),
            _spec("c3", 6, "信创适配研究及验证"), _spec("c4", 7, "工作项1：梳理分析现状")]
    nodes = build_outline(pts, spec_sections=spec)
    parent_idx = next(i for i, n in enumerate(nodes) if n.title == "方案先进性")
    child_idx = next(i for i, n in enumerate(nodes) if n.title == "信创适配研究及验证")
    assert child_idx == parent_idx + 1
    assert nodes[child_idx].level > nodes[parent_idx].level


# ── 过滤 ──────────────────────────────────────────────────────

def test_filters_by_category_and_package():
    pts = [
        _rp("技术方案", "技术方案", "技术条款得30分。"),
        _rp("商务响应", "响应和偏差", "商务条款得5分。", category="commerce"),
        _rp("技术方案", "技术方案", "别的标包得30分。", package_no="3"),
    ]
    nodes = build_scoring_skeleton(pts, category="tech", package_no="2")
    titles = [n.title for n in nodes]
    assert "商务响应" not in titles
    assert titles.count("技术方案") == 1


# ── 用户明确要求的算法：标星项 + 规范书要求内容都要进目录 ──────────
# 「先根据技术评分点拆分章节目录（大评分点为主目录，详细评分要求为子目录），
#   再根据技术规范书内容，将**标星项**和技术规范书**要求内容**补充到对应目录结构中」

def _spec_marked(id_, level, title, content="", marks=("★",)):
    s = _spec(id_, level, title, content)
    return {**s, "special_marks": list(marks)}


def test_starred_spec_section_lands_in_outline():
    """标星项（★/▲）是招标人点名的硬要求，必须出现在目录里。

    改造前它只被用于 grounding（给 8 字段提炼继承原文），目录里一个字都看不到 ——
    实测标的1 的「3.4.2. 自主可控要求」带★，161 节目录里 0 个带★。
    """
    from infra.parser.outline_builder import build_outline

    rp = _rp("技术方案", "技术方案", "1、技术方案可行性：架构合理得 10 分。")
    outline = build_outline(
        [rp],
        spec_sections=[_spec_marked("s1", 3, "自主可控要求", "应满足自主可控要求")],
    )
    starred = [n for n in outline if "★" in n.title]
    assert starred, "标星项没有进目录"
    assert "自主可控要求" in starred[0].title


def test_starred_section_never_dropped_when_unmatched():
    """匹配不到任何评分点时也必须出现（宁可挂在要求应答板块，也不能丢）。"""
    from infra.parser.outline_builder import build_outline

    rp = _rp("技术方案", "技术方案", "1、技术方案可行性：架构合理得 10 分。")
    outline = build_outline(
        [rp],
        spec_sections=[_spec_marked("s1", 3, "完全无关的某个专题名称", "", marks=("▲",))],
    )
    assert any("▲" in n.title and "完全无关的某个专题名称" in n.title for n in outline)


def test_requirement_groups_attach_to_matching_chapter_with_ids():
    """规范书要求内容按主题挂到对应章节，并把 requirement_id 挂到节点链上。

    覆盖度靠这些 id 计算；逐条成章会让目录爆掉（一个模块主题就有 282 条），
    所以按主题聚合、条数写在标题里。
    """
    from infra.parser.outline_builder import build_outline, compute_coverage

    rp = _rp("技术方案", "技术方案可行性", "1、技术方案可行性：实施计划详细得 30 分。")
    outline = build_outline(
        [rp],
        requirement_groups=[
            {"topic": "技术方案可行性", "ids": ["sr-1", "sr-2"]},
            {"topic": "完全不相关的主题", "ids": ["sr-9"]},
        ],
    )
    hit = [n for n in outline if "（规范要求 2 条）" in n.title]
    assert hit and hit[0].spec_requirement_ids == ["sr-1", "sr-2"]
    # 挂到匹配章节下（比它深一级），而不是另起一个顶级章节
    assert hit[0].level > 1
    # 匹配不到的主题也保留
    assert any("完全不相关的主题" in n.title for n in outline)

    class _R:
        def __init__(self, sid, title):
            self.stable_id, self.title, self.topic = sid, title, ""
    cov = compute_coverage(outline, [rp],
                           [_R("sr-1", "a"), _R("sr-2", "b"), _R("sr-9", "c")])
    assert cov["requirements_landed"] == 3      # 三个 id 都有落点


def test_topic_placement_answer_overrides_automatic_match():
    """用户指定主题归属时不再猜 —— 自动匹配总有猜错的时候（实测功能模块挂错过）。"""
    from infra.parser.outline_builder import build_outline

    rp = _rp("技术方案", "技术方案可行性", "1、技术方案可行性：实施计划详细得 30 分。")
    groups = [{"topic": "客户档案管理模块", "ids": ["sr-1"]}]
    for target, expect_under in (("技术方案可行性", True), ("__extra__", False)):
        outline = build_outline([rp], requirement_groups=groups,
                                topic_placement={"客户档案管理模块": target})
        node = next(n for n in outline if "客户档案管理模块" in n.title)
        if expect_under:
            assert node.level > 1 and node.review_point_id == rp.point_id
        else:
            assert not node.review_point_id      # 强制进"要求应答"板块
