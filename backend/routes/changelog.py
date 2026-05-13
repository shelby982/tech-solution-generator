import os
import re
import subprocess

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["changelog"])

_repo_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

_SPLIT_RE = re.compile(r"[；;\n]+")


def _parse_changes(message: str) -> list[str]:
    """将提交消息拆分为变更条目列表，过滤空行和序号前缀。"""
    parts = _SPLIT_RE.split(message.strip())
    changes = []
    for p in parts:
        # 去除"1、2、(1)"等序号前缀
        p = re.sub(r"^[\d一二三四五六七八九十]+[、.．。）)]\s*", "", p.strip())
        if p:
            changes.append(p)
    return changes or [message.strip()]


def _build_changelog() -> list[dict]:
    """从 git log 构建版本记录列表（最新在前）。"""
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H|%ad|%s", "--date=format:%Y-%m-%d"],
            cwd=_repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []

    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return []

    total = len(lines)
    changelog = []
    for i, line in enumerate(lines):
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        _, date, message = parts
        # 版本号：最旧 commit = v1.0，依次递增
        minor = total - 1 - i
        version = f"v1.{minor}"
        changelog.append({
            "version": version,
            "date": date,
            "changes": _parse_changes(message),
        })

    return changelog


@router.get("/changelog")
async def get_changelog():
    """从 git log 动态生成版本更新记录，每次 commit 后自动更新。"""
    data = _build_changelog()
    if not data:
        return JSONResponse(status_code=503, content={"detail": "无法读取版本历史"})
    return JSONResponse(content=data)
