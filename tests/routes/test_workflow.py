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

    async def rerun_match(self, thread_id):
        self.calls.append(("rerun_match", (thread_id,)))
        if thread_id == "missing":
            raise KeyError(thread_id)

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
    assert runner.calls[0] == ("rerun_match", ("tid-1",))


async def test_state_ok(client, runner):
    workflow_routes.set_runner(runner)
    r = await client.get("/api/workflow/tid-1/state")
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "idle"
    assert body["thread_id"] == "tid-1"


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
