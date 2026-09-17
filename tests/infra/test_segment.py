"""区段定位（infra/retrieval/segment.py）。

用户说的「按照主招标文件中的标包2的技术评分要求拆分章节」，要求的是**用文件的
哪一段**。实测一份 164 页采购文件里，标包2 的完整评分标准只有 5,757 字符（占全文
7.3%），而它跨的 34 个章节标题里一个「标包2」都没有——章级 BM25 表达不了这个区段。
但文档本身有稳定的结构锚点，按前缀词 + 编号就能整段切出来。
"""

import pytest

from infra.retrieval.segment import (
    Segment,
    cut_segments,
    extract_scope_tokens,
    locate_segments,
    select_segments,
)

INSTRUCTION = "按照主招标文件的PDF中的标包2的技术评分要求的点，按照评分要求中的每一点进行大纲拆分章节"


def _sec(title: str, content: str = "", level: int = 1) -> dict:
    return {"title": title, "content": content, "level": level}


# ─────────────────────────────────────────────
# extract_scope_tokens
# ─────────────────────────────────────────────

def test_extracts_prefix_form():
    assert extract_scope_tokens(INSTRUCTION) == [("标包", "2")]


def test_extracts_suffix_form():
    assert extract_scope_tokens("按2标段的技术要求拆分") == [("标段", "2")]


def test_normalizes_chinese_and_fullwidth_digits():
    assert extract_scope_tokens("标包二的技术要求") == [("标包", "2")]
    assert extract_scope_tokens("标包 12") == [("标包", "12")]
    assert extract_scope_tokens("标包２的要求") == [("标包", "2")]


def test_extracts_multiple_scopes():
    assert extract_scope_tokens("标包2和标包3都要") == [("标包", "2"), ("标包", "3")]


def test_deduplicates_repeated_scope():
    assert extract_scope_tokens("标包2、标包2") == [("标包", "2")]


@pytest.mark.parametrize("text", [
    "按评分项逐条拆章",          # 没点范围 → 安全退回既有路径
    "改成按实施流程组织",
    "按 2.2.4 节的要求拆分",     # 小数点：不是范围标识
    "2023年以来的项目",          # 数字后面跟单位词
    "项目团队不少于35人",
    "工作项1：梳理分析现状",     # docx 里真实存在的写法，绝不能误判
    "",
])
def test_no_scope_token_returns_empty(text):
    assert extract_scope_tokens(text) == []


# ─────────────────────────────────────────────
# cut_segments
# ─────────────────────────────────────────────

def _doc_with_three_packages() -> list[dict]:
    """仿真实采购文件：开头一大段公告，然后是标包1/2/3 各自的评审标准。"""
    return [
        _sec("招标公告", "招标人：某某电网有限责任公司\n招标文件（标准文件范本）"),
        _sec("投标人须知", "投标人应当具备下列条件…"),
        _sec("评审标准", "\n".join([
            "下列评审标准适用的标的/标包：标包1：关键业务场景研究与验证标包",
            "标包1 的评分内容甲",
            "下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包",
            "技术评分标准：技术方案、项目管理、实施方案、交付成果、服务团队",
            "下列评审标准适用的标的/标包：标包3：主数据管理研究及全过程技术管控标包",
            "标包3 的评分内容乙",
        ])),
        _sec("合同范本", "合同条款…"),
    ]


def test_cut_segments_splits_at_anchors():
    segments = cut_segments(_doc_with_three_packages())

    titles = [s.title for s in segments]
    assert titles[0] == "（文档开头）"
    assert "标包1：关键业务场景研究与验证标包" in titles[1]
    assert "标包2：高可靠技术专题研究与验证标包" in titles[2]
    assert "标包3：主数据管理研究及全过程技术管控标包" in titles[3]
    # 段是首尾相接的，没有内容凭空消失
    assert segments[0].start == 0
    assert all(
        a.end == b.start for a, b in zip(segments, segments[1:])
    )


def test_cut_segments_without_anchors_returns_empty():
    assert cut_segments([_sec("甲", "没有锚点"), _sec("乙", "也没有")]) == []


def test_cut_segments_anchor_at_content_start():
    """锚点行就是正文第一行时，段标题取锚点行，不重复输出也不丢字。"""
    segments = cut_segments([_sec("正文", "标包2：乙标包\n评分内容")])

    assert len(segments) == 1
    assert segments[0].start == 0
    assert "评分内容" in segments[0].content


def test_cut_segments_title_is_truncated():
    long_tail = "标包2：" + "很长的说明" * 40
    segments = cut_segments([_sec("正文", long_tail)])

    assert len(segments[0].title) <= 40


# ─────────────────────────────────────────────
# select_segments / locate_segments
# ─────────────────────────────────────────────

def test_selects_only_matching_number():
    segments = cut_segments(_doc_with_three_packages())
    picked = select_segments(segments, [("标包", "2")])

    assert len(picked) == 1
    assert "标包2" in picked[0].title
    assert "标包1 的评分内容甲" not in picked[0].content
    assert "标包3 的评分内容乙" not in picked[0].content


def test_selects_multiple_numbers():
    segments = cut_segments(_doc_with_three_packages())
    picked = select_segments(segments, [("标包", "1"), ("标包", "3")])

    assert len(picked) == 2
    assert all("标包2" not in s.title for s in picked)


def test_locate_marks_selected_and_keeps_the_rest_as_titles():
    segments = locate_segments(_doc_with_three_packages(), INSTRUCTION)

    assert segments is not None
    selected = [s for s in segments if s.selected]
    assert len(selected) == 1
    assert "标包2" in selected[0].title
    # 未选中的段仍在（下游只列它们的标题），但不是 selected
    assert sum(1 for s in segments if not s.selected) == len(segments) - 1


def test_locate_gives_everything_to_selected_segments():
    """选中段之外的正文一律不进 digest —— 这正是省 token 的地方。"""
    segments = locate_segments(_doc_with_three_packages(), INSTRUCTION)
    blob = "\n".join(s.content for s in segments if s.selected)

    assert "高可靠技术专题研究与验证标包" in blob
    assert "招标人：某某电网有限责任公司" not in blob
    assert "投标人应当具备下列条件" not in blob


def test_locate_returns_none_without_scope_token():
    assert locate_segments(_doc_with_three_packages(), "按评分项逐条拆章") is None


def test_locate_returns_none_when_document_has_no_anchor():
    """要求里点了范围、文档里却没有同类锚点 → 退回章级定位，不硬切。"""
    doc = [_sec(f"第{i}页", "招标人：某某公司") for i in range(20)]
    assert locate_segments(doc, INSTRUCTION) is None


def test_locate_returns_none_when_number_does_not_exist():
    doc = [_sec("正文", "标包7：某个标包\n内容")]
    assert locate_segments(doc, INSTRUCTION) is None


def test_locate_handles_empty_sections():
    assert locate_segments([], INSTRUCTION) is None


# ─────────────────────────────────────────────
# 回归：按项目 8 的实测结构造一份等价语料
# ─────────────────────────────────────────────

def _project8_like_doc() -> list[dict]:
    """仿项目 8：325 个噪声章节里夹着三个标包各自的评分标准。

    实测该文件 79,426 字符，标包2 段约 5,700 字符，段内标题（表格碎片）没有一个
    含「标包2」—— 所以章级检索必然漏掉它。
    """
    noise = [
        _sec(f"第{i}页", "招标人：广东电网有限责任公司\n投标人须知前附表")
        for i in range(120)
    ]
    body = "\n".join(
        [f"（{i}）项目管理方案不科学或不合理，项目扣分。" for i in range(30)]
    )
    packages = []
    for name, payload in [
        ("标包1：关键业务场景研究与验证标包", "标包1 的商务与技术要求"),
        ("标包2：高可靠技术专题研究与验证标包", body),
        ("标包3：主数据管理研究及全过程技术管控标包", "标包3 的商务与技术要求"),
    ]:
        packages.append(_sec("评审标准", f"下列评审标准适用的标的/标包：{name}\n{payload}"))
    return noise + packages + [_sec("合同范本", "合同条款范本" * 50)]


def test_project8_like_regression():
    doc = _project8_like_doc()
    segments = locate_segments(doc, INSTRUCTION)

    assert segments is not None
    selected = [s for s in segments if s.selected]
    assert len(selected) == 1
    assert "标包2" in selected[0].title

    blob = selected[0].content
    # 目标段整体进来，且不含别的标包
    assert "项目管理方案不科学或不合理" in blob
    assert "标包1 的商务与技术要求" not in blob
    assert "标包3 的商务与技术要求" not in blob
    # 噪声（封面、投标人须知）完全不进
    assert "投标人须知前附表" not in blob
    assert "合同条款范本" not in blob
    # 选中段远小于全文 —— 这就是省下来的部分
    total = sum(len(s["content"]) for s in doc)
    assert len(blob) < total * 0.2
