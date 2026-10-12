"""区段定位（infra/retrieval/segment.py）。

用户说的「按照主招标文件中的标包2的技术评分要求拆分章节」，要求的是**用文件的
哪一段**。实测一份 164 页采购文件里，标包2 的完整评分标准只有 5,757 字符（占全文
7.3%），而它跨的 34 个章节标题里一个「标包2」都没有——章级 BM25 表达不了这个区段。
但文档本身有稳定的结构锚点，按前缀词 + 编号就能整段切出来。
"""

import pytest

from infra.retrieval.segment import (
    Segment,
    diagnose_scope_miss,
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


# ─────────────────────────────────────────────
# 回归：段不得跨文件（F2）
# ─────────────────────────────────────────────

def _file_sec(title, content, source_file, level=1):
    """带来源文件的章节（与文件里既有的 _sec 助手区分开，别再撞名）。"""
    return {"title": title, "content": content, "level": level,
            "source_file": source_file}


def test_segments_never_swallow_the_next_file():
    """最后一段的边界是该**文件**末尾，不是拼接全文末尾。

    旧实现把多份文件直接 join，最后一个锚点之后那一段的 end=len(full)，于是
    「标包2」的选中段会把后面整份 docx（实测 20275 字符，其中只有 ~200 属实）
    吞进来，再以"【已定位到的材料】+ 强制复刻结构"的身份进 prompt —— 目录就按
    无关文件的结构拆了。
    """
    file_a = [
        _file_sec("标包1 评审标准", "标包1：内容甲" + "甲" * 60, "A.pdf"),
        _file_sec("标包2 评审标准", "标包2：高可靠技术专题研究与验证" + "乙" * 200, "A.pdf"),
    ]
    file_b = [
        _file_sec("技术规范", "整份技术规范书的正文" + "丙" * 3000, "B.docx"),
    ]

    segments = locate_segments(
        file_a + file_b, "按照主招标文件的PDF中的标包2的技术评分要求的点拆分章节",
    )
    picked = [s for s in segments if s.selected]

    assert len(picked) == 1
    assert "乙" in picked[0].content
    assert "丙" not in picked[0].content, "选中段跨文件吞进了另一份文件的正文"
    assert len(picked[0].content) < 1000


def test_segments_tile_every_file_without_loss():
    """按文件切段后仍然覆盖全部正文（不因为切分而丢内容），且各文件都有段。"""
    sections = [
        _file_sec("封面", "前置说明文字\n", "A.pdf"),       # 锚点之前的内容 → 产生「文档开头」段
        _file_sec("标包1 评审标准", "标包1：甲" * 20, "A.pdf"),
        _file_sec("标包2 评审标准", "标包2：乙" * 20, "A.pdf"),
        _file_sec("无关章节", "丙" * 500, "B.docx"),        # 这份没有任何锚点
    ]
    segments = cut_segments(sections)

    # A 有锚点 → 开头段 + 两个锚点段；B 无锚点 → 按它的章节各给一段（都未选中）
    assert any(s.title.startswith("（文档开头）·A.pdf") for s in segments)
    b_segments = [s for s in segments if s.title == "无关章节"]
    assert len(b_segments) == 1 and len(b_segments[0].content) == 500
    assert not b_segments[0].selected, "无锚点文件的段不能被范围匹配选中"

    # 每个文件内部按 "\n" 拼接，文件之间不拼 → join 分隔符数按文件分别计
    per_file: dict[str, int] = {}
    for s in sections:
        per_file[s["source_file"]] = per_file.get(s["source_file"], 0) + 1
    total = sum(len(s.content) for s in segments)
    expected = (sum(len(s["content"]) for s in sections)
                + sum(n - 1 for n in per_file.values()))
    assert total == expected, f"切段后字符数 {total} != 原文 {expected}（有内容丢失或重复）"


def test_single_file_without_source_file_still_works():
    """没有 source_file（单文件 / 老 checkpoint）时回退到原来的单文件切段。"""
    segments = cut_segments([
        {"title": "前置说明", "content": "开头文字\n"},
        {"title": "标包2 评审标准", "content": "标包2：乙" * 10},
    ])
    assert segments[0].title == "（文档开头）"
    # 锚点段的标题取自锚点行原文（不是章节标题）—— 这是既有设计
    assert segments[1].title.startswith("标包2：")
    assert "标包2：乙" in segments[1].content


# ─────────────────────────────────────────────
# 范围未命中的可诊断原因（F3）
# ─────────────────────────────────────────────

def test_diagnose_scope_miss_empty_when_no_scope_token():
    """要求里没点范围 → 空串（调用方不该提示"没定位到"，因为压根没要求定位）。"""
    assert diagnose_scope_miss([_file_sec("任意", "正文", "A.pdf")], "按评分项逐条拆章") == ""


def test_diagnose_scope_miss_says_no_anchor_when_file_has_none():
    reason = diagnose_scope_miss(
        [_file_sec("技术规范", "没有锚点的正文", "A.pdf")],
        "按照标包2的技术评分要求拆分章节",
    )
    assert "没有" in reason and "锚点" in reason and "标包2" in reason


def test_diagnose_scope_miss_says_number_mismatch_when_anchors_exist():
    reason = diagnose_scope_miss(
        [_file_sec("标包1 评审标准", "标包1：甲", "A.pdf")],
        "按照标包2的技术评分要求拆分章节",
    )
    assert "对不上" in reason and "标包2" in reason
