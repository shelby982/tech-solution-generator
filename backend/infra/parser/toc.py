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
        page: int = 0,
    ):
        self.id = section_id
        self.level = level
        self.title = title
        self.raw_content = raw_content
        self.special_marks: list[str] = special_marks or []
        # 页码（PDF 从行级 page_no 推出；Word 无页码概念，留 0）。
        # 素材切片把它带进 material_chunks.page，最终出现在依据的出处 locator 里。
        self.page = int(page or 0)

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
# 表格序列化
# ─────────────────────────────────────────────

def rows_to_markdown(rows: list[list[Optional[str]]]) -> str:
    """把二维单元格序列化为 Markdown 表格。

    docx 与 pdf 两条解析路径共用，两边的表格必须产出一致的文本 —— 下游是按
    同一套规则消费的。docx 侧原本自己实现了一份（见 ``docx._table_to_markdown``），
    行为与本函数逐字一致。

    单元格内的换行转 ``<br>``、竖线转义，空单元格写一个空格：Markdown 的行
    必须凑齐列数，空串会让两个 ``|`` 直接挨上，下游按 ``|`` 切列时错位。
    """
    if not rows:
        return ""

    cells_rows: list[list[str]] = []
    for row in rows:
        cells = []
        for cell in row:
            text = (cell or "").replace("|", "\\|").replace("\n", "<br>")
            cells.append(text or " ")
        cells_rows.append(cells)

    n_cols = max(len(r) for r in cells_rows)
    cells_rows = [r + [" "] * (n_cols - len(r)) for r in cells_rows]

    md_lines = [
        "| " + " | ".join(cells_rows[0]) + " |",
        "| " + " | ".join(["---"] * n_cols) + " |",
    ]
    for r in cells_rows[1:]:
        md_lines.append("| " + " | ".join(r) + " |")
    return "\n".join(md_lines)


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

# 十进制编号：**级别 = 点分段数**（1→1 级，1.1→2 级，1.1.1→3 级，1.1.1.1→4 级）。
# 旧实现用「(4) ^\d+\.\d+」这类写死的模式，结果是 `1.1 项目背景` 判成 **4 级**、
# `1.1.1 详细要求` 反而判不出（`^\d+\.\s` 要求点号后紧跟空白）—— 同一文档里
# 1. 判 2 级、1.1 判 4 级，层级整体错乱且出现跳级。
# 段内限 1~3 位数字，避免把表格里的价值（「2320.48 否」）当成章节号。
_DECIMAL_NUMBER_RE = re.compile(r'^(\d{1,2}(?:\.\d{1,3}){0,3})[.．、]?(?![.\d])')

# 其余编号族显式列出（级别是约定：章 > 节 > 一、/（一） > （1） > ①）
_CN_CHAPTER_RE = re.compile(r'^第\s*[一二三四五六七八九十百千\d]+\s*[章篇部]')
_CN_SECTION_RE = re.compile(r'^第\s*[一二三四五六七八九十百千\d]+\s*节')
_CN_COMMA_RE = re.compile(r'^[一二三四五六七八九十百千]+[、，]')
_PAREN_CN_RE = re.compile(r'^[（(][一二三四五六七八九十百千]+[）)]')
_PAREN_ARABIC_RE = re.compile(r'^[（(]\d{1,2}[）)]')
_CIRCLED_RE = re.compile(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]')


# 章节号长相：每段 1~2 位。用来把表格里的数量（「2320.48 否」）
# 和真正的二级章节号（「1.1 项目背景」）分开——前者整数部分常有 3 位以上。

# 列表项常见的收尾：以这些标点结束的多半是正文枚举，不是标题。
_LIST_ITEM_TAIL = "；;。，,：:"


def detect_level_from_numbering(text: str) -> Optional[int]:
    """按编号判章节级别。判不出返回 None（由调用方走字号兜底）。

    级别约定（同一份文档内自洽；文档顺序仍是层级的最终权威，见 normalize_nodes）：
    ``第X章/篇/部``=1、``第X节``=2、``1.``=1、``1.1``=2、``1.1.1``=3、``1.1.1.1``=4、
    ``一、``/``（一）``=2、``（1）``=3、``①``=4。判不出前缀里的数字是否为章节号时
    （表格数值）一律 None。
    """
    text = text.strip()
    if not text:
        return None

    for level, pattern in (
        (1, _CN_CHAPTER_RE),
        (2, _CN_SECTION_RE),
        (2, _CN_COMMA_RE),
        (2, _PAREN_CN_RE),
        (3, _PAREN_ARABIC_RE),
        (4, _CIRCLED_RE),
    ):
        m = pattern.match(text)
        if m:
            return level if _passes_heading_guards(text, m.end(), level) else None

    m = _DECIMAL_NUMBER_RE.match(text)
    if m:
        # 点分段数即级别；``1.`` 结尾的点号属于编号本身，级别算 1
        level = min(4, m.group(1).count(".") + 1)
        return level if _passes_heading_guards(text, m.end(), level) else None
    return None


def _passes_heading_guards(text: str, prefix_end: int, level: int) -> bool:
    """深层编号与空正文的过滤：列表项常以统一编号开头，不是章节标题。

    - ``level >= 3`` 时去前缀后通常 ≤ 20 字；超过视为正文枚举。
    - 去前缀后为空、或以顿挫/句号收尾的枚举项，也不是标题。
    """
    if level < 3:
        return True
    remainder = text[prefix_end:].strip()
    if len(remainder) > 20:
        return False
    if not remainder or remainder[-1] in _LIST_ITEM_TAIL:
        return False
    return True


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


# 编号前缀识别用的模式表（`_extract_numbering_prefix` 逐个试；分级规则在
# detect_level_from_numbering 里，另有"点分段数 = 级别"与长度守卫）。
_NUMBERING_PREFIX_PATTERNS: tuple[re.Pattern, ...] = (
    _CN_CHAPTER_RE, _CN_SECTION_RE, _CN_COMMA_RE, _PAREN_CN_RE,
    _PAREN_ARABIC_RE, _CIRCLED_RE, _DECIMAL_NUMBER_RE,
)


def _extract_numbering_prefix(text: str) -> str:
    """提取编号前缀（如 "一、"、"1."、"（1）"）。判不出返回空串。"""
    text = text.strip()
    for pattern in _NUMBERING_PREFIX_PATTERNS:
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
