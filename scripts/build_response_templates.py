"""重建内置交付样式模板。模板本体不包含用户正文。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from services.chapter_studio import TEMPLATES


def build():
    from docx import Document
    from docx.shared import Cm, Pt, RGBColor
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    root = Path(__file__).resolve().parents[1] / 'backend/infra/docx/templates'
    root.mkdir(exist_ok=True)
    for key, preset in TEMPLATES.items():
        doc = Document()
        section = doc.sections[0]
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.left_margin = section.right_margin = section.top_margin = section.bottom_margin = Cm(preset['margin'])
        normal = doc.styles['Normal']
        normal.font.name = preset['font']; normal.font.size = Pt(preset['size'])
        normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), preset['font'])
        normal.paragraph_format.line_spacing = 1.5
        normal.paragraph_format.space_after = Pt(6)
        for level in range(1,10):
            style = doc.styles[f'Heading {level}']
            style.font.name = preset['font'];style.font.size = Pt(max(11,17-level))
            style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),preset['font'])
            style.font.color.rgb=RGBColor(0,0,0);style.font.bold=True
            style.paragraph_format.keep_with_next=True
            style.paragraph_format.space_before=Pt(12);style.paragraph_format.space_after=Pt(6)
        title=doc.styles['Title'];title.font.name=preset['font'];title.font.size=Pt(22)
        title.font.color.rgb=RGBColor(0,0,0)
        title.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),preset['font'])
        caption=doc.styles['Caption'];caption.font.name=preset['font'];caption.font.size=Pt(10)
        caption.font.color.rgb=RGBColor(64,64,64);caption.font.italic=False
        code=doc.styles.add_style('Code Block',WD_STYLE_TYPE.PARAGRAPH)
        code.base_style=normal;code.font.name='Consolas';code.font.size=Pt(9)
        table=doc.styles.add_style('Response Table',WD_STYLE_TYPE.TABLE)
        table.base_style=doc.styles['Table Grid'];table.font.name=preset['font'];table.font.size=Pt(10)
        footer=section.footer.paragraphs[0];footer.alignment=1
        field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
        settings=OxmlElement('w:updateFields');settings.set(qn('w:val'),'true');doc.settings.element.append(settings)
        doc.core_properties.title='应答文档基础样式模板'
        doc.save(root/f'{key}.docx')
        print(root/f'{key}.docx')


if __name__ == '__main__': build()
