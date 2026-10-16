from infra.parser.technical_response import build_technical_response
from infra.parser.review_points import ReviewPoint
from infra.parser.tech_requirements import TechRequirement
from services.point_store import stable_requirement_id
from agents.zhang_heng import _ground_sections
from domain.spec import Section
from infra.parser.technical_response import check_response_structure
from domain.spec.outline_draft import normalize_nodes, build_sections


def requirement(path, text='必须提供方案', filename='规范.docx'):
    return TechRequirement(source_location=path, requirement_text=text,
                           mandatory_level='mandatory', source_file=filename)


def test_scoring_then_priority_then_exact_source_tree_and_full_original():
    points=[ReviewPoint(category='tech',factor='技术方案',item='总体架构',criterion='架构设计',point_id='p1',points=10)]
    sections=[dict(title='1. 总则',level=1,raw_content='背景全文',source_file='规范.docx'),
              dict(title='1.1. 安全要求',level=2,raw_content='全文'*4000,special_marks=['★'],source_file='规范.docx'),
              dict(title='2. 实施',level=1,raw_content='应提供计划',source_file='规范.docx')]
    req=requirement('1. 总则 > 1.1. 安全要求 · 第 1 条')
    nodes=build_technical_response(points,sections,[req])
    roots=[n.title for n in nodes if n.level==1]
    assert roots==['技术评分项应答','重点及必须响应要求','技术规范书点对点应答']
    original=[n for n in nodes if n.metadata.get('source_path') and n.metadata['response_part']=='point_to_point']
    assert [n.title for n in original]==['1. 总则','1.1. ★安全要求','2. 实施']
    assert [n.level for n in original]==[2,3,2]
    assert len(original[1].source_content)==8000
    rid=stable_requirement_id(req)
    assert original[1].spec_requirement_ids==[rid]
    assert any(n.metadata.get('response_part')=='priority' and rid in n.spec_requirement_ids for n in nodes)
    assert not any('规范要求 ' in n.title for n in nodes)


def test_priority_can_be_nested_under_scoring_but_point_to_point_stays_last():
    point=ReviewPoint(category='tech',factor='技术方案',item='自主可控',point_id='p')
    sections=[dict(title='自主可控要求',level=1,raw_content='要求',special_marks=['★'],source_file='规范.docx')]
    nodes=build_technical_response([point],sections,[])
    priority=next(n for n in nodes if n.metadata.get('response_part')=='priority')
    assert priority.review_point_id=='p'
    assert '重点及必须响应要求' not in [n.title for n in nodes]
    assert nodes[-1].title=='★自主可控要求'


def test_same_titles_in_different_files_and_context_only_sections_are_preserved():
    sections=[dict(title='1. 总则',level=1,raw_content=f,source_file=f) for f in ['甲.docx','乙.docx']]
    nodes=build_technical_response([],sections,[])
    original=[n for n in nodes if n.metadata.get('source_path')]
    assert [n.title for n in original]==['1. 总则','1. 总则']
    assert [n.source_content for n in original]==['甲.docx','乙.docx']
    assert all(n.level==3 for n in original)


def test_exact_response_content_is_not_replaced_by_fuzzy_grounding():
    exact=Section(id='s1',level=1,title='总则',raw_content='完整正文'*2000,chain={'response_part':'point_to_point'})
    _ground_sections([exact],[Section(id='x',level=1,title='总则',raw_content='其他文件')])
    assert exact.raw_content=='完整正文'*2000


def test_deep_and_duplicate_original_headings_survive_normalization_and_are_checked():
    raw=[{'title':'点对点应答','level':1,'response_part':'point_to_point'}]
    sections=[]
    for i in range(1,7):
        sections.append(dict(title=f'{i} 章节',level=i,source_file='规范.docx'))
        raw.append(dict(title=f'{i} 章节',level=i+1,parent=i-1,response_part='point_to_point',
                        source_path=[str(i)],source_file='规范.docx'))
    raw.append({**raw[-1], 'parent':4})
    nodes,_=normalize_nodes(raw,max_level=7,preserve_duplicates=True)
    assert len(nodes)==8
    assert build_sections(nodes,max_level=7)[6].level==7
    toc=[s.to_dict() for s in build_sections(nodes[:-1],max_level=7)]
    checks=check_response_structure(toc,sections,[],['规范.docx'])
    assert checks['original_structure_matches']
    toc[-1]['title']='改写的标题'
    assert not check_response_structure(toc,sections,[],['规范.docx'])['original_structure_matches']


def test_priority_must_be_visible_before_point_to_point_section():
    path={'source_file':'规范.docx','source_path':['安全']}
    priority={'title':'安全','level':1,'chain':{**path,'response_part':'priority'}}
    original={'title':'安全','level':2,'chain':{**path,'response_part':'point_to_point'}}
    sections=[{'title':'安全','level':1,'source_file':'规范.docx'}]
    assert check_response_structure([priority,original],sections,[],['规范.docx'],[path])['priority_sections_visible']==1
    assert check_response_structure([original],sections,[],['规范.docx'],[path])['priority_sections_visible']==0
    assert check_response_structure([original],sections,[],['规范.docx'],[path])['missing_priority_sections']==[path]
