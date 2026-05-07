"""
文件上传与目录解析路由
POST /api/upload
"""

import os
import uuid
import tempfile
import logging
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse

from services.parser import parse_document

logger = logging.getLogger(__name__)
router = APIRouter(tags=["upload"])

# 允许的文件类型
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}
# 最大文件大小：50 MB
MAX_FILE_SIZE = 50 * 1024 * 1024


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    上传技术规范书（PDF 或 DOCX），解析并返回结构化目录。

    返回格式：
    {
        "doc_id": "uuid",
        "title": "文档标题",
        "sections": [
            {"id": "s1", "level": 1, "title": "1. 项目概述", "content_hint": "..."},
            ...
        ]
    }
    """
    # ── 1. 文件类型校验 ──────────────────────────
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式「{suffix}」，请上传 PDF 或 DOCX 文件",
        )

    # ── 2. 文件大小校验 ──────────────────────────
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content) // 1024 // 1024} MB），请上传 50 MB 以内的文件",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # ── 3. 写入临时文件 ──────────────────────────
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
            prefix="tsg_upload_",
        ) as tmp:
            tmp.write(content)
            temp_path = tmp.name

        logger.info(f"接收文件：{file.filename}，大小：{len(content)} bytes，临时路径：{temp_path}")

        # ── 4. 调用解析服务 ──────────────────────
        parsed = parse_document(temp_path)

        # ── 5. 校验解析结果 ──────────────────────
        if not parsed.sections:
            raise HTTPException(
                status_code=422,
                detail=(
                    "未能从文档中识别出任何章节目录。"
                    "请确保文档使用了标准 Heading 样式（DOCX）或包含可识别的编号标题（PDF）。"
                ),
            )

        logger.info(f"解析完成：doc_id={parsed.doc_id}，章节数={len(parsed.sections)}")

        result = parsed.to_dict()
        result["filename"] = file.filename          # 原始上传文件名，供前端显示
        return JSONResponse(content=result)

    except HTTPException:
        raise

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.exception(f"文档解析失败：{e}")
        raise HTTPException(
            status_code=500,
            detail=f"文档解析失败，请检查文件是否损坏：{str(e)}",
        )

    finally:
        # ── 6. 清理临时文件 ──────────────────────
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


@router.get("/upload/supported-formats")
async def get_supported_formats():
    """返回支持的文件格式列表"""
    return {
        "formats": list(ALLOWED_EXTENSIONS),
        "max_size_mb": MAX_FILE_SIZE // 1024 // 1024,
        "description": "支持 PDF 和 DOCX 格式的技术规范书",
    }
