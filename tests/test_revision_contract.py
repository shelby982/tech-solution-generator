import pytest
from services.revision_contract import validate_revision


def test_edit_requires_original_facts_and_citations():
    original = '可用性 99.9%，依据【来源：技术规范书】。'
    validate_revision(original, '依据【来源：技术规范书】，可用性目标为 99.9%。', '精简措辞')
    with pytest.raises(ValueError, match='引用'):
        validate_revision(original, '可用性为 99.9%。', '精简措辞')
    with pytest.raises(ValueError, match='数值'):
        validate_revision(original, '可用性 99%，依据【来源：技术规范书】。', '优化表述')


def test_ordinary_edit_keeps_tables_and_graphs():
    original = '引言。\n\n| 指标 | 值 |\n| --- | --- |\n| 数量 | 3 |\n'
    validate_revision(original, original.replace('引言。', '调整后的引言。'), '精简说明')
    with pytest.raises(ValueError, match='表格'):
        validate_revision(original, '采用 3 项指标。', '精简说明')
    graph = ':::diagram type=flow 架构\n入口\n处理\n:::'
    with pytest.raises(ValueError, match='图示'):
        validate_revision(graph, '处理流程说明。', '精简文字')
