"""infra/parser/toc.py — 目录解析的公共工具与数据结构。

被 docx.py 与 pdf.py 共享。本模块不依赖 python-docx / pdfplumber，
仅做纯文本与编号识别。
"""

import re
from dataclasses import dataclass, field
from typing import Optional

_CIRCLED_DIGITS = set("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")

# TOC 样式名（Word 内置 TOC 段落样式）
_TOC_STYLES = {
    "toc 1", "toc 2", "toc 3", "toc 4",
    "toc1", "toc2", "toc3", "toc4",
    "目录 1", "目录 2", "目录 3", "目录 4",
}

# 手动目录区域的标记词
_MANUAL_TOC_MARKERS = {"目录", "目 录", "contents", "table of contents"}


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

class Section:
    """代表文档中的一个章节。"""
    def __init__(
        self,
        section_id: str,
        level: int,
        title: str,
        raw_content: str = "",
        special_marks: Optional[list] = None,
    ):
        self.id = section_id
        self.level = level
        self.title = title
        self.raw_content = raw_content
        self.special_marks: list[str] = special_marks or []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "raw_content": self.raw_content,
            "special_marks": self.special_marks,
        }


class ParsedDocument:
    """解析后的文档结构。"""
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


@dataclass
class TocEntry:
    """目录中的一个条目。"""
    level: int
    title: str
    raw_title: str
    special_marks: list[str] = field(default_factory=list)


@dataclass
class Block:
    """文档体内的一个块：段落或表格，按 XML 顺序排列。
    text:
      - paragraph: 段落原文，若段落内嵌图片会追加 OCR 结果
      - table:     Markdown 序列化后的表格
    style: 仅 paragraph 有意义（如 "heading 1"、"toc 2"）
    para_obj: 仅 paragraph，保留原 Paragraph 实例
    """
    kind: str
    text: str
    style: str = ""
    para_obj: object = None


# ─────────────────────────────────────────────
# 特殊标记
# ─────────────────────────────────────────────

_SPECIAL_MARK_RE = re.compile(r'[★▲]')


def extract_special_marks(text: str) -> tuple[list[str], str]:
    marks = []
    for m in ("★", "▲"):
        if m in text:
            marks.append(m)
    cleaned = _SPECIAL_MARK_RE.sub("", text).strip()
    return marks, cleaned


# ─────────────────────────────────────────────
# 编号识别正则（4 层级）
# ─────────────────────────────────────────────

HEADING_PATTERNS: list[tuple[int, re.Pattern]] = [
    (4, re.compile(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]')),
    (4, re.compile(r'^\d+\.\d+[\s　]')),
    (3, re.compile(r'^（\d+）')),
    (3, re.compile(r'^\(\d+\)')),
    (2, re.compile(r'^\d+\.\s')),
    (2, re.compile(r'^\d+\.　')),
    (1, re.compile(r'^[一二三四五六七八九十百千]+[、，]')),
    (1, re.compile(r'^第\s*[一二三四五六七八九十百千\d]+\s*[章节篇部]')),
]


def detect_level_from_numbering(text: str) -> Optional[int]:
    text = text.strip()
    for level, pattern in HEADING_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        # level 3/4 编号常被列表项使用，去前缀后通常 ≤ 20 字。
        # 超过则视为列表项不当作标题。
        if level >= 3:
            remainder = text[m.end():].strip()
            if len(remainder) > 20:
                return None
        return level
    return None


def clean_title(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def _toc_style_to_level(style_name: str) -> int:
    """TOC 样式名转层级数字。"""
    for i in range(4, 0, -1):
        if str(i) in style_name:
            return i
    return 1


def _clean_toc_line(text: str) -> str:
    """清理 TOC 行：去除尾部导引符、页码、Tab。"""
    text = re.sub(r'[\t]+', ' ', text)
    text = re.sub(r'[.·…]{3,}\s*\d*\s*$', '', text)
    text = re.sub(r'\s+\d+\s*$', '', text)
    return text.strip()


def _normalize_for_match(text: str) -> str:
    """归一化文本用于匹配：去除标记、空白、标点。"""
    text = _SPECIAL_MARK_RE.sub("", text)
    text = re.sub(r'[.·…]+\s*\d*\s*$', '', text)
    text = re.sub(r'\s+', '', text)
    return text.lower()


def _extract_numbering_prefix(text: str) -> str:
    """提取编号前缀（如 "一、"、"1."、"（1）"）。"""
    text = text.strip()
    for _, pattern in HEADING_PATTERNS:
        m = pattern.match(text)
        if m:
            return m.group(0)
    return ""


def _text_similarity(a: str, b: str) -> float:
    """简单的文本相似度（基于公共前缀长度比）。"""
    if not a or not b:
        return 0.0
    shorter = min(len(a), len(b))
    common = 0
    for i in range(shorter):
        if a[i] == b[i]:
            common += 1
        else:
            break
    prefix_ratio = common / shorter if shorter > 0 else 0

    # 也考虑包含关系
    if a in b or b in a:
        return 0.9

    return prefix_ratio
