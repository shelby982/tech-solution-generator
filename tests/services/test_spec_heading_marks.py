from services.spec_attention import annotate_requirements
from services.spec_attention import original_topic_title


def test_topic_star_stays_on_original_heading_only():
    sections=[{'title':'3.4.2. 自主可控要求','source_file':'甲.docx','special_marks':['★']}]
    assert original_topic_title('3.4.2. 自主可控要求',['甲.docx'],sections)=='3.4.2. ★自主可控要求'
    assert original_topic_title('3.4. 技术管控',['甲.docx'],sections)=='3.4. 技术管控'
    assert original_topic_title('3.4.2. 自主可控要求',['乙.docx'],sections)=='3.4.2. 自主可控要求'


def test_separate_section_star_is_inherited_by_clean_requirement_path():
    text='本项目涉及的产品必须符合自主可控要求'
    rows=[{'text':text,'title':'3. 项目服务 > 3.4. 技术管控 > 3.4.2. 自主可控要求 · 第 2 条','source_file':'规范.docx'}]
    sections=[{'title':'3.4.2. 自主可控要求','special_marks':['★'],'source_file':'规范.docx'}]
    result=annotate_requirements(rows,sections)[0]
    assert result['text']==text
    assert result['attention']['markers']==['★']
    assert result['attention']['inherited']
    assert 'marked' in result['attention']['flags']
    assert 'check' in result['attention']['flags']
    assert 'veto' not in result['attention']['flags']


def test_same_heading_in_other_file_does_not_inherit():
    rows=[{'text':'CPU兼容要求','title':'自主可控要求','source_file':'乙.docx'}]
    sections=[{'title':'自主可控要求','special_marks':['★'],'source_file':'甲.docx'}]
    assert annotate_requirements(rows,sections)[0]['attention']['markers']==[]
