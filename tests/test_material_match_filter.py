"""推荐结果不应把原始 Markdown 表格当作证据段落。"""
from services.material_match import recommend, section_profile


def test_raw_table_filtered_unless_table_wanted():
    prose = {"chunk_id": 1, "text": "本项目采用云原生架构，服务网关统一接入。", "tags": [], "role": "spec"}
    table = {"chunk_id": 2, "text": "| 项 | 值 |\n| --- | --- |\n| A | B |", "tags": [], "role": "spec"}
    profile = section_profile({"title": "总体架构", "points": ["云原生"], "deliverables": []})
    picked = recommend(profile, [table, prose], limit=5, min_score=0.0)
    assert [item["chunk_id"] for item in picked] == [1]
