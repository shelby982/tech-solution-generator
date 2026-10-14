"""routes/workflow.py 7 端点测试。

通过 ``set_runner`` 注入 FakeRunner，覆盖：
- 200 正常路径 + 返回字段
- 503 未注入 runner
- 404 thread_id 未知（resume / regen / abort / stream）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from routes import workflow as workflow_routes


# ─────────────────────────────────────────────
# Fake runner
# ─────────────────────────────────────────────

class FakeRunner:
    """最小可用 WorkflowRunner mock。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def start(self, project_id, config):
        self.calls.append(("start", (project_id, config)))
        return "fake-tid"

    async def resume(self, thread_id, user_choice, edits):
        self.calls.append(("resume", (thread_id, user_choice, edits)))
        if thread_id == "missing":
            raise KeyError(thread_id)
        # 同一 thread 已有后台 task 在跑（刷新页面/第二个标签页重复确认）
        if thread_id == "busy":
            raise ValueError("当前任务仍在运行，请等待完成后再操作")

    async def regen(self, thread_id, block_ids):
        self.calls.append(("regen", (thread_id, list(block_ids))))
        if thread_id == "missing":
            raise KeyError(thread_id)

    async def workspace_action(self, thread_id, block_ids, action):
        self.calls.append(("workspace_action", (thread_id, block_ids, action)))
        if thread_id == "missing": raise KeyError(thread_id)
        if thread_id == "busy": raise ValueError("当前任务仍在运行")

    async def abort(self, thread_id):
        self.calls.append(("abort", (thread_id,)))
        if thread_id == "missing":
            raise KeyError(thread_id)

    async def pause(self, thread_id):
        self.calls.append(("pause", (thread_id,)))
        if thread_id == "missing":
            raise KeyError(thread_id)

    async def skip_to_review(self, thread_id):
        self.calls.append(("skip_to_review", (thread_id,)))
        if thread_id == "missing":
            raise KeyError(thread_id)

    async def resume_generation(self, thread_id):
        self.calls.append(("resume_generation", (thread_id,)))
        if thread_id == "missing":
            raise KeyError(thread_id)
        if thread_id == "busy":
            raise ValueError("当前任务仍在运行，请等待完成后再操作")

    async def rerun_match(self, thread_id, *, full: bool = False):
        self.calls.append(("rerun_match", (thread_id, full)))
        if thread_id == "missing":
            raise KeyError(thread_id)
        if thread_id == "busy":
            raise ValueError("当前任务仍在运行，请等待完成后再操作")

    async def redraft_outline(self, thread_id, instruction):
        from orchestrator.runner import WorkflowAbortedError

        self.calls.append(("redraft_outline", (thread_id, instruction)))
        if thread_id == "missing":
            raise KeyError(thread_id)
        if thread_id == "aborted":
            raise WorkflowAbortedError("该工作流已作废，请创建新的工作流")
        if thread_id == "busy":
            raise ValueError("当前任务仍在运行，请等待完成后再操作")

    async def update_outline_structure(self, thread_id, nodes, *, purge=False, removed_ids=None):
        from domain.spec.structure import StructureError, validate_structure

        self.calls.append(
            ("update_outline_structure", (thread_id, nodes, purge, tuple(removed_ids or []))))
        if thread_id == "missing":
            raise KeyError(thread_id)
        if thread_id == "busy":
            raise ValueError("当前任务仍在运行，请等这一步结束后再调整目录")
        clean = validate_structure(nodes)          # 非法时抛 StructureError
        return {"created": 0, "updated": len(clean), "removed": 0, "kept": 0,
                "total": len(clean),
                "nodes": [{"block_id": n["block_id"], "level": n["level"],
                           "title": n["title"]} for n in clean]}

    async def recover(self, thread_id):
        self.calls.append(("recover", (thread_id,)))

    async def state(self, thread_id):
        self.calls.append(("state", (thread_id,)))
        return {"stage": "idle", "thread_id": thread_id}

    def has_run(self, thread_id: str) -> bool:
        return thread_id != "missing"

    def stream(self, thread_id):
        self.calls.append(("stream", (thread_id,)))
        if thread_id == "missing":
            raise KeyError(thread_id)

        async def _gen():
            yield "event: ping\ndata: {}\n\n"

        return _gen()


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(workflow_routes.router, prefix="/api")
    return application


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture(autouse=True)
def _reset_runner():
    """每个用例前后都把 _runner 清空，避免相互污染。"""
    workflow_routes.set_runner(None)
    yield
    workflow_routes.set_runner(None)


@pytest.fixture
async def client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ─────────────────────────────────────────────
# 正常路径
# ─────────────────────────────────────────────

async def test_start_returns_thread_id(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/start", json={"project_id": 1})
    assert r.status_code == 200
    assert r.json() == {"thread_id": "fake-tid"}
    assert runner.calls[0] == ("start", (1, {}))


async def test_start_passes_config(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post(
        "/api/workflow/start",
        json={"project_id": 9, "config": {"foo": "bar"}},
    )
    assert r.status_code == 200
    assert runner.calls[0] == ("start", (9, {"foo": "bar"}))


async def test_resume_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post(
        "/api/workflow/tid-1/resume",
        json={"user_choice": "approve", "edits": {"k": "v"}},
    )
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "resumed"}
    assert runner.calls[0] == ("resume", ("tid-1", "approve", {"k": "v"}))


async def test_regen_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post(
        "/api/workflow/tid-1/regen",
        json={"block_ids": ["b1", "b2"]},
    )
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "regen_targets": ["b1", "b2"]}
    assert runner.calls[0] == ("regen", ("tid-1", ["b1", "b2"]))


async def test_abort_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/abort")
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "aborted"}
    assert runner.calls[0] == ("abort", ("tid-1",))


async def test_recover_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/recover")
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "recovering"}
    assert runner.calls[0] == ("recover", ("tid-1",))


async def test_pause_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/pause")
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "pausing"}
    assert runner.calls[0] == ("pause", ("tid-1",))


async def test_skip_to_review_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/skip-to-review")
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "skipping_to_review"}
    assert runner.calls[0] == ("skip_to_review", ("tid-1",))


async def test_resume_generation_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/resume-generation")
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "resuming_generation"}
    assert runner.calls[0] == ("resume_generation", ("tid-1",))


async def test_rerun_match_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/rerun-match")
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "rerunning_match"}
    assert runner.calls[0] == ("rerun_match", ("tid-1", False))


async def test_state_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.get("/api/workflow/tid-1/state")
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "idle"
    assert body["thread_id"] == "tid-1"
    assert body["alive"] is True


async def test_state_reports_alive_false_for_unknown_thread(client, runner):
    """进程不认得这个 thread（典型场景：后端重启，内存里的 _runs 丢了）。

    stage 仍然照常返回——正是这个组合让前端能认出「孤儿 run」：快照看着正常，
    但已经没有东西在跑了。
    """
    workflow_routes.set_runner(runner)
    r = await client.get("/api/workflow/missing/state")
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "idle"
    assert body["alive"] is False


async def test_stream_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.get("/api/workflow/tid-1/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: ping" in r.text


# ─────────────────────────────────────────────
# 错误路径：未注入 runner → 503
# ─────────────────────────────────────────────

async def test_start_without_runner_returns_503(client):
    r = await client.post("/api/workflow/start", json={"project_id": 1})
    assert r.status_code == 503
    assert "WorkflowRunner" in r.json()["detail"]


async def test_state_without_runner_returns_503(client):
    r = await client.get("/api/workflow/tid-1/state")
    assert r.status_code == 503


async def test_stream_without_runner_returns_503(client):
    r = await client.get("/api/workflow/tid-1/stream")
    assert r.status_code == 503


# ─────────────────────────────────────────────
# 错误路径：thread_id 未知 → 404
# ─────────────────────────────────────────────

async def test_resume_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post(
        "/api/workflow/missing/resume",
        json={"user_choice": "approve"},
    )
    assert r.status_code == 404
    assert "thread_id" in r.json()["detail"]


async def test_regen_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post(
        "/api/workflow/missing/regen",
        json={"block_ids": ["b1"]},
    )
    assert r.status_code == 404


async def test_abort_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/abort")
    assert r.status_code == 404


async def test_pause_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/pause")
    assert r.status_code == 404


async def test_skip_to_review_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/skip-to-review")
    assert r.status_code == 404


async def test_resume_generation_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/resume-generation")
    assert r.status_code == 404


async def test_rerun_match_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/rerun-match")
    assert r.status_code == 404


async def test_stream_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.get("/api/workflow/missing/stream")
    assert r.status_code == 404


# ─────────────────────────────────────────────
# 入参校验
# ─────────────────────────────────────────────

async def test_start_rejects_invalid_project_id(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/start", json={"project_id": 0})
    assert r.status_code == 422


@pytest.mark.parametrize("tid,action,ids,status", [
    ("ok", "review", ["s1"], 200), ("ok", "revise", ["s2"], 200),
    ("missing", "review", ["s1"], 404), ("busy", "review", ["s1"], 409),
    ("ok", "delete", ["s1"], 422), ("ok", "review", [], 422),
])
async def test_workspace_action_contract(client, runner, tid, action, ids, status):
    workflow_routes.set_runner(runner)
    response = await client.post(f"/api/workflow/{tid}/workspace-action", json={"action": action, "block_ids": ids})
    assert response.status_code == status
    if status == 200:
        assert runner.calls[-1] == ("workspace_action", (tid, ids, action))


# ─────────────────────────────────────────────
# redraft-outline
# ─────────────────────────────────────────────

async def test_redraft_outline_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post(
        "/api/workflow/tid-1/redraft-outline",
        json={"instruction": "按评分项逐条拆章"},
    )
    assert r.status_code == 200
    assert r.json() == {"thread_id": "tid-1", "status": "redrafting_outline"}
    assert runner.calls[0] == ("redraft_outline", ("tid-1", "按评分项逐条拆章"))


async def test_redraft_outline_without_body_sends_empty_instruction(client, runner):
    """body 可省：不带提炼要求时按空串重出（模型只看规范书）。"""
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/redraft-outline")
    assert r.status_code == 200
    assert runner.calls[0] == ("redraft_outline", ("tid-1", ""))


async def test_redraft_outline_unknown_tid_returns_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/redraft-outline", json={})
    assert r.status_code == 404


async def test_redraft_outline_busy_returns_409(client, runner):
    """工作流正在跑 / 不在闸门 1：runner 抛 ValueError → 409，不是 500。"""
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/busy/redraft-outline", json={})
    assert r.status_code == 409
    assert "仍在运行" in r.json()["detail"]


# ─────────────────────────────────────────────
# 目录结构编辑（增删改 / 排序）
# ─────────────────────────────────────────────

async def test_outline_structure_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/tid-1/outline-structure", json={
        "nodes": [
            {"block_id": "s1", "level": 1, "title": "技术方案"},
            {"block_id": "s2", "level": 2, "title": "方案先进性"},
        ],
    })

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "structure_updated"
    assert body["total"] == 2
    # 响应必须带服务端归一化后的结构：前端据此重绘，才不会与库里的权威结构发散
    assert body["nodes"] == [
        {"block_id": "s1", "level": 1, "title": "技术方案"},
        {"block_id": "s2", "level": 2, "title": "方案先进性"},
    ]
    assert runner.calls[-1][0] == "update_outline_structure"
    assert runner.calls[-1][1][2] is False        # 默认不带 purge（保留正文）


async def test_outline_structure_unknown_thread_404(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/missing/outline-structure", json={
        "nodes": [{"block_id": "s1", "level": 1, "title": "A"}],
    })
    assert r.status_code == 404


async def test_outline_structure_invalid_400(client, runner):
    workflow_routes.set_runner(runner)
    """空标题 / 空目录 / id 重复 → 400，且消息可直接展示给用户。"""
    r = await client.post("/api/workflow/tid-1/outline-structure", json={
        "nodes": [{"block_id": "s1", "level": 1, "title": "  "}],
    })
    assert r.status_code == 400
    assert "标题不能为空" in r.json()["detail"]

    r = await client.post("/api/workflow/tid-1/outline-structure", json={"nodes": []})
    assert r.status_code == 400
    assert "不能为空" in r.json()["detail"]


async def test_outline_structure_busy_409(client, runner):
    workflow_routes.set_runner(runner)
    """任务运行中调整结构 → 409（一边写一边被读会打架）。"""
    r = await client.post("/api/workflow/busy/outline-structure", json={
        "nodes": [{"block_id": "s1", "level": 1, "title": "A"}],
    })
    assert r.status_code == 409
    assert "仍在运行" in r.json()["detail"]


async def test_redraft_outline_on_aborted_thread_returns_code(client, runner):
    """已作废的 thread → 409 + X-Error-Code，前端据此自动切到最近一次可用运行。

    只给中文提示的话，用户不知道该点哪里，页面会一直卡在这个死线程上
    （后续每个动作都打向它）。
    """
    workflow_routes.set_runner(runner)

    r = await client.post("/api/workflow/aborted/redraft-outline", json={"instruction": "x"})

    assert r.status_code == 409
    assert r.headers.get("X-Error-Code") == "workflow_aborted"
    assert "已作废" in r.json()["detail"]


async def test_outline_structure_forwards_purge_flag(client, runner):
    """用户选「移除并删除正文」时 purge 必须透传到 runner（否则正文会留下来继续被导出）。"""
    workflow_routes.set_runner(runner)

    r = await client.post("/api/workflow/tid-1/outline-structure", json={
        "nodes": [{"block_id": "s1", "level": 1, "title": "保留的章节"}],
        "purge": True,
    })

    assert r.status_code == 200
    assert runner.calls[-1][1][2] is True


# ── 运行中互斥：409 而不是 500 ────────────────────────────
# runner._assert_idle 在续跑入口抛 ValueError。路由必须把它映射成 409 + detail，
# 前端才能把「当前任务仍在运行」写进状态栏（否则用户看到的是 500 / 点了没反应）。

async def test_resume_busy_returns_409(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/busy/resume", json={"user_choice": "approve"})
    assert r.status_code == 409
    assert "仍在运行" in r.json()["detail"]


async def test_rerun_match_busy_returns_409(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/busy/rerun-match")
    assert r.status_code == 409
    assert "仍在运行" in r.json()["detail"]


async def test_resume_generation_busy_returns_409(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.post("/api/workflow/busy/resume-generation")
    assert r.status_code == 409
    assert "仍在运行" in r.json()["detail"]
