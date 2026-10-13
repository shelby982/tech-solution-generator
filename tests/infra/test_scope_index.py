"""范围索引与评分地图：多标的/多标包/商务技术混排时的定向筛选。

实测样本（南网中台，项目 19）：一份文件里
  标的1 → 标包1（应用中台客户档案管理认证系统）
  标的2 → 标包1（95598服务受理平台） / 标包2（智能客服应用）
且「（2）详细评审标准」一节里有 3 张评分表，每张各有「适用标包：」声明，
每张表内部再分「（一）、商务评审（100）分 /（二）、技术评审（100）分」。
旧实现把多张表混成一套 —— 本次改造的核心就是把它们切开并定向选中。
"""
from __future__ import annotations

from infra.parser.scope_index import (
    normalize_scope_name,
    parse_package_index,
    parse_target_tokens,
    resolve_scope,
    split_scoring_tables,
)

INDEX_SECTION = {
    "id": "s8", "level": 2, "title": "2.6标的清单及分包情况", "source_file": "招标文件.pdf",
    # 每行一条（真实文档就是逐行），标的列合并单元格只在第一行出现
    "raw_content": "\n".join([
        "| 序号 | 标的 | 概算金额<br>（万元） | 标包<br>号 | 标包名称 | 最高限价<br>（万元） |",
        "| --- | --- | --- | --- | --- | --- |",
        "| 1 | 应用中台客户档案管理认证系统开发实施服务采购项目 | 602.38 | 1 | 应用中台客户档案管理认证系统开发实施服务 | 602.38 |",
        "| 2 | 95598平台优化升级采购项目 | 386.28 | 1 | 95598服务受理平台优化升级及营销配套改造服务采购项目 | 183.25 |",
        "|  |  |  | 2 | 智能客服应用开发实施服务采购项目 | 203.03 |",
    ]),
}


def _scoring_section():
    """一节里塞三张表，每张各有适用标包声明 + 商务/技术两段。"""
    def table(pkg_name, tech_item):
        return [
            f"| 适用标包：95598平台优化升级采购项目[{pkg_name}] |  |  |  |  |",
            "| 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值 |",
            "| --- | --- | --- | --- | --- |",
            "| （一）、商务评审（100）分 |  |  |  |  |",
            "| 1 | 财务状况 | 营业净利率 | 按排名得分 | 10 |",
            "| （二）、技术评审（100）分 |  |  |  |  |",
            f"| 2 | 技术方案 | {tech_item} | 完整得 4 分 | 4 |",
        ]

    content = "\n".join(
        table("应用中台客户档案管理认证系统开发实施服务", "需求理解完整性")
        + table("95598服务受理平台优化升级及营销配套改造服务采购项目", "受理平台方案")
        + table("智能客服应用开发实施服务采购项目", "智能客服方案")
    )
    return {"id": "s96", "level": 2, "title": "（2）详细评审标准",
            "source_file": "招标文件.pdf", "raw_content": content}


# ── 索引 ──────────────────────────────────────────────────────

def test_package_index_reads_targets_and_packages_with_merged_cells():
    """标的列是合并单元格（只写一次），要向下继承。"""
    idx = parse_package_index([INDEX_SECTION])
    assert [t["target"] for t in idx] == ["标的1", "标的2"]
    assert [p["no"] for p in idx[0]["packages"]] == ["1"]
    assert [p["no"] for p in idx[1]["packages"]] == ["1", "2"]


def test_scope_name_normalization_ignores_whitespace_and_suffix():
    """同一标包在不同地方写法不同（换行、全半角、「采购项目」后缀）。"""
    a = normalize_scope_name("智能客服应用开发实施服务采购项目")
    b = normalize_scope_name("智能客服应用开发<br>实施服务采购项目")
    c = normalize_scope_name("智能客服应用开发实施服务")
    assert a == b == c


def test_resolve_scope_matches_package_by_name_not_by_guess():
    idx = parse_package_index([INDEX_SECTION])
    hit = resolve_scope("95598平台优化升级采购项目[智能客服应用开发实施服务采购项目]", idx)
    assert hit["resolved"] and hit["target"] == "标的2" and hit["package_no"] == "2"
    miss = resolve_scope("完全不存在的项目名称", idx)
    assert not miss["resolved"]           # 认不出就不猜


def test_target_tokens_from_instruction():
    assert parse_target_tokens("请按标的 1 的技术评分点拆目录") == ["标的1"]
    assert parse_target_tokens("标包2 和 标的3") == ["标包2", "标的3"]
    assert parse_target_tokens("没有任何范围词") == []


# ── 表切分 + 定向筛选 ─────────────────────────────────────────

def test_scoring_tables_are_split_by_scope_declaration():
    """一节三张表必须切成三张，而不是混成一套。"""
    tables = split_scoring_tables([_scoring_section()])
    assert len(tables) == 3
    assert all(t["scope_label"] for t in tables)


def test_scoring_map_selects_only_the_named_target():
    from services.scoring_map import build_scoring_map

    body = [_scoring_section(), INDEX_SECTION]
    m = build_scoring_map(body, "请按标的 1 的技术评分点拆目录")
    assert m["stats"]["tables"] == 3
    assert m["stats"]["resolved_tables"] == 3
    sel = next(e for e in m["entries"] if e["table_no"] == m["selected_table_no"])
    assert sel["target"] == "标的1" and sel["package_no"] == "1"
    # 选中表里只有它自己的技术评分点（不是三张表混起来的）
    tech = [p for p in m["_selected_points"] if p.category == "tech"]
    assert [p.item for p in tech] == ["需求理解完整性"]
    # 其余表要列出"未选中"
    assert any(i["key"] == "scope_excluded" for i in m["issues"])


def test_scoring_map_asks_when_scope_unresolved():
    """没有范围词、又有多张表 → 必须提问，不能自己挑一张。"""
    from services.scoring_map import build_scoring_map

    m = build_scoring_map([_scoring_section(), INDEX_SECTION], "帮我拆技术应答目录")
    assert m["selected_table_no"] is None
    issue = next(i for i in m["issues"] if i["key"] == "scope_unresolved")
    assert len(issue["options"]) == 3          # 三张表都摆出来让用户选


# ── ⑶ 跨文件：技术评分表可能整张在技术规范书里（无「适用标包」声明）──

def test_scope_inferred_from_file_name_when_table_has_no_declaration():
    """文件名写着「标的1」时，表就该归属标的1，而不是"无范围"。

    跨文件项目里技术评分表常在技术规范书里，且整张没有「适用标包」声明 ——
    不兜底的话它会与主文件里的表各自为政，分值合计被拆成两半、校验失效。
    """
    sec = _scoring_section()
    sec["source_file"] = "2_标的1：2026年南方电网应用中台档案管理认证系统开发.docx"
    # 去掉所有「适用标包」声明
    sec["raw_content"] = "\n".join(
        l for l in sec["raw_content"].splitlines() if "适用标包" not in l)
    tables = split_scoring_tables([sec])
    assert tables and all(t["scope_from_file"] for t in tables)
    assert all(t["scope_label"] == "标的1" for t in tables)


def test_scoring_map_merges_same_package_across_files():
    """同一标包分散在两个文件里的评分表要合成一条（含来源清单）。"""
    from services.scoring_map import build_scoring_map

    def one_table(item, pkg):
        return {"id": "s1", "level": 2, "title": "评分表", "source_file": "招标文件.pdf",
                "raw_content": "\n".join([
                    f"| 适用标包：{pkg} |  |  |  |  |",
                    "| 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值 |",
                    "| --- | --- | --- | --- | --- |",
                    "| （二）、技术评审（100）分 |  |  |  |  |",
                    f"| 1 | 技术方案 | {item} | 完整得 4 分 | 4 |",
                ])}

    pkg = "应用中台客户档案管理认证系统开发实施服务"
    a = one_table("需求理解完整性", pkg)
    a["source_file"] = "主招标文件.pdf"
    b = one_table("方案科学性", pkg)
    b["source_file"] = "技术规范书.docx"
    b["id"] = "s2"

    m = build_scoring_map([a, b, INDEX_SECTION], "按标的 1 拆分")
    assert len(m["entries"]) == 1, "同一标包的表没有合并"
    e = m["entries"][0]
    assert e["multi_file"] is True
    assert e["source_files"] == ["主招标文件.pdf", "技术规范书.docx"]
    assert e["total_count"] == 2                    # 两张表的评分点合起来了
    tech = [p for p in m["_selected_points"] if p.category == "tech"]
    assert {p.item for p in tech} == {"需求理解完整性", "方案科学性"}
