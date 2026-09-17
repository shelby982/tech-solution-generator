"""提炼要求定位（infra/retrieval/locate.py）。

用户说的「按照主招标文件中的标包2的技术评分要求拆分章节」，要求的是**用文件的
哪一部分**。整份文件灌进 prompt 时，封面、招标公告、合同范本会把真正的目标章节
挤出 8000 字预算，所以先按关键词定位出相关章节再派生。
"""

from infra.retrieval.locate import locate_sections


def _sec(title: str, content: str = "", level: int = 1) -> dict:
    return {"title": title, "content": content, "level": level}


def _noise(n: int) -> list[dict]:
    return [_sec(f"第{i}页", "招标人：广东电网有限责任公司", level=1) for i in range(n)]


def test_empty_sections_returns_empty():
    assert locate_sections([], "任意要求") == []


def test_no_instruction_keeps_everything_in_document_order():
    """没写提炼要求时行为不变：全量、原文序。"""
    sections = _noise(5)
    assert locate_sections(sections, "") == [0, 1, 2, 3, 4]
    assert locate_sections(sections, "   ") == [0, 1, 2, 3, 4]


def test_no_match_falls_back_to_everything():
    """一个都命中不了时退回全量，不能让该有的章节凭空消失。"""
    sections = _noise(5)
    assert locate_sections(sections, "量子纠缠拓扑绝缘体") == [0, 1, 2, 3, 4]


def test_relevant_section_ranked_before_noise():
    sections = _noise(4) + [
        _sec("标包2 技术评分要求", "评分因素：技术方案完整性、团队经验", level=1),
    ] + _noise(4)

    located = locate_sections(sections, "按照标包2的技术评分要求拆分章节")

    assert 4 in located
    # 相关章节必须排在最前面 —— 下游 build_spec_digest 按顺序吃预算
    assert located[0] == 4
    assert len(located) < len(sections)


def test_relevant_section_late_in_document_is_not_lost():
    """核心场景：目标章节在 325 章的文档后段，原文序下会被 8000 字预算整个切掉。"""
    sections = _noise(200) + [
        _sec("下列评审标准适用的标的/标包", "标包2：高可靠技术专题研究与验证标包", level=3),
    ]

    located = locate_sections(sections, "按照标包2的技术评分要求拆分章节")

    # 前一位是它的上级（噪声节都是 level 1，正好充当归属），目标紧随其后
    assert 200 in located
    assert located.index(200) <= 1


def test_ancestors_are_included_before_their_child():
    """叶子命中时要带上归属，否则派生出的目录没有上级。"""
    sections = [
        _sec("第一章 总则", "", level=1),
        _sec("1.1 招标范围", "", level=2),
        _sec("1.1.1 标包2 技术评分要求", "评分因素：技术方案完整性", level=3),
        _sec("第二章 合同范本", "", level=1),
    ]

    located = locate_sections(sections, "标包2 技术评分要求")

    assert located.index(0) < located.index(2)   # 一级祖先在最前
    assert located.index(1) < located.index(2)   # 直接上级紧随其后
    assert 3 not in located                      # 无关章节不带进来


def test_max_sections_caps_the_result():
    sections = [_sec(f"标包2 评分要求第{i}条", "评分因素") for i in range(50)]

    located = locate_sections(sections, "标包2 评分要求", max_sections=10)

    # 命中被截到 10 条；没有祖先（都是 level 1），所以结果就是 10 个下标
    assert len(located) == 10


def test_sections_without_text_fall_back_to_everything():
    sections = [_sec("", "", level=1) for _ in range(3)]
    assert locate_sections(sections, "标包2 评分要求") == [0, 1, 2]
