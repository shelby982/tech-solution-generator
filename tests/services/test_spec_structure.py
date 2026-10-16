from services.spec_structure import build_spec_structure


def test_preserves_source_order_parents_and_marks_without_semantic_regrouping():
    sections = [
        dict(title='1. 总则', level=1, source_file='a', raw_content='原文概述'),
        dict(title='1.1. 要求', level=2, source_file='a'),
        dict(title='2. 项目', level=1, source_file='a'),
        dict(title='2.1. 要求', level=2, source_file='a', special_marks=['★']),
    ]
    rows = [dict(requirement_id='b', source_file='a', title='2. 项目 > 2.1. 要求 · 第 1 条', topic='要求'),
            dict(requirement_id='a', source_file='a', title='1. 总则 > 1.1. 要求', topic='要求')]
    tree = build_spec_structure(rows, sections)
    assert [n['topic'] for n in tree] == ['1. 总则', '2. 项目']
    assert tree[0]['children'][0]['requirements'][0]['requirement_id'] == 'a'
    assert tree[1]['children'][0]['display_title'] == '2.1. ★要求'
    assert tree[0]['original_content'] == '原文概述'
    assert sum(n['count'] for n in tree) == len(rows)


def test_file_boundaries_and_unmatched_rows_are_not_silently_lost():
    sections = [dict(title='1. 要求', level=1, source_file=f) for f in ['a', 'b']]
    rows = [dict(source_file='b', title='1. 要求', text='B'),
            dict(source_file='a', title='未定位', text='A')]
    tree = build_spec_structure(rows, sections)
    assert tree[0]['requirements'][0]['text'] == 'B'
    assert tree[-1]['topic'] == '原文位置待核对'
    assert sum(n['count'] for n in tree) == 2
