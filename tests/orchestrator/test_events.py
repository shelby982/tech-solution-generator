"""orchestrator/events.py 单测。

覆盖：
- 14 个事件构造函数序列化为 SSE 格式后包含正确 ``event: xxx`` 与 data JSON
- ``EventEmitter`` 的 emit / stream / aclose 行为
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from orchestrator import events


# ─────────────────────────────────────────────
# 事件帧通用断言
# ─────────────────────────────────────────────

_FRAME_RE = re.compile(r"^event: (?P<event>[^\n]+)\ndata: (?P<data>.+)\n\n$", re.S)


def _parse_frame(frame: str) -> tuple[str, dict]:
    m = _FRAME_RE.match(frame)
    assert m, f"非法 SSE 帧：{frame!r}"
    return m.group("event"), json.loads(m.group("data"))


# ─────────────────────────────────────────────
# 14 个事件构造函数
# ─────────────────────────────────────────────

def test_stage_change():
    name, data = _parse_frame(events.stage_change("parsing", "tid-1"))
    assert name == "stage_change"
    assert data == {"stage": "parsing", "thread_id": "tid-1"}


def test_parse_progress():
    name, data = _parse_frame(events.parse_progress("扫描第 3/70 页", 3, 70))
    assert name == "parse_progress"
    assert data == {"step": "扫描第 3/70 页", "current": 3, "total": 70}


def test_outline_extract():
    matrix = {"requirements": ["R1"], "constraints": []}
    name, data = _parse_frame(
        events.outline_extract("s1", "技术方案", matrix)
    )
    assert name == "outline_extract"
    assert data == {"block_id": "s1", "title": "技术方案", "matrix": matrix}


def test_match_progress():
    matches = [{"chunk_id": "c1", "score": 0.9}]
    name, data = _parse_frame(events.match_progress("s1", matches))
    assert name == "match_progress"
    assert data == {"block_id": "s1", "matches": matches}


def test_block_start():
    name, data = _parse_frame(events.block_start("s1", "section", "技术方案"))
    assert name == "block_start"
    assert data == {"block_id": "s1", "kind": "section", "title": "技术方案"}


def test_block_token():
    name, data = _parse_frame(events.block_token("s1", "你好"))
    assert name == "block_token"
    assert data == {"block_id": "s1", "token": "你好"}


def test_block_done():
    name, data = _parse_frame(
        events.block_done("s1", "正文", [{"chunk_id": "c1"}], {"id": "s1"})
    )
    assert name == "block_done"
    assert data == {
        "block_id": "s1",
        "content": "正文",
        "sources": [{"chunk_id": "c1"}],
        "outline": {"id": "s1"},
    }


def test_review_finding():
    issues = [{"severity": "warn", "message": "缺少时间节点"}]
    name, data = _parse_frame(
        events.review_finding("s1", "wang_anshi", 0.82, issues)
    )
    assert name == "review_finding"
    assert data == {
        "block_id": "s1",
        "agent": "wang_anshi",
        "score": 0.82,
        "issues": issues,
    }


def test_report_ready():
    report = {"global_score": 0.9, "blocks": {}}
    name, data = _parse_frame(events.report_ready(report))
    assert name == "report_ready"
    assert data == {"report": report}


def test_gate_open():
    snapshot = {"stage": "outline_review", "spec": {"toc": []}}
    name, data = _parse_frame(events.gate_open("review_outline", snapshot))
    assert name == "gate_open"
    assert data == {"gate": "review_outline", "snapshot": snapshot}


def test_error_minimal():
    """error 仅传 message：agent / block_id 不出现在载荷里。"""
    name, data = _parse_frame(events.error("LLM timeout"))
    assert name == "error"
    assert data == {"message": "LLM timeout", "retryable": False}


def test_error_with_optional_fields():
    name, data = _parse_frame(
        events.error(
            "match failed",
            agent="shen_kuo",
            block_id="s1",
            retryable=True,
        )
    )
    assert name == "error"
    assert data == {
        "message": "match failed",
        "retryable": True,
        "agent": "shen_kuo",
        "block_id": "s1",
    }


def test_checkpoint():
    name, data = _parse_frame(events.checkpoint("tid-1", "matching"))
    assert name == "checkpoint"
    assert data == {"thread_id": "tid-1", "stage": "matching"}


def test_done():
    name, data = _parse_frame(events.done("/api/download/tid-1"))
    assert name == "done"
    assert data == {"download_url": "/api/download/tid-1"}


def test_aborted():
    name, data = _parse_frame(events.aborted("user_cancel"))
    assert name == "aborted"
    assert data == {"reason": "user_cancel"}


def test_chinese_payload_not_ascii_escaped():
    """中文不被转义，便于前端调试。"""
    frame = events.parse_progress("扫描第 3/70 页", 3, 70)
    assert "扫描第 3/70 页" in frame
    assert "\\u" not in frame


# ─────────────────────────────────────────────
# EventEmitter
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emitter_emit_then_stream_yields_frames():
    emitter = events.EventEmitter()

    await emitter.emit(events.stage_change("parsing", "tid-1"))
    await emitter.emit(events.parse_progress("step", 1, 2))
    await emitter.aclose()

    frames = [f async for f in emitter.stream()]
    assert len(frames) == 2
    assert _parse_frame(frames[0])[0] == "stage_change"
    assert _parse_frame(frames[1])[0] == "parse_progress"


@pytest.mark.asyncio
async def test_emitter_stream_consumes_concurrently_with_emit():
    """节点 emit 与 routes stream 真正并发：消费者先启动，等生产者推帧。"""
    emitter = events.EventEmitter()
    received: list[str] = []

    async def consumer() -> None:
        async for frame in emitter.stream():
            received.append(frame)

    consumer_task = asyncio.create_task(consumer())

    # 让消费者先进入 await
    await asyncio.sleep(0)
    await emitter.emit(events.checkpoint("tid-1", "parsing"))
    await emitter.emit(events.checkpoint("tid-1", "matching"))
    await emitter.aclose()

    await asyncio.wait_for(consumer_task, timeout=1.0)
    assert len(received) == 2
    assert _parse_frame(received[0])[1]["stage"] == "parsing"
    assert _parse_frame(received[1])[1]["stage"] == "matching"


@pytest.mark.asyncio
async def test_emitter_emit_after_close_is_silently_dropped():
    """关闭后再 emit 不抛异常、不进队列。"""
    emitter = events.EventEmitter()
    await emitter.aclose()
    await emitter.emit(events.checkpoint("tid-1", "done"))  # 不应抛错

    frames = [f async for f in emitter.stream()]
    assert frames == []


@pytest.mark.asyncio
async def test_emitter_aclose_is_idempotent():
    emitter = events.EventEmitter()
    await emitter.aclose()
    await emitter.aclose()  # 第二次不应抛错或阻塞

    frames = [f async for f in emitter.stream()]
    assert frames == []


# ─────────────────────────────────────────────
# 协同闭环事件（spec §10）
# ─────────────────────────────────────────────

def test_iteration_and_gap_events():
    """新增的迭代/补料事件帧格式合法。"""
    from orchestrator import events

    for frame in [
        events.iteration_start(1),
        events.gaps_collecting("s1", "配电柜型式试验报告"),
        events.gaps_done("s1", 3, 5),
        events.feedback_ready({"targets": ["s1"], "material_request_count": 2, "iteration": 1}),
    ]:
        assert frame.startswith("event: ")
        assert frame.endswith("\n\n")


def test_iteration_start_frame_content():
    """只断言帧外壳是弱断言（任何帧都成立），必须钉住事件名与载荷。"""
    name, data = _parse_frame(events.iteration_start(2))
    assert name == "iteration_start"
    assert data == {"iteration": 2}


def test_gaps_collecting_frame_content():
    name, data = _parse_frame(
        events.gaps_collecting("s1", "配电柜型式试验报告")
    )
    assert name == "gaps_collecting"
    assert data == {"block_id": "s1", "query": "配电柜型式试验报告"}


def test_gaps_done_frame_content():
    name, data = _parse_frame(events.gaps_done("s1", 3, 5))
    assert name == "gaps_done"
    assert data == {"block_id": "s1", "new_matches": 3, "total_matches": 5}


def test_feedback_ready_frame_content():
    payload = {"targets": ["s1", "s3"], "material_request_count": 4, "iteration": 2}
    name, data = _parse_frame(events.feedback_ready(payload))
    assert name == "feedback_ready"
    assert data == payload


def test_convergence_event_payload():
    """convergence 事件携带判定结果。"""
    import json
    from orchestrator import events

    frame = events.convergence({
        "status": "refine",
        "unconverged_blocks": ["s1"],
        "reason": "存在未达标 block",
        "iteration": 1,
    })

    assert "event: convergence" in frame
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["status"] == "refine"
    assert payload["unconverged_blocks"] == ["s1"]


def test_convergence_frame_content():
    """收敛帧的事件名与全部载荷字段都要落到位。"""
    payload = {
        "status": "max_iterations",
        "unconverged_blocks": ["s1", "s2"],
        "reason": "已达迭代上限 3 轮",
        "iteration": 3,
        "review_failed": False,
    }
    name, data = _parse_frame(events.convergence(payload))
    assert name == "convergence"
    assert data == payload
