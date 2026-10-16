from services.spec_attention import annotate_requirements


def annotate(text, title='', rules='', source='a'):
    return annotate_requirements([{'text':text,'title':title,'source_file':source}],
                                 [{'source_file':'a','raw_content':rules}])[0]['attention']


def test_star_alone_is_not_a_veto():
    result=annotate('★系统支持备份')
    assert set(result['flags'])=={'marked','check'}


def test_explicit_document_rule_maps_marker():
    result=annotate('★系统支持备份',rules='标有★的条款不满足将否决投标。')
    assert 'veto' in result['flags']
    assert result['evidence'][0]['basis']=='同文件标记说明'


def test_rules_do_not_leak_across_files():
    assert 'veto' not in annotate('★系统支持备份',rules='★条款不满足将否决投标',source='b')['flags']


def test_heading_marker_does_not_automatically_apply_to_children():
    result=annotate('支持备份',title='★4 技术要求',rules='★条款不满足将否决投标')
    assert 'veto' not in result['flags']
    assert result['inherited'] and 'check' in result['flags']


def test_explicit_veto_and_negated_veto():
    assert 'veto' in annotate('未提供证书将否决投标')['flags']
    assert 'veto' not in annotate('★缺少此项不作为否决投标依据')['flags']


def test_substantive_and_evidence_requirements():
    result=annotate('本项不得负偏离，必须提供检测报告。')
    assert 'substantive' in result['flags']
    assert 'proof' in result['flags']


def test_original_content_is_preserved():
    text='▲原文\n完整条款'
    assert annotate_requirements([{'text':text}])[0]['text']==text
