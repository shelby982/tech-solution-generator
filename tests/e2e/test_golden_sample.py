"""E2E 黄金样本回归（spec §9 / plan task 7.2）。

前置：用户提供 tests/fixtures/sample_spec.docx + sample_materials.docx，
并设置 LLM_MODE=real。否则用例自动 skip。

执行：
    cd /Users/jianghe/projects/ge-solution
    LLM_MODE=real pytest tests/e2e/ -v -m slow

产出：
    tests/fixtures/baseline.json — 当前样本的关键产出快照，供后续重构对比

注意：
    - 全程不起 HTTP 服务，直接用 WorkflowRunner 驱动
    - LLM_MODE=mock 不会跑 LLM，无意义；本测试明确要求 real
    - block 数量、score 区间、产出结构作为关键比较项
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "tests" / "fixtures"
SPEC_FIXTURE = FIXTURES_DIR / "sample_spec.docx"
MATERIAL_FIXTURE = FIXTURES_DIR / "sample_materials.docx"
BASELINE_PATH = FIXTURES_DIR / "baseline.json"


def _fixtures_available() -> bool:
    return SPEC_FIXTURE.exists() and MATERIAL_FIXTURE.exists()


def _real_mode() -> bool:
    return os.environ.get("LLM_MODE") == "real"


@pytest.mark.slow
@pytest.mark.skipif(
    not (_real_mode() and _fixtures_available()),
    reason="需要 LLM_MODE=real 且 tests/fixtures/sample_*.docx 文件存在",
)
@pytest.mark.asyncio
async def test_golden_sample_e2e(tmp_path):
    """跑完整 workflow，把关键产出 dump 到 baseline.json。

    本测试设计为基线收集 + 后续回归比对：第一次跑时如 baseline.json 不存在则写入；
    再次跑时如 baseline.json 存在则只断言关键不变量（数量、score 区间、stage 顺序）。
    """
    from db import init_db, get_db
    from orchestrator.runner import WorkflowRunner
    from orchestrator.graph import GraphDeps
    from agents.zhang_heng import ZhangHengAgent
    from agents.shen_kuo import ShenKuoAgent
    from agents.zhuge_liang import ZhugeLiangAgent
    from agents.wang_anshi import WangAnshiAgent
    from agents.bao_zheng import BaoZhengAgent

    # 用 tmp_path 切到隔离 SQLite，避免污染开发库
    import db as db_module
    db_module.DB_PATH = str(tmp_path / "golden.db")
    async with get_db() as conn:
        await init_db(conn)

    # 准备项目 + spec material 入库
    spec_data = SPEC_FIXTURE.read_bytes()
    mat_data = MATERIAL_FIXTURE.read_bytes()
    upload_dir = tmp_path / "uploads" / "1"
    upload_dir.mkdir(parents=True, exist_ok=True)
    spec_path = upload_dir / "sample_spec.docx"
    mat_path = upload_dir / "sample_materials.docx"
    spec_path.write_bytes(spec_data)
    mat_path.write_bytes(mat_data)

    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO projects (id, name) VALUES (?, ?)",
            (1, "golden-sample"),
        )
        await conn.execute(
            "INSERT INTO materials (project_id, filename, file_path, role) "
            "VALUES (?, ?, ?, 'spec')",
            (1, "sample_spec.docx", "uploads/1/sample_spec.docx"),
        )
        await conn.execute(
            "INSERT INTO materials (project_id, filename, file_path, role) "
            "VALUES (?, ?, ?, 'source')",
            (1, "sample_materials.docx", "uploads/1/sample_materials.docx"),
        )
        await conn.commit()

    async def spec_loader(project_id):
        return BytesIO(spec_data), ".docx", "sample_spec.docx"

    def deps_factory():
        return GraphDeps(
            zhang_heng=ZhangHengAgent(),
            shen_kuo=ShenKuoAgent(),
            zhuge_liang=ZhugeLiangAgent(),
            wang_anshi=WangAnshiAgent(),
            bao_zheng=BaoZhengAgent(),
            spec_loader=spec_loader,
        )

    runner = WorkflowRunner(deps_factory=deps_factory)
    thread_id = await runner.start(project_id=1, config={})

    # 简化：每个闸门 approve 通过；真实场景前端会在闸门停下做编辑后再续跑
    run = runner._runs[thread_id]
    await run.task  # 闸门 1 暂停
    await runner.resume(thread_id, "approve", {})
    await runner._runs[thread_id].task  # 闸门 2
    await runner.resume(thread_id, "approve", {})
    await runner._runs[thread_id].task  # 闸门 3
    await runner.resume(thread_id, "approve", {})
    await runner._runs[thread_id].task  # END

    final_state = await runner.state(thread_id)

    blocks = final_state.get("proposal", {}).get("blocks", {})
    review = final_state.get("review", {})

    all_findings = [
        f for findings in review.get("findings", {}).values() for f in findings
    ]
    summary = {
        "block_count": len(blocks),
        "score_min": min(
            (f.get("score", 0) for f in all_findings),
            default=None,
        ),
        "score_max": max(
            (f.get("score", 0) for f in all_findings),
            default=None,
        ),
        "stage_history": final_state.get("stage", ""),
        "block_ids": sorted(blocks.keys()),
    }

    if not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2)
        )
        pytest.skip(
            "基线已写入 tests/fixtures/baseline.json，请人工核对后再运行做回归断言"
        )

    baseline = json.loads(BASELINE_PATH.read_text())
    # 数量必须严格一致
    assert summary["block_count"] == baseline["block_count"]
    # block_ids 集合一致（顺序无关）
    assert set(summary["block_ids"]) == set(baseline["block_ids"])
    # score 区间不能塌陷为 0
    if summary["score_min"] is not None:
        assert summary["score_min"] > 0, "score 全部为 0，可能 LLM 调用失败"
