"""评审要素表结构化抽取的用例。

为什么值得单独锁（背景）：第一步解析此前的产物是"节 → 原文"，第三步派生目录时靠
``build_spec_digest`` 按字符预算压缩成 8000 字文本。实测同一份招标文件：

- 提炼要求写了「标包 2」→ 区段定位命中，评分表活下来；
- 提炼要求没写范围 → 退回全量，digest 末尾是
  「……以下 380 个章节因长度限制未列出」，**标包2 / 技术条款差异性 / 对项目的理解 /
  服务团队 / 分值 全部不在 prompt 里**。

于是"目录拆得对不对"取决于提示词措辞。这里的断言从**真实招标文件**的评分表结构来，
用来钉住"评分点必须可结构化、可校验"这件事。

用例分两层：
1. 纯构造用例 —— 覆盖分类合并单元格、跨页窄行左移、续行合并、标包作用域；
2. 真实文件回归 —— 断言标包 2 技术评分 = 8 项、分值和 100、分项逐一相等。
"""

import pytest

from infra.parser.review_points import (
    CATEGORY_COMMERCE,
    CATEGORY_TECH,
    classify_objective,
    extract_bands,
    extract_review_points,
)


# ─────────────────────────────────────────────
# 纯函数：档位与客观项判定
# ─────────────────────────────────────────────

def test_extract_bands_reads_range_and_single():
    """区间档位（得8-12分）与单值档位（得5分）都要认出来。"""
    bands = extract_bands("（1）遵循数字化转型顶层设计，得8-12分。（2）得5分。")
    assert [(b.lo, b.hi) for b in bands] == [(8, 12)]

    single = extract_bands("投标人承诺优于项目要求，得5分。")
    assert [(b.lo, b.hi) for b in single] == [(5, 5)]


def test_classify_objective_separates_material_from_writing():
    """客观项（证书/社保/业绩）= 准备材料；主观项 = 写内容。

    实测口径：标包 2 技术评分里 25 分是"项目负责人 + 项目团队"，
    得分动作是**交扫描件**，不是写一段话。
    """
    assert classify_objective("服务团队", "项目负责人", "具有PMP证书，满足得5分；需提供社保证明") is True
    assert classify_objective("服务团队", "项目团队（项目负责人除外）",
                              "团队不少于35人，需提供资格证书与社保") is True
    assert classify_objective("技术方案", "技术方案", "遵循数字化转型顶层设计，方案总体架构设计合理") is False
    assert classify_objective("对项目的理解", "对项目的理解正确、深入、全面",
                              "深刻理解项目背景、现状、目标") is False


# ─────────────────────────────────────────────
# 构造用例：真实表格形状
# ─────────────────────────────────────────────

# 宽行表：条款号 | 分类 | 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值
_WIDE_HEADER = "| 条款号 |   | 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值 |"

# 窄行表：pdfplumber 丢掉行首空列后整体左移
_NARROW_HEADER = "| 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值 |"


def _sec(content: str, sid: str = "s1"):
    return {"id": sid, "title": "详细评审标准", "raw_content": content, "source_file": "招标文件.pdf"}


def test_package_scope_becomes_structured_field():
    """「下列评审标准适用的标的/标包：标包N：…」要成为作用域，而不只是一行表格文本。"""
    content = "\n".join([
        "| 下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包 |   |   |   |   |   |   |",
        _WIDE_HEADER,
        "| 2.2.4<br>（2） | 技术评分标准 | 1 | 技术条款差异性 | 技术条款差异性 | 优良（7-10分）：完全满足 | 10 |",
        "| 下列评审标准适用的标的/标包：标包3：主数据管理研究及全过程技术管控标包 |   |   |   |   |   |   |",
        _WIDE_HEADER,
        "| 2.2.4<br>（2） | 技术评分标准 | 1 | 数据治理 | 数据治理 | 得5-8分 | 20 |",
    ])
    t = extract_review_points([_sec(content)])

    assert t.packages == {"2": "高可靠技术专题研究与验证标包",
                          "3": "主数据管理研究及全过程技术管控标包"}
    p2 = t.of_category(CATEGORY_TECH, "2")
    p3 = t.of_category(CATEGORY_TECH, "3")
    assert [(p.factor, p.points) for p in p2] == [("技术条款差异性", 10.0)]
    assert [(p.factor, p.points) for p in p3] == [("数据治理", 20.0)]
    assert p2[0].package_label == "标包2：高可靠技术专题研究与验证标包"


def test_narrow_rows_shifted_left_are_read_correctly():
    """窄行（pdfplumber 丢行首空列）不能把序号读成评分因素。

    实测形态：同一个逻辑表里，宽行 7 个单元格、窄行 5 个单元格并存，
    按固定下标取值会把窄行的"序号"读成"评分因素"。
    """
    content = "\n".join([
        "| 下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包 |   |   |   |   |   |   |",
        _WIDE_HEADER,
        "| 2.2.4<br>（2） | 技术评分标准 | 3 | 技术方案 | 技术方案 | 1、方案可行性：得8-12分。 | 30 |",
        "| 4 | 项目管理 | 项目管理方案及质量管理方案 | （1）具备科学合理的方案，得7-10分。 | 10 |",
        "| 5 | 实施方案 | 实施进度及保障措施 | （1）实施计划详细，得8-10分。 | 10 |",
    ])
    t = extract_review_points([_sec(content)])
    got = [(p.seq, p.factor, p.item, p.points) for p in t.of_category(CATEGORY_TECH, "2")]

    assert got == [
        ("3", "技术方案", "技术方案", 30.0),
        ("4", "项目管理", "项目管理方案及质量管理方案", 10.0),
        ("5", "实施方案", "实施进度及保障措施", 10.0),
    ]


def test_continuation_row_merges_into_previous_point():
    """跨页断行的正文要并回上一条，且**不得污染评分因素/评审项**。

    实测踩到：把续行文字并进 factor 后，「技术方案」的评分因素被灌进后面所有档位描述。
    """
    content = "\n".join([
        "| 下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包 |   |   |   |   |   |   |",
        _WIDE_HEADER,
        "| 2.2.4<br>（2） | 技术评分标准 | 3 | 技术方案 | 技术方案 | 1、方案可行性：得8-12分。 | 30 |",
        "|   |   |   |   |   | （2）基本符合顶层设计，得5-7分。 |   |",
    ])
    t = extract_review_points([_sec(content)])
    p = t.of_category(CATEGORY_TECH, "2")[0]

    assert p.factor == "技术方案"
    assert p.item == "技术方案"
    assert "（2）基本符合顶层设计" in p.criterion
    assert "得8-12分" in p.criterion
    # 档位应同时包含 8-12 与 5-7
    assert [(b.lo, b.hi) for b in p.bands] == [(8, 12), (5, 7)]


def test_commerce_and_tech_categories_are_distinguished():
    """分类列是合并单元格（只在部分行出现）→ 空则沿用上一行的类别。"""
    content = "\n".join([
        "| 下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包 |   |   |   |   |   |   |",
        _WIDE_HEADER,
        "| 2.2.4<br>（1） | 商务评分标准 | 1 | 财务状况 | 营业净利率 | 优（10分）：连续3年为正 | 10 |",
        "|   |   | 2 | 商务响应 | 响应和偏差 | 全部响应得满分 | 5 |",
        "| 2.2.4<br>（2） | 技术评分标准 | 1 | 技术条款差异性 | 技术条款差异性 | 优良（7-10分） | 10 |",
    ])
    t = extract_review_points([_sec(content)])

    assert [p.factor for p in t.of_category(CATEGORY_COMMERCE, "2")] == ["财务状况", "商务响应"]
    assert [p.factor for p in t.of_category(CATEGORY_TECH, "2")] == ["技术条款差异性"]


def test_rows_without_points_are_kept():
    """没有分值列的行（价格分算法等）也要保留，不能因为缺分值被丢掉。"""
    content = "\n".join([
        "| 下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包 |   |   |   |   |   |   |",
        _WIDE_HEADER,
        "| 2.2.4<br>（2） | 技术评分标准 | 1 | 技术条款差异性 | 技术条款差异性 | 优良（7-10分） | 10 |",
    ])
    t = extract_review_points([_sec(content)])
    assert len(t.of_category(CATEGORY_TECH, "2")) == 1


# ─────────────────────────────────────────────
# 真实文件回归（标包 2 技术评分）
# ─────────────────────────────────────────────

_TENDER = (
    "/Users/jianghe/work/软研工作/项目支撑/广东电网/营销系统升级/一期招投标/"
    "广东电网营销一体化(1)/广东电网营销一体化招标文件/"
    "1_广东电网有限责任公司2026年四个数智信息化项目专项采购（数智营销一体化平台关键.pdf"
)
# 期望值直接来自人工拆解结果（见 docs/tender/标包2-技术评分点拆分.md）
_EXPECTED_TECH_PKG2 = [
    ("1", "技术条款差异性", 10),
    ("2", "对项目的理解", 10),
    ("3", "技术方案", 30),
    ("4", "项目管理", 10),
    ("5", "实施方案", 10),
    ("6", "交付成果", 5),
    ("7", "服务团队", 10),
    ("8", "服务团队", 15),
]


@pytest.mark.skipif(
    not __import__("os").path.exists(_TENDER),
    reason="真实招标文件不在本机（夹具为本机专有）",
)
def test_real_tender_package2_technical_points():
    """真实招标文件：标包 2 技术评分 = 8 项、分值和 100、分项逐一相等。

    这是「第一步解析结果与人工拆解一致」的验收判据 —— 一次锁住标包作用域、
    跨页列布局、分值列读取三件事。
    """
    from infra.parser import parse_document

    doc = parse_document(_TENDER)
    sections = [
        {"id": s.id, "title": s.title, "raw_content": s.raw_content or "",
         "source_file": "招标文件.pdf"}
        for s in doc.sections
    ]
    table = extract_review_points(sections)

    # 标包号 → 标包名称（该 PDF 的评分表覆盖标包 1/2/3）
    assert table.packages["2"] == "高可靠技术专题研究与验证标包"

    points = table.of_category(CATEGORY_TECH, "2")
    assert len(points) == 8
    assert table.total_points(CATEGORY_TECH, "2") == 100
    assert [(p.seq, p.factor, p.points) for p in points] == [
        (seq, factor, float(pts)) for seq, factor, pts in _EXPECTED_TECH_PKG2
    ]

    # 客观/主观分流：服务团队两项是材料项
    by_seq = {p.seq: p for p in points}
    assert by_seq["7"].objective is True
    assert by_seq["8"].objective is True
    assert by_seq["3"].objective is False
    assert by_seq["3"].bands and by_seq["3"].bands[0].hi == 12


# ── 不同招标文件的表头写法（实测：南网中台那份 13 条类别全空）──────

def _tbl(*rows):
    header = "| 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值 |"
    sep = "| --- | --- | --- | --- | --- |"
    # 注意：作用域那行也必须是**表格行** —— 抽取器逐行解析表格单元格，
    # 写成普通段落行不会被处理（真实文档里它就是表格首行）。
    return {"id": "s96", "level": 2, "title": "（2）详细评审标准",
            "raw_content": "\n".join(["| 适用标包：某某采购项目[开发实施服务] |  |  |  |  |",
                                      header, sep, *rows]),
            "source_file": "招标文件.pdf"}


def test_category_detected_from_in_table_subheading():
    """表内小标题「（一）、商务评审（100）分 / （二）、技术评审（100）分」要能定类别。

    旧实现只做精确匹配（"技术评分标准"这类），这套写法一个都命中不了 ——
    实测 13 条评分点类别全空，按"技术类"过滤时一条都取不到。
    """
    from infra.parser.review_points import extract_review_points

    sec = _tbl(
        "| （一）、商务评审（100）分 |  |  |  |  |",
        "| 1 | 财务状况 | 营业净利率 | 按排名得分 | 10 |",
        "| （二）、技术评审（100）分 |  |  |  |  |",
        "| 2 | 项目理解 | 需求理解完整性 | 完整得4分 | 4 |",
        "| 3 | 技术实现方案 | 工作方案的科学性 | 科学得6分 | 6 |",
    )
    table = extract_review_points([sec])
    by_cat = {}
    for p in table.points:
        by_cat.setdefault(p.category, []).append((p.factor, p.item))
    assert ("财务状况", "营业净利率") in by_cat.get("commerce", [])
    assert ("项目理解", "需求理解完整性") in by_cat.get("tech", [])
    assert ("技术实现方案", "工作方案的科学性") in by_cat.get("tech", [])


def test_scope_name_without_number_is_captured():
    """「适用标包：<名称>」没有编号，也要把名称读出来（编号留空）。

    实测这份文件就是这种写法，旧实现只认「下列评审标准适用的…标包N：」。
    """
    from infra.parser.review_points import extract_review_points

    sec = _tbl("| 1 | 项目理解 | 需求理解完整性 | 完整得4分 | 4 |")
    table = extract_review_points([sec])
    p = table.points[0]
    assert p.package_no == ""                     # 没有编号
    assert "某某采购项目" in p.package_label      # 名称读到了
