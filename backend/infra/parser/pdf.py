"""infra/parser/pdf.py — PDF 解析。

文字层路径：pdfplumber 提取 words → 行聚合 → 剔除页眉/页脚/页码
→ 按「本页字号基线」识别标题。
扫描件兜底：每页渲图 → RapidOCR → 行结构（无位置信息，只靠页码正则兜底）。
"""

import io
import re
import uuid
import logging
from typing import Optional, Callable, Union, BinaryIO

from ._ocr import ocr_image
from .toc import (
    Section, ParsedDocument,
    extract_special_marks, detect_level_from_numbering, clean_title,
    rows_to_markdown,
)

logger = logging.getLogger(__name__)


# 页面上下各 10% 视为页眉/页脚带。正文标题不会落在这里，
# 招投标文件却常把「第N页」「招标人：…」用 14pt 排在这一带——
# 字号判据单靠 body*1.15 会把它们全判成标题。
_MARGIN_BAND = 0.10

# 同一行文本在 ≥ 这么多页的边距带重复出现 → 判为页眉/页脚。
# 取 3 而不是 2：跨页续表头只出现两次，不该被误杀。
_RUNNING_MIN_PAGES = 3

# 页码长相：不去边距带也要剔除，因为扫描件（OCR 路径）没有位置信息。
_EXPLICIT_PAGE_NUMBER_RES = (
    re.compile(r"^第\s*\d+\s*页$"),
    re.compile(r"^第\s*\d+\s*页\s*共\s*\d+\s*页$"),
    re.compile(r"^共\s*\d+\s*页\s*第\s*\d+\s*页$"),
    re.compile(r"^[-—–]\s*\d+\s*[-—–]$"),
    re.compile(r"^page\s*\d+(\s*of\s*\d+)?$", re.I),
)

# 光秃秃的数字：只在边距带里才当页码，正文里的「2026」可能是正常内容。
_BARE_NUMBER_RE = re.compile(r"^\d{1,4}$")


def _is_explicit_page_number(text: str) -> bool:
    return any(r.match(text) for r in _EXPLICIT_PAGE_NUMBER_RES)


def _running_key(text: str) -> str:
    """页眉归一化：去空白、数字统一成 #。

    这样「第1页」「第2页」归到同一个 key，不同页的同一句页眉也能对上。
    """
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text)).lower()


def _drop_running_furniture(lines: list[dict]) -> list[dict]:
    """剔除页眉、页脚、页码。

    判据一（位置无关）：文本本身就是页码的样子。
    判据二（边距带 + 重复）：落在上下边距带，且同一文本（数字归一后）
    在 ≥_RUNNING_MIN_PAGES 页上出现过。
    """
    pages_seen: dict[str, set] = {}
    for line in lines:
        if not line.get("in_margin"):
            continue
        pages_seen.setdefault(_running_key(line["text"]), set()).add(line.get("page_no", 0))

    kept: list[dict] = []
    dropped = 0
    for line in lines:
        text = line["text"]
        if _is_explicit_page_number(text):
            dropped += 1
            continue
        if line.get("in_margin"):
            repeated = len(pages_seen.get(_running_key(text), ())) >= _RUNNING_MIN_PAGES
            if repeated or _BARE_NUMBER_RE.match(text):
                dropped += 1
                continue
        kept.append(line)

    if dropped:
        logger.info(f"PDF 剔除页眉/页脚/页码 {dropped} 行")
    return kept


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

        all_lines: list[dict] = []
        # 过滤表格内 word 之前的提取总数，用来判「这份 PDF 有没有文字层」
        extracted_word_count = 0
        for page_no, page in enumerate(pdf.pages):
            page_height = page.height or 0
            tables = _find_tables(page, page_no)

            words = page.extract_words(
                extra_attrs=["fontname", "size"],
                use_text_flow=True,
            )
            extracted_word_count += len(words)

            # 表格内的文字由 _table_line 单独还原成 Markdown，这里先剔掉，
            # 否则同一批文字会在 raw_content 里出现两遍。
            text_words = _words_outside_tables(words, [t.bbox for t in tables])

            # 按页聚合行：行需要带「在本页的什么位置」，才能判出页眉/页脚带
            page_entries: list[tuple[float, dict]] = [
                (line["top"], line)
                for line in _page_words_to_lines(text_words, page_no, page_height)
            ]
            # 表格按 bbox 上沿插回本页的阅读顺序：整页是表格的文档若一律排在
            # 页尾，表格与它前后散文的相对位置就丢了。
            for table in tables:
                rows = _table_rows(table)
                markdown = _table_to_markdown(table, rows)
                if markdown:
                    page_entries.append((
                        table.bbox[1],
                        _table_line(markdown, page_no, page_height, table.bbox, rows),
                    ))
            page_entries.sort(key=lambda pair: pair[0])
            all_lines.extend(line for _, line in page_entries)

            if progress_callback and page_no % 5 == 0:
                progress_callback(f"扫描第 {page_no+1}/{total_pages} 页", page_no + 1, total_pages)

        # 判空用过滤前的数量：整页都是表格的 PDF 剔完表格会剩 0 个 word，
        # 拿它判空会把有文字层的文件误当扫描件跑一遍 OCR。
        if not extracted_word_count:
            logger.warning(f"PDF 未能提取到文字，启用 OCR 兜底（页数={total_pages}）")
            if progress_callback:
                progress_callback("扫描件 OCR 中...", 0, total_pages)
            lines = _pdf_pages_to_lines_via_ocr(pdf, progress_callback)
            if not lines:
                return ParsedDocument(doc_id, doc_title, [])
            lines = _drop_running_furniture(lines)
            # 扫描件无字号信息，body_size 给一个让"字号判定标题"分支永远不命中的值
            body_size = 12.0
            sections, doc_title = _pdf_lines_to_sections(lines, body_size, doc_title, progress_callback)
            return ParsedDocument(doc_id, doc_title, sections)

        lines = _drop_running_furniture(all_lines)
        if not lines:
            return ParsedDocument(doc_id, doc_title, [])

        from collections import Counter
        # 正文字号从「非页眉页脚带」的行里取，避免页眉那档字号把众数带偏
        size_counter = Counter(
            round(line["avg_size"], 1)
            for line in lines
            if not line.get("in_margin") and line.get("avg_size", 0) > 0
        )
        # 整页都是表格时没有任何散文行，字号统计为空。此时给一个基准值让
        # "字号判据"永不命中（表格行的 avg_size 恒为 0），标题识别只靠编号走。
        body_size = size_counter.most_common(1)[0][0] if size_counter else 12.0

        sections, doc_title = _pdf_lines_to_sections(lines, body_size, doc_title, progress_callback)

    return ParsedDocument(doc_id, doc_title, sections)


# ─────────────────────────────────────────────
# 表格区域（find_tables → Markdown）
# ─────────────────────────────────────────────

def _find_tables(page, page_no: int) -> list:
    """找出本页的表格区域。识别不出来就当作没有表格。

    pdfplumber 默认走 lines 策略（靠表格框线），没有框线的表抠不出来 ——
    这类文档的解析结果与改造前一致：不倒退，但也不会变好。
    """
    try:
        return page.find_tables()
    except Exception as e:
        logger.warning(f"第 {page_no+1} 页表格识别失败，按纯文本处理：{e}")
        return []


def _words_outside_tables(words: list[dict], bboxes: list[tuple]) -> list[dict]:
    """滤掉落在表格框内的 word。

    表格区域的文字由 ``_table_line`` 单独还原，同一批字若再走一遍散文聚合，
    就会在 raw_content 里出现两遍。

    按 word 的中心点判归属：压在表格边线上的字一律算表格内的 —— 宁可少一行
    散文，也不要把半个单元格漏到表格外面。
    """
    if not bboxes:
        return list(words)

    kept: list[dict] = []
    for word in words:
        cx = (word.get("x0", 0) + word.get("x1", 0)) / 2
        cy = (word.get("top", 0) + word.get("bottom", 0)) / 2
        inside = any(
            x0 <= cx <= x1 and top <= cy <= bottom
            for x0, top, x1, bottom in bboxes
        )
        if not inside:
            kept.append(word)
    return kept


def _table_rows(table) -> list[list[str]]:
    """提取表格的二维单元格。抠不出内容时返回空列表。"""
    try:
        rows = table.extract()
    except Exception as e:
        logger.warning(f"表格内容提取失败：{e}")
        return []
    return [[(c or "").strip() for c in row] for row in (rows or [])]


def _table_to_markdown(table, rows: Optional[list[list[str]]] = None) -> str:
    """pdfplumber 的表格对象 → Markdown。抠不出内容时返回空串。

    ``rows`` 可由调用方传入（已经 extract 过一次），避免同一张表重复解析。
    """
    if rows is None:
        rows = _table_rows(table)
    if not rows:
        return ""
    return rows_to_markdown(rows)


def _table_line(
    markdown: str,
    page_no: int,
    page_height: float,
    bbox: tuple,
    rows: Optional[list[list[str]]] = None,
) -> dict:
    """把表格的 Markdown 包成一条 line 记录，混进散文行里。

    字号刻意置 0：表格只能是正文，不能变成章节标题（判据见
    ``_pdf_lines_to_sections``）。``in_margin`` 恒为 False 同理 —— 表格是
    正文，不该被页眉/页脚的剔除逻辑吃掉。

    ``rows`` 是原始二维单元格：整篇都是表格的文档没有散文标题可判，只能靠它
    按行还原章节（见 ``_sections_from_tables``）。
    """
    _, top, _, bottom = bbox
    if page_height > 0:
        rel_top, rel_bottom = top / page_height, bottom / page_height
    else:
        rel_top = rel_bottom = 0.0
    return {
        "text": markdown,
        "kind": "table",
        "rows": rows or [],
        "avg_size": 0.0,
        "page_body_size": 0.0,
        "page_no": page_no,
        "top": top,
        "rel_top": rel_top,
        "rel_bottom": rel_bottom,
        "in_margin": False,
    }


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
                # 扫描件拿不到字的坐标，只带页码；页眉靠正则兜底（见 _drop_running_furniture）
                lines.append({"text": t, "avg_size": 12, "page_no": page_no})
        if progress_callback:
            progress_callback(f"OCR 第 {page_no+1}/{total_pages} 页", page_no + 1, total_pages)
    return lines


# ─────────────────────────────────────────────
# 表格型文档：按表格行还原章节
# ─────────────────────────────────────────────

# 表头里出现这些词的那一列，就是「章节名」所在列。招投标文件的评分表
# 基本都用这几个词做表头（报价表用「评分因素」，条款表用「条款」）。
_TITLE_HEADER_NAMES = (
    "评分因素", "评审因素", "评分项", "评审项", "项目名称", "子项名称", "名称",
    "条款", "章节", "项目", "内容", "指标", "要求",
)

# 序号列的长相：1-3 位纯整数。「2.2.4（2）」这种条款号不在此列 ——
# 它是条款编号，不是行序号，当标题前缀会喧宾夺主。
_SERIAL_RE = re.compile(r"^\d{1,3}$")


def _norm_cell(text) -> str:
    """归一化单元格用于比较：去掉全部空白。

    PDF 抽出来的中文常被拆开（「技术条款 差异性」「项目管理方 案及质量管 理方案」），
    不归一化的话同样的词永远匹配不上。
    """
    return re.sub(r"\s+", "", str(text or ""))


def _header_title_col(rows: list[list[str]]) -> Optional[int]:
    """从表头行里找「章节名」列。表头通常在最前面几行。"""
    for row in rows[:3]:
        for c, cell in enumerate(row):
            if _norm_cell(cell) in _TITLE_HEADER_NAMES:
                return c
    return None


def _table_layout(rows: list[list[str]]) -> tuple[Optional[int], list[int]]:
    """推断表格的 (序号列, 标题列们)。推不出来返回 ``(None, [])``。

    按**形状**推断而不是写死列序：同一份评分表在不同页上列数就不一样
    （首页多出「条款号」「分类」两列，续表就没有），写死列序换个文件就错位。

    标题列 = 序号列（或表头列）之右、描述列之左的全部有内容的列。描述列取
    字符数最多的那列 —— 评分标准的长文永远是最长的那列，它右边只剩分值/备注。
    """
    width = max((len(r) for r in rows), default=0)
    if width < 2:
        return None, []

    cells = [[(r[c] if c < len(r) else "") for c in range(width)] for r in rows]
    filled = [sum(1 for r in cells if _norm_cell(r[c])) for c in range(width)]
    numish = [sum(1 for r in cells if _SERIAL_RE.match(_norm_cell(r[c]))) for c in range(width)]
    charlen = [sum(len(_norm_cell(r[c])) for r in cells) for c in range(width)]

    header_col = _header_title_col(rows)
    if header_col is not None:
        serial, start = None, header_col
    else:
        # 序号列取最左侧符合的那个：分值列也长着「1-3 位整数」的样子，
        # 取最左才不会把分值当序号。
        serial = next(
            (c for c in range(width) if filled[c] and numish[c] >= filled[c] * 0.6),
            None,
        )
        start = serial + 1 if serial is not None else 0

    # 描述列只在标题列右侧找：评分标准的长文永远在右边，而左侧的条款号
    # （「2.2.4（3）」）字符数可能反而更多 —— 全局取最长会选错列。
    desc = max(
        (c for c in range(start + 1, width) if c != serial),
        key=lambda c: charlen[c],
        default=None,
    )
    if desc is None:
        # 右侧没有别的列了：标题列本身就是最后一列（如 [序号, 名称] 两列表）
        return serial, ([start] if filled[start] else [])
    if not filled[desc]:
        return serial, []

    title_cols = [c for c in range(start, desc) if filled[c]]
    return serial, title_cols


def _dedupe_title(cells: list[str]) -> str:
    """把标题列拼成一个章节名。

    标题列常有「标签 + 展开」两列（「对项目的理解」/「对项目的理解正确、深入、全面」），
    或者两列逐字相同。留短的那条 —— 短的才是章节名，长的属于正文。
    """
    normed = [_norm_cell(c) for c in cells]
    kept: list[str] = []
    for i, n in enumerate(normed):
        # 本列是另一列的展开版本（包含它且更长）→ 丢掉本列
        if any(
            n and other and other != n and other in n
            for j, other in enumerate(normed) if j != i
        ):
            continue
        kept.append(cells[i])

    out: list[str] = []
    seen: set[str] = set()
    for c in kept:
        # 用归一化后的文本当章节名：PDF 抽出来的中文带断行空格
        # （「技术条款 差异性」），原样进目录会很难读。
        n = _norm_cell(c)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return " ".join(out)


def _sections_from_tables(table_lines: list[dict]) -> list[Section]:
    """表格型文档：把表格行还原成章节。

    有一类招投标文件整篇就是表格（技术评分标准表、报价评分表），没有散文也就
    没有标题 —— 字号/编号判据全都无从下手，整篇会退化成单个 section，定位
    （``infra.retrieval.locate``）与 grounding 随之失效：提炼要求定位不到任何
    章节，每次都把全文灌给模型。

    这里按行的形状还原：一行一章。序号与标题列作章节名，该行其余列作本章原文。
    跨页断开的行（没有序号也没有标题）并进上一章 —— 它们本就是上一行的尾部。
    """
    sections: list[Section] = []
    pending: list[str] = []

    for line in table_lines:
        rows = line.get("rows") or []
        if not rows:
            continue
        serial_col, title_cols = _table_layout(rows)

        for row in rows:
            cells = [(c or "").strip() for c in row]

            picked: list[str] = []
            if title_cols:
                picked = [cells[c] for c in title_cols if c < len(cells) and cells[c]]
            # 表头行本身不是章节
            if picked and _norm_cell(picked[0]) in _TITLE_HEADER_NAMES:
                picked = []

            body = " | ".join(
                c for i, c in enumerate(cells)
                if c and i != serial_col and i not in title_cols
            )

            if not picked:
                # 续行：并进上一章；还没有章节就先攒着，等第一章出现时并进去
                if body:
                    if sections:
                        prev = sections[-1]
                        prev.raw_content = f"{prev.raw_content}\n{body}".strip()
                    else:
                        pending.append(body)
                continue

            title = _dedupe_title(picked)
            if not title:
                continue
            serial = (
                cells[serial_col]
                if serial_col is not None and serial_col < len(cells)
                else ""
            )
            content = "\n".join(pending + ([body] if body else []))
            pending = []
            sections.append(Section(
                section_id=f"t{len(sections) + 1}",
                level=1,
                title=clean_title(f"{serial} {title}".strip()),
                raw_content=content,
                special_marks=[],
            ))

    if sections and pending:
        prev = sections[-1]
        prev.raw_content = f"{prev.raw_content}\n{' '.join(pending)}".strip()

    return sections


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
        # 表格只能是正文，不能变成章节标题。
        # Markdown 行以「|」开头，本来就不命中下面任何一条编号判据 —— 这里
        # 显式短路，是为了让这条规则不依赖 rows_to_markdown 的输出格式：
        # 哪天改了序列化写法，不该悄悄冒出章节标题。
        # 它仍留在 lines 里，会被下面收进前一个标题的 raw_content。
        if line.get("kind") == "table":
            continue
        # 边距带里的残留行（未被判为重复页眉的）也不作标题：那一带是版记区
        if line.get("in_margin"):
            continue

        marks, text = extract_special_marks(raw_text)
        avg_size = line["avg_size"]
        numbering_level = detect_level_from_numbering(text)

        # 字号判据以「本页基线」为准：整页 14pt 排的合同范本页不该冒出满页标题。
        # 页基线拿不到时退回全局正文字号。
        base_size = line.get("page_body_size") or body_size

        is_heading = False
        level = 1

        if numbering_level is not None:
            is_heading = True
            level = numbering_level
        elif avg_size > base_size * 1.15:
            is_heading = True
            level = 1
        elif avg_size > base_size * 1.05 and len(text) < 60:
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
        # 散文里判不出标题时，先试表格行还原。整篇是评分表/报价表的文档一个
        # 标题都判不出来，直接兜底成一块会让下游定位与 grounding 全部失效。
        table_lines = [line for line in lines if line.get("kind") == "table"]
        if table_lines:
            sections = _sections_from_tables(table_lines)
            if sections:
                logger.info(
                    f"PDF 未识别到散文标题，改由表格行还原 {len(sections)} 个章节"
                )

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


def _page_words_to_lines(words: list[dict], page_no: int, page_height: float) -> list[dict]:
    """把一页的 words 聚成行，并标注该行在页面中的相对位置与所在页的字号基线。

    相对位置用于识别页眉/页脚带：``rel_top`` 是行顶距页顶的比例，
    ``rel_bottom`` 是行底距页顶的比例。页高缺失时两者都是 0，
    ``in_margin`` 退化为 False（不做位置判断，只靠页码正则兜底）。

    ``page_body_size`` 是该页出现最多的字号。合同/范本页整页都用 14pt 排，
    拿全局正文字号（10.5）去比，这些页面上的每一行都会"比正文大"而被当成标题。
    """
    if not words:
        return []
    from collections import Counter

    page_sizes = [round(w.get("size", 0), 1) for w in words if w.get("size", 0) > 0]
    page_body_size = Counter(page_sizes).most_common(1)[0][0] if page_sizes else 0

    lines: list[dict] = []
    current_line_words = [words[0]]
    for word in words[1:]:
        prev = current_line_words[-1]
        y_diff = abs(word.get("top", 0) - prev.get("top", 0))
        avg_size = prev.get("size", 12) or 12
        if y_diff < avg_size * 0.5:
            current_line_words.append(word)
        else:
            lines.append(_words_to_line(current_line_words, page_no, page_height, page_body_size))
            current_line_words = [word]
    lines.append(_words_to_line(current_line_words, page_no, page_height, page_body_size))
    return lines


def _words_to_line(
    words: list[dict],
    page_no: int,
    page_height: float,
    page_body_size: float = 0,
) -> dict:
    text = " ".join(w.get("text", "") for w in words)
    sizes = [w.get("size", 0) for w in words if w.get("size", 0) > 0]
    avg_size = sum(sizes) / len(sizes) if sizes else 12
    tops = [w.get("top", 0) for w in words]
    bottoms = [w.get("bottom", 0) for w in words]
    if page_height > 0:
        rel_top = min(tops) / page_height
        rel_bottom = max(bottoms) / page_height
        in_margin = rel_top < _MARGIN_BAND or rel_bottom > 1 - _MARGIN_BAND
    else:
        rel_top = rel_bottom = 0.0
        in_margin = False
    return {
        "text": text,
        "avg_size": avg_size,
        "page_no": page_no,
        # 绝对上沿：表格区域的 Markdown 要按它插回本页的阅读顺序
        "top": min(tops) if tops else 0.0,
        "page_body_size": page_body_size,
        "rel_top": rel_top,
        "rel_bottom": rel_bottom,
        "in_margin": in_margin,
    }
