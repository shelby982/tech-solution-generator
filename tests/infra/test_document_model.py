import io
from docx import Document
import pytest
from infra.docx import sections_to_docx
from infra.docx.document_model import parse_markdown, model_html, image_urls
from infra.docx.template_store import load_template


def test_commonmark_nested_lists_tables_inline_and_code_preserve_semantics():
    blocks=parse_markdown('3. **验证方法**\n   - *子项*\n     - `a|b`\n\n| 字段 | 说明 |\n| --- | --- |\n| a\\|b | **完整内容** |\n\n```text\n:::diagram 非图示\nA -> B\n:::\n```')
    paragraphs=[b for b in blocks if b['type']=='paragraph']
    assert paragraphs[0]['list']['number']==3
    assert paragraphs[2]['list']['depth']==3
    table=next(b for b in blocks if b['type']=='table')
    assert table['rows'][1][0][0]['text']=='a|b'
    assert any(r.get('bold') for r in table['rows'][1][1])
    assert blocks[-1]['type']=='code'
    assert not any(b['type']=='diagram' for b in blocks)


def test_body_headings_are_relative_to_chapter_and_template_styles_are_loaded():
    data=sections_to_docx([{'title':'第三层章节','level':3,'done':True,'content':'# 第一节\n\n正文\n\n## 子节\n\n说明'}],template='formal')
    doc=Document(io.BytesIO(data))
    headings=[(p.text,p.style.name) for p in doc.paragraphs if p.style.name.startswith('Heading')]
    assert headings==[('第三层章节','Heading 3'),('第一节','Heading 4'),('子节','Heading 5')]
    assert doc.styles['Normal'].font.name=='仿宋'


def test_model_html_escapes_untrusted_markup_and_rejects_remote_images():
    html=model_html(parse_markdown('<script>alert(1)</script>\n\n**安全文本**'))
    assert '<script>' not in html and '<strong>安全文本</strong>' in html
    with pytest.raises(ValueError):list(image_urls(parse_markdown('![图](https://example.com/image.png)')))
    assert list(image_urls(parse_markdown('![图](/api/studio/materials/12/image "题注")')))==['/api/studio/materials/12/image']


def test_frozen_template_bytes_control_output_even_if_template_file_changes(monkeypatch):
    data,version=load_template('formal')
    assert len(version)==64
    monkeypatch.setattr('infra.docx.template_store.load_template',lambda key:(_ for _ in ()).throw(AssertionError('不能读新模板')))
    output=sections_to_docx([{'title':'测试','done':True,'content':'正文'}],template='formal',template_data=data)
    assert Document(io.BytesIO(output)).styles['Normal'].font.name=='仿宋'


def test_unclosed_diagram_is_explicit_error():
    with pytest.raises(ValueError,match='结束标记'):parse_markdown(':::diagram 流程\nA -> B')
