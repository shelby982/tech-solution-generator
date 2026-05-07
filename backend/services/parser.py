"""
文档解析服务 — 支持 PDF 和 DOCX 格式
负责从技术规范书中提取结构化目录（TOC）及各章节原始内容。

层级支持：
  level 1 — 第一章 / 一、 / 1. 标题
  level 2 — 1.1 标题
  level 3 — 1.1.1 标题
  level 4 — 1.1.1.1 标题（新增）
"""

import re
import uuid
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# content_hint 最大保留字符数（提供给 LLM 的原始内容上下文）
CONTENT_HINT_LIMIT = 2000


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

class Section:
    """代表文档中的一个章节"""
    def __init__(
        self,
        section_id: str,
        level: int,
        title: str,
        content_hint: str = "",
    ):
        self.id = section_id
        self.level = level
        self.title = title
        self.content_hint = content_hint[:CONTENT_HINT_LIMIT]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "content_hint": self.content_hint,
        }


class ParsedDocument:
    """解析后的文档结构"""
    def __init__(self, doc_id: str, title: str, sections: list[Section]):
        self.doc_id = doc_id
        self.title = title
        self.sections = sections

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
        }


# ─────────────────────────────────────────────
# 编号识别正则（支持4层级）
# 注意：必须从最具体（层级最深）到最宽泛排列，避免误匹配
# ─────────────────────────────────────────────

HEADING_PATTERNS = [
    # level 4 — 1.1.1.1 或 1.1.1.1.（最先匹配，最具体）
    (4, re.compile(r'^(\d+)\.(\d+)\.(\d+)\.(\d+)\.?\s+\S')),
    # level 3 — 1.1.1 或 1.1.1.
    (3, re.compile(r'^(\d+)\.(\d+)\.(\d+)\.?\s+\S')),
    # level 2 — 1.1 或 1.1.
    (2, re.compile(r'^(\d+)\.(\d+)\.?\s+\S')),
    # level 1 — 第一章 / 第一节
    (1, re.compile(r'^第\s*[一二三四五六七八九十百千\d]+\s*[章节篇部]')),
    # level 1 — 一、 二、
    (1, re.compile(r'^[一二三四五六七八九十]+、')),
    # level 1 — 1. 标题（数字 + 点 + 空格）
    (1, re.compile(r'^(\d+)\.\s+\S')),
]


def detect_level_from_numbering(text: str) -> Optional[int]:
    """根据编号格式推断标题层级，无法识别返回 None"""
    text = text.strip()
    for level, pattern in HEADING_PATTERNS:
        if pattern.match(text):
            return level
    return None


def clean_title(text: str) -> str:
    """清理标题文本，去除多余空白"""
    return re.sub(r'\s+', ' ', text).strip()


# ─────────────────────────────────────────────
# PDF 解析
# ─────────────────────────────────────────────

def parse_pdf(file_path: str) -> ParsedDocument:
    """
    解析 PDF 文档，提取目录结构与各章节原始内容。

    策略（多层融合）：
    1. 优先根据字体大小排序识别标题层级
    2. 辅以编号正则匹配
    3. 提取每个标题后的全量段落文字作为 content_hint（最多 CONTENT_HINT_LIMIT 字符）
    """
    import pdfplumber

    doc_id = str(uuid.uuid4())
    sections: list[Section] = []
    doc_title = Path(file_path).stem

    with pdfplumber.open(file_path) as pdf:
        # 第一步：收集所有文本块及其字体信息
        all_words: list[dict] = []
        for page in pdf.pages:
            words = page.extract_words(
                extra_attrs=["fontname", "size"],
                use_text_flow=True,
            )
            all_words.extend(words)

        if not all_words:
            logger.warning("PDF 未能提取到文字，可能是扫描件")
            return ParsedDocument(doc_id, doc_title, [])

        # 第二步：统计字体大小分布，推断正文字号
        sizes = [w.get("size", 0) for w in all_words if w.get("size", 0) > 0]
        if not sizes:
            return ParsedDocument(doc_id, doc_title, [])

        from collections import Counter
        size_counter = Counter(round(s, 1) for s in sizes)
        body_size = size_counter.most_common(1)[0][0]

        # 第三步：按行合并文字，识别标题行
        lines = _group_words_into_lines(all_words)

        # 第四步：两遍扫描 — 先找到所有标题位置，再提取各段内容
        heading_indices: list[tuple[int, int, str]] = []  # (line_idx, level, text)
        for i, line in enumerate(lines):
            text = line["text"].strip()
            if not text:
                continue

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
                heading_indices.append((i, level, text))

        section_counter = 0
        for h_idx, (line_idx, level, text) in enumerate(heading_indices):
            section_counter += 1

            # 收集本标题到下一标题之间的全量内容
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
            content_hint = " ".join(hint_parts)

            if section_counter == 1 and level == 1:
                doc_title = clean_title(text)

            sections.append(Section(
                section_id=f"s{section_counter}",
                level=level,
                title=clean_title(text),
                content_hint=content_hint,
            ))

    return ParsedDocument(doc_id, doc_title, sections)


def _group_words_into_lines(words: list[dict]) -> list[dict]:
    """将 pdfplumber 的 word 列表按行分组"""
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


# ─────────────────────────────────────────────
# DOCX 解析
# ─────────────────────────────────────────────

def parse_docx(file_path: str) -> ParsedDocument:
    """
    解析 DOCX 文档，提取目录结构与各章节原始内容。

    策略：
    1. 优先读取 Heading 1/2/3/4 样式段落作为标题（支持4层级）
    2. 备用：正则匹配编号格式的 Normal 样式段落
    3. 提取每个标题到下一标题之间的全量正文作为 content_hint
    """
    from docx import Document

    doc_id = str(uuid.uuid4())
    sections: list[Section] = []

    document = Document(file_path)
    paragraphs = document.paragraphs

    doc_title = Path(file_path).stem
    for para in paragraphs:
        style_name = para.style.name.lower() if para.style else ""
        if "title" in style_name and para.text.strip():
            doc_title = clean_title(para.text)
            break

    # 第一遍：找出所有标题段落的索引
    heading_indices: list[tuple[int, int, str]] = []  # (para_idx, level, text)
    for i, para in enumerate(paragraphs):
        text = para.text.strip()
        if not text:
            continue
        style_name = para.style.name if para.style else ""
        level = _get_heading_level(style_name, text)
        if level is not None:
            heading_indices.append((i, level, text))

    # 第二遍：提取各章节到下一标题之间的全量内容
    section_counter = 0
    for h_idx, (para_idx, level, text) in enumerate(heading_indices):
        section_counter += 1

        next_para_idx = (
            heading_indices[h_idx + 1][0]
            if h_idx + 1 < len(heading_indices)
            else len(paragraphs)
        )

        hint_parts = []
        for j in range(para_idx + 1, next_para_idx):
            next_text = paragraphs[j].text.strip()
            if next_text:
                hint_parts.append(next_text)

        content_hint = " ".join(hint_parts)

        sections.append(Section(
            section_id=f"s{section_counter}",
            level=level,
            title=clean_title(text),
            content_hint=content_hint,
        ))

    return ParsedDocument(doc_id, doc_title, sections)


def _get_heading_level(style_name: str, text: str) -> Optional[int]:
    """
    根据样式名和文本推断标题层级（1-4）。
    返回 None 表示不是标题。
    """
    s = style_name.lower().strip()

    # 标准 Heading 样式（支持4层级）
    heading_map = {
        "heading 1": 1, "heading1": 1, "标题 1": 1, "标题1": 1,
        "heading 2": 2, "heading2": 2, "标题 2": 2, "标题2": 2,
        "heading 3": 3, "heading3": 3, "标题 3": 3, "标题3": 3,
        "heading 4": 4, "heading4": 4, "标题 4": 4, "标题4": 4,
        "heading 5": 4, "heading5": 4, "标题 5": 4, "标题5": 4,
        "heading 6": 4, "heading6": 4, "标题 6": 4, "标题6": 4,
    }
    for key, level in heading_map.items():
        if s == key.lower():
            return level

    # 备用：编号正则匹配（文本长度 < 100 视为标题）
    numbering_level = detect_level_from_numbering(text)
    if numbering_level is not None and len(text) < 100:
        return numbering_level

    return None


# ─────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────

def parse_document(file_path: str) -> ParsedDocument:
    """
    根据文件扩展名自动选择解析器。
    支持 .pdf 和 .docx。
    """
    suffix = Path(file_path).suffix.lower()

    if suffix == ".pdf":
        return parse_pdf(file_path)
    elif suffix in (".docx", ".doc"):
        return parse_docx(file_path)
    else:
        raise ValueError(f"不支持的文件格式：{suffix}，请上传 PDF 或 DOCX 文件")
