"""
技术方案生成助手 — FastAPI 主入口
同时提供 REST API 和前端静态文件服务
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from routes.upload import router as upload_router
from routes.config import router as config_router
from routes.generate import router as generate_router
from routes.download import router as download_router
from routes.changelog import router as changelog_router

# ── 日志配置 ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── 生命周期 ──────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("════════════════════════════════════")
    logger.info("  技术方案生成助手 v1.0 启动")
    logger.info("  访问地址：http://localhost:8000")
    logger.info("  API 文档：http://localhost:8000/docs")
    logger.info("════════════════════════════════════")
    yield
    logger.info("服务已关闭")


# ── 应用实例 ──────────────────────────────────
app = FastAPI(
    title="技术方案生成助手",
    description="从技术规范书自动生成完整技术方案",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS（开发阶段允许所有来源）────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 全局未处理异常捕获 ─────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"未处理的异常 [{request.method} {request.url.path}]: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误，请稍后重试",
            "error_type": type(exc).__name__,
        },
    )


# ── 路由注册 ──────────────────────────────────
app.include_router(upload_router,    prefix="/api")
app.include_router(config_router,    prefix="/api")
app.include_router(generate_router,  prefix="/api")
app.include_router(download_router,  prefix="/api")
app.include_router(changelog_router, prefix="/api")


# ── 前端静态文件 ──────────────────────────────
_this_dir    = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.join(_this_dir, "..", "frontend")
frontend_dir = os.path.normpath(frontend_dir)
_index_path  = os.path.join(frontend_dir, "index.html")

if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    logger.info(f"前端目录已挂载：{frontend_dir}")
else:
    logger.warning(f"前端目录不存在，跳过静态文件挂载：{frontend_dir}")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """根路径：返回前端 index.html"""
    if os.path.isfile(_index_path):
        return FileResponse(_index_path)
    return JSONResponse(content={
        "message": "技术方案生成助手 API 运行中",
        "docs": "/docs",
        "hint": "前端文件不存在，请确认 frontend/index.html 已创建",
    })


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
