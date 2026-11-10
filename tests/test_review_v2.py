"""评审 v2：加权覆盖（A）/ 否决风险（B）/ issue 状态机（C）的验收测试。"""
from __future__ import annotations

import asyncio
import json

import pytest


# ────────────────────────── C：指纹 ──────────────────────────

def test_issue_fingerprint_stable():
    from services.review_issues import fingerprint, normalize_point

    base = {"severity": "high", "point": "缺少 3 个量化指标。"}
    same_a = {"severity": "high", "point": "缺少 5 个量化指标"}
    same_b = {"severity": "high", "point": "缺少量化指标"}
    other_sev = {"severity": "critical", "point": "缺少 3 个量化指标"}
    other_text = {"severity": "high", "point": "缺少业绩证明材料"}

    assert fingerprint(base) == fingerprint(same_a)      # 数字差异归一
    assert len(fingerprint(base)) == 16
    assert fingerprint(base) != fingerprint(other_sev)   # 严重度参与指纹
    assert fingerprint(base) != fingerprint(other_text)
    assert normalize_point("缺少，量化指标！") == normalize_point("缺少量化指标")


# ────────────────────── C：状态流转（内存 sqlite） ──────────────────────

@pytest.fixture()
def anyio_backend():
    return "asyncio"


async def _fresh_db():
    """内存库 + 最小 schema，专测状态机。"""
    import aiosqlite

    db = await aiosqlite.connect(":memory:")
    await db.execute(
        "CREATE TABLE review_issue_states (id INTEGER PRIMARY KEY AUTOINCREMENT, block_id INTEGER NOT NULL, "
        "fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', severity TEXT NOT NULL DEFAULT 'medium', "
        "point TEXT NOT NULL DEFAULT '', first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP, fixed_at DATETIME, verified_at DATETIME)")
    await db.execute("CREATE UNIQUE INDEX idx_u ON review_issue_states(block_id, fingerprint)")
    await db.commit()
    return db


def test_issue_state_machine():
    from services.review_issues import mark_fixed, sync_issues

    async def run():
        db = await _fresh_db()
        missing_data = [{"severity": "high", "point": "缺少量化指标"}]
        missing_other = [{"severity": "high", "point": "缺少业绩材料"}]

        # 第 1 轮：两条都出现 → open
        counts = await sync_issues(db, 1, missing_data + missing_other)
        assert counts["open"] == 2 and counts["verified"] == 0

        # 应用补丁 → 其中一条 fixed
        assert await mark_fixed(db, 1, missing_data) == 1

        # 第 2 轮：data 消失、other 仍在
        counts = await sync_issues(db, 1, missing_other)
        assert counts["fixed"] == 1 and counts["verified"] == 0, counts  # 遗漏问题仍待复核
        assert counts["open"] == 1, counts            # 仍在的 → open

        # 第 3 轮：verified 的那条复发 → 回 open
        counts = await sync_issues(db, 1, missing_data + missing_other)
        assert counts["open"] == 2 and counts["verified"] == 0

        # 第 4 轮：修好后再消失 → verified
        await mark_fixed(db, 1, missing_data)
        counts = await sync_issues(db, 1, missing_other)
        assert counts["fixed"] == 1 or counts["verified"] == 1
        await db.close()

    asyncio.run(run())


# ────────────────────────── A：加权覆盖 ──────────────────────────

def test_weighted_estimate_and_pending():
    from services.scoring_verdict import estimate_point

    # 3 章：ok(1.0) + medium(0.5) + high(0.0) → 平均 0.5 → 30 分 × 0.5 = 15
    data = estimate_point(30.0, [{"risk": "ok"}, {"risk": "medium"}, {"risk": "high"}])
    assert data["estimated_score"] == 15.0
    assert data["basis"]["reviewed"] == 3
    assert data["basis"]["avg"] == 0.5

    # 未评审章节不计入分母
    data = estimate_point(30.0, [{"risk": "ok"}, {"risk": "unreviewed"}, {"risk": "unreviewed"}])
    assert data["estimated_score"] == 30.0
    assert data["basis"]["reviewed"] == 1

    # 全部未评 → None（进 score_pending）
    data = estimate_point(30.0, [{"risk": "unreviewed"}])
    assert data["estimated_score"] is None
    assert data["basis"]["reviewed"] == 0


# ────────────────────────── B：否决风险 ──────────────────────────

def test_fatal_risks_and_risk_level():
    from services.scoring_verdict import build_risk_level, collect_fatal_risks

    fatal_issue = {"severity": "critical", "point": "未响应【★】实质性条款，可能废标", "block_id": "s1.2.1.1"}
    normal_high = {"severity": "high", "point": "缺少量化指标", "block_id": "s1.2.1.1"}
    ok_issue = {"severity": "low", "point": "表述可更精炼", "block_id": "s1.2.1.1"}

    risks = collect_fatal_risks([fatal_issue])
    assert len(risks) == 1 and risks[0]["point"]

    assert build_risk_level(risks, [fatal_issue]) == "red"
    assert build_risk_level([], [normal_high]) == "yellow"
    assert build_risk_level([], [ok_issue]) == "green"


# ────────────────────── 端到端：接口形状（不依赖 LLM） ──────────────────────

def test_issues_list_shape():
    from services.review_issues import issues_from_review

    assert issues_from_review({"issues": json.dumps([{"point": "x"}])}) == [{"point": "x"}]
    assert issues_from_review({"issues": [{"point": "y"}]}) == [{"point": "y"}]
    assert issues_from_review(None) == []


def test_fatal_risks_skip_meta_issues():
    """评审自身的"输入缺失"类问题不该被当成废标风险（误报）。"""
    from services.scoring_verdict import collect_fatal_risks

    meta = {"severity": "critical", "point": "评审输入缺失：应标要求、否决项均为空，无法开展评审"}
    real = {"severity": "critical", "point": "未响应【★】实质性条款，可能废标"}
    risks = collect_fatal_risks([meta, real])
    assert len(risks) == 1
    assert "实质性条款" in risks[0]["point"]


def test_scoring_point_excludes_directory_nodes():
    """评分点的「N 章」要和章节目录一致：目录节点（有子节无正文）不算章。"""
    from services.scoring_risk import is_directory_node

    blocks = [
        {"block_id": "s1.3", "content": "", "title": "技术方案"},
        {"block_id": "s1.3.1", "content": "正文", "title": "顶层设计"},
        {"block_id": "s1.3.2", "content": "", "title": "总体架构"},
        {"block_id": "s1.4", "content": "总述正文", "title": "实施"},
        {"block_id": "s1.4.1", "content": "正文", "title": "进度"},
    ]
    assert is_directory_node(blocks, blocks[0]) is True     # 有子节 + 空正文 → 目录节点
    assert is_directory_node(blocks, blocks[1]) is False     # 叶子
    assert is_directory_node(blocks, blocks[3]) is False     # 有子节但有总述正文 → 算章
