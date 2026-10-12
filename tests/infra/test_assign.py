"""素材 → 章节 归属引擎（匹配阶段 P0）。

背景：旧实现方向是反的（逐章检索 top-10 + LLM 重排，调用数 O(章节数)），而且只留下
rerank 的评语、没有素材原文。这里锚住新方向的关键行为：
规则命中、语义兜底、宁缺毋滥（低于阈值不归属）、招标文件用途受限、原文/出处/性质齐备。
"""

from __future__ import annotations

import json

import pytest

from domain.spec import Section
from infra.retrieval.assign import (
    assign_evidence,
    allows_tender_evidence,
    chunk_title,
    classify_kind,
    normalize_title,
)


def _secs(*titles: str) -> list[Section]:
    return [Section(id=f"s{i}", level=1, title=t, raw_content="", special_marks=[])
            for i, t in enumerate(titles, start=1)]


def _matrix(**rows) -> dict:
    return {k: v for k, v in rows.items()}


def _chunk(cid: int, content: str, *, role: str = "source", filename: str = "素材.docx",
           material_id: int = 1, index: int = 0) -> dict:
    return {"id": cid, "material_id": material_id, "chunk_index": index,
            "filename": filename, "material_role": role, "content": content}


# ── 标题归一化 ────────────────────────────────────────────────

def test_normalize_title_strips_numbering_and_punct():
    assert normalize_title("第 3 章 技术架构设计") == "技术架构设计"
    assert normalize_title("3.2 性能指标（拖移项目）") == "性能指标"
    assert normalize_title("一、项目理解") == "项目理解"


def test_chunk_title_is_first_line():
    assert chunk_title("数据层设计\n正文内容…") == "数据层设计"
    assert chunk_title("\n\n  性能指标  \n") == "性能指标"
    assert chunk_title("") == ""


# ── 规则层 ────────────────────────────────────────────────────

def test_rule_match_by_section_title():
    r = assign_evidence(
        [_chunk(1, "技术架构设计\n四层架构，数据层使用达梦 DM8。")],
        _secs("项目理解", "技术架构设计"),
        _matrix(s2={"requirement": "总体架构与分层设计"}),
    )
    assert [e.chunk_id for e in r.evidence["s2"]] == [1]
    assert r.evidence["s1"] == []
    e = r.evidence["s2"][0]
    # 出处升级：段号 + 切片标题（锚点），有页码时前缀 P{n}
    assert e.by == "rule" and e.locator == "第 1 段 · 技术架构设计"
    assert e.content.startswith("技术架构设计")     # 原文必须保留
    assert e.filename == "素材.docx"


def test_rule_match_rejects_generic_single_token_overlap():
    """「技术评分表」不得靠一个共同词"技术"挂到「技术架构设计」。"""
    r = assign_evidence(
        [_chunk(1, "技术评分表\n技术方案完整性 20 分。", role="scoring", filename="评分表.docx")],
        _secs("技术架构设计", "性能指标响应"),
        _matrix(s1={"requirement": "总体技术架构与分层设计、部署拓扑"}),
        min_semantic_score=99.0,          # 关掉语义层，只考察规则层
    )
    assert r.by_rule == 0
    assert r.unassigned_chunk_ids == [1]


# ── 语义层 ────────────────────────────────────────────────────

def test_semantic_fallback_matches_topic_across_sections():
    r = assign_evidence(
        [_chunk(1, "某材料片段\n实测并发 2500 用户，平均响应时间 180ms，TPS 1200。")],
        _secs("项目理解与需求分析", "技术架构设计", "性能指标响应"),
        _matrix(s3={"requirement": "并发不低于 2000 用户，响应时间不超过 200ms",
                    "indicators": "并发 2000；时延 200ms"}),
    )
    assert [e.chunk_id for e in r.evidence["s3"]] == [1]
    assert r.evidence["s3"][0].by == "semantic"
    assert r.by_semantic == 1


def test_unrelated_chunk_is_not_attached():
    """宁缺毋滥：低于阈值不归属（旧实现会给每章硬塞 5 段弱关联评语）。"""
    r = assign_evidence(
        [_chunk(1, "无关段落\n关于食堂管理与员工班车安排的说明。")],
        _secs("项目理解与需求分析", "技术架构设计", "性能指标响应"),
        _matrix(s3={"requirement": "并发不低于 2000 用户"}),
    )
    assert all(v == [] for v in r.evidence.values())
    assert r.unassigned_chunk_ids == [1]
    assert r.stats()["assigned_chunks"] == 0


def test_min_semantic_score_is_honoured():
    chunks = [_chunk(1, "性能相关\n并发 2500 用户，响应 180ms。")]
    secs = _secs("项目理解与需求分析", "技术架构设计", "性能指标响应")
    matrix = _matrix(s3={"requirement": "并发不低于 2000 用户", "indicators": "并发 2000"})

    loose = assign_evidence(chunks, secs, matrix, min_semantic_score=0.1)
    strict = assign_evidence(chunks, secs, matrix, min_semantic_score=99.0)
    assert any(loose.evidence.values())
    assert all(v == [] for v in strict.evidence.values())


# ── 招标文件用途受限 ─────────────────────────────────────────

def test_tender_evidence_only_for_background_chapters():
    chunks = [
        _chunk(1, "技术评分表\n技术方案完整性 20 分。", role="scoring", filename="评分表.docx"),
        _chunk(2, "项目背景与现状\n现有系统存在数据孤岛。", role="spec", filename="规范书.pdf"),
    ]
    r = assign_evidence(
        chunks,
        _secs("项目理解与现状分析", "技术架构设计"),
        _matrix(s1={"requirement": "理解本项目建设背景与现状"},
                s2={"requirement": "总体技术架构、分层设计与部署拓扑"}),
    )
    # 招标"评分表"不得挂到技术架构章节
    assert all(e.source_kind == "ours" for e in r.evidence["s2"])
    # 招标"背景"可以挂到理解类章节，并被标为 background
    tender_evs = [e for evs in r.evidence.values() for e in evs if e.source_kind == "tender"]
    assert tender_evs and all(e.kind == "background" for e in tender_evs)


def test_allows_tender_evidence_keywords():
    assert allows_tender_evidence("项目理解与需求分析")
    assert allows_tender_evidence("建设背景")
    assert not allows_tender_evidence("技术架构设计")
    assert not allows_tender_evidence("性能指标响应")


# ── 性质分类 ─────────────────────────────────────────────────

def test_classify_kind():
    assert classify_kind("并发不低于 2000 用户，响应 ≤200ms") == "fact"
    assert classify_kind("采用四层架构，分层设计与部署拓扑") == "solution"
    assert classify_kind("项目背景与现状、需求理解") == "background"
    # 招标侧文本没有"我方实现"含义，默认按背景性处理更安全
    assert classify_kind("技术评分表", source_kind="tender") == "background"


# ── 契约与增量 ────────────────────────────────────────────────

def test_evidence_is_json_serializable():
    """score/confidence 来自 BM25（numpy.float64）—— 不转 float 会在写库与推帧时炸。"""
    r = assign_evidence(
        [_chunk(1, "某片段\n实测并发 2500 用户。")],
        _secs("项目理解与需求分析", "技术架构设计", "性能指标响应"),
        _matrix(s3={"requirement": "并发不低于 2000 用户", "indicators": "并发 2000"}),
    )
    payload = [e.to_dict() for evs in r.evidence.values() for e in evs]
    assert payload
    json.dumps(payload, ensure_ascii=False)     # 不抛 TypeError
    assert all(isinstance(e["score"], float) and isinstance(e["confidence"], float) for e in payload)


def test_incremental_only_chunk_ids():
    chunks = [
        _chunk(1, "技术架构设计\n四层架构。"),
        _chunk(2, "性能指标响应\n并发 2500 用户。"),
    ]
    secs = _secs("技术架构设计", "性能指标响应")
    matrix = _matrix(s2={"requirement": "性能指标"})
    r = assign_evidence(chunks, secs, matrix, only_chunk_ids={2})
    assert r.total_chunks == 1
    assert [e.chunk_id for e in r.evidence["s2"]] == [2]
    assert r.evidence["s1"] == []


def test_empty_inputs():
    assert assign_evidence([], _secs("A"), {}).stats()["assigned_chunks"] == 0
    r = assign_evidence([_chunk(1, "x")], [], {})
    assert r.unassigned_chunk_ids == [1]


def test_stats_zero_call_ratio():
    r = assign_evidence(
        [_chunk(1, "技术架构设计\n四层架构。")],
        _secs("技术架构设计"),
        _matrix(s1={"requirement": "架构"}),
    )
    st = r.stats()
    assert st["by_rule"] == 1 and st["zero_call_ratio"] == 1.0
    assert st["chapters_with_evidence"] == 1


# ── 条目级挂载与出处 ─────────────────────────────────────────

def test_item_mounting_attaches_evidence_to_matching_requirement():
    """素材要挂到**具体条目**上：撰写阶段按条目逐条作答，挂错等于拿别的素材回答问题。"""
    from domain.spec.response_items import expand_response_items

    row = {
        "requirement": "并发不低于 2000 用户\n响应时间不超过 200ms",
        "indicators": "并发 2000；时延 200ms",
        "score_items": "技术方案完整性 20 分",
    }
    items = expand_response_items("s3", row)
    types = [it["type"] for it in items]
    # indicators 是分号分隔的两项（并发 2000 / 时延 200ms）→ 拆成 2 条
    assert types.count("requirement") == 2 and types.count("indicator") == 2
    assert items[0]["item_id"] == "s3#R1"

    # 至少 3 章：BM25 在 2 章语料下 IDF 恒为 0，语义层会失效（见 assign.py 顶部注释）
    r = assign_evidence(
        [_chunk(1, "性能\n实测并发 2500 用户，响应 180ms，TPS 1200。")],
        _secs("项目理解与需求分析", "技术架构设计", "性能指标响应"),
        {"s3": {"block_id": "s3", "title": "性能指标响应",
                "requirement": "并发不低于 2000 用户，响应时间不超过 200ms",
                "indicators": "并发 2000"}},
        items_by_section={"s3": items},
    )
    ev = r.evidence["s3"][0]
    assert ev.item_ids, "应挂到具体条目"
    assert all(iid.startswith("s3#") for iid in ev.item_ids)
    # 评分项条目不该被这条性能素材命中
    assert "s3#S1" not in ev.item_ids


def test_item_mounting_stays_empty_when_no_item_matches():
    from domain.spec.response_items import expand_response_items

    items = expand_response_items("s1", {"requirement": "支持国产数据库与信创适配"})
    r = assign_evidence(
        [_chunk(1, "技术架构设计\n拟委派的项目负责人具有 PMP 证书。")],
        _secs("技术架构设计"),
        {"s1": {"block_id": "s1", "title": "技术架构设计",
                "requirement": "支持国产数据库与信创适配"}},
        items_by_section={"s1": items},
    )
    evs = r.evidence["s1"]
    assert evs and evs[0].item_ids == [], "挂不上就留空（只作章节级依据），不能乱挂"


def test_locator_includes_page_and_chunk_title():
    from infra.retrieval.assign import build_locator

    loc = build_locator({"chunk_index": 7, "page": 12, "content": "服务团队\n拟委派 PMP。"})
    assert loc == "P12 · 第 8 段 · 服务团队"
    # Word 没有页码：至少给段号 + 切片标题
    assert build_locator({"chunk_index": 2, "content": "性能指标\n并发 2000"}) == "第 3 段 · 性能指标"


def test_evidence_carries_page_when_chunk_has_one():
    r = assign_evidence(
        [dict(_chunk(1, "技术架构设计\n四层架构。"), page=5)],
        _secs("技术架构设计"),
        {"s1": {"block_id": "s1", "title": "技术架构设计", "requirement": "架构"}},
    )
    ev = r.evidence["s1"][0]
    assert ev.page == 5 and ev.locator.startswith("P5")


def test_long_requirement_mounts_via_its_clauses():
    """长条目挂着素材时，素材只提到其中一个子句也算挂上（整条比对会漏）。

    实测背景（真实项目 643 条要求 / 37 段素材）：整条打分只挂上 8 条、按实质子句 21 条。
    """
    from infra.retrieval.assign import item_match_score, mount_items

    item = {"item_id": "s1#R1", "text": "数据安全管理：支持数据库审计能力集成、数据脱敏能力集成、"
                                       "数据水印能力集成、接口安全监测能力集成；"}
    content = "数据脱敏实施\n测试环境对用户档案做脱敏处理，生产数据不出域。"
    # 整条比对：分母是四个"能力集成"，只命中一个 → 低于阈值
    assert item_match_score(content, item["text"]) < 0.34
    # 子句比对：命中"数据脱敏能力集成"这一句 → 挂上
    assert mount_items(content, [item]) == ["s1#R1"]


def test_meaningless_clause_does_not_create_a_hit():
    """全是通用词的子句（"具体包括："）不能算命中 —— 它两边都容易"匹配"上。"""
    from infra.retrieval.assign import _item_clauses, mount_items

    assert _item_clauses("具体包括：相关内容以及部分说明") == []
    assert mount_items("具体包括：相关内容以及部分说明。", [
        {"item_id": "s1#R1", "text": "具体包括：相关内容以及部分说明"}
    ]) == []


def test_clause_split_only_for_mounting_not_for_the_whole_item_path():
    """默认（整条）路径不变：阈值是按它标定的，不能被子句路径悄悄改掉。"""
    from infra.retrieval.assign import item_match_score

    assert item_match_score("数据脱敏实施", "数据脱敏能力集成") > 0.34
    assert item_match_score("完全无关的招标资质说明", "数据脱敏能力集成") == 0.0
