"""Deterministic protections applied before an AI edit can be saved."""
import re


def validate_revision(original: str, revised: str, instruction: str) -> None:
    if not revised.strip():
        raise ValueError('未生成有效修改，原文已保留')
    citations = set(re.findall(r'【来源[:：][^】]+】', original))
    if not citations.issubset(set(re.findall(r'【来源[:：][^】]+】', revised))):
        raise ValueError('修改遗漏原文引用，结果已保留待核对')
    numeric_change = re.search(r'(?:修改|更新|更正|调整|替换|删除|去掉).{0,16}(?:数字|数值|指标|参数|日期|年份|金额|\d)', instruction)
    if not numeric_change:
        # Ignore ordered-list and heading numbers; factual values must remain present.
        def values(text):
            text = re.sub(r'(?m)^\s*(?:#{1,6}\s*)?\d+[.、)](?:\s+|$)', '', text)
            return set(re.findall(r'\d+(?:\.\d+)*(?:%|％)?', text))
        if not values(original).issubset(values(revised)):
            raise ValueError('修改遗漏或改变原文数值，结果已保留待核对')
    for pattern, label, permission in (
        (r'(?ms)^:::diagram[^\n]*\n.*?^:::[ \t]*$', '图示', r'图示|图片|架构图|关系图|流程图'),
        (r'(?m)(?:^[ \t]*\|[^\n]+\n?){2,}', '表格', r'表格|表中|表头|行列'),
    ):
        if not re.search(permission, instruction):
            for artifact in re.findall(pattern, original):
                if artifact.strip() not in revised:
                    raise ValueError(f'修改改变了原有{label}，结果已保留待核对')
