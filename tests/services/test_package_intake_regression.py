from services.package_analysis import extract_package_mentions, analyze_packages


def test_year_is_not_a_package_number():
    assert extract_package_mentions('适用标包：2026年南网软件开发项目') == []
    assert extract_package_mentions('标包20：软件开发')[0]['package_no'] == '20'


def test_unique_parent_merges_partial_package_mentions():
    result = analyze_packages([
        {'material_id': 1, 'content': '标的1标包2'},
        {'material_id': 2, 'content': '标包2的技术要求'},
    ])
    assert len(result) == 1
    assert result[0]['target'] == '1'
    assert result[0]['mentions'] == 2


def test_ambiguous_parent_does_not_guess():
    result = analyze_packages([
        {'material_id': 1, 'content': '标的1标包2，标的3标包2'},
        {'material_id': 2, 'content': '标包2的技术要求'},
    ])
    assert len(result) == 3
