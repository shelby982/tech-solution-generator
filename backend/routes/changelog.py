import json
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["changelog"])

_data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "changelog.json")


@router.get("/changelog")
async def get_changelog():
    """返回版本更新记录列表（从 changelog.json 读取）"""
    path = os.path.normpath(_data_path)
    if not os.path.isfile(path):
        return JSONResponse(status_code=404, content={"detail": "changelog.json 不存在"})
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return JSONResponse(content=data)
