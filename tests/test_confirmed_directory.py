from services.confirmed_directory import body_only
from infra.docx.document_model import parse_markdown


def test_body_cannot_add_headings_to_confirmed_word_directory():
    original = '# 本章\n\n## AI 新增章节\n内容。\n\n次级标题\n---\n\n> ### 引用中的标题\n\n| 表 | 值 |\n| --- | --- |\n| a | 3 |\n'
    result = body_only(original, '本章')
    assert '内容。' in result and '| a | 3 |' in result
    assert '**AI 新增章节**' in result
    assert '**次级标题**' in result
    assert not any(block['type'] == 'heading' for block in parse_markdown(result))
    assert body_only(result, '本章') == result


def test_code_example_headings_remain_code():
    content = '```markdown\n# 示例代码\n```\n\n## 正文分项\n内容'
    assert '# 示例代码' in body_only(content)
    assert '**正文分项**' in body_only(content)
