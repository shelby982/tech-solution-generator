"""核心数据关系的存储层测试。

用户（2026-10-13）把产品核心数据关系定为：

    招标评分点 ↔ 技术规范要求 ↔ 应标章节 ↔ 企业素材 ↔ 生成内容 ↔ 审查结果

这里钉住左半边接入后必须成立的性质：**稳定 id、落库幂等、覆盖可算、字段透传**。
"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from infra.parser.review_points import ReviewPoint
from services.point_store import (
    list_review_points,
    list_spec_requirements,
    requirement_ids_by_title,
    replace_review_points,
    replace_spec_requirements,
    stable_requirement_id,
    stable_review_point_id,
)


def _rp(package="2", category="tech", clause="2.2.4(2)", factor="技术方案",
        item="技术方案", criterion="1、方案可行性：…", points=30.0):
    return ReviewPoint(package_no=package, category=category, clause=clause,
                       factor=factor, item=item, criterion=criterion, points=points)


class _Req:
    def __init__(self, rid, title, text, topic="高可靠技术专题研究与验证", source_file="规范书.docx"):
        self.requirement_id = rid
        self.title = title
        self.requirement_text = text
        self.topic = topic
        self.source_file = source_file
        self.level = 5
        self.mandatory_level = ""
        self.verification_method = ""
        self.parent_id = ""


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "chain.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


# ── 稳定 id ───────────────────────────────────────────────────

def test_review_point_id_is_content_derived_and_stable():
    """同一条评分点即使分值/分项要素措辞变了，也必须是同一个 id。

    否则重出目录后章节上的 review_point_id 会对不上，
    "这章为哪个评分点得分"这条链就断了。
    """
    a = _rp(points=30.0, criterion="旧措辞")
    b = _rp(points=28.0, criterion="新措辞（招标文件修订）")
    assert stable_review_point_id(a) == stable_review_point_id(b)
    # 不同评分点必须可区分
    assert stable_review_point_id(a) != stable_review_point_id(_rp(item="项目管理"))
    # 标包/类别参与区分
    assert stable_review_point_id(a) != stable_review_point_id(_rp(package="3"))


def test_requirement_id_does_not_depend_on_order():
    """规范要求 id 由内容派生，不随顺序变化（顺序编号 R01 只作展示）。"""
    r1 = _Req("R01", "信创适配研究及验证", "开展信创适配研究与验证")
    r2 = _Req("R02", "信创适配研究及验证", "开展信创适配研究与验证")
    assert stable_requirement_id(r1) == stable_requirement_id(r2)   # 同内容 → 同 id
    r3 = _Req("R02", "容灾研究及验证", "开展容灾研究")
    assert stable_requirement_id(r1) != stable_requirement_id(r3)


# ── 落库 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_points_roundtrip_and_idempotent(db):
    pts = [_rp(item="技术方案"), _rp(item="项目管理", factor="项目管理", points=10.0)]
    rows = await replace_review_points(db, 7, "tid-1", pts)
    assert len(rows) == 2
    assert all(p.point_id.startswith("rp-") for p in pts)      # id 回写到对象上

    got = await list_review_points(db, run_thread_id="tid-1")
    assert {r["item"] for r in got} == {"技术方案", "项目管理"}
    assert got[0]["points"] == 30.0

    # 再跑一次：不应累积重复行（重出目录会反复调用）
    await replace_review_points(db, 7, "tid-1", pts)
    assert len(await list_review_points(db, run_thread_id="tid-1")) == 2


@pytest.mark.asyncio
async def test_spec_requirements_roundtrip(db):
    reqs = [_Req("R01", "信创适配研究及验证", "开展信创适配研究与验证"),
            _Req("R02", "容灾研究及验证", "开展容灾研究")]
    rows = await replace_spec_requirements(db, 7, "tid-1", reqs)
    assert all(r.startswith("sr-") for r in rows)
    assert all(r.stable_id.startswith("sr-") for r in reqs)     # 回写，供章节引用

    got = await list_spec_requirements(db, run_thread_id="tid-1")
    assert [r["title"] for r in got] == ["信创适配研究及验证", "容灾研究及验证"]
    assert got[0]["text"] == "开展信创适配研究与验证"


@pytest.mark.asyncio
async def test_requirement_ids_by_title_links_chapters_to_requirements(db):
    """`{标题: [requirement_id]}` 是"章节承载了哪几条规范要求"的连接表。"""
    reqs = [_Req("R01", "信创适配研究及验证", "开展信创适配研究与验证")]
    await replace_spec_requirements(db, 7, "tid-1", reqs)
    mapping = requirement_ids_by_title(reqs)
    assert mapping["信创适配研究及验证"] == [reqs[0].stable_id]
    # topic 也参与索引（规范书里专题名与小节名常不一致）
    assert reqs[0].stable_id in mapping["高可靠技术专题研究与验证"]


# ── 覆盖率 ────────────────────────────────────────────────────

def test_compute_coverage_counts_points_score_and_requirements():
    from infra.parser.outline_builder import OutlineNode, compute_coverage

    p1, p2 = _rp(item="技术方案", points=30.0), _rp(item="项目管理", factor="项目管理", points=10.0)
    p1.point_id, p2.point_id = "rp-a", "rp-b"
    r = _Req("R01", "信创适配研究及验证", "开展信创适配研究与验证")
    r.stable_id = "sr-x"

    nodes = [
        OutlineNode(level=1, title="技术方案", review_point_id="rp-a"),
        OutlineNode(level=2, title="方案可行性", review_point_id="rp-a"),
        OutlineNode(level=3, title="信创适配研究及验证", source="spec",
                    spec_requirement_ids=["sr-x"]),
    ]
    cov = compute_coverage(nodes, [p1, p2], [r])
    assert cov["review_points_total"] == 2
    assert cov["review_points_covered"] == 1           # 只覆盖了技术方案
    assert cov["review_points_decomposed"] == 1        # 它被拆出了下级
    assert cov["review_points_missing"] == ["项目管理／项目管理"]
    # 分值合计最能暴露漏评分点：30/40 说明漏了一项
    assert (cov["score_covered"], cov["score_total"]) == (30.0, 40.0)
    assert (cov["requirements_landed"], cov["requirements_total"]) == (1, 1)


def test_compute_coverage_lists_missing_requirements():
    from infra.parser.outline_builder import OutlineNode, compute_coverage

    r1, r2 = _Req("R01", "信创适配研究及验证", "x"), _Req("R02", "容灾研究及验证", "y")
    r1.stable_id, r2.stable_id = "sr-1", "sr-2"
    nodes = [OutlineNode(level=3, title="信创适配研究及验证", source="spec",
                         spec_requirement_ids=["sr-1"])]
    cov = compute_coverage(nodes, [], [r1, r2])
    assert cov["requirements_landed"] == 1
    assert cov["requirements_missing"] == ["容灾研究及验证"]      # 缺口要列出来，不能静默


# ── 字段透传（来源链必须能到 blocks）──────────────────────────

def test_chain_passes_through_normalize_and_build_sections():
    from domain.spec.models import Section
    from domain.spec.outline_draft import build_sections, normalize_nodes

    raw = [
        {"level": 1, "title": "技术方案", "review_point_id": "rp-a", "score_weight": 30.0},
        {"level": 2, "title": "方案可行性", "parent": 0, "review_point_id": "rp-a",
         "criterion_text": "评分口径原文", "spec_requirement_ids": ["sr-1"]},
    ]
    nodes, _ = normalize_nodes(raw)
    sections = build_sections(nodes)
    assert sections[0].chain["review_point_id"] == "rp-a"
    assert sections[1].chain["spec_requirement_ids"] == ["sr-1"]
    assert sections[1].chain["criterion_text"] == "评分口径原文"
    # 透传到 dict（SSE / 前端 / 落库都走它）
    assert sections[1].to_dict()["chain"]["review_point_id"] == "rp-a"
    assert Section.from_dict(sections[1].to_dict()).chain["spec_requirement_ids"] == ["sr-1"]


@pytest.mark.asyncio
async def test_blocks_columns_carry_chain(db):
    """占位落库要真的把来源链写进 blocks 行上。"""
    from services.block_store import sync_outline_placeholders

    sec = type("S", (), {})()
    sec.id, sec.level, sec.title = "s1", 1, "技术方案"
    sec.chain = {"review_point_id": "rp-a", "spec_requirement_ids": ["sr-1"],
                 "score_weight": 30.0, "criterion_text": "口径", "spec_topic": "信创适配研究及验证"}

    await sync_outline_placeholders(db, 7, [sec], run_thread_id="tid-1")
    cur = await db.execute("SELECT review_point_ids, spec_requirement_ids, score_weight, "
                           "criterion_text, spec_topic FROM blocks WHERE project_id = 7")
    row = await cur.fetchone()
    assert row["review_point_ids"] == '["rp-a"]'
    assert row["spec_requirement_ids"] == '["sr-1"]'
    assert row["score_weight"] == 30.0
    assert row["criterion_text"] == "口径"
    assert row["spec_topic"] == "信创适配研究及验证"
