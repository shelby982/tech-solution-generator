"""
DOCX 生成服务
将 Markdown 章节内容转换为格式规范的 Word 文档（.docx）
依赖：python-docx（已在 requirements.txt 中）
"""

import io
import re
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 公共入口
# ─────────────────────────────────────────────

def sections_to_docx(
    sections: list[dict],
    doc_title: str = "技术方案",
    *, template: str | None = None, images: dict | None = None, include_toc: bool = False, template_data: bytes | None = None,
) -> bytes:
    """
    将章节数据列表转为 DOCX 字节流。

    sections 格式：
        [{"id": "s1", "title": "...", "level": 1, "content": "...", "done": True}, ...]

    Returns:
        bytes — DOCX 文件内容
    """
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor

    if template:
        from .template_store import load_template
        template_data = template_data or load_template(template)[0]
    doc = Document(io.BytesIO(template_data)) if template_data else Document()

    if not template_data:
        sec = doc.sections[0]
        sec.page_width, sec.page_height = Cm(21), Cm(29.7)
        sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(2.5)
        doc.styles['Normal'].font.name = '宋体'
        doc.styles['Normal'].font.size = Pt(11)
        _apply_heading_styles(doc)
    if template:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        if include_toc:
            doc.add_heading(doc_title, level=0)
            doc.add_paragraph('目录', style='Title')
            paragraph = doc.add_paragraph()
            toc = OxmlElement('w:fldSimple')
            toc.set(qn('w:instr'), 'TOC \\o "1-9" \\h \\z \\u')
            paragraph._p.append(toc)
            doc.add_paragraph('目录页码在 Word 中更新域后显示。')
            doc.add_page_break()

    # ── 各章节 ────────────────────────────────
    for sec_data in sections:
        if not sec_data.get("done"):
            continue

        sec_level = max(1, min(int(sec_data.get("level", 1)), 9 if template else 4))
        sec_title = sec_data.get("title", "")
        sec_content = sec_data.get("content", "")
        sec_content = _strip_source_markers(sec_content)

        doc.add_heading(sec_title, level=sec_level)
        if sec_content.strip():
            if not template:
                sec_content = _strip_leading_headings(sec_content)
            else:
                # 仅去除与本章同名的重复抬头，保留用户编写的第一节标题。
                first = re.match(r'^\s*#{1,6}\s+([^\n]+)\n?', sec_content)
                if first and first.group(1).strip() == sec_title.strip():
                    sec_content = sec_content[first.end():]
            from .document_model import parse_markdown, render_blocks
            model = sec_data.get('document_blocks')
            render_blocks(doc, model if model is not None else parse_markdown(sec_content),
                          chapter_level=sec_level, images=images)

    # ── 序列化 ────────────────────────────────
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────
# 辅助：剥离溯源标记
# ─────────────────────────────────────────────

_SOURCE_MARKER_RE = re.compile(r'\[/?[GME]\]')

def _strip_source_markers(content: str) -> str:
    """去除 [G][/G][M][/M][E][/E] 溯源标记，导出时不保留"""
    return _SOURCE_MARKER_RE.sub('', content)


# ─────────────────────────────────────────────
# 辅助：剥离内容首部重复标题行
# ─────────────────────────────────────────────

def _strip_leading_headings(content: str) -> str:
    """去除 LLM 输出内容开头的 # 标题行，避免与 doc.add_heading 产生重复标题"""
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if re.match(r'^#{1,6}\s+', stripped) or stripped == "":
            i += 1
        else:
            break
    return "\n".join(lines[i:])


# ─────────────────────────────────────────────
# 占位符识别（与前端 decoratePlaceholders 保持一致）
# ─────────────────────────────────────────────

# 整行图形占位符：【架构图：xx】（独占一行时插入占位图片 + 图说）
_PH_IMG_BLOCK_PAT = re.compile(
    r'^\s*【(架构图|流程图|拓扑图|部署图|时序图|网络图|示意图)[：:]\s*([^】]+)】\s*$'
)
# 行内 inline 占位符：先匹配最长，避免局部覆盖
_PH_INLINE_PAT = re.compile(
    r'【(待补充)[：:]\s*([^】]+)】'
    r'|【(公司全称|公司名称|投标人名称|签署日期|签字日期|签字人|法定代表人|授权代表|联系电话|联系地址|项目名称|项目编号)】'
)


def _add_placeholder_image(doc, kind: str, desc: str):
    """绘制 600×360 灰底虚线占位 PNG，按图片插入；下方加图说。"""
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (600, 360), (244, 245, 248))
        draw = ImageDraw.Draw(img)
        # 虚线边框（PIL 不支持 dash，用短线段循环模拟）
        dash = 12
        gap = 8
        x0, y0, x1, y1 = 4, 4, 595, 355
        # 上下边
        for x in range(x0, x1, dash + gap):
            draw.line([(x, y0), (min(x + dash, x1), y0)], fill=(150, 160, 200), width=2)
            draw.line([(x, y1), (min(x + dash, x1), y1)], fill=(150, 160, 200), width=2)
        # 左右边
        for y in range(y0, y1, dash + gap):
            draw.line([(x0, y), (x0, min(y + dash, y1))], fill=(150, 160, 200), width=2)
            draw.line([(x1, y), (x1, min(y + dash, y1))], fill=(150, 160, 200), width=2)
        # 中央文字（用默认字体；中文显示靠 Word 渲染图说，不强求 PIL 渲染）
        try:
            label = f"[{kind}]"
            draw.text((280, 165), label, fill=(80, 95, 145))
        except Exception:
            pass
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        run.add_picture(buf, width=Inches(5.5))
    except Exception as exc:
        # PIL 不可用或绘制失败：退化为带边框的灰色段落
        logger.warning(f"占位图绘制失败，退化为文字占位：{exc}")
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(f"[ {kind}：{desc} ]")
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x60, 0x70, 0x95)

    # 图说
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap_run = cap.add_run(f"图：{desc}")
    cap_run.font.size = Pt(10)
    cap_run.font.color.rgb = RGBColor(0x60, 0x70, 0x80)
    cap_run.italic = True


# ─────────────────────────────────────────────
# 样式设置
# ─────────────────────────────────────────────

def _apply_heading_styles(doc):
    """微调内置 Heading 样式的颜色与间距"""
    from docx.shared import Pt, RGBColor

    mapping = {
        "Heading 1": (16, "1F3864", 240, 120),
        "Heading 2": (14, "2E5496", 200, 80),
        "Heading 3": (12, "2E75B6", 160, 60),
        "Heading 4": (11, "404040", 120, 40),
    }
    for name, (size, color_hex, before, after) in mapping.items():
        try:
            st = doc.styles[name]
            st.font.size = Pt(size)
            r, g, b = int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16)
            st.font.color.rgb = RGBColor(r, g, b)
            st.paragraph_format.space_before = Pt(before / 20)
            st.paragraph_format.space_after  = Pt(after  / 20)
        except Exception:
            pass  # 样式不存在时静默忽略


def _add_runs_with_placeholders(para, text: str):
    """在普通文本里识别并着色占位符：【待补充：xx】红字、【公司全称】等变量蓝字。"""
    from docx.shared import RGBColor

    pos = 0
    for m in _PH_INLINE_PAT.finditer(text):
        if m.start() > pos:
            para.add_run(text[pos:m.start()])
        todo_label, todo_desc, var_name = m.group(1), m.group(2), m.group(3)
        if todo_label:
            run = para.add_run(f"【待补充：{todo_desc}】")
            run.bold = True
            run.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
        elif var_name:
            run = para.add_run(f"【{var_name}】")
            run.bold = True
            run.font.color.rgb = RGBColor(0x1D, 0x4E, 0xD8)
        pos = m.end()
    if pos < len(text):
        para.add_run(text[pos:])


def _add_studio_image(doc, data, title):
    from docx.shared import Emu
    section = doc.sections[-1]
    width = section.page_width - section.left_margin - section.right_margin
    paragraph = doc.add_paragraph()
    paragraph.alignment = 1
    from PIL import Image
    from docx.shared import Cm
    with Image.open(io.BytesIO(data)) as image:
        height = width * image.height / image.width
    max_height = Cm(21)
    if height > max_height:
        paragraph.add_run().add_picture(io.BytesIO(data), height=Emu(max_height))
    else:
        paragraph.add_run().add_picture(io.BytesIO(data), width=Emu(width))
    caption = doc.add_paragraph(style='Caption')
    caption.alignment = 1
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    caption.add_run('图 ')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'SEQ Figure \\* ARABIC')
    run = OxmlElement('w:r')
    text = OxmlElement('w:t')
    text.text = str(len(doc.inline_shapes))
    run.append(text)
    field.append(run)
    caption._p.append(field)
    caption.add_run(' ' + title)
