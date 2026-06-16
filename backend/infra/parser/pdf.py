"""infra/parser/pdf.py — PDF 解析。

文字层路径：pdfplumber 提取 words → 行聚合 → 字号识别标题。
扫描件兜底：每页渲图 → RapidOCR → 行结构。
"""

import io
import uuid
import logging
from typing import Optional, Callable, Union, BinaryIO

from ._ocr import ocr_image
from .toc import (
    Section, ParsedDocument,
    extract_special_marks, detect_level_from_numbering, clean_title,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# PDF 解析入口
# ─────────────────────────────────────────────

def parse_pdf(
    file_source: Union[str, BinaryIO],
    filename: str = "",
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> ParsedDocument:
    """解析 PDF 文档，提取目录结构与各章节原始内容。"""
    import pdfplumber

    doc_id = str(uuid.uuid4())
    sections: list[Section] = []
    doc_title = filename or "PDF Document"

    with pdfplumber.open(file_source) as pdf:
        total_pages = len(pdf.pages)
        if progress_callback:
            progress_callback("读取 PDF 页面...", 0, total_pages)

        all_words: list[dict] = []
        for page_no, page in enumerate(pdf.pages):
            words = page.extract_words(
                extra_attrs=["fontname", "size"],
                use_text_flow=True,
            )
            all_words.extend(words)
            if progress_callback and page_no % 5 == 0:
                progress_callback(f"扫描第 {page_no+1}/{total_pages} 页", page_no + 1, total_pages)

        if not all_words:
            logger.warning(f"PDF 未能提取到文字，启用 OCR 兜底（页数={total_pages}）")
            if progress_callback:
                progress_callback("扫描件 OCR 中...", 0, total_pages)
            lines = _pdf_pages_to_lines_via_ocr(pdf, progress_callback)
            if not lines:
                return ParsedDocument(doc_id, doc_title, [])
            # 扫描件无字号信息，body_size 给一个让"字号判定标题"分支永远不命中的值
            body_size = 12.0
            sections, doc_title = _pdf_lines_to_sections(lines, body_size, doc_title, progress_callback)
            return ParsedDocument(doc_id, doc_title, sections)

        sizes = [w.get("size", 0) for w in all_words if w.get("size", 0) > 0]
        if not sizes:
            return ParsedDocument(doc_id, doc_title, [])

        from collections import Counter
        size_counter = Counter(round(s, 1) for s in sizes)
        body_size = size_counter.most_common(1)[0][0]

        lines = _group_words_into_lines(all_words)

        sections, doc_title = _pdf_lines_to_sections(lines, body_size, doc_title, progress_callback)

    return ParsedDocument(doc_id, doc_title, sections)


def _pdf_pages_to_lines_via_ocr(
    pdf,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:
    """对每页 pdf 整页渲染图后做 OCR，返回 lines 结构。"""
    lines: list[dict] = []
    total_pages = len(pdf.pages)
    for page_no, page in enumerate(pdf.pages):
        try:
            pil_img = page.to_image(resolution=200).original
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            text = ocr_image(buf.getvalue())
        except Exception as e:
            logger.warning(f"第 {page_no+1} 页 OCR 失败：{e}")
            text = ""
        for raw_line in text.splitlines():
            t = raw_line.strip()
            if t:
                lines.append({"text": t, "avg_size": 12})
        if progress_callback:
            progress_callback(f"OCR 第 {page_no+1}/{total_pages} 页", page_no + 1, total_pages)
    return lines


def _pdf_lines_to_sections(
    lines: list[dict],
    body_size: float,
    doc_title: str,
    progress_callback: Optional[Callable] = None,
) -> tuple[list[Section], str]:
    """共用的 PDF 标题识别 → sections 流程。"""
    sections: list[Section] = []

    if progress_callback:
        progress_callback("识别章节标题...", 0, -1)

    heading_indices: list[tuple[int, int, str, list[str]]] = []
    for i, line in enumerate(lines):
        raw_text = line["text"].strip()
        if not raw_text:
            continue

        marks, text = extract_special_marks(raw_text)
        avg_size = line["avg_size"]
        numbering_level = detect_level_from_numbering(text)

        is_heading = False
        level = 1

        if numbering_level is not None:
            is_heading = True
            level = numbering_level
        elif avg_size > body_size * 1.15:
            is_heading = True
            level = 1
        elif avg_size > body_size * 1.05 and len(text) < 60:
            is_heading = True
            level = 2

        if is_heading and len(text) >= 2:
            heading_indices.append((i, level, text, marks))

    total_headings = len(heading_indices)
    for h_idx, (line_idx, level, text, marks) in enumerate(heading_indices):
        if progress_callback:
            progress_callback(text, h_idx + 1, total_headings)

        next_line_idx = (
            heading_indices[h_idx + 1][0]
            if h_idx + 1 < len(heading_indices)
            else len(lines)
        )
        hint_parts = []
        for j in range(line_idx + 1, next_line_idx):
            next_text = lines[j]["text"].strip()
            if next_text:
                hint_parts.append(next_text)
        raw_content = "\n".join(hint_parts)

        if h_idx == 0 and level == 1:
            doc_title = clean_title(text)

        sections.append(Section(
            section_id=f"s{h_idx + 1}",
            level=level,
            title=clean_title(text),
            raw_content=raw_content,
            special_marks=marks,
        ))

    if not sections:
        all_text = "\n".join(line["text"].strip() for line in lines if line["text"].strip())
        if all_text:
            logger.info(f"PDF 未识别到任何标题，启用全文兜底（{len(all_text)} 字符）")
            sections.append(Section(
                section_id="s1",
                level=1,
                title=doc_title or "全文",
                raw_content=all_text,
                special_marks=[],
            ))

    return sections, doc_title


def _group_words_into_lines(words: list[dict]) -> list[dict]:
    if not words:
        return []
    lines = []
    current_line_words = [words[0]]
    for word in words[1:]:
        prev = current_line_words[-1]
        y_diff = abs(word.get("top", 0) - prev.get("top", 0))
        avg_size = prev.get("size", 12) or 12
        if y_diff < avg_size * 0.5:
            current_line_words.append(word)
        else:
            lines.append(_words_to_line(current_line_words))
            current_line_words = [word]
    lines.append(_words_to_line(current_line_words))
    return lines


def _words_to_line(words: list[dict]) -> dict:
    text = " ".join(w.get("text", "") for w in words)
    sizes = [w.get("size", 0) for w in words if w.get("size", 0) > 0]
    avg_size = sum(sizes) / len(sizes) if sizes else 12
    return {"text": text, "avg_size": avg_size}
