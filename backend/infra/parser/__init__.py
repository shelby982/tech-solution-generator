"""infra/parser 公开 API：

- parse_document：统一入口，按扩展名分发到 docx / pdf
- ParsedDocument / Section / TocEntry：解析结果数据结构

逻辑零改动，移自 backend/services/parser.py。
"""

from pathlib import Path
from typing import Optional, Callable, Union, BinaryIO

from .toc import Section, ParsedDocument, TocEntry
from .docx import parse_docx, parse_doc_via_convert
from .pdf import parse_pdf


# ─────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────

def parse_document(
    file_source: Union[str, BinaryIO],
    suffix: str = "",
    filename: str = "",
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> ParsedDocument:
    if isinstance(file_source, str):
        suffix = suffix or Path(file_source).suffix.lower()
        filename = filename or Path(file_source).stem

    suffix = suffix.lower()

    actual_type = _detect_file_type(file_source)
    if actual_type == "ole" and suffix == ".docx":
        import logging
        logging.getLogger(__name__).warning(
            f"文件扩展名为 .docx 但实际是 OLE (.doc) 格式：{filename}"
        )
        suffix = ".doc"

    if suffix == ".pdf":
        return parse_pdf(file_source, filename, progress_callback)
    elif suffix == ".docx":
        return parse_docx(file_source, filename, progress_callback)
    elif suffix == ".doc":
        return parse_doc_via_convert(file_source, filename, progress_callback)
    else:
        raise ValueError(f"不支持的文件格式：{suffix}，请上传 PDF 或 DOCX 文件")


def _detect_file_type(file_source: Union[str, BinaryIO]) -> str:
    if isinstance(file_source, str):
        try:
            with open(file_source, "rb") as f:
                header = f.read(8)
        except Exception:
            return "unknown"
    else:
        pos = file_source.tell()
        header = file_source.read(8)
        file_source.seek(pos)

    if header[:4] == b'PK\x03\x04':
        return "zip"
    if header[:4] == b'\xd0\xcf\x11\xe0':
        return "ole"
    if header[:5] == b'%PDF-':
        return "pdf"
    return "unknown"


__all__ = [
    "parse_document",
    "ParsedDocument",
    "Section",
    "TocEntry",
]
