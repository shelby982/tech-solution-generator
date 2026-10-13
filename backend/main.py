"""
技术方案生成助手 — FastAPI 主入口
同时提供 REST API 和前端静态文件服务
"""

import logging
from typing import Optional
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from routes.upload import router as upload_router
from routes.config import router as config_router
from routes.changelog import router as changelog_router
from routes.projects import router as projects_router
from routes.materials import router as materials_router
from routes.export import router as export_router
from routes.workflow import router as workflow_router, set_runner as set_workflow_runner
from routes.review import router as review_router
from routes.blocks import router as blocks_router
from routes.revisions import router as revisions_router

from orchestrator.runner import WorkflowRunner
from orchestrator.graph import GraphDeps
from agents.zhang_heng import ZhangHengAgent
from agents.shen_kuo import ShenKuoAgent
from agents.zhuge_liang import ZhugeLiangAgent
from agents.wang_anshi import WangAnshiAgent
from agents.bao_zheng import BaoZhengAgent

# ── 日志配置 ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── 生命周期 ──────────────────────────────────
# 「应标要求」面板（frontend/project-init.html 的 01 区）允许上传的文件角色，
# 与前端 reqRoles 对齐（requirement 是早期版本遗留的「主招标」写法）。
# 提炼不再绑定技术规范书 —— 用户可能一份规范书都没有，只丢进来一份主招标文件
# 或评分表，那就是本轮大纲唯一的依据。
REQUIREMENT_ROLES = ("spec", "main_rfp", "requirement", "scoring", "evaluation")

# 上传文件根目录。测试通过 monkeypatch 本常量指向 tmp_path，
# 避免往真实 backend/data/ 下写文件（同 db.DB_PATH 的做法）。
DATA_DIR = Path(__file__).resolve().parent / "data"


async def _load_spec_for_project(project_id: int, *, with_ids: bool = False,
                                 material_ids: Optional[list[int]] = None):
    """spec_loader：取「应标要求」文件，按上传顺序返回列表。

    每项是 ``(BytesIO, suffix, filename)``；``with_ids=True`` 时是四元组
    ``(BytesIO, suffix, filename, material_id)`` —— 解析节点靠它把"这次用了哪几份文件"
    记进 checkpoint（``spec.material_ids``），重跑派生时按 id 原样取回**那次运行的那批**，
    与项目当前材料表无关。

    ``material_ids``：给了就只取这几份（**含已软删的**）。这正是"重新上传文件"
    不再影响旧 run 的关键：旧 run 认自己的 id，新文件各走各的运行。

    单份文件在磁盘上缺失只跳过并告警；一份都取不到才抛错。
    materials.file_path 形如 ``uploads/{pid}/{filename}``，相对于 backend/data/。
    """
    from io import BytesIO
    from db import get_db
    from services.block_store import ROLE_PRIORITY_SQL

    data_dir = DATA_DIR

    async with get_db() as conn:
        if material_ids:
            placeholders = ",".join("?" * len(material_ids))
            cursor = await conn.execute(
                f"SELECT id, filename, file_path FROM materials "
                f"WHERE id IN ({placeholders}) ORDER BY id",
                tuple(int(i) for i in material_ids),
            )
        else:
            placeholders = ",".join("?" * len(REQUIREMENT_ROLES))
            cursor = await conn.execute(
                # 顺序即拼接顺序、也是 digest 吃预算的顺序：主招标/规范书类排最前
                # （它定义章节骨架），评分表/评审要素随后。与 list_materials 同一片段，
                # 保证「界面看到的顺序」=「派生用的顺序」。
                "SELECT id, filename, file_path FROM materials "
                f"WHERE project_id = ? AND role IN ({placeholders}) "
                f"ORDER BY {ROLE_PRIORITY_SQL}",
                (project_id, *REQUIREMENT_ROLES),
            )
        rows = await cursor.fetchall()
    if not rows:
        raise FileNotFoundError(
            f"项目 {project_id} 未上传要求文件（规范书 / 主招标文件 / 评分表 / 评审要素）"
        )

    sources = []
    for row in rows:
        filename = row["filename"]
        rel_path = row["file_path"]
        # file_path 形如 uploads/{pid}/{落盘名}，相对 backend/data/。
        # （曾经有个「相对工程根」兜底，算出来是 backend/uploads/…，永远不可能命中，
        #   已删 —— 上传写盘用的是 routes/materials.UPLOAD_ROOT，与此处 data_dir 同源。）
        abs_path = data_dir / rel_path
        if not abs_path.exists():
            logger.warning(f"要求文件不存在，跳过：{abs_path}")
            continue
        suffix = Path(filename).suffix.lower()
        with open(abs_path, "rb") as f:
            data = f.read()
        if with_ids:
            sources.append((BytesIO(data), suffix, filename, int(row["id"])))
        else:
            sources.append((BytesIO(data), suffix, filename))

    if not sources:
        raise FileNotFoundError(f"项目 {project_id} 的要求文件均不存在于磁盘，请重新上传")
    return sources


def _build_deps() -> GraphDeps:
    """每次 start/resume 时构造一份新的 GraphDeps。

    各 agent 默认从 ``services.config_store`` 读 LLM 配置；构造无参即可。
    """
    return GraphDeps(
        zhang_heng=ZhangHengAgent(),
        shen_kuo=ShenKuoAgent(),
        zhuge_liang=ZhugeLiangAgent(),
        wang_anshi=WangAnshiAgent(),
        bao_zheng=BaoZhengAgent(),
        spec_loader=_load_spec_for_project,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("════════════════════════════════════")
    logger.info("  技术方案生成助手 v1.0 启动")
    logger.info("  访问地址：http://localhost:8000")
    logger.info("  API 文档：http://localhost:8000/docs")
    logger.info("════════════════════════════════════")
    # 初始化 SQLite 数据库（幂等，CREATE TABLE IF NOT EXISTS；
    # workflow_runs / reviews 表也在 db._DDL 内一并创建）
    from db import get_db, init_db
    async with get_db() as conn:
        await init_db(conn)
    logger.info("SQLite 数据库已初始化")

    # 装配 LLM 用量记账（llm_usage 表）。必须在 verify_all 之前：
    # 之后所有真实调用都会落一条账，供「token 花在哪」按调用点/模型汇总。
    from services import usage_store
    usage_store.install()

    # 启动期验证所有 LLM 配置；不通过的会被标 verified=False，
    # round-robin 池会自动跳过，避免死配置 hang 流式生成。
    # 0 个通过时不阻止启动——用户可启动后再到 /api/configs 配置新 key。
    from services.config_store import config_store
    verify_results = await config_store.verify_all()
    if verify_results:
        ok_count = sum(1 for ok in verify_results.values() if ok)
        logger.info(f"LLM 配置验证：{ok_count}/{len(verify_results)} 通过")
    else:
        logger.info("无 LLM 配置，跳过启动验证")

    # 构造 WorkflowRunner 并注入到 routes.workflow
    runner = WorkflowRunner(deps_factory=_build_deps)
    set_workflow_runner(runner)
    logger.info("WorkflowRunner 已注入")

    yield
    logger.info("服务已关闭")


# ── 应用实例 ──────────────────────────────────
app = FastAPI(
    title="技术方案生成助手",
    description="从技术规范书自动生成完整技术方案",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS（本服务为本地单机部署，仅允许同源）──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 全局未处理异常捕获 ─────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"未处理的异常 [{request.method} {request.url.path}]: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


# ── 路由注册 ──────────────────────────────────
app.include_router(upload_router,    prefix="/api")
app.include_router(config_router,    prefix="/api")
app.include_router(changelog_router, prefix="/api")
app.include_router(projects_router,  prefix="/api")
app.include_router(materials_router, prefix="/api")
app.include_router(export_router,   prefix="/api")
app.include_router(blocks_router,    prefix="/api")
app.include_router(revisions_router, prefix="/api")
app.include_router(workflow_router,  prefix="/api")
app.include_router(review_router,    prefix="/api")


# ── 前端静态文件 ──────────────────────────────
_this_dir    = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.join(_this_dir, "..", "frontend")
frontend_dir = os.path.normpath(frontend_dir)
_index_path        = os.path.join(frontend_dir, "index.html")
_projects_path     = os.path.join(frontend_dir, "projects.html")
_workbench_path    = os.path.join(frontend_dir, "workbench.html")
_project_init_path = os.path.join(frontend_dir, "project-init.html")
# 三模式页（把 project-init 的要求与大纲 / 素材库 与 workbench 的方案编辑合到一页）。
# 原两页在合并验证期内保持可用，便于对照与回退。
_three_mode_path   = os.path.join(frontend_dir, "three-mode.html")
_diff_review_path  = os.path.join(frontend_dir, "diff-review.html")
_review_path       = os.path.join(frontend_dir, "review.html")

if os.path.isdir(frontend_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dir, "assets")), name="assets")
    logger.info(f"前端目录已挂载：{frontend_dir}")
else:
    logger.warning(f"前端目录不存在，跳过静态文件挂载：{frontend_dir}")


@app.middleware("http")
async def _assets_no_cache(request, call_next):
    """静态资源一律回源校验。

    为什么：本地开发时前端模块带 `?v=` 版本号，但**浏览器可能仍用缓存里的旧模块**
    （尤其 HTML 也来自缓存时，版本号根本没变），表现为"改完前端没生效/整页卡死"。
    给显式 no-cache，让浏览器每次带 If-None-Match 回源 —— 未变化仍是 304，开销极小。
    """
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


_HTML_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/", include_in_schema=False)
async def serve_frontend():
    if os.path.isfile(_projects_path):
        return FileResponse(_projects_path, headers=_HTML_NO_CACHE)
    return JSONResponse(content={"message": "技术方案生成助手 API 运行中", "docs": "/docs"})


@app.get("/three-mode", include_in_schema=False)
async def serve_three_mode():
    """三模式工作台（合并验证期入口）。"""
    if os.path.isfile(_three_mode_path):
        return FileResponse(_three_mode_path, headers=_HTML_NO_CACHE)
    return JSONResponse(status_code=404, content={"detail": "three-mode.html 不存在"})


@app.get("/projects", include_in_schema=False)
async def serve_projects():
    if os.path.isfile(_projects_path):
        return FileResponse(_projects_path, headers=_HTML_NO_CACHE)
    return JSONResponse(status_code=404, content={"detail": "projects.html 不存在"})


@app.get("/workbench", include_in_schema=False)
async def serve_workbench():
    if os.path.isfile(_workbench_path):
        return FileResponse(_workbench_path, headers=_HTML_NO_CACHE)
    return JSONResponse(status_code=404, content={"detail": "workbench.html 不存在"})


@app.get("/diff-review", include_in_schema=False)
async def serve_diff_review():
    if os.path.isfile(_diff_review_path):
        return FileResponse(_diff_review_path, headers=_HTML_NO_CACHE)
    return JSONResponse(status_code=404, content={"detail": "diff-review.html 不存在"})


@app.get("/project-init", include_in_schema=False)
async def serve_project_init():
    if os.path.isfile(_project_init_path):
        return FileResponse(_project_init_path, headers=_HTML_NO_CACHE)
    return JSONResponse(status_code=404, content={"detail": "project-init.html 不存在"})


@app.get("/review", include_in_schema=False)
async def serve_review():
    if os.path.isfile(_review_path):
        # HTML 不缓存：避免改完顶部模块用户强刷仍看到旧版（CSS/JS 已带版本戳）
        return FileResponse(_review_path, headers=_HTML_NO_CACHE)
    return JSONResponse(status_code=404, content={"detail": "review.html 不存在"})


@app.get("/api/readme", include_in_schema=False)
async def get_readme():
    """返回项目 README.md 的文本内容"""
    readme_path = os.path.normpath(os.path.join(_this_dir, "..", "README.md"))
    if not os.path.isfile(readme_path):
        return JSONResponse(status_code=404, content={"detail": "README.md 不存在"})
    with open(readme_path, encoding="utf-8") as f:
        content = f.read()
    return JSONResponse(content={"content": content})


@app.get("/health", tags=["system"])
async def health_check():
    """健康检查"""
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,   # 开发模式热重载（生产请去掉此行）
        log_level="info",
    )
