from infra.parser.technical_response import check_response_structure


def sample(text='必须提供实施计划', marked=False):
    sections=[dict(title='1. 实施要求',level=1,source_file='甲.docx',special_marks=['★'] if marked else [])]
    rows=[dict(requirement_id='r1',title='1. 实施要求',text=text,source_file='甲.docx',mandatory_level='mandatory')]
    original=dict(title='1. ★实施要求' if marked else '1. 实施要求',level=2,
                  chain=dict(response_part='point_to_point',source_file='甲.docx',source_path=['1. 实施要求'],spec_requirement_ids=['r1']))
    paths=[dict(source_file='甲.docx',source_path=['1. 实施要求'])]
    return sections,rows,original,paths


def check(toc, sections, rows, paths):
    return check_response_structure(toc,sections,rows,['甲.docx'],paths)


def test_merged_scoring_chapter_accepts_links_without_old_priority_title():
    sections,rows,original,paths=sample()
    merged=dict(title='总体方案与实施保障',level=1,
                chain=dict(response_part='scoring',spec_requirement_ids=['r1']))
    result=check([merged,original],sections,rows,paths)
    assert result['can_confirm']
    assert result['focus_requirements_in_front']==1
    assert result['warnings']==[]
    assert result['priority_sections_visible']==0  # 旧章节已删除，仍通过


def test_ordinary_mandatory_only_in_point_to_point_is_advisory():
    sections,rows,original,paths=sample()
    result=check([original],sections,rows,paths)
    assert result['can_confirm']
    assert len(result['warnings'])==1
    assert result['blocking_issues']==[]


def test_symbol_without_explicit_meaning_is_not_a_blocking_risk():
    sections,rows,original,paths=sample('提供实施计划',marked=True)
    result=check([original],sections,rows,paths)
    assert result['can_confirm']
    assert result['risk_requirements_total']==0
    assert len(result['warnings'])==1


def test_explicit_risk_with_point_to_point_landing_is_allowed_and_flagged():
    sections,rows,original,paths=sample('不满足本条要求则否决投标')
    result=check([original],sections,rows,paths)
    assert result['can_confirm']
    assert result['risk_requirements_landed']==1
    assert result['warnings'][0]['critical']


def test_explicit_risk_without_any_landing_blocks_confirmation():
    sections,rows,original,paths=sample('不满足本条要求则否决投标')
    original['chain']['spec_requirement_ids']=[]
    result=check([original],sections,rows,paths)
    assert not result['can_confirm']
    assert result['missing_risk_requirements'][0]['requirement_id']=='r1'
    assert result['point_to_point_requirements']==0


def test_renaming_source_heading_still_blocks_confirmation():
    sections,rows,original,paths=sample()
    original['title']='自行提炼的标题'
    result=check([original],sections,rows,paths)
    assert not result['can_confirm']
    assert not result['original_structure_matches']
